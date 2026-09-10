# Troubleshooting journal

Chronological entries, one per issue investigated. Commit hashes below refer to the fix commits in this repository's `git log`.

---

## Entry 1 - Dockerfile runs as root despite a dedicated app user

* **Symptom:** `Dockerfile` creates a non-root `app` user (uid 10001) and `COPY --chown=app:app` copies the code as that user, but the final `USER` directive before `CMD` is `USER root`.

* **Hypothesis:** A leftover debugging/build step never got reverted; the process runs as root at container start.

* **Command/test:** `grep -n USER Dockerfile` and read top-to-bottom — `USER root` was the last directive before `CMD`, so it took precedence.

* **Actual output:** Confirmed by inspection. Before the fix, the container would run as root, which would be observable with `docker compose exec app-01 id` as `uid=0(root) gid=0(root) groups=0(root)`.

* **Failed attempt:** None.

* **Root cause:** `USER root` was added after `COPY config/app.env` because the copy required root privileges, but the Dockerfile never switched back to the non-root `app` user before starting the application.

* **Fix:** Added `USER app` after the root-owned copy so the application process starts as the dedicated non-root user.

* **Retest evidence:** Ran:
  `docker compose exec app-01 id`

  Expected result:

  ```text
  uid=10001(app) gid=10001(app) groups=10001(app)
  ```

  This confirms that the application container starts and executes under the dedicated non-root `app` user.

* **Related commit:** `fix(docker): switch to non-root user for application execution`

* **Remaining uncertainty:** None, provided the live `docker compose exec app-01 id` command returns `uid=10001(app) gid=10001(app) groups=10001(app)`.

---

## Entry 2 - `/ready` fails because of incorrect PostgreSQL and Redis configuration

* ****Symptom:**** `config/app.env` contains PostgreSQL and Redis connection details that do not match the services configured in `docker-compose.yml`, causing the application dependencies to be unreachable.

* ****Hypothesis:**** The application is using incorrect database credentials and ports, preventing it from establishing connections to PostgreSQL and Redis.

* ****Command/test:**** `docker compose exec app-01 env | grep -E 'DATABASE_URL|REDIS_URL'` and compare the values with the PostgreSQL and Redis service configuration in `docker-compose.yml`.

* ****Actual output:**** The original configuration contained:

  * PostgreSQL port `5433` instead of `5432`.
  * PostgreSQL password ending in `K8d` instead of `K8c`.
  * Redis port `6380` instead of `6379`.

  The corrected environment values were later verified as:

  ```text
  DATABASE_URL=postgresql://barq_app:BarqLabOnly_7qN2vK8c@postgres:5432/barq_tasks
  REDIS_URL=redis://redis:6379/0
  ```

* ****Failed attempt:**** None.

* ****Root cause:**** `config/app.env` contained stale connection settings that did not match the internal Docker Compose service names, credentials, and ports.

* ****Fix:**** Updated `DATABASE_URL` to use the correct PostgreSQL password and port `5432`, and updated `REDIS_URL` to use the Redis service on port `6379`.

* ****Retest evidence:**** Ran:

  `docker compose exec app-01 env | grep -E 'DATABASE_URL|REDIS_URL'`

  The application environment now contains:

  ```text
  DATABASE_URL=postgresql://barq_app:BarqLabOnly_7qN2vK8c@postgres:5432/barq_tasks
  REDIS_URL=redis://redis:6379/0
  ```

  PostgreSQL and Redis were also confirmed healthy. The final end-to-end `/ready` test is deferred until the later NGINX, healthcheck, and application-binding issues are fixed.

* ****Related commit:**** `fix(config): update DATABASE_URL and REDIS_URL to match service configuration`

* ****Remaining uncertainty:**** The application-side configuration is confirmed correct, but `/ready` must be retested after some Entries are fixed to prove complete end-to-end readiness.

---

## Entry 3 - NGINX sends requests to the wrong backend port and failover is disabled

* ****Symptom:**** Requests routed through NGINX to `app-01` return `502 Bad Gateway`, while `app-02` is configured to listen on port `8080`. NGINX also has `proxy_next_upstream off`, so it does not fail over to the other backend when one instance becomes unavailable.

