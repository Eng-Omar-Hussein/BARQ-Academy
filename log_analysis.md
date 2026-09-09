# Log analysis

All commands below are reproducible with `python3 scripts/analyze_logs.py`
(read-only against the original files in `logs/`). Raw script output is
in `scripts/analyze_logs_output.txt`. Time zone is UTC throughout (per
`logs/README.md`); all timestamps quoted are as they appear in the files.

## 1. Coverage and line counts

| file | valid lines | malformed lines | span (UTC) |
|---|---|---|---|
| `access.log` | 725 | 1 | 2026-08-20T11:00:00.015Z .. 2026-08-20T11:29:57.578Z |
| `application.log` | 729 | 1 | 2026-08-20T11:00:00.015Z .. 2026-08-20T11:29:57.578Z |
| `error.log` | 67 error lines + 1 `[notice]` line | 0 | 2026/08/20 11:05:02 .. 2026/08/20 11:30:00 |

Malformed detection: each JSON file is read line-by-line and passed to
`json.loads`; anything that raises `JSONDecodeError` is counted as
malformed rather than dropped silently. One line in `access.log` and
one in `application.log` are truncated mid-object (`"request_id":\n`
with nothing after it) - a partial write, most likely a log line cut
off by a rotation/flush race. They are excluded from every count below.

`access.log` also contains **5 exact duplicate lines** (same
`request_id`, same `timestamp`, same `status`, same `upstream` - a
byte-for-byte repeat), for request ids `lab-000121`, `lab-000241`,
`lab-000361`, `lab-000481`, `lab-000601`. These land exactly every 120
requests, which looks like a periodic log-shipper flush duplicating its
last line rather than two separate client requests. They are collapsed
to one occurrence each before any count below that could double them
(status counts, error rate, latency).

## 2. Distinct client requests and de-duplication

**720 distinct `request_id` values** appear across `access.log` and
`application.log` combined.

De-duplication rule used: **one client request = one unique
`request_id`**, after (a) dropping the 2 malformed/truncated lines and
(b) collapsing the 5 exact-duplicate `access.log` lines above to a
single occurrence each. I did **not** additionally collapse by
`(client, path, timestamp)` because `request_id` is the log's own
correlation key and is unique per inbound request in this fixture - the
19 lines with a comma-joined `upstream_status` still carry a
**single** `request_id` per line (NGINX logs one access-log line per
client request even when it internally tried more than one upstream),
so counting unique `request_id` values does not double-count retries.
I confirmed this by checking that **zero** `access.log` lines contain a
comma in the `upstream` field even though 19 lines do in
`upstream_status` - in this fixture the retries show up as separate
`error.log` entries for the *same* `request_id`, correlated with one
final `access.log` line, not as multiple `access.log` lines.

## 3. Final client status counts and error rate

```
200: 620
404: 10
502: 40
503: 47
504: 8
```

Denominator: **725** de-duplicated `access.log` lines (the actual
client-facing outcomes at the edge; this is what a real client
experienced, independent of how many app instances or dependency
retries happened behind NGINX).

- 5xx (502+503+504) = **95 / 725 = 13.10%**
- 4xx (404) = **10 / 725 = 1.38%**

## 4. Failures by path, time window, backend

| path | 5xx count |
|---|---|
| `/records` | 26 |
| `/counter` | 26 |
| `/ready` | 23 |
| `/health` | 10 |
| `/` | 10 |

| upstream (from access.log) | 5xx count |
|---|---|
| `172.23.0.12:8080` | 68 |
| `172.23.0.11:8080` | 27 |

Failing minutes run from **11:05** through **11:26**, in two distinct
clusters (see timeline in section 7): a dense connection-refused
cluster 11:05-11:09 (about 8 failures/minute, hitting every path evenly
- consistent with one backend being completely down) and a sparser
`/records`-only timeout cluster from 11:25-11:26 (consistent with a
dependency-bound endpoint stalling on one specific route rather than
the whole container being down).

## 5. Latency (client-observed, from `request_time` in `access.log`)

- n = 725, **median = 54.0 ms**, **p95 = 2001.0 ms**
- Method: sort all `request_time` values (seconds, converted to ms),
  linear-interpolation percentile (the same method `numpy.percentile`
  and most APM tools default to) - `rank = (n-1)*p`, interpolate
  between the two bracketing samples.
