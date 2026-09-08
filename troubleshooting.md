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