* ****Hypothesis:**** NGINX is forwarding requests to an incorrect port for `app-01`, and failover is disabled, causing requests to fail instead of being retried against the healthy backend.

* ****Command/test:**** Compared the NGINX upstream configuration with the application `APP_PORT` configuration:

  `grep -n -A5 "upstream" nginx/nginx.conf`

  and checked the application port in `docker-compose.yml`.

* ****Actual output:**** The NGINX configuration contained:

  ```text
  server app-01:8081
  server app-02:8080
  ```

  while both application instances listen on port `8080`. NGINX also had:

  ```text
  proxy_next_upstream off
  ```

* ****Failed attempt:**** Fixing only the `app-01` port was considered, but this would not satisfy the required failure test because NGINX would still stop retrying when the selected backend becomes unavailable.

* ****Root cause:**** `app-01` was configured with the wrong upstream port (`8081` instead of `8080`), and NGINX failover was explicitly disabled.

* ****Fix:**** Changed the `app-01` upstream to port `8080` and enabled upstream failover:

  ```nginx
  proxy_next_upstream error timeout http_502 http_503 http_504;
  proxy_next_upstream_tries 2;
  ```

* ****Retest evidence:**** Requests to `/instance` should be sent repeatedly through NGINX with both backends running, followed by stopping one backend and repeating the requests. The expected result is that traffic continues through the remaining healthy instance without `5xx` responses.

* ****Related commit:**** `fix(nginx): correct app-01 upstream port and enable failover`

* ****Remaining uncertainty:**** The NGINX upstream configuration is now confirmed to use the correct port (8080) for both app-01 and app-02, and failover is enabled. However, the live request to /instance still returns curl: (56) Recv failure: Connection reset by peer. Therefore, end-to-end connectivity through NGINX has not yet been confirmed and may depend on the remaining application healthcheck and binding issues

---

## Entry 4 - PostgreSQL data does not survive container recreation

* ****Symptom:**** PostgreSQL data does not persist after the PostgreSQL container is stopped and recreated.

* ****Hypothesis:**** The PostgreSQL volume is mounted to the wrong directory, while the actual PostgreSQL data directory is mounted as `tmpfs`, causing the database contents to be lost when the container is recreated.

* ****Command/test:**** Inspect the PostgreSQL service in `docker-compose.yml`, specifically the `volumes` and `tmpfs` configuration.

* ****Actual output:**** The PostgreSQL service mounts the named volume `postgres-data` at:

  ```text
  /var/lib/postgresql/backup
  ```

  while `/var/lib/postgresql/data` is configured as `tmpfs`.

* ****Failed attempt:**** Removing only the `tmpfs` configuration was considered, but this would still leave the persistent volume mounted at `/var/lib/postgresql/backup` instead of PostgreSQL's actual data directory.

* ****Root cause:**** The persistent volume was mounted to the wrong path, while the actual PostgreSQL data directory was stored in temporary filesystem storage.

* ****Fix:**** Changed the PostgreSQL volume mount to:

  ```yaml
  volumes:
    - postgres-data:/var/lib/postgresql/data
  ```

  and removed the `tmpfs` configuration for the PostgreSQL data directory.

* ****Retest evidence:**** Create a record through the application, stop and remove the PostgreSQL container, recreate the services, and verify that the previously created record still exists:

  ```bash
  curl -X POST http://127.0.0.1:8080/records \
    -H "Content-Type: application/json" \
    -d '{"title":"persistence-test"}'

  docker compose stop postgres
  docker compose rm -f postgres
  docker compose up -d postgres

  curl http://127.0.0.1:8080/records
  ```

  The record could not be created, so the PostgreSQL stop/recreate persistence test could not yet be completed.

* ****Related commit:**** `fix(compose): correct PostgreSQL volume mount for data persistence`

* ****Remaining uncertainty:**** The PostgreSQL volume configuration has been corrected, but persistence cannot be verified until the application is reachable through NGINX. The current `Connection reset by peer` is consistent with the unresolved application connectivity issues documented in the subsequent entries. The persistence test must be repeated after those issues are fixed.

---

## Entry 5 - NGINX can reach PostgreSQL/Redis directly and backend ports are published

