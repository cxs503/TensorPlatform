#!/usr/bin/env python3
"""在线服务合同/物理漂移探针（m1762 / tp-infra-1）。

三件事，全部机器判据：

1. **合同可达性**：把仓内受证层需要的物理输入，与在线 ``openapi.json`` 的
   请求合同对照（``contract_drift.py``），得出每端点 ``reachability``；
2. **在线数值**：用 *在线合法* 方言发包，记录服务自报的 KB/BM/GM/衡准；
   再用 *仓内受证* 方言发包，记录被拒字段（schema 漂移的直接证据）；
3. **解析裁决**：用 ``src/ship_perf/stability_reference`` 的独立积分
   （矩形浮箱精确解，与 wall-sided 闭式零共享代码）给出同一几何的
   KB/BM/GM/GZ 与 IS Code 面积量，逐项算相对误差。

输出 ``artifacts/drift_probe.json`` + 两张图。
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
SERVICES = os.path.join(ROOT, "backend", "services")
sys.path.insert(0, SERVICES)

import stability_reference as sr  # noqa: E402  独立解析（零共享代码路径）
import contract_drift as cd  # noqa: E402

LIVE = os.environ.get("SHIP_API", "http://127.0.0.1:8001")
ART = os.path.join(HERE, "artifacts")

# 案例：矩形浮箱驳（棱柱形，与在线内部假定的几何同类，便于逐量比对）
CASE = {"length": 30.0, "beam": 10.0, "draft": 3.0, "displacement": 500.0,
        "cb": 0.6, "kg": 4.0}
DEPTHS = (4.5, 6.0, 8.0)   # 在线不收 depth → 深的灵敏度必须显式给出


def post(path: str, body: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{LIVE}/api/v1{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return {"status": fh.status, "data": json.load(fh)}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw[:400]}
        missing = [d.get("loc", ["", ""])[-1] for d in payload.get("detail", [])
                   if isinstance(d, dict) and d.get("type") == "missing"]
        return {"status": exc.code, "data": payload, "missing": missing}


def analytic(beam: float, draft: float, kg: float, depth: float) -> Dict[str, float]:
    """独立解析：浮箱 KB/BM/GM + GZ 曲线派生量（恒定排水量横倾）。"""
    gz, hind = [], []
    hd = 0.0
    while hd <= 90.0 + 1e-9:
        gz.append(sr.exact_gz_box(beam, draft, kg, math.radians(hd), depth)["gz_m"])
        hind.append(hd)
        hd += 0.5
    rad = [math.radians(h) for h in hind]
    a30 = _area(rad, gz, 30.0, 40.0)
    a40 = _area(rad, gz, 0.0, 40.0)
    imax = max(range(len(gz)), key=lambda i: gz[i])
    vanish = next((hind[i] for i in range(len(gz)) if hind[i] > hind[imax] and gz[i] <= 0), 90.0)
    return {
        "kb_m": draft / 2.0,
        "bm_m": beam ** 2 / (12.0 * draft),
        "gm_analytic_m": draft / 2.0 + beam ** 2 / (12.0 * draft) - kg,
        "gz_max_m": gz[imax],
        "gz_max_angle_deg": hind[imax],
        "vanishing_angle_deg": vanish,
        "area_30_40": a30,
        "area_0_40": a40,
        "depth_m": depth,
        "wall_sided_limit_deg": sr.wall_sided_limit_deg(beam, draft, depth),
        "depth_assumed": depth is None,
    }


def _area(rad: List[float], gz: List[float], a: float, b: float) -> float:
    """梯形积分（只取 GZ>0 段，与 IS Code 面积定义一致）。"""
    tot = 0.0
    for i in range(len(rad) - 1):
        da, db = math.degrees(rad[i]), math.degrees(rad[i + 1])
        if db <= a or da >= b:
            continue
        g0 = gz[i] if gz[i] > 0 else 0.0
        g1 = gz[i + 1] if gz[i + 1] > 0 else 0.0
        w = (min(db, b) - max(da, a)) / (db - da)
        tot += 0.5 * (g0 + g1) * w * (rad[i + 1] - rad[i])
    return tot


def rel(new: float, ref: float) -> float:
    return (new - ref) / abs(ref) * 100.0 if abs(ref) > 1e-12 else float("nan")


def probe_stability() -> Dict[str, Any]:
    live_ok = post("/calculations/stability", CASE)
    # 仓内受证方言（m1749 层）**单独**发包：只用 gm_m/bm_m/kb_m/depth_m/heel_angle_deg，
    # 不带在线方言的 length/beam/draft/displacement/cb/kg —— 用来证明在线拒收受证方言。
    canonical = {"gm_m": 0.2778, "bm_m": 2.7778, "kb_m": 1.5, "displacement_t": 500.0,
                 "heel_angle_deg": 40.0, "beam_m": 10.0, "draft_m": 3.0, "depth_m": 4.5}
    live_rejects = post("/calculations/stability", canonical)

    rows = []
    if live_ok["status"] == 200:
        d = live_ok["data"]
        ref = analytic(CASE["beam"], CASE["draft"], CASE["kg"], DEPTHS[0])
        for q, live_key in (("kb_m", "kb"), ("bm_m", "bm"), ("gm_analytic_m", "gm")):
            rows.append({"quantity": q, "live": d.get(live_key), "analytic": ref[q],
                         "rel_pct": rel(d.get(live_key, 0.0), ref[q]),
                         "label": {"kb": "KB", "bm": "BM", "gm": "GM"}[live_key]})
        for q in ("gz_max_m", "area_30_40", "area_0_40"):
            rows.append({"quantity": q, "live": d.get(q, d.get("gz_max")),
                         "analytic": ref[q], "rel_pct": rel(_pick(d, q), ref[q]),
                         "label": {"gz_max_m": "GZmax", "area_30_40": "A30-40",
                                   "area_0_40": "A0-40"}[q]})
    return {
        "case": CASE,
        "live_schema_legal": {"status": live_ok["status"], "response": live_ok["data"]},
        "repo_canonical_dialect": {"status": live_rejects["status"],
                                   "missing_fields": live_rejects.get("missing", [])},
        "comparison_vs_analytic": rows,
        "analytic_depth_sensitivity": [
            {k: v for k, v in analytic(CASE["beam"], CASE["draft"], CASE["kg"], D).items()}
            for D in DEPTHS
        ],
    }


def _pick(d: Dict[str, Any], q: str) -> float:
    if q == "gz_max_m":
        return float(d.get("gz_max", 0.0))
    return float(d.get(q, 0.0))


def probe_hardcode() -> List[Dict[str, Any]]:
    """KG 扫描：受证实现里消失角必须随 KG 变化；恒定值 = 硬编码指纹。"""
    out = []
    for kg in (3.2, 4.0, 4.8):
        body = dict(CASE, kg=kg)
        r = post("/calculations/stability", body)
        ref = analytic(CASE["beam"], CASE["draft"], kg, DEPTHS[0])
        out.append({
            "kg": kg,
            "live": {k: r["data"].get(k) for k in
                     ("kb", "bm", "gm", "gz_max", "gz_max_angle", "vanishing_angle")}
            if r["status"] == 200 else None,
            "analytic": ref,
            "status": r["status"],
        })
    return out


def figure(probe: Dict[str, Any], hard: List[Dict[str, Any]], outdir: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    figs = []

    # 图 1：在线 vs 解析 —— 逐量相对误差（3% 门 / 15% 拒收线）
    rows = probe["comparison_vs_analytic"]
    labels = [r["label"] for r in rows]
    errs = [abs(r["rel_pct"]) for r in rows]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    bars = ax.bar(labels, errs, color=["#c0392b" if e > 15 else "#27ae60" for e in errs])
    ax.set_yscale("log")
    ax.axhline(3, color="g", ls="--", lw=1.2, label="3% gate")
    ax.axhline(15, color="r", ls=":", lw=1.4, label="15% reject")
    for b, e in zip(bars, errs):
        ax.text(b.get_x() + b.get_width() / 2, e * 1.15, f"{e:.1f}%",
                ha="center", fontsize=9)
    ax.set_ylabel("|rel. error| vs analytic box (%, log)")
    ax.set_title("Live :8001 stability vs independent analytic box solution\n"
                 f"L={CASE['length']:.0f} B={CASE['beam']:.0f} T={CASE['draft']:.0f} "
                 f"Cb={CASE['cb']} KG={CASE['kg']}", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    f1 = os.path.join(outdir, "fig_live_vs_analytic.png")
    fig.tight_layout()
    fig.savefig(f1, dpi=150)
    plt.close(fig)
    figs.append(f1)

    # 图 2：GZ 曲线（解析，多个 depth）+ 在线自报 GZmax 点
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for D in DEPTHS:
        xs = [h / 2 for h in range(0, 181)]
        ys = [sr.exact_gz_box(CASE["beam"], CASE["draft"], CASE["kg"],
                              math.radians(x), D)["gz_m"] for x in xs]
        ax.plot(xs, ys, lw=1.8, label=f"analytic box, D={D} m")
    live = probe["live_schema_legal"]["response"]
    if live:
        ax.plot([live.get("gz_max_angle", 40)], [live.get("gz_max", 0)], "X",
                ms=11, color="#c0392b", label="live self-reported GZmax")
        ax.annotate(f"live GZmax={live.get('gz_max')} m @{live.get('gz_max_angle')}°",
                    (live.get("gz_max_angle", 40), live.get("gz_max", 0)),
                    textcoords="offset points", xytext=(8, -14), fontsize=8, color="#c0392b")
    ax.set_xlabel("heel angle φ (deg)")
    ax.set_ylabel("GZ (m)")
    ax.set_title("Righting arm: independent analytic box vs live self-report", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    f2 = os.path.join(outdir, "fig_gz_curve.png")
    fig.tight_layout()
    fig.savefig(f2, dpi=150)
    plt.close(fig)
    figs.append(f2)

    # 图 3：合同漂移矩阵（字段存在性热图）
    m = probe["contract_matrix"]
    focus = [r for r in m["rows"] if r["status"] in
             (cd.STATUS_TOPOLOGY, cd.STATUS_HIDDEN, cd.STATUS_INPUT_DRIFT)]
    if focus:
        fields, _ = [], set()
        for r in focus:
            _ = r.get("repo_accepted") or []
        allf = sorted({f for r in focus for f in (r.get("repo_accepted") or [])} |
                      {f for r in focus for f in r.get("live_props", [])})
        mat = []
        rowlabels = []
        for r in focus:
            acc = set(r.get("repo_accepted") or [])
            lprops = set(r.get("live_props") or [])
            mat.append([1 if (f in acc and f in lprops) else
                        (0.5 if f in acc else (0.2 if f in lprops else 0.0)) for f in allf])
            rowlabels.append(f"{r['endpoint'].split('/api/v1/')[1]}\n({r['status']})")
        fig, ax = plt.subplots(figsize=(max(6.4, 0.34 * len(allf) + 3), 0.9 * len(focus) + 2.2))
        im = ax.imshow(mat, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
        ax.set_xticks(range(len(allf)))
        ax.set_xticklabels(allf, rotation=90, fontsize=7)
        ax.set_yticks(range(len(focus)))
        ax.set_yticklabels(rowlabels, fontsize=7)
        ax.set_title("Contract reachability: green=both, amber=repo-only (hidden), "
                     "pale=live-only (demanded)", fontsize=9)
        fig.colorbar(im, ax=ax, shrink=0.7, ticks=[0, 0.5, 1])
        f3 = os.path.join(outdir, "fig_contract_matrix.png")
        fig.tight_layout()
        fig.savefig(f3, dpi=150)
        plt.close(fig)
        figs.append(f3)
    return figs


def main() -> int:
    os.makedirs(ART, exist_ok=True)
    snapshot = os.path.join(ART, "live_openapi_trimmed.json")
    if not os.path.exists(snapshot):
        spec = cd.fetch_live_spec(f"{LIVE}/openapi.json")
        trim = {"paths": {}, "components": {"schemas": spec.get("components", {}).get("schemas", {})}}
        for p, ops in spec["paths"].items():
            if any(t in p.lower() for t in ("calculations/", "resistance", "stability",
                                            "propulsion", "hydrostatics", "quality/check")):
                trim["paths"][p] = ops
        with open(snapshot, "w", encoding="utf-8") as fh:
            json.dump(trim, fh)
    spec = json.load(open(snapshot, encoding="utf-8"))
    matrix = cd.build_matrix(os.path.join(ROOT, "backend", "api"), spec)
    matrix["source"] = snapshot

    probe = {
        "live_base": LIVE,
        "contract_matrix": matrix,
        "stability": probe_stability(),
        "hardcode_sensitivity": probe_hardcode(),
    }
    figs = figure(dict(probe["stability"], contract_matrix=matrix),
                  probe["hardcode_sensitivity"], ART)
    probe["figures"] = [os.path.basename(f) for f in figs]
    out = os.path.join(ART, "drift_probe.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(probe, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: probe["stability"][k] for k in
                      ("live_schema_legal", "repo_canonical_dialect")}, ensure_ascii=False)[:900])
    print("comparison:", json.dumps(probe["stability"]["comparison_vs_analytic"], indent=1)[:1200])
    print("hardcode:", json.dumps([{ "kg": h["kg"], "live": h["live"]} for h in
                                   probe["hardcode_sensitivity"]], ensure_ascii=False)[:600])
    print("figs:", figs, "->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())