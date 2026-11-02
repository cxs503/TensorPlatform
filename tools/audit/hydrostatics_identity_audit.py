"""静水力恒等式审计器 — 把 `geometry.hydro_curves`（平台审计标 REAL）钉到机器判据上.

背景（为什么需要这个工具）
--------------------------
`GET /api/v1/geometry/hydro-curves/{ship}` 返回 {drafts, displacement, Cb, Awp, LCB,
VCB, TPC, MTC, wet_surface}，是**稳性/几何域最下游的公共输入**（GZ、KM、装载、破损稳性
都从它取数）。平台能力清单把它标成 REAL，但此前没有任何独立判据检查过它。
它的正确性不需要任何外部系数表：静水力存在**恒等式级**约束，偏差即缺陷：

    H1  水线—排水量微分恒等式     d∇/dT = A_wp(T)
    H2  浮心垂向积分恒等式        d(∇·KB)/dT = ∇(T)
    H3  TPC 定义                  TPC = rho · A_wp / 100            [t/cm]
    H4  方形系数物理闸门          Cb(T) = ∇/(L·B·T) <= 1  且  = 标模 Cb
    H6  排水量标定                ∇(T) = Cb_meta · L · B · T        （误差须 < 3%）

H1/H2 是 Stokes 定理级的恒等式（对任意船型严格成立，与格距无关的连续形式）；
数值上用二阶中心差分实现，其截断误差为 O(h^2)，对线性 ∇(T) 精确（见自检用例）。

诊断量（非恒等式，用于识别"占位式"实现）
    D1  A_wp 吃水spread：`(max-min)/mean` over d ∈ [0.1T, T]。真实有舷弧/舭部的船体
        Cb < 1 时水线面积必须随吃水单调增长；若 spread < 0.5% 且 Cb_meta < 1，则
        A_wp 是吃水无关的占位常数（方箱 Cb=1 是唯一合法的退化情形）。

用法
----
    python3 tools/audit/hydrostatics_identity_audit.py --record     # 录 live 快照
    python3 tools/audit/hydrostatics_identity_audit.py --offline    # 只读快照出判据
    python3 tools/audit/hydrostatics_identity_audit.py --selftest   # 解析方箱自检

产物
----
    artifacts/hydrostatics_audit/probe_raw.json    live 原始响应（零手抄）
    artifacts/hydrostatics_audit/audit.json        逐船逐判据判定 + 汇总
    artifacts/hydrostatics_audit/fig_*.png         报告图件
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

RHO_SW = 1.025          # t/m^3，与端点内部一致（海水）
CB_TOL_PCT = 3.0        # 平台精度标准：关键量 vs 基准 < 3%
DEAD_DRIFT_TOL = 0.005  # D1：A_wp 相对 spread 低于 0.5% 视为吃水无关
NOISE_FLOOR_FRAC = 0.01 # 有限差分只在 ∇ >= 1% ∇(T) 的吃水点上评估（规避端点取整噪声）
RES_LIMIT_T = 10.0      # displacement < 10 t 时端点 0.1 t 取整即 >1%，判据精度受限
TPC_TOL = 1e-3          # TPC 端点保留 2 位小数，定义式判据给 1e-3 容差
CB_TOL_CLOSURE = 1e-3   # Cb 列取整容差
DEFAULT_BASE = "http://127.0.0.1:8001/api/v1"
ART = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "artifacts", "hydrostatics_audit")

HULLS = ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC")

# 判据的基准先自证：平台的 meta Cb × L·B·T 是否等于公开的排水体积？
# 若是（偏差 <0.5%），则 H6 的基准成立，端点偏差归端点。公开值仅用于校验基准本身。
PUBLISHED_NABLA = {
    "KCS": {"nabla_m3": 52030.0, "source": "KRISO KCS 官方标模 (Lpp=230 m, B=32.2 m, T=10.8 m, Cb=0.651)"},
    "KVLCC2": {"nabla_m3": 312622.0, "source": "SIMMAN 2008 / MOERI KVLCC2 官方标模主尺度"},
}


# ---------------------------------------------------------------------------
# 1. curve container
# ---------------------------------------------------------------------------

class Curves:
    """端点返回的曲线族（drafts 严格递增）。"""

    def __init__(self, payload: Dict[str, Any]):
        c = payload.get("curves") or payload
        self.meta = payload.get("meta") or {}
        self.draft = [float(v) for v in c["drafts"]]
        self.nabla = [float(v) / RHO_SW for v in c["displacement"]]
        self.cb = [float(v) for v in c["Cb"]]
        self.awp = [float(v) for v in c["Awp"]]
        self.lcb = [float(v) for v in c["LCB"]]
        self.vcb = [float(v) for v in c["VCB"]]
        self.tpc = [float(v) for v in c["TPC"]]
        self.mtc = [float(v) for v in c["MTC"]]
        self.wet = [float(v) for v in c["wet_surface"]]

    # ---- derivative helpers ----------------------------------------------
    # 内部点用二阶中心差分，端点用三点单侧公式；两者对二次多项式都是**精确**的，
    # 因此解析方箱自检（∇ 线性、∇·KB 二次）残差严格为 0——判据本身不含截断误差。
    def _d1(self, y: Sequence[float], i: int) -> float:
        d = self.draft
        n = len(y)
        if n < 3:
            return (y[1] - y[0]) / (d[1] - d[0])
        if i == 0:
            h = d[1] - d[0]
            return (-3.0 * y[0] + 4.0 * y[1] - y[2]) / (2.0 * h)
        if i == n - 1:
            h = d[-1] - d[-2]
            return (3.0 * y[-1] - 4.0 * y[-2] + y[-3]) / (2.0 * h)
        return (y[i + 1] - y[i - 1]) / (d[i + 1] - d[i - 1])

    def _rel(self, a: float, b: float) -> float:
        return abs(a - b) / abs(b) if b else float("inf")

    # ---- evaluation mask（噪声底） ----------------------------------------
    def _idx_eval(self) -> List[int]:
        """只在 ∇ 未落到取整噪声里的吃水点做有限差分。"""
        floor = NOISE_FLOOR_FRAC * abs(self.nabla[-1]) if self.nabla else 0.0
        idx = [i for i in range(len(self.draft)) if abs(self.nabla[i]) >= max(floor, 1e-12)]
        return idx if len(idx) >= 3 else list(range(len(self.draft)))

    # ---- identities -------------------------------------------------------
    def h1(self) -> Dict[str, Any]:
        """d∇/dT 对 A_wp 的最大相对偏差（全吃水）。"""
        rows = []
        for i in self._idx_eval():
            dnab = self._d1(self.nabla, i)
            rows.append((self.draft[i], dnab, self.awp[i], self._rel(dnab, self.awp[i])))
        worst = max(rows, key=lambda r: r[3])
        return {
            "id": "H1", "name": "d∇/dT = A_wp(T)",
            "max_rel_err": round(worst[3], 6),
            "at_draft": round(worst[0], 3),
            "lhs_dnabla_dT": round(worst[1], 3),
            "rhs_awp": round(worst[2], 3),
        }

    def h2(self) -> Dict[str, Any]:
        """d(∇·KB)/dT 对 ∇ 的最大相对偏差。"""
        mom = [n * k for n, k in zip(self.nabla, self.vcb)]
        rows = []
        for i in self._idx_eval():
            dmom = self._d1(mom, i)
            rows.append((self.draft[i], dmom, self.nabla[i], self._rel(dmom, self.nabla[i])))
        worst = max(rows, key=lambda r: r[3])
        return {
            "id": "H2", "name": "d(∇·KB)/dT = ∇(T)",
            "max_rel_err": round(worst[3], 6),
            "at_draft": round(worst[0], 3),
            "lhs_d(nabla_kb)_dT": round(worst[1], 3),
            "rhs_nabla": round(worst[2], 3),
        }

    def h3(self) -> Dict[str, Any]:
        """TPC = rho·A_wp/100 的定义偏差。"""
        errs = [self._rel(self.tpc[i], RHO_SW * self.awp[i] / 100.0) for i in self._idx_eval()]
        i = errs.index(max(errs))
        return {"id": "H3", "name": "TPC = rho*A_wp/100",
                "max_rel_err": round(max(errs), 9), "at_draft": round(self.draft[i], 3)}

    def h4(self) -> Dict[str, Any]:
        """Cb 物理闸门：逐吃水 Cb = ∇/(L·B·T) 必须 <= 1，且设计吃水处 = 标模 Cb。"""
        meta = self.meta or {}
        L, B, T, cb_meta = (float(meta.get(k)) for k in ("L", "B", "T", "Cb"))
        idx = self._idx_eval()
        closed = [self.nabla[i] / (L * B * self.draft[i]) for i in idx]
        closure_err = max(self._rel(c, self.cb[i]) for c, i in zip(closed, idx))
        iT = len(self.draft) - 1
        cb_T = self.cb[iT]
        return {
            "id": "H4", "name": "Cb = ∇/(L*B*T) <= 1 and equals standard-model Cb",
            "cb_at_design_draft": round(cb_T, 4),
            "cb_meta": cb_meta,
            "cb_max_over_drafts": round(max(self.cb), 4),
            "cb_column_closure_rel_err": round(closure_err, 9),
            "cb_vs_meta_rel_err": round(self._rel(cb_T, cb_meta), 6),
            "physical_violation": bool(max(self.cb) > 1.0 + 1e-3),
        }

    def h6(self) -> Dict[str, Any]:
        """排水量标定：∇(T) 对 Cb_meta·L·B·T 的相对误差。"""
        meta = self.meta or {}
        L, B, T, cb_meta = (float(meta.get(k)) for k in ("L", "B", "T", "Cb"))
        ref = cb_meta * L * B * T
        got = self.nabla[-1]
        return {"id": "H6", "name": "∇(T) = Cb_meta*L*B*T",
                "nabla_design_m3": round(got, 1), "nabla_ref_m3": round(ref, 1),
                "rel_err": round(self._rel(got, ref), 6),
                "displacement_endpoint_t": round(got * RHO_SW, 1),
                "displacement_ref_t": round(ref * RHO_SW, 1)}

    def d1(self) -> Dict[str, Any]:
        """诊断：A_wp 吃水 spread（占位式实现的指纹）。"""
        mn, mx = min(self.awp), max(self.awp)
        mean = sum(self.awp) / len(self.awp)
        spread = (mx - mn) / mean if mean else 0.0
        cb_meta = float((self.meta or {}).get("Cb", 0.0) or 0.0)
        degenerate = bool(spread < DEAD_DRIFT_TOL)
        flag = bool(degenerate and cb_meta < 1.0 - 1e-9)
        return {"id": "D1", "name": "A_wp draft spread (placeholder fingerprint)",
                "spread": round(spread, 6), "awp_min": round(mn, 1), "awp_max": round(mx, 1),
                "draft_invariant": degenerate,
                "suspected_placeholder": flag}

    # ---- aggregate --------------------------------------------------------
    def audit(self, label: str = "") -> Dict[str, Any]:
        h1, h2, h3 = self.h1(), self.h2(), self.h3()
        h4, h6, d1 = self.h4(), self.h6(), self.d1()
        passes = {
            "H1": h1["max_rel_err"] < 1e-2,
            "H2": h2["max_rel_err"] < 1e-2,
            "H3": h3["max_rel_err"] < TPC_TOL,
            "H4": (not h4["physical_violation"])
                  and h4["cb_vs_meta_rel_err"] < 0.01
                  and h4["cb_column_closure_rel_err"] < CB_TOL_CLOSURE,
            "H6": h6["rel_err"] < CB_TOL_PCT / 100.0,
        }
        n_fail = sum(1 for v in passes.values() if not v)
        disp_t = self.nabla[-1] * RHO_SW
        return {
            "hull": label or (self.meta or {}).get("ship_name", ""),
            "resolution_limited": bool(disp_t < RES_LIMIT_T),
            "displacement_design_t": round(disp_t, 3),
            "drafts_evaluated": len(self._idx_eval()),
            "meta": {k: (self.meta or {}).get(k) for k in ("L", "B", "T", "Cb")},
            "identities": [h1, h2, h3, h4, h6],
            "diagnostics": [d1],
            "passes": passes,
            "failed_identity_count": n_fail,
            "grade": "REAL" if n_fail == 0 else ("STUB/MOCK" if n_fail >= 3 else "PARTIAL"),
        }


# ---------------------------------------------------------------------------
# 2. analytic self-test (wall-sided box: every identity has a closed form)
# ---------------------------------------------------------------------------

def box_curves(L: float = 100.0, B: float = 20.0, T: float = 10.0,
               n: int = 21, meta_cb: Optional[float] = None) -> Dict[str, Any]:
    """解析方箱：∇ = L·B·d，A_wp = L·B，KB = d/2，Cb = 1，TPC = rho·L·B/100."""
    drafts = [0.1 * T + i * (0.9 * T) / (n - 1) for i in range(n)]
    return {
        "curves": {
            "drafts": drafts,
            "displacement": [RHO_SW * L * B * d for d in drafts],
            "Cb": [1.0] * n,
            "Awp": [L * B] * n,
            "LCB": [L / 2.0] * n,
            "VCB": [0.5 * d for d in drafts],
            "TPC": [RHO_SW * L * B / 100.0] * n,
            "MTC": [0.01 * L * B * L / 100.0] * n,
            "wet_surface": [L * B + 2.0 * L * d for d in drafts],
        },
        "meta": {"L": L, "B": B, "T": T, "Cb": 1.0 if meta_cb is None else meta_cb},
    }


def selftest() -> Dict[str, Any]:
    rep = Curves(box_curves()).audit("ANALYTIC_BOX")
    return rep


# ---------------------------------------------------------------------------
# 3. live probe / snapshot IO
# ---------------------------------------------------------------------------

def _get(base: str, path: str, timeout: float = 90.0) -> Dict[str, Any]:
    req = urllib.request.Request(base + path)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return {"http": 200, "body": json.load(f)}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": None}
    except Exception as e:                     # noqa: BLE001 - offline is a legal outcome
        return {"http": None, "body": {"error": repr(e)[:200]}}


def record(base: str = DEFAULT_BASE, hulls: Sequence[str] = HULLS,
           n_drafts: int = 21, out_path: Optional[str] = None) -> Dict[str, Any]:
    snap = {"base": base, "n_drafts": n_drafts,
            "probed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hulls": {}}
    for h in hulls:
        snap["hulls"][h] = _get(base, f"/geometry/hydro-curves/{h}?n_drafts={n_drafts}")
        print(f"  probe {h}: http={snap['hulls'][h]['http']}", file=sys.stderr)
    path = out_path or os.path.join(ART, "probe_raw.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    return snap


def load_snapshot(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or os.path.join(ART, "probe_raw.json"), encoding="utf-8") as f:
        return json.load(f)


def reference_anchor_check(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """用公开排水体积校验「Cb_meta·L·B·T」这个基准本身是否可信。"""
    out = []
    for r in rows:
        pub = PUBLISHED_NABLA.get(r["hull"])
        if not pub:
            continue
        ref = next(x for x in r["identities"] if x["id"] == "H6")["nabla_ref_m3"]
        out.append({"hull": r["hull"], "nabla_ref_m3": ref,
                    "nabla_published_m3": pub["nabla_m3"],
                    "rel_err_pct": round(abs(ref - pub["nabla_m3"]) / pub["nabla_m3"] * 100.0, 4),
                    "source": pub["source"],
                    "reference_validated": bool(abs(ref - pub["nabla_m3"]) / pub["nabla_m3"] < 0.005)})
    return out


def audit_snapshot(snap: Dict[str, Any]) -> Dict[str, Any]:
    rows, skipped = [], []
    for hull, call in snap["hulls"].items():
        if call.get("http") != 200 or not call.get("body"):
            skipped.append({"hull": hull, "http": call.get("http")})
            continue
        rows.append(Curves(call["body"]).audit(hull))
    scored = [r for r in rows if not r.get("resolution_limited")]
    n_bad = sum(1 for r in scored if r["grade"] != "REAL")
    return {
        "source": snap.get("base"), "probed_utc": snap.get("probed_utc"),
        "n_drafts": snap.get("n_drafts"), "n_hulls": len(rows),
        "hulls": rows, "skipped": skipped,
        "reference_anchor_check": reference_anchor_check(rows),
        "summary": {
            "hulls_audited": len(rows),
            "hulls_scored": len(scored),
            "hulls_resolution_limited": len(rows) - len(scored),
            "hulls_failing_at_least_one_identity": n_bad,
            "grade_counts": {g: sum(1 for r in scored if r["grade"] == g)
                             for g in ("REAL", "PARTIAL", "STUB/MOCK")},
            "verdict": "REAL" if n_bad == 0 else "NOT_REAL",
        },
    }


# ---------------------------------------------------------------------------
# 4. figures
# ---------------------------------------------------------------------------

def figures(audit: Dict[str, Any], snap: Dict[str, Any], outdir: Optional[str] = None) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir = outdir or ART
    os.makedirs(outdir, exist_ok=True)
    paths: List[str] = []

    # Fig 1: displacement vs draft, endpoint vs Cb_meta identity
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    for hull in ("KCS", "KVLCC2", "JBC"):
        call = snap["hulls"].get(hull) or {}
        if call.get("http") != 200:
            continue
        c = Curves(call["body"])
        L, B, T, cb = (float(c.meta[k]) for k in ("L", "B", "T", "Cb"))
        ax.plot(c.draft, c.nabla, "o-", ms=3.5, label=f"{hull} endpoint ∇")
        ax.plot(c.draft, [cb * L * B * d for d in c.draft], "--",
                label=f"{hull} Cb·L·B·T reference")
    ax.set_xlabel("draft T [m]"); ax.set_ylabel("displaced volume ∇ [m³]")
    ax.set_title("hydro_curves: endpoint volume vs Cb_meta identity")
    ax.grid(alpha=0.3); ax.legend(fontsize=7)
    p = os.path.join(outdir, "fig1_volume_vs_reference.png")
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig); paths.append(p)

    # Fig 2: identity residuals per hull (log scale)
    scored = [r for r in audit["hulls"] if not r.get("resolution_limited")]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    hulls = [r["hull"] for r in scored]
    ids = ["H1", "H2", "H6"]
    width = 0.8 / len(ids)
    for k, ident in enumerate(ids):
        vals = []
        for r in scored:
            row = next(x for x in r["identities"] if x["id"] == ident)
            v = row.get("max_rel_err", row.get("rel_err", 0.0)) * 100.0
            vals.append(min(v, 1e3) if (v == v and v != float("inf")) else 1e3)
        ax.bar([i + k * width for i in range(len(hulls))], vals, width, label=ident)
    ax.set_yscale("log"); ax.set_xticks(range(len(hulls))); ax.set_xticklabels(hulls)
    ax.axhline(1.0, color="k", ls=":", lw=1, label="1% gate")
    ax.set_ylabel("relative residual [%]")
    ax.set_title("hydrostatic identity residuals (endpoint vs exact identities)")
    ax.grid(alpha=0.3, axis="y"); ax.legend(fontsize=8)
    p = os.path.join(outdir, "fig2_identity_residuals.png")
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig); paths.append(p)

    # Fig 3: A_wp and KB/T draft dependence (placeholder fingerprint)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 4.0))
    for hull in ("KCS", "KVLCC2", "JBC"):
        call = snap["hulls"].get(hull) or {}
        if call.get("http") != 200:
            continue
        c = Curves(call["body"])
        a1.plot(c.draft, c.awp, "o-", ms=3, label=hull)
        a2.plot(c.draft, [k / d for k, d in zip(c.vcb, c.draft)], "o-", ms=3, label=hull)
    a1.set_xlabel("draft [m]"); a1.set_ylabel("A_wp [m²]")
    a1.set_title("waterplane area is draft-invariant"); a1.grid(alpha=0.3); a1.legend(fontsize=7)
    a2.axhline(0.5, color="k", ls=":", lw=1, label="wall-sided KB/T = 0.5")
    a2.set_xlabel("draft [m]"); a2.set_ylabel("KB/T [-]")
    a2.set_title("KB/T vs draft (identity H2 proxy)"); a2.grid(alpha=0.3); a2.legend(fontsize=7)
    p = os.path.join(outdir, "fig3_awp_vcb_fingerprint.png")
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig); paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="store_true", help="probe the live endpoint into probe_raw.json")
    ap.add_argument("--offline", action="store_true", help="audit the recorded snapshot only")
    ap.add_argument("--selftest", action="store_true", help="run the analytic box self-test")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--n-drafts", type=int, default=21)
    ap.add_argument("--outdir", default=ART)
    args = ap.parse_args(argv)

    if args.selftest:
        print(json.dumps(selftest(), ensure_ascii=False, indent=2))
        return 0

    if args.record:
        snap = record(base=args.base, n_drafts=args.n_drafts,
                      out_path=os.path.join(args.outdir, "probe_raw.json"))
    else:
        snap = load_snapshot(os.path.join(args.outdir, "probe_raw.json"))

    audit = audit_snapshot(snap)
    figs = figures(audit, snap, args.outdir)
    audit["figures"] = [os.path.basename(p) for p in figs]
    with open(os.path.join(args.outdir, "audit.json"), "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=1)
    print(json.dumps(audit["summary"], ensure_ascii=False, indent=1))
    for r in audit["hulls"]:
        print(f"{r['hull']:>8s}  grade={r['grade']:<9s} failed={r['failed_identity_count']}  "
              f"H1={r['identities'][0]['max_rel_err']*100:7.2f}%  "
              f"H2={r['identities'][1]['max_rel_err']*100:7.2f}%  "
              f"Cb(T)={r['identities'][3]['cb_at_design_draft']:.4f}  "
              f"∇err={r['identities'][4]['rel_err']*100:7.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())