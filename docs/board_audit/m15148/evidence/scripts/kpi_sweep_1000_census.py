#!/usr/bin/env python3
"""KPI-SWEEP-1000 计量普查（只计量、不改任何数据）

三类子命令：
  samples    对 ship_samples 逐工况行做 L1 数学校验（内部恒等式复算），统计各口径工况数
  artifacts  对 worktree 批量/扫描线工件做内容哈希去重 + 逐工况清点
  ledger     合并 samples / artifacts / cluster_jobs 三方计数 → 最终 result JSON

口径铁律（详见 docs/kpi_sweep_1000_census.md）：
  一个"工况"= (求解链 identity, 参数向量) 的唯一实例，被无人值守批量执行器算至终态
  并留下可溯源记录（DB 行 / 工件 per-case 记录）。跨来源按工况身份去重，只计一次。

用法：
  python3 scripts/kpi_sweep_1000_census.py samples --in artifacts/kpi_sweep_1000/stage/ship_samples.json \
      --out artifacts/kpi_sweep_1000/samples_census.json
  python3 scripts/kpi_sweep_1000_census.py artifacts --manifest /tmp/artifact_manifest.txt \
      --hashes /tmp/artifact_hashes_full.tsv --out artifacts/kpi_sweep_1000/artifact_census.json
  python3 scripts/kpi_sweep_1000_census.py ledger --samples <...> --artifacts <...> --cluster <...> \
      --out artifacts/kpi_sweep_1000/kpi_sweep_1000_result.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import sys

TOL = 1e-3  # 相对误差容差：行内数值保留 6 位有效数字


def rel_err(calc: float, rec: float) -> float:
    if rec == 0 or not math.isfinite(rec) or not math.isfinite(calc):
        return float("inf")
    return abs(calc - rec) / abs(rec)


def close(calc, rec, tol=TOL):
    try:
        return rel_err(float(calc), float(rec)) <= tol
    except Exception:
        return False


# ── 各物理域的 L1 恒等式（复算 vs 落盘值） ──────────────────────────
def check_resistance(p, r):
    g, bc = p["geometry_params"], p["bc"]
    L = g.get("Lwl") or g.get("Lpp")
    V = bc["V_knot"] * 0.514444
    out = {"Rt=Rf+Rw+Ra": close(r["RF_N"] + r["RW_N"] + r["RA_N"], r["Rt_N"], 1e-6)}
    try:
        Re = V * L / bc["nu"]
        Cf = 0.075 / (math.log10(Re) - 2) ** 2
        out["Cf=ITTC57(Re)"] = close(Cf, r["Cf"])
        out["Re=VL/nu"] = close(Re, r["Re"])
        Rf = 0.5 * bc["rho"] * V * V * r["S_m2"] * Cf * r["one_plus_k"]
        out["Rf=0.5rhoV2S(1+k)Cf"] = close(Rf, r["RF_N"])
    except Exception:
        out["Cf=ITTC57(Re)"] = False
    return out


def check_stability(p, r):
    g = p["geometry_params"]
    return {
        "GM=KB+BM-KG": close(r["KB_m"] + r["BM_m"] - g["KG"], r["GM_m"], 1e-6),
        "BM=B^2/12T": close(g["B"] ** 2 / (12.0 * g["T"]), r["BM_m"]),
    }


def check_seakeeping(p, r):
    return {
        "significant_heave=4*sigma_heave": close(4.0 * r["sigma_heave_m"], r["significant_heave_m"], 1e-6),
        "GM_T_m=GM_T": close(p["geometry_params"]["GM_T"], r["GM_T_m"], 1e-6),
    }


def check_propulsion(p, r):
    g, bc = p["geometry_params"], p["bc"]
    out = {}
    try:
        out["eta0=J*KT/(2pi*KQ)"] = close(bc["J"] * r["KT"] / (2 * math.pi * r["KQ"]), r["eta0"])
    except Exception:
        out["eta0=J*KT/(2pi*KQ)"] = False
    try:
        T = r["KT"] * bc["rho"] * bc["n_rps"] ** 2 * g["D"] ** 4
        out["T=KT*rho*n^2*D^4"] = close(T, r["thrust_N"])
    except Exception:
        out["T=KT*rho*n^2*D^4"] = False
    return out


def check_ice(p, r):
    comp = sum(r.get(k, 0.0) for k in
               ("R_bending_N", "R_clearing_N", "R_submersion_N", "R_friction_N", "R_rotary_N"))
    out = {"R_ice=sum(components)": close(comp, r["R_ice_N"], 1e-3)}
    if r.get("reference_value_N"):
        recomputed = 100.0 * abs(r["R_ice_N"] - r["reference_value_N"]) / r["reference_value_N"]
        out["rel_err_vs_reference"] = close(recomputed, r["relative_error_pct"], 1e-2)
    return out


def check_green(p, r):
    try:
        ratio = r["attained_cii_g_t_nmi"] / r["required_cii_g_t_nmi"]
        return {"attained_ratio=attained/required": close(ratio, r["attained_ratio"])}
    except Exception:
        return {"attained_ratio=attained/required": False}


CHECKERS = {
    "kb_resistance": check_resistance,
    "kb_stability": check_stability,
    "kb_seakeeping": check_seakeeping,
    "kb_propulsion": check_propulsion,
    "kb_ice_navigation": check_ice,
    "kb_green_shipping": check_green,
}


def since_identity(calc_type: str, p, r):
    """工况身份键：求解链 + 归一化参数向量（跨来源去重用）。"""
    g = p.get("geometry_params") or {}
    bc = p.get("bc") or {}
    def norm(d):
        return tuple(sorted((k, round(float(v), 9)) for k, v in d.items()
                            if isinstance(v, (int, float)) and not isinstance(v, bool)))
    return f"{calc_type}|{norm(g)}|{norm(bc)}"


def cmd_samples(args):
    rows = json.load(open(args.inp))
    by_type = collections.Counter()
    checks = collections.defaultdict(lambda: collections.Counter())
    n_checked = collections.Counter()
    failed_rows = collections.defaultdict(list)
    ids = collections.defaultdict(set)
    nonempty = collections.Counter()
    rng = random.Random(20261111)  # 固定种子，可复算

    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["calc_type"]].append(r)

    for ct, grp in sorted(groups.items()):
        by_type[ct] = len(grp)
        sample = grp if len(grp) <= args.per_type else rng.sample(grp, args.per_type)
        checker = CHECKERS.get(ct)
        for row in grp:
            p, res = row.get("params") or {}, row.get("result") or {}
            if isinstance(p, str):
                p = json.loads(p)
            if isinstance(res, str):
                res = json.loads(res)
            if res:
                nonempty[ct] += 1
            ids[ct].add(since_identity(ct, p, res) if res else None)
        if checker:
            for row in sample:
                p, res = row.get("params") or {}, row.get("result") or {}
                if isinstance(p, str):
                    p = json.loads(p)
                if isinstance(res, str):
                    res = json.loads(res)
                try:
                    out = checker(p, res)
                except Exception as exc:  # 参数缺失等 → 记失败，不掩盖
                    out = {"checker_error": False}
                    failed_rows[ct].append({"row_id": row.get("tags", {}).get("case_id"), "err": str(exc)[:120]})
                for k, ok in out.items():
                    checks[ct][k + "|n"] += 1
                    checks[ct][k + "|pass"] += int(bool(ok))
                n_checked[ct] += 1

    report = {
        "input": args.inp,
        "rows_total": len(rows),
        "rows_by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "distinct_case_identity_by_type": {k: len([x for x in v if x]) for k, v in ids.items()},
        "rows_with_result_by_type": dict(nonempty),
        "l1_identity_checks": {
            ct: {
                "sampled_rows": n_checked[ct],
                "details": {k.rsplit("|", 1)[0]: {"n": checks[ct][k], "pass": checks[ct][k.rsplit("|", 1)[0] + "|pass"],
                                                 "pass_rate": round(checks[ct][k.rsplit("|", 1)[0] + "|pass"] / max(checks[ct][k], 1), 4)}
                            for k in checks[ct] if k.endswith("|n")},
                "checker_errors": failed_rows[ct][:5],
            } for ct in sorted(checks)
        },
    }
    # 口径分层（kb_* = 批量回填；geometry_validation = 单点几何自洽，非物理工况）
    kb = sum(v for k, v in by_type.items() if k.startswith("kb_"))
    geo = by_type.get("geometry_validation", 0)
    report["tiers"] = {
        "T1b_kb_batch_cases": kb,
        "T2_geometry_validation_rows": geo,
        "note": "kb_* 行=无人值守程序化批量回填（m9475），带 case_id+params+result，可 L1 复算；"
                "geometry_validation 行仅 Cb 自洽性检查（无物理量），不计入工况 KPI",
    }
    json.dump(report, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: report[k] for k in ("rows_total", "rows_by_type", "tiers")}, ensure_ascii=False, indent=1))


# ── 工件清点 ──────────────────────────────────────────────────────
CASE_LIST_KEYS = ("results", "cases", "case_results", "rows", "points", "runs", "samples")
BATCH_NAME_HINTS = ("batch", "sweep", "scan", "matrix", "campaign", "cases", "case_",
                    "series", "grid", "shard", "validation")


def looks_like_case(obj):
    if not isinstance(obj, dict):
        return False
    keys = set(obj)
    paramish = {"params", "parameters", "case_id", "index", "i", "speed", "re", "Re",
                "reynolds", "reynolds_number", "U", "L", "name", "case"} & keys
    resultish = {"status", "result", "data", "results", "value", "rt", "error", "ok",
                 "residual", "metrics", "Cd", "drag"} & keys
    return bool(paramish) and bool(resultish)


def count_cases(obj):
    """返回工件内逐工况记录数（终态记录），非工况结构返回 0。"""
    best = 0
    for k in CASE_LIST_KEYS:
        v = obj.get(k) if isinstance(obj, dict) else None
        if isinstance(v, list) and len(v) >= 2 and sum(1 for e in v[:50] if looks_like_case(e)) >= max(2, min(10, len(v[:50]) * 0.6)):
            best = max(best, len(v))
    if isinstance(obj, list) and len(obj) >= 2:
        if sum(1 for e in obj[:50] if looks_like_case(e)) >= max(2, min(10, len(obj[:50]) * 0.6)):
            best = max(best, len(obj))
    return best


def cmd_artifacts(args):
    manifest = [l.rstrip("\n").split("\t", 1) for l in open(args.manifest) if "\t" in l]
    hashed = [l.rstrip("\n").split("\t", 2) for l in open(args.hashes) if l.count("\t") == 2]
    # 去重：同一 sha256 = 同一工件内容（worktree 复制不算新执行）
    reps = {}
    for h, size, path in hashed:
        if h not in reps:
            reps[h] = {"sha256": h, "size": int(size), "rep_path": path, "copies": 0}
        reps[h]["copies"] += 1
    files_total = len(manifest)
    # 只解析名字带批量/扫描线索的工件（其余为图/单报告，不做工况清点）
    parsed, errors = 0, 0
    per_artifact = []
    for h, rec in sorted(reps.items(), key=lambda kv: -kv[1]["copies"]):
        name = os.path.basename(rec["rep_path"]).lower()
        if not any(x in name for x in BATCH_NAME_HINTS):
            continue
        try:
            obj = json.load(open(rec["rep_path"]))
        except Exception:
            errors += 1
            continue
        parsed += 1
        n = count_cases(obj)
        if n:
            per_artifact.append({"sha256": h, "path": rec["rep_path"],
                                 "copies": rec["copies"], "cases": n})
    out = {
        "manifest_files": files_total,
        "distinct_artifact_contents": len(reps),
        "artifacts_parsed": parsed,
        "parse_errors": errors,
        "case_bearing_artifacts": len(per_artifact),
        "cases_sum_over_distinct_artifacts": sum(a["cases"] for a in per_artifact),
        "artifacts": per_artifact[:400],
        "note": "同一 sha256 视为同一工件（worktree 复制不计新执行）；cases=工件内逐工况记录数",
    }
    json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "artifacts"}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("samples")
    s.add_argument("--in", dest="inp", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--per-type", type=int, default=400)
    a = sub.add_parser("artifacts")
    a.add_argument("--manifest", required=True)
    a.add_argument("--hashes", required=True)
    a.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "samples":
        cmd_samples(args)
    elif args.cmd == "artifacts":
        cmd_artifacts(args)


if __name__ == "__main__":
    main()