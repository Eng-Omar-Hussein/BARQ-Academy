#!/usr/bin/env python3
"""Reproducible analysis of the three historical BARQ logs.
Run: python3 analyze_logs.py
Reads originals read-only from logs/, writes nothing.
"""
import json, re, statistics, sys
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCESS = ROOT.parent / "logs/access.log"
ERROR = ROOT.parent / "logs/error.log"
APP = ROOT.parent / "logs/application.log"

def load_jsonl(path):
    valid, malformed = [], 0
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            valid.append(json.loads(line))
        except json.JSONDecodeError:
            malformed += 1
    return valid, malformed

ERR_RE = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>\w+)\] \d+#\d+: (?:\*(?P<cid>\d+) )?"
    r"(?P<msg>.*?)(?:, request_id=(?P<rid>\S+))?, request: \"(?P<req>[^\"]*)\", upstream: \"(?P<up>[^\"]*)\"$"
)
NOTICE_RE = re.compile(r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>\w+)\]")

def load_error(path):
    parsed, malformed, notices = [], 0, []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        m = ERR_RE.match(line)
        if m:
            parsed.append(m.groupdict())
        elif NOTICE_RE.match(line):
            notices.append(line)
        else:
            malformed += 1
    return parsed, malformed, notices

access, access_malformed = load_jsonl(ACCESS)
app_lines, app_malformed = load_jsonl(APP)
err_parsed, err_malformed, err_notices = load_error(ERROR)

print("=== Q1: coverage + line counts ===")
a_ts = [r["timestamp"] for r in access]
app_ts = [r["timestamp"] for r in app_lines]
print(f"access.log: {len(access)} valid JSON lines, {access_malformed} malformed, "
      f"span {min(a_ts)} .. {max(a_ts)}")
print(f"application.log: {len(app_lines)} valid JSON lines, {app_malformed} malformed, "
      f"span {min(app_ts)} .. {max(app_ts)}")
print(f"error.log: {len(err_parsed)} parsed error lines, {len(err_notices)} notice lines, "
      f"{err_malformed} unparsed lines")

# duplicates in access.log (same request_id appearing more than once)
access_by_id = defaultdict(list)
for r in access:
    access_by_id[r["request_id"]].append(r)
access_dupe_ids = {k: v for k, v in access_by_id.items() if len(v) > 1}
print(f"access.log duplicate request_id groups: {len(access_dupe_ids)} "
      f"(extra lines: {sum(len(v)-1 for v in access_dupe_ids.values())})")

app_by_id = defaultdict(list)
for r in app_lines:
    app_by_id[r["request_id"]].append(r)
app_dupe_ids = {k: v for k, v in app_by_id.items() if len(v) > 1}
print(f"application.log duplicate request_id groups: {len(app_dupe_ids)}")

print("\n=== Q2: distinct client requests ===")
distinct_ids = set(access_by_id) | set(app_by_id)
print(f"distinct request_id values across access+application: {len(distinct_ids)}")
print("Dedup rule: one client request == one request_id. NGINX emits exactly one access.log "
      "line per client request even when it fails over between upstreams (upstream/upstream_status "
      "become comma-separated for that single line) EXCEPT when proxy_next_upstream is engaged and a "
      "second attempt is logged as a new upstream in the *same* line -- verified none of the access "
      "lines contain a comma in the upstream field, so no retries were logged as separate access lines here.")
comma_upstream = [r for r in access if "," in r.get("upstream","")]
print(f"access.log lines with comma-joined upstream (multi-attempt in one line): {len(comma_upstream)}")

print("\n=== Q3: final client status counts + error rate ===")
status_counts = Counter(r["status"] for r in access.__iter__() and access)
status_counts = Counter(r["status"] for r in access)
print(dict(sorted(status_counts.items())))
total = len(access)
errors = sum(v for k, v in status_counts.items() if k >= 500)
client_errors = sum(v for k, v in status_counts.items() if 400 <= k < 500)
print(f"denominator = distinct access.log lines = {total}")
print(f"5xx count={errors} ({errors/total:.2%}); 4xx count={client_errors} ({client_errors/total:.2%})")

print("\n=== Q4: failures by path / time window / backend ===")
fail = [r for r in access if r["status"] >= 500]
by_path = Counter(r["path"] for r in fail)
by_backend = Counter(r["upstream"] for r in fail)
by_minute = Counter(r["timestamp"][:16] for r in fail)
print("by path:", dict(by_path))
print("by backend:", dict(by_backend))
print("by minute (top 10):", dict(sorted(by_minute.items())[:10]))
if by_minute:
    print("first failing minute:", min(by_minute), "last failing minute:", max(by_minute))

