#!/usr/bin/env python3
"""
Standalone verification for M4 timing baseline.

Uses FastAPI TestClient to test the calculations endpoints directly
without needing the full backend server with all its dependencies.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# Build a minimal FastAPI app with just the calculations router
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import the ship_timing module directly
import importlib.util
spec = importlib.util.spec_from_file_location("ship_timing", str(BACKEND_DIR / "services" / "ship_timing.py"))
ship_timing_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ship_timing_mod)

# Register it so calculations.py can import it
sys.modules["services"] = type(sys)("services")
sys.modules["services.ship_timing"] = ship_timing_mod

# Now import calculations
spec2 = importlib.util.spec_from_file_location("calculations", str(BACKEND_DIR / "api" / "calculations.py"))
calculations_mod = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(calculations_mod)

# Create minimal app
app = FastAPI(title="Ship Performance Test")
app.include_router(calculations_mod.router)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = ARTIFACTS_DIR / "timing_baseline.json"

# SUBOFF model parameters (DARPA SUBOFF)
SUBOFF = {
    "ship_type": "SUBOFF",
    "speed_ms": 1.0,
    "length": 4.356,
    "beam": 0.508,
    "draft": 0.476,
    "cb": 0.542,
    "water_density": 998.0,
    "kinematic_viscosity": 1.004e-6,
}

NUM_CALLS = 50


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Clear any existing timing records
    ship_timing_mod.get_collector().clear()
    ship_timing_mod.reset_counters()

    # Run 50 resistance calculations with different speeds
    print(f"[INFO] Running {NUM_CALLS} resistance calculations (SUBOFF, varying speeds)...")
    results = []
    for i in range(NUM_CALLS):
        # Speed range: 0.5 to 5.0 m/s (covering low to high Fn)
        speed = 0.5 + (i / (NUM_CALLS - 1)) * 4.5
        body = {**SUBOFF, "speed_ms": round(speed, 3)}
        try:
            resp = client.post("/calculations/resistance", json=body)
            assert resp.status_code == 200, f"POST /calculations/resistance failed: {resp.status_code} {resp.text}"
            data = resp.json()
            results.append({
                "call": i + 1,
                "speed_ms": speed,
                "total_resistance_n": data.get("total_resistance_n"),
                "frictional_resistance_n": data.get("frictional_resistance_n"),
                "residual_resistance_n": data.get("residual_resistance_n"),
                "reynolds_number": data.get("reynolds_number"),
                "froude_number": data.get("froude_number"),
                "computation_time_ms": data.get("computation_time_ms"),
            })
            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{NUM_CALLS}] speed={speed:.2f} m/s, Rt={data.get('total_resistance_n', '?')} N, Fn={data.get('froude_number', '?')}")
        except Exception as e:
            print(f"  [WARN] Call {i+1} failed: {e}")

    print(f"\n[OK] Completed {len(results)} successful calls.")

    # Also test stability endpoint (a few calls)
    print("\n[INFO] Testing stability endpoint (5 calls)...")
    for i in range(5):
        heel = 5 + i * 5
        body = {
            "ship_type": "KVLCC2",
            "displacement_t": 1000.0,
            "gm_m": 1.0,
            "kb_m": 2.0,
            "bm_m": 3.0,
            "heel_angle_deg": heel,
        }
        resp = client.post("/calculations/stability", json=body)
        assert resp.status_code == 200, f"POST /calculations/stability failed: {resp.status_code}"

    # Fetch timing baseline
    print("\n[INFO] Fetching timing baseline...")
    resp = client.get("/calculations/timing_baseline")
    assert resp.status_code == 200, f"GET /calculations/timing_baseline failed: {resp.status_code}"
    baseline_data = resp.json()

    # Validate
    records = baseline_data.get("timing_records", [])
    baseline = baseline_data.get("baseline", {})
    total = baseline_data.get("total_records", 0)

    print(f"  Total records: {total}")
    for ep, stats in baseline.items():
        print(f"  {ep}: count={stats['count']}, p50={stats['p50']:.3f}ms, p90={stats['p90']:.3f}ms, p99={stats['p99']:.3f}ms")

    # Check ship types
    ship_types = set(r["ship_type"] for r in records)
    print(f"  Ship types captured: {ship_types}")

    # Assertions
    assert len(records) >= 50, f"Expected >= 50 records, got {len(records)}"
    for ep, stats in baseline.items():
        assert stats["p50"] > 0, f"p50 for {ep} must be > 0, got {stats['p50']}"
        assert stats["p99"] >= stats["p90"] >= stats["p50"], \
            f"p99 >= p90 >= p50 violated for {ep}: p50={stats['p50']}, p90={stats['p90']}, p99={stats['p99']}"

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

    # Print summary table
    print("\n" + "=" * 60)
    print("TIMING BASELINE SUMMARY")
    print("=" * 60)
    print(f"{'Endpoint':<15} {'Count':>6} {'p50 (ms)':>10} {'p90 (ms)':>10} {'p99 (ms)':>10}")
    print("-" * 60)
    for ep, stats in baseline.items():
        print(f"{ep:<15} {stats['count']:>6} {stats['p50']:>10.3f} {stats['p90']:>10.3f} {stats['p99']:>10.3f}")
    print("=" * 60)

    # Sample resistance results
    print("\nSAMPLE RESISTANCE RESULTS (first 5):")
    print(f"{'Call':>5} {'Speed (m/s)':>12} {'Rt (N)':>10} {'Fn':>8}")
    print("-" * 40)
    for r in results[:5]:
        print(f"{r['call']:>5} {r['speed_ms']:>12.3f} {r['total_resistance_n']:>10.4f} {r['froude_number']:>8.4f}")

    print("\n[ALL CHECKS PASSED]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
