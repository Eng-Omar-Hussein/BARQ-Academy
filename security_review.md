# Security and production-readiness review

## Finding 1 - Dockerfile baked the real DATABASE_URL/Redis credential into an image layer
- Risk and evidence: `Dockerfile` contained `COPY config/app.env /srv/app.env`
  right before `USER app`. Nothing in `app/server.py` reads `/srv/app.env` —
  the app only reads `DATABASE_URL`/`REDIS_URL` from process environment,
  which compose already injects via `env_file: ./config/app.env`. The `COPY`
  had no function except permanently embedding the PostgreSQL password into
  the image's filesystem history.
- Impact: anyone with pull/export access to the built image (`docker save`,
  a registry, `docker history` + layer extraction) recovers the plaintext
  credential, even after `config/app.env` is later rotated or deleted from
  the repo — it lives on in every image built before the fix.
- Implemented fix / commit: removed the `COPY config/app.env /srv/app.env`
  line from `Dockerfile` (this review). `fix(docker): remove unused COPY instruction for app.env to enhance security`
- Production follow-up: never bake secrets into images at all, including via
  build args (`ARG` values also land in image history unless using BuildKit
  secret mounts). Use `--mount=type=secret` at build time if a secret is ever
  genuinely needed during build, and a real secret manager (Vault, AWS/GCP
  secret manager, Docker/Swarm secrets) at runtime instead of a committed
  `.env` file, even a synthetic lab one.
- How to verify: `docker history <image>` before the fix showed the file being
  added; after the fix, `docker run --rm <image> cat /srv/app.env` fails with
  "No such file or directory", and `grep -R app.env Dockerfile` returns
  nothing.

## Finding 2 - PostgreSQL credential lives in plaintext in `config/app.env` and `docker-compose.yml`
- Risk and evidence: `config/app.env` and the `postgres` service's
  `environment:` block both contain `BarqLabOnly_7qN2vK8c` in plaintext.
  `config/app.env` is not `.gitignore`d (only `.env`/`.env.*` are), so it is
  tracked in the repository.
- Impact: this specific value is a disposable, synthetic lab credential (per
  `TASK.md`/`README.md`, "never for real services"), so the immediate impact
  is limited to this lab. The pattern is still worth flagging because the
  same structure, reused with a real credential, would leak it to every
  clone/fork of the repo and every CI log that echoes the file.
- Implemented fix / commit: none needed for the lab value itself (it is
  intentionally synthetic and disposable); CI already runs a Gitleaks scan
  (`.github/workflows/ci.yaml`) that would flag a real high-entropy secret
  landing in the tree.
- Production follow-up: move real credentials out of any committed file into
  a secret manager or CI/CD-injected environment variables that are never
  written to disk in the repo, and rotate immediately if a real secret is
  ever accidentally committed (rewriting history is not sufficient once
  something has been pushed).
- How to verify: `git log -p -- config/app.env` shows the value has been committed.

## Finding 3 - Application process runs Flask's built-in dev server, not gunicorn
- Risk and evidence: `requirements.txt` includes `gunicorn`, but
  `Dockerfile`'s `CMD` is `python -m app.server`, which runs
  `app.run(..., threaded=True, debug=False)` — Flask's development server.
- Impact: the Werkzeug dev server is not designed for concurrent production
  load (its own docs warn against this); it lacks the worker-process model,
  graceful-reload, and hardening that a WSGI server like gunicorn provides.
  `debug=False` at least avoids the interactive debugger/RCE risk, but the
  server model itself is still not production-grade.
- Implemented fix / commit: not changed in this pass — flagging it here so it
  is a documented, deliberate trade-off rather than an oversight.
- Production follow-up: change `CMD` to
  `gunicorn -w 2 -b 0.0.0.0:8080 --access-logfile - app.server:create_app()`
  (or similar) and drop the now-actually-used `gunicorn` pin's justification
  from "aspirational" to "used."
- How to verify: `docker compose exec app-01 ps aux` currently shows a Python
  process running `app.server` directly; after the follow-up it should show a
  `gunicorn` master/worker process tree.

## Finding 4 - No TLS termination anywhere in the request path
- Risk and evidence: `nginx/nginx.conf` only has `listen 80;`; NGINX-to-app and
  client-to-NGINX are both plain HTTP.
- Impact: acceptable for a local, loopback-only lab
  (`127.0.0.1:${PUBLIC_PORT}:80`), but would expose credentials/session data
  in cleartext if this configuration were ever pointed at a non-loopback
  interface.
- Implemented fix / commit: none — out of scope for a `127.0.0.1`-only lab,
  but recorded so it isn't silently assumed to be production-ready.
- Production follow-up: terminate TLS at NGINX (or an upstream load balancer)
  with certificates from a real CA/ACME, and consider mTLS or a service mesh
  for the NGINX-to-app hop if it ever crosses a network boundary.
- How to verify: `curl -v http://127.0.0.1:8080/` today shows plaintext HTTP;
  after the follow-up, plain HTTP should redirect to HTTPS or be refused.

## Finding 5 - `POST /records` is retried by NGINX's failover on a partial failure
- Risk and evidence: `proxy_next_upstream error timeout http_502 http_503
  http_504;` and `proxy_next_upstream_tries 2;` apply globally in
  `nginx/nginx.conf`, with no per-location exclusion for write requests.
