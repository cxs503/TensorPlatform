#!/usr/bin/env python3
"""阻力引擎外部基准账本（m1666 验证欠账单）。

产出 ``runs/m1666/resistance_external_audit.json``：本单全部数字的唯一来源，
文档 ``docs/research/resistance_external_baseline_audit.md`` 由
``scripts/gen_resistance_external_doc.py`` 从该 JSON 注入，零手抄。

跑法::

    python3 scripts/resistance_external_audit.py            # 写账本
    python3 scripts/resistance_external_audit.py --print    # 只打印摘要

设计要点
--------
* 纯进程内计算（stdlib + `services.*`），不连 DB、不起 HTTP，秒级完成；
  本机 load15 > 100 时也不会给算力加负担。
* 对比对象**分两层**：
  1. ``existing_ct_only``：平台现有 `POST /calculations/benchmark_validate` 的口径
     （逐例比 Ct，15% 门槛）——用来界定"这条欠账以前被谁挡住了"；
  2. ``external_baseline``：本单新增的 S / Rt / Cr 外部基准复核。
* 参考行不可裁决（``reference_status=WITHHELD``）时一律 WITHHELD，不产生精度声明。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from services import resistance_external_baseline as ext  # noqa: E402
from services import ship_resistance as sr  # noqa: E402

CANON = BACKEND / "data" / "benchmark_reference.json"
OUT_DIR = REPO / "runs" / "m1666"
OUT_JSON = OUT_DIR / "resistance_external_audit.json"


def _extract_ship_geometry(path: Path) -> dict:
    """Read the endpoint's ``SHIP_GEOMETRY`` preset table *without importing it*.

    `api.domain.ship_benchmark` pulls psycopg2 (DB layer) at import time, which is
    not installed in this audit environment — but the preset table is a plain
    module-level dict literal. Parsing it with :mod:`ast` keeps the numbers
    identical to the shipped endpoint while avoiding the DB dependency.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SHIP_GEOMETRY" for t in node.targets):
            out = {}
            for k, v in zip(node.value.keys, node.value.values):
                ship = ast.literal_eval(k)
                preset = {}
                for kk, vv in zip(v.keys, v.values):
                    key = ast.literal_eval(kk)
                    try:
                        preset[key] = ast.literal_eval(vv)
                    except Exception:
                        preset[key] = None      # 派生量（displacement_from_cb 调用）跳过
                out[ship] = preset
            return out
    raise RuntimeError("SHIP_GEOMETRY not found")


def existing_ct_only_verdict(dataset: dict):
    """Reproduce the shipped ``benchmark_validate`` comparison (Ct only, 15% bar).

    This is the layer that already existed: per case it compares the engine's
    *total-resistance coefficient* with the recorded Ct and passes at <=15%.
    Ct is per unit wetted area, so by construction this metric **cannot see** an
    error in S — which is exactly the blind spot audited below.
    ``source`` records where the preset geometry came from.
    """
    src = BACKEND / "api" / "domain" / "ship_benchmark.py"
    try:
        presets = _extract_ship_geometry(src)
    except Exception as exc:  # pragma: no cover - source layout may drift
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    rows = []
    for case in dataset["cases"]:
        ship = case.get("ship_type")
        preset = presets.get(ship)
        if preset is None or not case.get("speed_ms") or case.get("experimental_ct") is None:
            continue
        bd = sr.resistance_breakdown(
            preset["length"], preset["beam"], preset["draft"], preset["cb"],
            case["speed_ms"],
            case.get("reference_density_kg_m3", 998.2),
            case.get("reference_kinematic_viscosity_m2_s", 1.004e-06),
        )
        ref_ct = case["experimental_ct"]
        err = ext.rel_err_pct(bd["ct"], ref_ct)
        rows.append({
            "case_id": case["case_id"],
            "ship_type": ship,
            "speed_ms": case["speed_ms"],
            "cb_preset": preset["cb"],
            "calc_ct": bd["ct"],
            "ref_ct": ref_ct,
            "error_pct": err,
            "pass_15pct": abs(err) <= 15.0,
            "reference_status": case.get("reference_status"),
        })
    return {
        "available": True,
        "source": str(src.relative_to(REPO)),
        "rows": rows,
        "note": "现有 benchmark_validate 口径：逐例比 Ct，|误差|<=15% 判 pass。"
                "Ct 按定义与湿面积无关 → 该口径结构上看不见 S 误差（本单的欠账所在）",
    }


