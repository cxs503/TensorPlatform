#!/usr/bin/env python3
"""m15118 (KPI-SWEEP-1000) 独立复核脚本 —— board-audit m15148 自建，不复用被审脚本代码。

目的：用**独立实现**复算被审单的三个核心声明，避免"用被审脚本验被审脚本"的循环论证。
  C1 计数：kb_* 工况行数 / 三档口径 A,B,C / headline=12,116
  C2 镜像：kb_samples(11,861) 与 ship_samples.tags->>'case_id' 一一对应（只计一次的前提）
  C3 质量：L1 恒等式复算合格率（**重新抽样**，seed 与原件不同）
  C4 去重：batch_sweep_demo.json 的 976 份拷贝是否内容相同（sha256）
  C5 外部锚：tensor_benchmark_cases 是否登记本锚点（基准裁判）
  C6 可复现：重跑 ledger 脚本是否产出与入库 JSON 相同内容

用法: python3 docs/board_audit/m15148/verify_m15118.py [--md]
输出: docs/board_audit/m15148/verify_output.json
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path

DB = os.environ.get("TP_PG_DB", "tensorplatform")
M15118 = Path("/root/tp-agent/work/m15118")
OUT_DIR = Path(__file__).resolve().parent
TOL = 1e-3


def sql(query: str) -> list[list[str]]:
    cmd = ["su", "postgres", "-c", f'psql -d {DB} -t -A -F "\t" -c "{query}"']
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:400])
    return [ln.split("\t") for ln in r.stdout.strip().splitlines() if ln.strip()]


def rows(domain: str, n: int, seed: int) -> list[dict]:
    q = (f"SELECT geometry_params::text || E'\\x01' || bc::text || E'\\x01' || results::text "
         f"FROM kb_samples WHERE physics_domain='{domain}' "
         f"ORDER BY md5(case_id || 'audit{seed}') LIMIT {n}")
    out = []
    for (payload, *_) in sql(q):
        g, b, r = payload.split("\x01")
        out.append({"params": {"geometry_params": json.loads(g), "bc": json.loads(b)},
                    "results": json.loads(r)})
    return out


def close(a, b, tol=TOL) -> bool:
    try:
        a, b = float(a), float(b)
        if b == 0 or not math.isfinite(a) or not math.isfinite(b):
            return False
        return abs(a - b) / abs(b) <= tol
    except Exception:
        return False


def rate(hits: list[bool]) -> dict:
    return {"n": len(hits), "pass": sum(hits), "rate": round(sum(hits) / max(len(hits), 1), 4)}


# ── C1 计数 ──────────────────────────────────────────────────────────
def c1_counts() -> dict:
    per = {r[0]: int(r[1]) for r in sql(
        "SELECT calc_type, count(*) FROM ship_samples GROUP BY 1 ORDER BY 2 DESC")}
    kb = int(sql("SELECT count(*) FROM kb_samples")[0][0])
    kb_face = sum(v for k, v in per.items() if k.startswith("kb_"))
    geo = per.get("geometry_validation", 0)
    return {
        "ship_samples_total": sum(per.values()),
        "by_calc_type": per,
        "kb_samples_rows": kb,
        "kb_prefixed_ship_rows": kb_face,
        "geometry_validation_rows": geo,
        "mirror_delta_kb_minus_ship": kb - kb_face,
        "C_wide_recompute": kb_face + geo,
    }


# ── C2 镜像（只计一次的前提）────────────────────────────────────────
def c2_mirror() -> dict:
    # kb_samples.case_id ↔ ship_samples.tags.case_id 的交集/差集
    q = ("SELECT count(*) FROM (SELECT case_id FROM kb_samples EXCEPT "
         "SELECT tags->>'case_id' FROM ship_samples WHERE calc_type LIKE 'kb\\_%') t")
    only_kb = int(sql(q)[0][0])
    q2 = ("SELECT count(*) FROM (SELECT tags->>'case_id' FROM ship_samples WHERE calc_type LIKE 'kb\\_%' "
          "EXCEPT SELECT case_id FROM kb_samples) t")
    only_ship = int(sql(q2)[0][0])
    return {"kb_only": only_kb, "ship_only": only_ship,
            "verdict": "IDENTICAL" if only_kb == 0 and only_ship == 0 else "MISMATCH"}


# ── C3 L1 恒等式（独立重抽）─────────────────────────────────────────
def c3_identities(n=300, seed=20261111) -> dict:
    out: dict[str, dict] = {}

    rs = rows("resistance", n, seed)
    for key, fn in {
        "Cf=ITTC57(Re)": lambda p, r: close(r["Cf"], 0.075 / (math.log10(r["Re"]) - 2) ** 2),
        "Rt=sum(1e-3)": lambda p, r: close(r["Rt_N"], r["RF_N"] + r["RW_N"] + r["RA_N"]),
        "Re=VL/nu": lambda p, r: close(r["Re"], p["bc"]["V_knot"] * 0.514444 *
                                       (p["geometry_params"].get("Lwl") or p["geometry_params"].get("Lpp")) /
                                       p["bc"]["nu"], 1e-4),
    }.items():
        out[f"resistance.{key}"] = rate([bool(fn(x["params"], x["results"])) for x in rs])

    pr = rows("propulsion", n, seed)
    out["propulsion.eta0=J*KT/(2pi*KQ)"] = rate(
        [close(x["results"]["eta0"], x["results"]["J"] * x["results"]["KT"] /
               (2 * math.pi * x["results"]["KQ"])) for x in pr])
    out["propulsion.T=KT*rho*n^2*D^4"] = rate(
        [close(x["results"]["thrust_N"], x["results"]["KT"] * x["params"]["bc"]["rho"] *
               x["params"]["bc"]["n_rps"] ** 2 * x["params"]["geometry_params"]["D"] ** 4, 1e-4)
         if "n_rps" in x["params"].get("bc", {}) else False for x in pr])

    ic = rows("ice_navigation", n, seed)
    out["ice.R_ice=sum(components)"] = rate(
        [close(x["results"]["R_ice_N"], sum(x["results"].get(k, 0.0) for k in
              ("R_rotary_N", "R_bending_N", "R_clearing_N", "R_friction_N", "R_submersion_N"))) for x in ic])
    out["ice.rel_err_vs_reference<10%"] = rate(
        [abs(x["results"].get("relative_error_pct", 999)) < 10.0 for x in ic])

    st = rows("stability", n, seed)
    out["stability.GM=KB+BM-KG"] = rate(
        [close(x["results"]["GM_m"], x["results"]["KB_m"] + x["results"]["BM_m"] -
               x["params"]["geometry_params"]["KG"]) for x in st])

    sk = rows("seakeeping", n, seed)
    out["seakeeping.significant=2sigma"] = rate(
        [close(x["results"]["significant_heave_m"], 2.0 * x["results"]["sigma_heave_m"]) for x in sk])
    out["seakeeping.significant=4sigma"] = rate(
        [close(x["results"]["significant_heave_m"], 4.0 * x["results"]["sigma_heave_m"]) for x in sk])
    return out


# ── C4 工件去重 ─────────────────────────────────────────────────────
def c4_dedup() -> dict:
    paths = []
    for p in Path("/root/tp-agent/work").glob("*/artifacts/**/batch_sweep_demo.json"):
        paths.append(p)
    for p in Path("/root/tp-agent/work").glob("*/runs/**/batch_sweep_demo.json"):
        paths.append(p)
    digests, cases = set(), []
    for p in paths:
        try:
            b = p.read_bytes()
        except OSError:
            continue
        digests.add(hashlib.sha256(b).hexdigest())
        try:
            d = json.loads(b)
            c = d.get("cases") if isinstance(d, dict) else d
            cases.append(len(c) if isinstance(c, list) else None)
        except Exception:
            cases.append(None)
    distinct_cases = sorted({c for c in cases if isinstance(c, int)})
    return {"copies_found": len(paths), "distinct_sha256": len(digests),
            "cases_per_copy_distinct": distinct_cases,
            "note": "若 distinct_sha256==1 则 976 份拷贝同一执行，只应计一次"}


# ── C5 外部锚 ───────────────────────────────────────────────────────
def c5_anchor() -> dict:
    n = int(sql("SELECT count(*) FROM tensor_benchmark_cases WHERE anchor_slug='KPI-SWEEP-1000'")[0][0])
    tot = int(sql("SELECT count(*) FROM tensor_benchmark_cases")[0][0])
    return {"anchor_rows": n, "table_rows_total": tot,
            "verdict": "NO_BENCHMARK_CASE" if n == 0 else "REGISTERED"}


# ── C6 可复现（重跑 ledger 到临时副本）──────────────────────────────
def c6_repro() -> dict:
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="audit_m15118_"))
    for sub in ("artifacts", "scripts"):
        shutil.copytree(M15118 / sub, tmp / sub)
    committed = json.loads((M15118 / "artifacts/kpi_sweep_1000/kpi_sweep_1000_result.json").read_text())
    r = subprocess.run([sys.executable, "scripts/kpi_sweep_1000_ledger.py"], cwd=tmp,
                       capture_output=True, text=True)
    regenerated = json.loads((tmp / "artifacts/kpi_sweep_1000/kpi_sweep_1000_result.json").read_text())
    a, b = committed.get("kpi", committed), regenerated.get("kpi", regenerated)
    diff_keys = sorted(set(committed) ^ set(regenerated))
    differing = sorted(k for k in set(a) & set(b) if a.get(k) != b.get(k))
    return {
        "ledger_exit": r.returncode,
        "stdout_tail": r.stdout.strip().splitlines()[-3:],
        "headline_committed": a.get("current"),
        "headline_regenerated": b.get("current"),
        "headline_delta": (None if a.get("current") is None or b.get("current") is None
                           else b.get("current") - a.get("current")),
        "keys_only_in_committed": [k for k in diff_keys if k in committed],
        "keys_differing_values": differing,
        "non_idempotent": bool(diff_keys or differing),
        "stage_input_committed": (M15118 / "artifacts/kpi_sweep_1000/stage/ship_samples.json").exists()
        and bool(subprocess.run(["git", "-C", str(M15118), "ls-files",
                                 "artifacts/kpi_sweep_1000/stage/ship_samples.json"],
                                capture_output=True, text=True).stdout.strip()),
        "tmpdir": str(tmp),
    }


def main() -> int:
    res = {
        "mission": 15118,
        "anchor": "KPI-SWEEP-1000",
        "audited_commit": subprocess.run(["git", "-C", str(M15118), "rev-parse", "HEAD"],
                                         capture_output=True, text=True).stdout.strip(),
        "C1_counts": c1_counts(),
        "C2_mirror": c2_mirror(),
        "C3_identities": c3_identities(),
        "C4_dedup": c4_dedup(),
        "C5_anchor": c5_anchor(),
        "C6_repro": c6_repro(),
    }
    res["C1_counts"]["B_headline_recompute"] = (
        res["C1_counts"]["kb_prefixed_ship_rows"] + 255)  # A 档待 C6/工件核对
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "verify_output.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())