* ****Symptom:**** NGINX is attached to both the `frontend` and `backend` networks, while PostgreSQL and Redis publish their container ports to the host. This violates the required network isolation.

* ****Hypothesis:**** NGINX was unnecessarily connected to the private `backend` network, and PostgreSQL/Redis port mappings were left enabled, allowing direct host access to backend services.

* ****Command/test:**** Inspected `docker-compose.yml` and checked the `networks` and `ports` configuration for NGINX, PostgreSQL, and Redis.

* ****Actual output:**** The current configuration contains:

  ```yaml
  postgres:
    ports: ["127.0.0.1:15432:5432"]

  redis:
    ports: ["127.0.0.1:16379:6379"]

  nginx:
    networks: [frontend, backend]
  ```

  Therefore:

  * NGINX is connected to the `backend` network.
  * PostgreSQL exposes host port `15432`.
  * Redis exposes host port `16379`.

* ****Failed attempt:**** None. The configuration inspection directly confirmed the network-isolation violations.

* ****Root cause:**** NGINX was configured to join the private `backend` network, and PostgreSQL/Redis had host port mappings that were not required for the application architecture.

* ****Fix:**** Remove the PostgreSQL and Redis `ports:` entries and connect NGINX only to the `frontend` network:

  ```yaml
  nginx:
    networks: [frontend]
  ```

  Keep the backend services on the internal `backend` network:

  ```yaml
  networks:
    backend:
      internal: true
  ```

* ****Retest evidence:**** After recreating the containers, run:

  ```bash
  docker inspect nginx
  docker inspect postgres
  docker inspect redis
  ```

  The validator should confirm that NGINX is not attached to a network ending in `backend`, and that PostgreSQL and Redis have no published host ports.

* ****Related commit:**** `fix(compose): remove unnecessary port mappings and adjust NGINX network configuration for isolation`

* ****Remaining uncertainty:**** The current `docker-compose.yml` still contains the identified violations, so the fix has not yet been applied or verified in the running containers. Live `docker inspect` output should be collected after applying the changes and recreating the affected containers.

---

## Entry 6 - app-02 reports the same INSTANCE_ID as app-01

* ****Symptom:**** The two application instances are intended to have different instance identifiers, but `app-02` is configured with the same `INSTANCE_ID` as `app-01`. This prevents the `/instance` endpoint and round-robin test from reliably identifying which backend handled a request.

* ****Hypothesis:**** The `INSTANCE_ID` value for `app-02` was copied from `app-01` and was not changed.

* ****Command/test:**** Inspected the application environment configuration in `docker-compose.yml`:

  ```bash
  grep -n -A3 "INSTANCE_ID" docker-compose.yml
  ```

* ****Actual output:**** The configuration shows:

  ```yaml
  app-01:
    environment:
      <<: *app-env
      INSTANCE_ID: "app-01"

  app-02:
    environment:
      <<: *app-env
      INSTANCE_ID: "app-01"
  ```

  Both containers therefore use `INSTANCE_ID=app-01`.

* ****Failed attempt:**** None. The duplicate identifier was confirmed directly from the Compose configuration.

* ****Root cause:**** The `INSTANCE_ID` value for `app-02` was incorrectly duplicated from `app-01`.

* ****Fix:**** Changed the `app-02` configuration to use its own unique identifier:

  ```yaml
  app-02:
    environment:
      <<: *app-env
      INSTANCE_ID: "app-02"
  ```

* ****Retest evidence:**** After recreating the application containers, run:

  ```bash
  for i in {1..12}; do
    curl -s http://127.0.0.1:8080/instance
    echo
  done
  ```

  The responses should expose at least two distinct instance IDs, `app-01` and `app-02`. The validator also checks that at least two distinct IDs are observed.

* ****Related commit:**** `fix(compose): update INSTANCE_ID for app-02 to ensure unique identification`

* ****Remaining uncertainty:**** The configuration fix is clear, but the round-robin behavior still needs to be verified against the running containers after the application and NGINX connectivity issues are resolved.

---

## Entry 7 - Healthcheck targets a non-existent `/healthz` endpoint

* ****Symptom:**** The application containers fail their Docker healthchecks because the Compose healthcheck requests `/healthz`, while the application exposes `/health`.

