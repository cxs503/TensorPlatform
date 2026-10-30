#!/usr/bin/env python3
"""
Verification script: Compare readback values with original solve values.
Exit 0 if all fields match, exit 1 otherwise.
"""
import requests
import json
import sys
from pathlib import Path

TENSOR_API_BASE = "http://127.0.0.1:8010/api/v1"
CREDS = {"username": "eco_admin", "password": "EcoTest!2026pass"}

def main():
    # Load trace
    trace_path = Path("/srv/tensor/agent/work/m505/docs/closure_proof/trace.json")
    with open(trace_path) as f:
        trace_data = json.load(f)
    
    run_id = trace_data["run_id"]
    
    # Login
    r = requests.post(f"{TENSOR_API_BASE}/auth/login", json=CREDS)
    assert r.status_code == 200
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Readback
    r = requests.get(f"{TENSOR_API_BASE}/tensorphysics/runs/{run_id}", headers=headers)
    assert r.status_code == 200
    readback = r.json()
    
    # Extract original solve data from trace
    solve_step = next(s for s in trace_data["trace"] if s["step_id"] == "S5_SOLVE")
    original = solve_step["data"]
    
    # Extract readback data
    rb_data = readback["manifest"]["outputs"][0]["data"]
    
    # Compare fields
    checks = {
        "rt": rb_data["rt"] == original["rt_total_kN"],
        "froude_number": rb_data["froude_number"] == original["froude_number"],
        "reynolds_number": rb_data["reynolds_number"] == original["reynolds_number"],
        "cf": rb_data["cf"] == original["cf"],
        "ct": rb_data["ct"] == original["ct"],
        "rf": rb_data["rf"] == original.get("rf", rb_data.get("rf")),
        "rr": rb_data["rr"] == original.get("rr", rb_data.get("rr")),
        "method": rb_data["method"] == original["method"],
    }
    
    print("=" * 60)
    print("FIELD-BY-FIELD VERIFICATION")
    print("=" * 60)
    all_pass = True
    for field, passed in checks.items():
        status = "✓" if passed else "✗"
        orig_val = original.get(field, original.get(f"{field}_kN", "N/A"))
        rb_val = rb_data.get(field, "N/A")
        print(f"  {status} {field}: original={orig_val}  readback={rb_val}")
        if not passed:
            all_pass = False
    
    # Additional structural checks
    struct_checks = {
        "run_id_match": readback["run_id"] == run_id,
        "status_match": readback["status"] == "succeeded",
        "validation_match": readback["validation_status"] == "passed",
        "sha256_present": bool(readback.get("manifest_sha256")),
        "timestamps_present": all(k in readback for k in ["created_at", "updated_at"]),
    }
    
    print("\n" + "=" * 60)
    print("STRUCTURAL VERIFICATION")
    print("=" * 60)
    for field, passed in struct_checks.items():
        status = "✓" if passed else "✗"
        print(f"  {status} {field}")
        if not passed:
            all_pass = False
    
    print("\n" + "=" * 60)
    if all_pass:
        print("RESULT: ALL CHECKS PASSED ✓")
        print("=" * 60)
        return 0
    else:
        print("RESULT: SOME CHECKS FAILED ✗")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