- Impact: if a backend accepts a `POST /records` TCP connection, executes the
  `INSERT`, and then dies/times out before sending the response, NGINX could
  retry the same POST against the surviving backend, inserting the record
  twice. This was not observed in `failure_test.py` (it only exercises GET
  `/instance`), so it is a documented risk, not a confirmed bug.
- Implemented fix / commit: none yet — documented so it's a known limitation,
  not a silent gap. (See Decision 5 in `decisions.md`.)
- Production follow-up: split NGINX config into a `location` block for
  mutating endpoints with `proxy_next_upstream off;`, or make `/records`
  creation idempotent (client-supplied idempotency key, unique constraint) so
  a retried write is a safe no-op.
- How to verify: extend `failure_test.py` to POST to `/records` during the
  outage window and assert the resulting row count increases by exactly the
  number of successful client-visible 201s, not more.

## Finding 6 - Single NGINX instance is a load-balancer-level single point of failure
- Risk and evidence: `docker-compose.yml` defines exactly one `nginx` service;
  there is no redundancy at the edge.
- Impact: if the `nginx` container dies, both/all app instances become
  unreachable from outside even though they are individually healthy. This is
  called out on the architecture diagram (`architecture.png`) under
  "Remaining Single Points of Failure."
- Implemented fix / commit: `restart: unless-stopped` on `nginx` (already in
  `docker-compose.yml`) bounds the outage to a restart cycle, but does not
  eliminate the SPOF.
- Production follow-up: run at least two NGINX replicas behind an external
  load balancer / VIP (or move load balancing to a managed service), and add
  a synthetic external health check independent of the Docker host.
- How to verify: `docker stop nginx` and confirm requests fail until restart;
  in production, the equivalent test should show zero client-visible downtime
  because a second edge instance took over.

## Finding 7 - No log retention, rotation, or shipping — logs are container stdout only
- Risk and evidence: the app (`log_event`) and NGINX (`access_log
  /dev/stdout`) both log JSON to stdout/stderr, captured only by the Docker
  logging driver. `logs/README.md` and the historical fixtures acknowledge
  this is a synthetic/offline exercise, but no rotation or shipping is
  configured for the live containers.
- Impact: logs are lost on container removal (`docker compose down -v` or
  host disk pressure triggering the default json-file driver's log rotation
  settings, if any). There is no correlation store beyond what a human reads
  live during the window a container exists.
- Implemented fix / commit: none — the app-level structured JSON logging
  (`log_event`) and NGINX's `assessment` `log_format` already make the logs
  machine-parseable, which is a prerequisite for the follow-up below.
- Production follow-up: ship stdout/stderr to a log aggregator (Loki, ELK,
  CloudWatch, etc.) with a defined retention window, and alert on the
  `dependency_error` / 5xx patterns this repo's log analysis already knows
  how to detect (see `log_analysis.md`).
- How to verify: `docker compose logs` today only shows what the daemon has
  buffered; after the follow-up, the same events should be queryable in the
  aggregator after the container is gone.

## Finding 8 - No rate limiting or request-size abuse protection at the edge
- Risk and evidence: `nginx/nginx.conf` has no `limit_req`/`limit_conn` zones.
  The app does cap body size (`MAX_CONTENT_LENGTH = 16 * 1024`), but nothing
  limits request *rate* per client, and `/counter` and `/records` both do a
  real Redis/PostgreSQL round-trip per call.
- Impact: a client that hammers `/counter` or `/records` can drive real
  database/cache load with no backpressure, which is also why the current
  resource limits (Decision 6 in `decisions.md`) matter — they cap the blast
  radius but don't prevent it.
- Implemented fix / commit: none — `MAX_CONTENT_LENGTH` in `app/server.py` is
  the only existing mitigation, and it addresses payload size, not request
  rate.
- Production follow-up: add `limit_req_zone`/`limit_req` in NGINX keyed on
  client IP or an API key, sized against measured legitimate traffic.
- How to verify: a simple loop of rapid `/counter` requests today succeeds
  indefinitely; after the follow-up, it should start receiving `429` past the
  configured threshold.

## Finding 9 - NGINX (and the app) still run as their images' default users where not overridden
- Risk and evidence: `Dockerfile` sets `USER app` for the Flask container, but
  `docker-compose.yml` does not set a `user:` override for the `nginx`
  service, and the stock `nginx:alpine` image's worker processes run as a
  non-root `nginx` user by default while the master process starts as root
  (needed to bind port 80 inside the container and then drop privileges) —
  this is standard for that image, not a misconfiguration introduced here,
  but it's worth recording explicitly rather than assuming.
- Impact: low in this lab (container-internal root, not host root, and
  `cap_drop`/`no-new-privileges` are not currently set anywhere), but it means
  the "avoid root/privileged operation where practical" requirement is only
  fully satisfied for the app containers today.
- Implemented fix / commit: none — recorded as a known gap, not fixed in this
  pass.
- Production follow-up: add `cap_drop: [ALL]` and
  `security_opt: [no-new-privileges:true]` to every service in
  `docker-compose.yml`, and consider `nginxinc/nginx-unprivileged` if binding
  a non-privileged port (>1024) end-to-end is acceptable.
- How to verify: `docker compose exec nginx id` today reports the master
  process context; after the follow-up, `docker inspect nginx --format
  '{{.HostConfig.CapDrop}}'` should show `[ALL]` plus any explicitly re-added
  capability.