def geometry_baselines(dataset: dict):
    """Per-row external baselines for the wetted surface (E1/E2/E3)."""
    out = {}
    for case in dataset["cases"]:
        ws = ext.check_wetted_surface_external(case)
        if ws.get("wetted_surface_baseline_m2") is None:
            continue
        out[case["case_id"]] = ws
    return out


def cb_provenance_sweep(dataset: dict):
    """How sensitive is the engine ``S`` to the unprovenanced Cb constant?"""
    rows = []
    for cb in (ext.CB_PROALTE_SPHEROID, ext.CB_FALLBACK_UNPROVENANCED, 0.6, 0.7, 0.8):
        rows.append({
            "cb": cb,
            "wetted_surface_m2": sr.wetted_surface_axisym(0.508, 4.356, cb),
            "error_pct_vs_recorded_5p42": ext.rel_err_pct(
                sr.wetted_surface_axisym(0.508, 4.356, cb), 5.42),
        })
    return {"length_m": 4.356, "diameter_m": 0.508,
            "scan": rows,
            "note": "Cb 从 π/6 扫到 0.8，引擎 S 全区间都到不了记录的 5.42 m²："
                    "该式是船体拟合，适用于平坦船中剖面，不适用于细长回转体"}


def cr_law_sweep(cases, cb: float = ext.CB_FALLBACK_UNPROVENANCED):
    """Engine Cr vs implied Cr over Fn (SUBOFF rows + a continuous Fn sweep)."""
    sweep = []
    for i in range(1, 61):
        fn = i * 0.015
        sweep.append({"fn": fn, "cr_engine": sr.residual_coefficient(fn, cb)})
    return {"cb": cb, "sweep": sweep,
            "note": "引擎 Cr=0.002(1+2Cb)Fn^2.5 在 Fn<=0.05 归零（阻塞流区保护）"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="do_print", action="store_true")
    args = ap.parse_args()

    dataset = json.loads(CANON.read_text(encoding="utf-8"))
    audit = ext.audit_dataset(dataset)

    payload = {
        "mission": 1666,
        "task": "resistance-engine external-baseline audit (verification debt R5-R7)",
        "canon": {"path": str(CANON.relative_to(REPO)),
                  "version": dataset.get("version"),
                  "n_cases": len(dataset.get("cases", []))},
        "bars": {"target_pct": ext.TARGET_BAR_PCT,
                 "accept_pct": ext.ACCEPT_BAR_PCT},
        "cb_fallback_constant": ext.CB_FALLBACK_UNPROVENANCED,
        "cb_prolate_spheroid": ext.CB_PROALTE_SPHEROID,
        "external_baseline": audit,
        "existing_ct_only": existing_ct_only_verdict(dataset),
        "wetted_surface_baselines": geometry_baselines(dataset),
        "cb_sensitivity": cb_provenance_sweep(dataset),
        "cr_law_sweep": cr_law_sweep(dataset["cases"]),
    }
    # 引擎默认路径的数值指纹：本单不动任何默认输出，用指纹锁回归
    payload["engine_fingerprint"] = {
        "suboff01_default": {
            k: v for k, v in sr.resistance_breakdown(
                4.356, 0.508, 0.508, 0.542, 3.0, 998.2, 1.004e-06).items()
            if k in ("wetted_surface_m2", "cf", "cr", "ct", "rt_n")
        },
        "note": "改前/改后必须一字不差；本单只加闸门，不改引擎默认数值",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    if args.do_print:
        print(json.dumps({
            "binding": audit["binding"],
            "per_form": audit["per_form_verdict"],
            "row_verdicts": audit["row_verdicts"],
        }, ensure_ascii=False, indent=1))
    print(f"wrote {OUT_JSON.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())