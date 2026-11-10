"""Produce one arm record for the cross-solver consistency gate.

Usage
-----
    python -m tools.consistency_arms.run_arm --solver lbm --case config/consistency/arms/... \\
        --out artifacts/consistency/<case>/arm_lbm_d24.json

The record is the only thing the gate consumes, so it must carry its own
provenance: sha256 of the case configuration, sha256 of the gate configuration,
the git commit of the working tree and the UTC timestamp.  Nothing about the
physics is hard-coded here -- every parameter comes from the case JSON.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.consistency_arms import lbm2d, polar2d  # noqa: E402
from backend.services.consistency.metrics import sha256_of  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:                                  # pragma: no cover
        pass
    return "unknown"


def _grid_summary(res: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("grid", "mesh"):
        if key in res:
            val = res[key]
            if isinstance(val, dict):
                return {k: v for k, v in val.items() if isinstance(v, (int, float, str, bool))}
    return {}


def _surface(res: Dict[str, Any], cfg: Dict[str, Any], solver: str
             ) -> Dict[str, Any]:
    if solver == "lbm":
        return lbm2d.surface_pressure(res, cfg)
    if solver == "polar":
        return polar2d.surface_pressure(res)
    return res["cp"]


def run(model: str, cfg: Dict[str, Any], gate_cfg_path: str) -> Dict[str, Any]:
    if model == "lbm":
        res = lbm2d.simulate(cfg, verbose=bool(cfg.get("verbose")))
    elif model == "polar":
        res = polar2d.solve_case(cfg, verbose=bool(cfg.get("verbose")))
    else:
        raise SystemExit(f"unknown model {model!r}")

    profile = _surface(res, cfg, model)
    gate_cfg = json.loads(Path(gate_cfg_path).read_text(encoding="utf-8"))
    arm = {
        "arm_id": cfg["arm_id"],
        "solver_family": cfg["solver_family"],
        "solver_version": cfg["solver_version"],
        "case_id": gate_cfg["case_id"],
        "quantities": {
            "cd": float(res["cd"]),
            "cd_friction": float(res.get("cd_friction", float("nan"))),
            "cd_pressure": float(res.get("cd_pressure", float("nan"))),
        },
        "profiles": {"cp_surface": profile},
        "energy": {"terms": {"power_in": float(res["power_in"]),
                            "dissipation": float(res["dissipation"])}},
        "declarations": list(cfg.get("declarations") or []),
        "convergence": {
            "steps": int(res.get("steps", 0)),
            "converged": bool(res.get("converged", False)),
            "steady_change": res.get("steady_change"),
            "dt": res.get("dt"),
        },
        "mesh": _grid_summary(res),
        "post": {"nu": res.get("nu")},
        "provenance": {
            "input_sha256": sha256_of(cfg),
            "config_sha256": sha256_of(gate_cfg),
            "git_commit": git_commit(),
            "generated_utc": _utc(),
            "case_file": cfg.get("_case_file"),
            "gate_config": gate_cfg_path,
        },
    }
    hist = res.get("history") or []
    arm["history_tail"] = [{k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                            for k, v in h.items()} for h in hist[-5:]]
    return arm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="case JSON (arm parameters)")
    ap.add_argument("--gate-config", default="config/consistency/bluff-body-2d-re100.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.case).read_text(encoding="utf-8"))
    cfg["_case_file"] = str(args.case)
    if args.steps:
        cfg["max_steps"] = args.steps
    if args.verbose:
        cfg["verbose"] = True
    arm = run(cfg["model"], cfg, args.gate_config)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(arm, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(json.dumps({"arm_id": arm["arm_id"], "cd": arm["quantities"]["cd"],
                      "dissipation": arm["energy"]["terms"]["dissipation"],
                      "converged": arm["convergence"]["converged"],
                      "steady_change": arm["convergence"]["steady_change"],
                      "steps": arm["convergence"]["steps"]}, indent=2))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())