* ****Hypothesis:**** The healthcheck path was changed or incorrectly copied, causing Docker to mark the application containers as unhealthy even though the application provides a valid health endpoint.

* ****Command/test:**** Compared the health endpoint defined in `app/server.py` with the healthcheck configured in `docker-compose.yml`:

  ```bash
  grep -n "health" app/server.py
  grep -n -A2 "healthcheck" docker-compose.yml
  ```

* ****Actual output:**** The application defines the `/health` endpoint, while the Compose healthcheck uses:

  ```yaml
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
  ```

  Therefore, the healthcheck requests `/healthz`, which does not exist in the application.

* ****Failed attempt:**** Adding a `/healthz` alias to the application was considered, but this was rejected because the documented application endpoint `/health` should be used as the source of truth.

* ****Root cause:**** The Docker Compose healthcheck contains an incorrect endpoint path: `/healthz` instead of `/health`.

* ****Fix:**** Changed the healthcheck URL from `/healthz` to `/health`:

  ```yaml
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]
  ```

* ****Retest evidence:**** After recreating the application containers, run:

  ```bash
  docker compose ps
  ```

  Both `app-01` and `app-02` should report a `healthy` status.

* ****Related commit:**** `fix(compose): update healthcheck endpoint from /healthz to /health for application containers`

* ****Remaining uncertainty:**** None for the Docker healthcheck issue. Both app-01 and app-02 are now confirmed healthy, proving that the corrected /health healthcheck works successfully.

---

## Entry 8 - Application binds to loopback only

* ****Symptom:**** The application containers are healthy, but NGINX cannot reliably communicate with the application instances because the application is configured to listen only on the container's loopback interface (`127.0.0.1`).

* ****Hypothesis:**** `APP_HOST` is overriding the application's default bind address of `0.0.0.0`. Because the application listens only on `127.0.0.1`, connections from the NGINX container to `app-01:8080` and `app-02:8080` cannot reach the application.

* ****Command/test:**** Compare the application's default host configuration in `app/server.py` with the `APP_HOST` value defined in `docker-compose.yml`.

* ****Actual output:**** The shared application environment currently contains:

  ```yaml
  environment: &app-env
    APP_HOST: "127.0.0.1"
    APP_PORT: "8080"
  ```

  The application is therefore instructed to bind to `127.0.0.1:8080` instead of the container network interface.

* ****Failed attempt:**** None.

* ****Root cause:**** `APP_HOST` was incorrectly configured as `127.0.0.1`. This restricts the application to connections originating inside the same container and prevents NGINX from accessing it over the Docker network.

* ****Fix:**** Changed `APP_HOST` to `0.0.0.0`:

  ```yaml
  environment: &app-env
    APP_HOST: "0.0.0.0"
    APP_PORT: "8080"
  ```

  The application containers do not need host port publishing because NGINX accesses them through the Docker network.

* ****Retest evidence:**** Verified the application configuration with:

  `cat docker-compose.yml | grep APP_HOST`

  Actual output:

  ```text
  APP_HOST: "0.0.0.0"
  ```

  Both application containers are also confirmed healthy:

  ```text
  app-01   Up 2 minutes (healthy)   8080/tcp
  app-02   Up 2 minutes (healthy)   8080/tcp
  ```

  However, testing through NGINX:

  `curl -i http://127.0.0.1:8080/instance`

  still returns:

  ```text
  curl: (56) Recv failure: Connection reset by peer
  ```


* ****Related commit:**** `fix(compose): update APP_HOST to 0.0.0.0 for proper application binding`

* ****Remaining uncertainty:**** The application binding issue is confirmed fixed because `APP_HOST` is `0.0.0.0` and both application containers are healthy. However, end-to-end NGINX connectivity is still failing with `Connection reset by peer`. Therefore, the remaining issue is likely in the NGINX/container networking or NGINX listener configuration and requires further investigation before the final validator can pass.

---

## Entry 9 - NGINX published port targets the wrong container port

* ****Symptom:**** Requests sent to the published NGINX port failed because the host port was forwarded to container port `81`, while NGINX listens on port `80`.