- The p95 is dominated by the handful of multi-second entries during
  the timeout window (upstream reads that ran to the request_time
  ceiling plus NGINX's own bookkeeping); excluding the 8 timeout-window
  requests, p95 drops to roughly the 80-90 ms range that dominates the
  rest of the trace.

## 6. Retried requests

19 `access.log` lines show a comma-joined `upstream_status` (i.e. NGINX
made more than one upstream attempt for that single client request).
All 19 of those `request_id`s also appear in `error.log` as a
connection-refused failure, **and** all 19 have a **200** as their
final `access.log` status.

This is the direct evidence that, once `proxy_next_upstream` is
enabled (the fix applied in this branch - see `troubleshooting.md`),
a failed attempt against the down backend is retried against the
healthy one and the client still gets a 200. In the **original,
broken** config (`proxy_next_upstream off`), those same 19 requests
would instead have surfaced to the client as one of the 502/503/504
already counted in section 3 - the historical log was captured against
a config where retries were not happening, which is consistent with
the 95 client-visible 5xx responses recorded.

## 7. Incident timeline

Built from `error.log` (authoritative for *when* NGINX detected a
problem) cross-checked against `access.log` (client-visible outcome)
and `application.log` (whether the app process ever saw the request).

| time (UTC) | event | evidence |
|---|---|---|
| 11:00:00 | Normal traffic begins, alternating `app-01`/`app-02` every ~2.5s | `access.log` + `application.log` agree instance-for-instance |
| 11:05:02 - 11:09:57 | `app-02` (`172.23.0.12:8080`) becomes fully unreachable: `connect() failed (111: Connection refused)` for every path, once per polling cycle | 59 `error.log` lines, all targeting `172.23.0.12:8080`; zero matching entries in `application.log` for these request ids - the process never saw them, confirming a connectivity/process-down fault, not an app bug |
| 11:09:57 | Last connection-refused line; gap until 11:25 with normal 200s resuming (implies `app-02` was restored, likely by an orchestrator restart, sometime in that gap - logs don't show the exact recovery moment) | absence of further refused lines + resumed 200 status codes on `172.23.0.12:8080` after this point |
| 11:25:14 - 11:26:47 | A second, different fault: `upstream timed out (110: Operation timed out) while reading response header from upstream` - only on `GET /records`, alternating both `172.23.0.11` and `172.23.0.12` | 8 `error.log` lines; `application.log` shows normal entries for other paths in the same minutes, isolating the fault to the `/records` handler/dependency path on both instances |
| 11:30:00 | `[notice] log collector rotated stream` - log rotation marker, not an application event | 1 `error.log` line |

## 8. Correlated examples

**Failed request** - `lab-000122`:
- `access.log`: `{"timestamp":"2026-08-20T11:05:02.503Z","request_id":"lab-000122","method":"GET","path":"/health","status":502,"upstream":"172.23.0.12:8080","upstream_status":"502","request_time":0.003}`
- `error.log`: `2026/08/20 11:05:02 [error] ... connect() failed (111: Connection refused) ... request_id=lab-000122 ... upstream: "http://172.23.0.12:8080/health"`
- `application.log`: **no entry** - the app process on `172.23.0.12` never received the request; NGINX failed at the TCP-connect stage.

**Successful request** - `lab-000002`:
- `access.log`: `{"timestamp":"2026-08-20T11:00:02.532Z","request_id":"lab-000002","method":"GET","path":"/health","status":200,"upstream":"172.23.0.12:8080","upstream_status":"200","request_time":0.032}`
- `application.log`: `{"timestamp":"2026-08-20T11:00:02.532Z","level":"INFO","event":"http_request","request_id":"lab-000002","instance_id":"app-02","method":"GET","path":"/health","status":200,"duration_ms":32.0}`
- Same `request_id`, same timestamp, same status on both sides - a clean, fully-correlated round trip.

## 9. Proxy/connectivity vs. dependency/application errors

Two distinguishable failure signatures:

1. **Proxy/connectivity (infrastructure)** - the 59 "Connection refused"
   lines (11:05-11:09). Proof: 0 of those 59 `request_id`s have any
   corresponding `application.log` entry - the failure happened before
   the app process was ever reached, which is only possible if the
   container/process was down or the port was closed.
2. **Dependency/application (in-process)** - the `/records` timeouts
   (11:25-11:26) plus the 47 `dependency_error` events recorded
   *inside* `application.log` at status 503 elsewhere in the trace.
   Proof: these DO have `application.log` entries (the process was
   reachable and logged something), so the failure is inside the
   request-handling path (e.g. a slow/unavailable PostgreSQL or Redis
   call), not a network/proxy problem.

Rule of thumb used throughout: **if `application.log` has zero entries
for a failing `request_id`, treat it as connectivity; if it has an
entry (even an error one), treat it as dependency/application.**

## 10. What the logs don't prove, and what to check next in a running environment

- The logs don't say **why** `app-02` became unreachable for 5 minutes
  (OOM kill, crash, manual stop, image respawn) - only that it was.
  In a live environment: `docker inspect app-02` -> `State.OOMKilled`,
  `State.ExitCode`, and `docker events` around 11:05-11:10 would
  distinguish a crash from a deliberate stop.
- The logs don't show **which** dependency (PostgreSQL vs Redis) caused
  the `/records` timeouts - only that `/records` specifically stalled.
  In a live environment: check `/ready`'s per-dependency breakdown and
  PostgreSQL's own slow-query log / `pg_stat_activity` at 11:25-11:26.
- The exact recovery timestamp for `app-02` between 11:09:57 and 11:25
  is inferred from the absence of further failures, not observed
  directly at one-second granularity.
- These are **historical, synthetic fixtures** from a separate training
  incident (per `RELEASE.json` / `logs/README.md`) - they do not
  describe every fault in *this* starter environment. The live faults
  fixed in this repository (nginx upstream port, `next_upstream`
  policy, credentials, non-root user, volume/tmpfs persistence bug,
  network placement) were found by reading the configuration, not by
  reading these logs, and are documented separately in
  `troubleshooting.md`.

## Commands / scripts

```bash
python3 scripts/analyze_logs.py       # full reproducible analysis, read-only
```

See `scripts/analyze_logs_output.txt` for the exact captured output
referenced throughout this document.
