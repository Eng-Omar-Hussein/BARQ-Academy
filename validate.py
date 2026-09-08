#!/usr/bin/env python3
"""Environment validation for the BARQ DevOps assessment.

Checks, in order, with bounded waits and a non-zero exit on any failure:
  1. Public NGINX access on the configured host port.
  2. Every required endpoint contract (/, /health, /ready, /instance,
     /records GET+POST, /counter).
  3. Both app backends are reachable through NGINX (round-robin over N
     requests must surface >=2 distinct instance_id values).
  4. PostgreSQL and Redis readiness via /ready.
  5. Network isolation: nginx must NOT be attached to the backend
     network, and postgres/redis must NOT publish host ports.

Usage:
    python3 validate.py [--url http://127.0.0.1:8080] [--project barq-assessment]

Exit code 0 = PASS, non-zero = FAIL. Prints a PASS/FAIL line per check.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

FAILED = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)
    return ok

def http_get(url, timeout=3):
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp), dict(resp.headers)

    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
        except Exception:
            body = {}

        return exc.code, body, dict(exc.headers)


def http_post(url, payload, timeout=3):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def wait_for(url, timeout_s=30, interval_s=1):
    """Bounded wait: poll until the URL returns 200 or the timeout elapses."""
    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            status, body, _ = http_get(url)
            if status == 200:
                return True, body
        except Exception as exc:  # connection refused while stack is still starting
            last_error = exc
        time.sleep(interval_s)
    return False, last_error


def docker_json(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--project", default="barq-assessment")
    parser.add_argument("--instances", type=int, default=2,
                         help="expected distinct backend instances (2 by default, 3 after the live scale-up)")
    parser.add_argument("--probe-count", type=int, default=12,
                         help="requests to send while checking backend distribution")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    print(f"== BARQ environment validation against {base} (project={args.project}) ==\n")

    # 1. Public access
    ok, body = wait_for(base + "/health", timeout_s=30, interval_s=1)
    check("public NGINX access on configured port", ok,
          "waited up to 30s for 200 from /health" if ok else f"never became reachable: {body}")
    if not ok:
        return finish()

    # 2. Endpoint contract
    status, body, headers = http_get(base + "/")
    check("GET / returns 200 with message", status == 200 and "message" in body)

    status, body, headers = http_get(base + "/health")
    check("GET /health returns 200", status == 200)

    status, body, headers = http_get(base + "/instance")
    check("GET /instance returns 200 with instance_id + X-Instance-ID header",
          status == 200 and "instance_id" in body and "X-Instance-ID" in headers)

    status, body, headers = http_get(base + "/ready")
    check("GET /ready returns 200 (postgres+redis ready)", status == 200,
          json.dumps(body.get("dependencies", body)))

    status, body = http_post(base + "/records", {"title": "validate.py smoke record"})
    check("POST /records returns 201 with stored record", status == 201 and "record" in body)

    status, body, headers = http_get(base + "/records")
    check("GET /records returns list including new record", status == 200 and
          any(r.get("title") == "validate.py smoke record" for r in body.get("records", [])))

    status, body = http_post(base + "/records", {"title": ""})
    check("POST /records with invalid title returns 400", status == 400)

    status, body, headers = http_get(base + "/counter")
    c1 = body.get("counter") if status == 200 else None
    status2, body2, _ = http_get(base + "/counter")
    c2 = body2.get("counter") if status2 == 200 else None
    check("GET /counter increments atomically across calls", status == 200 and status2 == 200
          and isinstance(c1, int) and isinstance(c2, int) and c2 == c1 + 1,
          f"counter {c1} -> {c2}")

    status, body, headers = http_get(base + "/does-not-exist")
    check("unknown route returns 404", status == 404)

    # 3. Both/all backends reachable through NGINX
    seen = set()
    for _ in range(args.probe_count):
        try:
            _, body, _ = http_get(base + "/instance")
            seen.add(body.get("instance_id"))
        except Exception:
            pass
    check(f"round-robin surfaces >= {args.instances} distinct backend instance_id(s)",
          len(seen) >= args.instances, f"observed: {sorted(seen)}")

    # 4. Network isolation
    try:
        nginx_info = docker_json("inspect", "nginx")[0]
        nginx_nets = set(nginx_info["NetworkSettings"]["Networks"].keys())
        backend_nets = {n for n in nginx_nets if n.endswith("backend")}
        check("nginx container is NOT attached to the backend network", len(backend_nets) == 0,
              f"nginx networks: {sorted(nginx_nets)}")
    except Exception as exc:
        check("nginx container is NOT attached to the backend network", False, str(exc))

    for svc in ("postgres", "redis"):
        try:
            info = docker_json("inspect", svc)[0]
            ports = info["NetworkSettings"].get("Ports") or {}
            published = {k: v for k, v in ports.items() if v}
            check(f"{svc} publishes no host ports", len(published) == 0, f"ports: {published}")
        except Exception as exc:
            check(f"{svc} publishes no host ports", False, str(exc))

    return finish()


def finish():
    print()
    if FAILED:
        print(f"VALIDATION FAILED: {len(FAILED)} check(s) failed -> {FAILED}")
        return 1
    print("VALIDATION PASSED: all checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

