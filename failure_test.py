#!/usr/bin/env python3
"""Failure / recovery test for the BARQ DevOps assessment.

Stops one backend container, keeps sending client traffic through
NGINX for the whole outage, restores the backend, and proves it is
serving requests again once healthy. Never uses `docker compose down`.

Usage:
    python3 failure_test.py [--url http://127.0.0.1:8080] [--target app-02]

Exit code 0 = client saw uninterrupted availability with the surviving
backend + full recovery; non-zero = FAIL. The script always restarts
the target backend in a `finally` block, even on failure, so it never
leaves the lab in a stopped state.
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


def docker(*args, timeout=20):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def probe(base):
    """One client request through NGINX. Returns (status_code_or_None, instance_id_or_None)."""
    try:
        req = urllib.request.Request(base + "/instance")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.load(resp)
            return resp.status, body.get("instance_id")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return None, None


def wait_healthy(container, timeout_s=60, interval_s=2):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            info = json.loads(docker("inspect", container))[0]
            if info["State"].get("Health", {}).get("Status") == "healthy":
                return True
        except Exception:
            pass
        time.sleep(interval_s)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--target", default="app-02", help="backend container to stop")
    parser.add_argument("--duration", type=float, default=10.0,
                         help="seconds of continuous traffic to send during the outage")
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between probes")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    other = "app-02" if args.target == "app-01" else "app-01"

    print(f"== Failure/recovery test: stopping {args.target}, expecting {other} to keep serving ==\n")

    baseline_status, baseline_id = probe(base)
    check("baseline request succeeds before the induced failure",
          baseline_status == 200, f"status={baseline_status} instance={baseline_id}")

    stopped = False
    try:
        docker("stop", args.target)
        stopped = True
        print(f"Stopped {args.target}. Sending traffic for {args.duration}s...")

        results = []
        deadline = time.time() + args.duration
        while time.time() < deadline:
            results.append(probe(base))
            time.sleep(args.interval)

        client_errors = [r for r in results if r[0] is None or r[0] >= 500]
        served_by_other = [r for r in results if r[1] == other]
        served_by_target = [r for r in results if r[1] == args.target]

        check("zero client-visible 5xx/timeouts while one backend is down",
              len(client_errors) == 0,
              f"{len(client_errors)}/{len(results)} requests failed: {client_errors[:5]}")
        check(f"traffic continued to be served by surviving backend ({other})",
              len(served_by_other) > 0, f"{len(served_by_other)}/{len(results)} requests")
        check(f"stopped backend ({args.target}) served zero requests while down",
              len(served_by_target) == 0, f"{len(served_by_target)} requests slipped through")

        print(f"\nRestarting {args.target}...")
        docker("start", args.target)
        stopped = False
        healthy = wait_healthy(args.target, timeout_s=60)
        check(f"{args.target} reports healthy again within 60s", healthy)

        recovered_seen = set()
        for _ in range(20):
            _, instance_id = probe(base)
            if instance_id:
                recovered_seen.add(instance_id)
            time.sleep(0.3)
        check(f"{args.target} serves requests again after recovery",
              args.target in recovered_seen, f"observed instances: {sorted(recovered_seen)}")

    finally:
        if stopped:
            print(f"Ensuring {args.target} is restarted (cleanup)...")
            try:
                docker("start", args.target)
            except Exception as exc:
                print(f"WARNING: cleanup restart of {args.target} failed: {exc}", file=sys.stderr)

    print()
    if FAILED:
        print(f"FAILURE TEST FAILED: {len(FAILED)} check(s) failed -> {FAILED}")
        return 1
    print("FAILURE TEST PASSED: availability held and the backend recovered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
