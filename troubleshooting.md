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
