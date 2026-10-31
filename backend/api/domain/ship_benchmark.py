"""
Ship benchmark validation endpoint.

POST /api/v1/calculations/benchmark_validate
  Input:  {ship_type, benchmark_id}
  Output: {case_id, calc_ct, ref_ct, error_pct, pass: bool}

Compares Holtrop/ITTC-57 resistance calculation against experimental reference data.
Honest-FAIL: Holtrop method is calibrated for merchant ships (Cb>0.55); for SUBOFF
(Fr>0.4) and fine-form hulls, errors may exceed 15%. This is documented, not hidden.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
ConfigDict, BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["船舶基准验证"])

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
_BENCHMARK_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "benchmark_reference.json"


def _load_benchmark_data() -> Dict[str, Any]:
    """Load benchmark reference dataset."""
    if not _BENCHMARK_PATH.exists():
        raise FileNotFoundError(f"Benchmark reference file not found: {_BENCHMARK_PATH}")
    with open(_BENCHMARK_PATH, "r") as f:
        return json.load(f)


# Ship geometry presets for known benchmark hulls
SHIP_GEOMETRY = {
    "SUBOFF": {
        "length": 4.356,
        "beam": 0.508,       # diameter used as beam proxy
        "draft": 0.508,      # diameter used as draft proxy
        "displacement": 0.508,  # approximate
        "cb": 0.508,
    },
    "DTMB-5415": {
        "length": 5.720,
        "beam": 0.759,
        "draft": 0.246,
        "displacement": 466.3,
        "cb": 0.508,
    },
    "KVLCC2": {
        "length": 320.0,
        "beam": 58.0,
        "draft": 20.8,
        "displacement": 307000.0,
        "cb": 0.8098,
    },
}

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class BenchmarkValidateRequest(BaseModel):
    ship_type: str = Field(..., description="Ship type: SUBOFF, DTMB-5415, KVLCC2")
    benchmark_id: str = Field(..., description="Case ID from benchmark_reference.json")


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str
    ship_type: str
    speed_ms: float
    calc_ct: float
    ref_ct: float
    error_pct: float
    pass_: bool = Field(..., alias="pass")
    method: str = ""
    note: str = ""


class BenchmarkValidateResponse(BaseModel):
    results: List[BenchmarkResult]
    summary: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Resistance calculation via ship platform API
# ---------------------------------------------------------------------------

def _call_resistance_api(ship_type: str, speed_ms: float, geometry: Dict[str, Any]) -> Optional[Dict]:
    """Call the ship platform resistance calculation API."""
    try:
        import httpx
        # Ship platform runs on port 8002
        base_url = os.getenv("SHIP_PLATFORM_URL", "http://localhost:8002")
        payload = {
            "ship_type": ship_type,
            "speed": speed_ms,
            "length": geometry["length"],
            "beam": geometry["beam"],
            "draft": geometry["draft"],
            "displacement": geometry["displacement"],
            "cb": geometry["cb"],
        }
        resp = httpx.post(f"{base_url}/api/v1/calculations/resistance", json=payload, timeout=30.0)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Resistance API returned %d: %s", resp.status_code, resp.text[:200])
        return None
    except Exception as e:
        logger.error("Failed to call resistance API: %s", e)
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/calculations/benchmark_validate")
def benchmark_validate(req: BenchmarkValidateRequest) -> BenchmarkValidateResponse:
    """
    Validate resistance calculation against experimental benchmark data.

    Returns comparison of calculated Ct vs experimental Ct with error percentage
    and pass/fail judgment (pass = error_pct <= 15%).
    """
    data = _load_benchmark_data()
    cases = data.get("cases", [])

    # Find matching case(s)
    if req.benchmark_id == "all":
        matched = [c for c in cases if c["ship_type"] == req.ship_type]
    else:
        matched = [c for c in cases if c["case_id"] == req.benchmark_id and c["ship_type"] == req.ship_type]

    if not matched:
        raise HTTPException(
            status_code=404,
            detail=f"No benchmark case found for ship_type='{req.ship_type}', benchmark_id='{req.benchmark_id}'"
        )

    # Get geometry preset
    geometry = SHIP_GEOMETRY.get(req.ship_type)
    if not geometry:
        raise HTTPException(
            status_code=400,
            detail=f"No geometry preset for ship_type='{req.ship_type}'. Available: {list(SHIP_GEOMETRY.keys())}"
        )

    results: List[BenchmarkResult] = []
    honest_fail_cases: List[str] = []

    for case in matched:
        case_id = case["case_id"]
        speed_ms = case.get("speed_ms", 0.0)
        ref_ct = case["experimental_ct"]

        # Call resistance calculation
        calc_data = _call_resistance_api(req.ship_type, speed_ms, geometry)

        if calc_data is None:
            results.append(BenchmarkResult(
                case_id=case_id,
                ship_type=req.ship_type,
                speed_ms=speed_ms,
                calc_ct=0.0,
                ref_ct=ref_ct,
                error_pct=999.0,
                pass_=False,
                method="N/A",
                note="Resistance API call failed",
            ))
            continue

        calc_ct = calc_data.get("ct", 0.0)
        method = calc_data.get("method", "Unknown")

        # Calculate error percentage
        if ref_ct > 0:
            error_pct = abs(calc_ct - ref_ct) / ref_ct * 100.0
        else:
            error_pct = 999.0

        # Pass criterion: error <= 15%
        passed = error_pct <= 15.0

        note = ""
        if req.ship_type == "SUBOFF" and case.get("froude_number", 0) > 0.4:
            note = "HONEST-FAIL: Holtrop method calibrated for Cb>0.55 merchant ships; SUBOFF at Fr>0.4 is outside validity range. Algorithm upgrade recommended (CFD or slender-body theory)."
            if not passed:
                honest_fail_cases.append(case_id)

        results.append(BenchmarkResult(
            case_id=case_id,
            ship_type=req.ship_type,
            speed_ms=speed_ms,
            calc_ct=round(calc_ct, 6),
            ref_ct=ref_ct,
            error_pct=round(error_pct, 2),
            pass_=passed,
            method=method,
            note=note,
        ))

    # Summary
    total = len(results)
    passed_count = sum(1 for r in results if r.pass_)
    all_fail = total > 0 and passed_count == 0

    summary = {
        "total_cases": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate": round(passed_count / total * 100, 1) if total > 0 else 0,
        "honest_fail": len(honest_fail_cases) > 0,
        "honest_fail_cases": honest_fail_cases,
        "algorithm_upgrade_recommended": all_fail,
    }

    if all_fail:
        summary["recommendation"] = (
            f"All {total} cases exceed 15% error threshold. "
            f"Holtrop method is not suitable for {req.ship_type}. "
            "Recommend: CFD (RANS) or specialized method for this hull type."
        )

    return BenchmarkValidateResponse(results=results, summary=summary)


# ---------------------------------------------------------------------------
# Also expose a GET endpoint to list available benchmark cases
# ---------------------------------------------------------------------------

@router.get("/calculations/benchmark_cases")
def list_benchmark_cases(ship_type: Optional[str] = None) -> Dict[str, Any]:
    """List available benchmark cases, optionally filtered by ship_type."""
    data = _load_benchmark_data()
    cases = data.get("cases", [])
    if ship_type:
        cases = [c for c in cases if c["ship_type"] == ship_type]
    return {"total": len(cases), "cases": cases, "source": data.get("description", "")}
