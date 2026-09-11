# Technical decisions

## Decision 1 - Pin base images by digest, not just tag
- Choice: `python:3.12-slim-bookworm@sha256:...`, `postgres:16-alpine@sha256:...`,
  `redis:7.4-alpine@sha256:...`, `nginx:1.28-alpine@sha256:...` — every image is
  pinned to an immutable digest, not a mutable tag.
- Why: a floating tag (`python:3.12-slim`) can change under us between a local
  build and CI, or between the video build and a later one, breaking
  reproducibility and making "it worked on my machine" failures possible.
- Alternative: pin by tag only, or use `latest`.
- Trade-off: digests must be refreshed manually to pick up security patches;
  they don't update themselves. Alpine bases also trade some glibc/tooling
  compatibility for smaller image size.
- Evidence / commit: `Dockerfile`, `docker-compose.yml` (image lines).
- Production improvement: automate digest refresh with Renovate/Dependabot plus
  the Trivy scan already in CI, so bumps are proposed and re-scanned instead of
  drifting silently.

## Decision 2 - Non-root, gid/uid-pinned application user
- Choice: create `app` (uid/gid 10001) in the Dockerfile, `COPY --chown=app:app`
  the code, and `USER app` before `CMD`.
- Why: the Flask process has no reason to run as root; a fixed high uid avoids
  collisions with the host and makes the container's effective privileges
  explicit and auditable.
- Alternative: rely on the base image's default user, or drop privileges only
  via a compose-level `user:` override.
- Trade-off: a couple of extra Dockerfile lines, and one more thing to keep in
  sync if the app ever needs to write to a path it doesn't own.
- Evidence / commit: `Dockerfile`; `fix(docker): switch to non-root user for
  application execution`.
- Production improvement: add a read-only root filesystem
  (`read_only: true` plus an explicit `tmpfs` for anything that truly needs to
  be writable) and drop all Linux capabilities the process doesn't use.

## Decision 3 - Health-check tools already inside each image, not curl
- Choice: app healthcheck uses `python -c "urllib.request.urlopen(...)"`
  (Python is already in the image); Postgres uses `pg_isready`; Redis uses
  `redis-cli ping`; NGINX uses `wget` (present in the `nginx:alpine` base).
- Why: none of these images ship `curl` by default. Adding `curl` just for a
  healthcheck means an extra package layer, more attack surface, and more to
  patch/scan for no functional gain over a tool that's already there.
- Alternative: install `curl` in every image for a uniform healthcheck command.
- Trade-off: healthcheck syntax differs per service instead of being uniform,
  slightly less copy-pasteable across services.
- Evidence / commit: `docker-compose.yml` (`healthcheck:` blocks per service).
- Production improvement: expose a dedicated `/livez` vs `/readyz` split at the
  orchestrator level (e.g. Kubernetes liveness/readiness probes) instead of
  Compose healthchecks, backed by real dependency-latency SLOs.

## Decision 4 - `depends_on: condition: service_healthy` plus bounded startup waits
- Choice: `app-01`/`app-02` wait on `postgres`/`redis` healthy; `nginx` waits on
  both `app-01` and `app-02` healthy. `validate.py`/CI poll `/ready` for up to
  30s instead of assuming instant availability.
- Why: Postgres/Redis/Flask/NGINX all have non-trivial startup time; a fixed
  `sleep N` is either too short (flaky) or too long (slow CI). A health-gated
  dependency graph plus a bounded poll gives a deterministic "ready" signal
  without an arbitrary constant.
- Alternative: no `depends_on` conditions, and a fixed `sleep 15` before tests.
- Trade-off: slightly longer worst-case startup (waiting for retries/intervals
  to elapse) in exchange for not racing the stack.
- Evidence / commit: `docker-compose.yml`; `validate.py::wait_for`;
  `.github/workflows/ci.yaml` ("Wait for readiness (bounded)" step).