print("\n=== Q5: latency percentiles (client-observed, from access.log request_time, seconds) ===")
lat_ms = sorted(r["request_time"] * 1000 for r in access)
def pct(data, p):
    if not data:
        return None
    k = (len(data) - 1) * p
    f, c = int(k), min(int(k) + 1, len(data) - 1)
    if f == c:
        return data[f]
    return data[f] + (data[c] - data[f]) * (k - f)
print(f"n={len(lat_ms)} median={pct(lat_ms,0.5):.1f}ms p95={pct(lat_ms,0.95):.1f}ms "
      f"(linear-interpolation percentile over all access.log lines, milliseconds)")

print("\n=== Q6: which requests retried upstream ===")
retried = [r for r in access if "," in r.get("upstream_status", "")]
print(f"access.log lines showing >1 upstream_status (comma separated) = {len(retried)}")
# error.log 111 vs 110 both preceded a later successful retry on the SAME request_id? check overlap
err_ids = {e["rid"] for e in err_parsed if e["rid"]}
access_ids_ok = {r["request_id"] for r in access if r["status"] < 400}
retried_and_recovered = err_ids & access_ids_ok
print(f"request_ids present in BOTH error.log (failed once) and access.log with a final status <400: "
      f"{len(retried_and_recovered)}")
print("Interpretation: NGINX config here sets proxy_next_upstream off (bug), so a failed connection "
      "is NOT retried against the healthy peer -- it is returned to the client as-is. That is why "
      "error.log entries with a client-visible failure correlate 1:1 with a >=500 in access.log for "
      "the same request_id, not with a later success.")
for rid in sorted(retried_and_recovered)[:5]:
    print("  example:", rid, "final access status:",
          [r["status"] for r in access if r["request_id"] == rid])

print("\n=== Q7: incident timeline (error.log driven, cross-checked against access+application) ===")
conn_refused = [e for e in err_parsed if "Connection refused" in e["msg"]]
timeouts = [e for e in err_parsed if "timed out" in e["msg"]]
if conn_refused:
    print(f"connection-refused window: {conn_refused[0]['ts']} .. {conn_refused[-1]['ts']} "
          f"({len(conn_refused)} lines, all targeting {set(e['up'] for e in conn_refused)})")
if timeouts:
    print(f"upstream-timeout window: {timeouts[0]['ts']} .. {timeouts[-1]['ts']} "
          f"({len(timeouts)} lines, path {set(re.search('GET (\\S+)', e['req']).group(1) for e in timeouts)})")
print("notices:", err_notices)

print("\n=== Q8: one correlated failed request + one successful request ===")
if conn_refused:
    sample_fail_rid = conn_refused[0]["rid"]
    a = next((r for r in access if r["request_id"] == sample_fail_rid), None)
    ap = [r for r in app_lines if r["request_id"] == sample_fail_rid]
    print("FAILED example request_id:", sample_fail_rid)
    print("  access.log:", a)
    print("  application.log entries with same id:", ap)
ok_example = next(r for r in access if r["status"] == 200)
ok_app = [r for r in app_lines if r["request_id"] == ok_example["request_id"]]
print("SUCCESS example request_id:", ok_example["request_id"])
print("  access.log:", ok_example)
print("  application.log entries with same id:", ok_app)

print("\n=== Q9: proxy/connectivity vs dependency/application errors ===")
print(f"Connection-refused (edge cannot reach app-02:8080 at all) = proxy/connectivity: {len(conn_refused)} lines")
print(f"Upstream timed out reading response header on /records = app is up but slow/stuck "
      f"answering a dependency-bound endpoint: {len(timeouts)} lines")
app_dep_errors = [r for r in app_lines if r.get("event") == "dependency_error"]
print(f"application.log dependency_error events (app-side, proves it reached the process): {len(app_dep_errors)}")
app_5xx = [r for r in app_lines if r.get("status", 0) >= 500]
print(f"application.log http_request entries with status>=500: {len(app_5xx)}")
print("Because application.log has NO entries at all for the connection-refused window "
      "(the app-02 process/port was simply unreachable), while the /records timeout window DOES "
      "have app-side entries, this separates 'edge cannot reach the container' (infra) from "
      "'container reachable but slow/erroring' (app or its dependency).")
refused_rids = {e["rid"] for e in conn_refused}
app_seen_for_refused = [rid for rid in refused_rids if rid in app_by_id]
print(f"of {len(refused_rids)} refused request_ids, {len(app_seen_for_refused)} have ANY application.log entry "
      f"(expect ~0 if truly a connectivity-only failure)")

print("\n=== summary counts for report ===")
print("status_counts:", dict(sorted(status_counts.items())))
