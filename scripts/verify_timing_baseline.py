#!/usr/bin/env python3
"""
End-to-end verification for M4 timing baseline.

Calls the resistance endpoint 50 times with SUBOFF at different speeds,
then fetches the timing baseline and saves to artifacts/timing_baseline.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import subprocess
import signal
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = ARTIFACTS_DIR / "timing_baseline.json"

# SUBOFF model parameters (DARPA SUBOFF)
SUBOFF = {
    "ship_type": "SUBOFF",
    "length": 4.356,    # m
    "beam": 0.508,      # m
    "draft": 0.476,     # m
    "cb": 0.542,
    "water_density": 998.0,
    "kinematic_viscosity": 1.004e-6,
}

NUM_CALLS = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_post(path: str, body: dict) -> dict:
    """POST JSON to the API and return parsed response."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_get(path: str) -> dict:
    """GET JSON from the API and return parsed response."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    req = Request(url, method="GET")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_server(url: str, timeout: int = 30) -> bool:
    """Wait for the server to become ready."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urlopen(f"{url}/calculations/timing_baseline", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    server_proc = None
    need_server = False

    # Check if server is already running
    try:
        api_get("calculations/timing_baseline")
        print("[OK] Server is already running.")
    except Exception:
        need_server = True

    if need_server:
        print("[INFO] Starting backend server...")
        backend_dir = Path(__file__).resolve().parents[1] / "backend"
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001", "--workers", "1"],
            cwd=str(backend_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not wait_for_server(BASE_URL.rsplit("/api/v1", 1)[0]):
            print("[FAIL] Server did not start in time.")
            if server_proc:
                server_proc.kill()
            sys.exit(1)
        print("[OK] Server started.")

    try:
        # Clear any existing timing records
        from services.ship_timing import get_collector
        get_collector().clear()

        # Run 50 resistance calculations with different speeds
        print(f"\n[INFO] Running {NUM_CALLS} resistance calculations (SUBOFF, varying speeds)...")
        results = []
        for i in range(NUM_CALLS):
            # Speed range: 0.5 to 5.0 m/s (covering low to high Fn)
            speed = 0.5 + (i / (NUM_CALLS - 1)) * 4.5
            body = {**SUBOFF, "speed_ms": round(speed, 3)}
            try:
                resp = api_post("calculations/resistance", body)
                results.append({
                    "call": i + 1,
                    "speed_ms": speed,
                    "total_resistance_n": resp.get("total_resistance_n"),
                    "froude_number": resp.get("froude_number"),
                })
                if (i + 1) % 10 == 0:
                    print(f"  [{i+1}/{NUM_CALLS}] speed={speed:.2f} m/s, Rt={resp.get('total_resistance_n', '?')} N")
            except Exception as e:
                print(f"  [WARN] Call {i+1} failed: {e}")

        print(f"\n[OK] Completed {len(results)} successful calls.")

        # Fetch timing baseline
        print("\n[INFO] Fetching timing baseline...")
        baseline_data = api_get("calculations/timing_baseline")

        # Validate
        records = baseline_data.get("timing_records", [])
        baseline = baseline_data.get("baseline", {})
        total = baseline_data.get("total_records", 0)

        print(f"  Total records: {total}")
        for ep, stats in baseline.items():
            print(f"  {ep}: count={stats['count']}, p50={stats['p50']:.3f}ms, p90={stats['p90']:.3f}ms, p99={stats['p99']:.3f}ms")

        # Assertions
        assert len(records) >= 50, f"Expected >= 50 records, got {len(records)}"
        for ep, stats in baseline.items():
            assert stats["p50"] > 0, f"p50 for {ep} must be > 0"
            assert stats["p99"] >= stats["p90"] >= stats["p50"], \
                f"p99 >= p90 >= p50 violated for {ep}: {stats}"

        # Save output
        output = {
            "timing_records": records,
            "baseline": baseline,
            "total_records": total,
            "verification": {
                "num_calls": NUM_CALLS,
                "successful_calls": len(results),
                "sample_results": results[:5],
            }
        }
        OUTPUT_FILE.write_text(json.dumps(output, indent=2))
        print(f"\n[OK] Saved timing baseline to {OUTPUT_FILE}")
        print(f"  Records: {len(records)}, p50={baseline.get('resistance', {}).get('p50', 'N/A')}ms")

    finally:
        if server_proc:
            print("\n[INFO] Stopping server...")
            server_proc.send_signal(signal.SIGTERM)
            server_proc.wait(timeout=10)
            print("[OK] Server stopped.")


if __name__ == "__main__":
    main()