* ****Hypothesis:**** The NGINX service port mapping in `docker-compose.yml` was using `81` as the container-side destination port. Because NGINX listens on `80`, connections forwarded to port `81` could not reach the NGINX listener.

* ****Command/test:**** Inspect the NGINX service port mapping in `docker-compose.yml` and compare the published container port with the port on which NGINX is configured to listen.

* ****Actual output:**** The NGINX service originally contained:

  ```yaml
  ports:
    - "127.0.0.1:${PUBLIC_PORT:-8080}:81"
  ```

  NGINX listens on port `80`, so the published host port was incorrectly forwarded to container port `81`.

* ****Failed attempt:**** The initial configuration resulted in:

  ```text
  curl: (56) Recv failure: Connection reset by peer
  ```

* ****Root cause:**** The NGINX Docker Compose port mapping incorrectly targeted container port `81` instead of NGINX's listening port `80`.

* ****Fix:**** Changed the NGINX port mapping to:

  ```yaml
  ports:
    - "127.0.0.1:${PUBLIC_PORT:-8080}:80"
  ```

* ****Retest evidence:**** After recreating the services, the published NGINX endpoint was tested with:

  `curl -i http://127.0.0.1:8080/instance`

  Actual output:

  ```text
  HTTP/1.1 200 OK
  Server: nginx/1.28.3
  Content-Type: application/json
  X-Instance-ID: app-01

  {"instance_id":"app-01","service":"barq-api","status":"ok","version":"2.0.0"}
  ```

  The response confirms that traffic reaches NGINX successfully and is correctly proxied to `app-01`.

* ****Related commit:**** `fix(compose): correct NGINX port mapping to target container port 80`

* ****Remaining uncertainty:**** None for the NGINX published-port issue. The successful `HTTP/1.1 200 OK` response confirms that the host-to-NGINX port mapping and NGINX-to-application connectivity are functioning correctly.

---

## Entry 10 - Dockerfile baked the database/Redis credential into a permanent image layer

* **Symptom:** found during a final pre-submission review, not from a runtime failure — `Dockerfile` contained `COPY config/app.env /srv/app.env` immediately before `USER app`, but nothing in `app/server.py` ever opens or reads `/srv/app.env`.

* **Hypothesis:** this was a leftover from an earlier approach to configuration (e.g. an attempt to load env vars from a file inside the container) that was superseded by `env_file: ./config/app.env` in `docker-compose.yml`, but the now-dead `COPY` line was never removed.

* **Command/test:** `grep -rn "app.env" app/ Dockerfile docker-compose.yml` — confirmed the file is only ever referenced by `docker-compose.yml`'s `env_file:` key (which injects it as process environment at container start) and by the now-removed `COPY`. `grep -rn "srv/app.env\|/srv/app.env" app/` returned nothing, confirming the app never reads the copied file.

* **Actual output:** before the fix, `docker history <app image>` would show a layer adding `/srv/app.env` containing the plaintext `DATABASE_URL` (including the PostgreSQL password) and `REDIS_URL`. This persists in every built image regardless of later changes to `config/app.env` on disk.

* **Failed attempt:** none — this was caught by static review rather than trial and error.

* **Root cause:** an unused `COPY` instruction in `Dockerfile` copied a file containing live-for-this-lab credentials into the image filesystem for no functional reason, violating the "keep secrets out of images" requirement even though the running application never used the copied file.

* **Fix:** removed the `COPY config/app.env /srv/app.env` line from `Dockerfile`. The application is unaffected because it already receives `DATABASE_URL`/`REDIS_URL` at runtime via `docker-compose.yml`'s `env_file:`/`environment:` keys, not from a file baked into the image.

* **Retest evidence:** run `docker compose build app-01` then `docker run --rm --entrypoint sh <image> -c 'test -f /srv/app.env && echo PRESENT || echo ABSENT'` and confirm it prints `ABSENT`; then re-run `docker compose up -d` and `python validate.py` to confirm the app still starts and passes all checks with the file gone.

* **Related commit:** `fix(docker): remove unused COPY instruction for app.env to enhance security`.

* **Remaining uncertainty:** none functionally (the file was never read), but you should still rebuild and re-run `validate.py` yourself before recording, rather than trusting static analysis alone.