- Production improvement: separate startup/liveness/readiness into three
  distinct probes with different thresholds, and alert on repeated readiness
  flapping rather than only gating first boot.

## Decision 5 - NGINX failover: `proxy_next_upstream` plus bounded retries/timeouts
- Choice: `proxy_next_upstream error timeout http_502 http_503 http_504;`,
  `proxy_next_upstream_tries 2;`, `proxy_connect_timeout 2s;`,
  `proxy_read_timeout 3s;`, and `max_fails=3 fail_timeout=10s` per upstream
  server.
- Why: with two (later three) interchangeable backends, a single dead instance
  should be invisible to the client. Short, bounded timeouts mean a hung
  backend is retried against a healthy one quickly instead of the client
  hanging or a slow backend dragging down the whole pool.
- Alternative: leave `proxy_next_upstream off` (the original starter config)
  and rely solely on Docker healthchecks/restarts to remove a bad backend.
- Trade-off: retrying is only safe for idempotent requests. `GET` endpoints are
  safe to retry; `POST /records` being retried after a partial failure could
  in theory double-write. This is called out again in `security_review.md`.
- Evidence / commit: `nginx/nginx.conf`; `fix(nginx): correct app-01 upstream
  port and enable failover`; `failure_test.py`.
- Production improvement: scope `proxy_next_upstream` per-location so writes
  are excluded, and add idempotency keys if retries on writes are ever needed.

## Decision 6 - Compose-level `deploy.resources` limits instead of unbounded containers
- Choice: every service gets a `cpus`/`memory` limit (Postgres and the app also
  get a memory reservation) directly in `docker-compose.yml`.
- Why: on a shared dev/CI host, one runaway container should not be able to
  starve the others or the host. Explicit limits also make the "suggested
  capacity: 2 CPU / 4 GB RAM" claim in the README testable rather than
  aspirational.
- Alternative: no limits, relying on the host having "enough" headroom.
- Trade-off: limits that are too tight cause OOM kills/throttling under real
  load; the current numbers are sized for the lab's synthetic traffic, not a
  production workload, and would need load-testing before reuse.
- Evidence / commit: `docker-compose.yml` (`deploy.resources` per service);
  `feat(docker): add CPU and memory resource limits for services in
  docker-compose`.
- Production improvement: derive limits from measured p95 CPU/memory under
  representative load, and add horizontal autoscaling instead of a fixed
  instance count.

## Decision 7 - PostgreSQL persistence via named volume, Redis via AOF
- Choice: named volume `postgres-data` mounted at the real
  `PGDATA` path (`/var/lib/postgresql/data`); Redis runs with
  `--appendonly yes --appendfsync everysec` against a `redis-data`
  named volume, replacing the shipped `--save "" --appendonly no`
  (no persistence at all) plus a `tmpfs`-shadowed Postgres volume.
- Why: TASK.md explicitly requires PostgreSQL data to survive
  container recreation, and "configure Redis persistence where
  appropriate" - the counter is disposable (fine to reset), but a lab
  environment restart shouldn't silently lose the visible `/counter`
  demo state either, so AOF (durable, replay-based) was chosen over
  RDB snapshots (point-in-time, can lose the last few seconds).
- Alternative: RDB snapshotting (`--save 60 1000`) for Redis -
  cheaper, but can lose recent writes on a crash between snapshots.
- Trade-off: AOF has higher disk I/O and slightly larger files than
  RDB; `everysec` fsync bounds worst-case data loss to ~1 second
  instead of `always` (safer, slower) or `no` (fastest, unsafe).
- Evidence / commit: `docker-compose.yml`; persistence proof via
  `backup.sh`/`restore.sh` and the container-recreation test in `README.md`.
- Production improvement: move PostgreSQL to a managed
  service (RDS/Cloud SQL) with automated point-in-time recovery
  instead of a single-node container + manual `pg_dump`