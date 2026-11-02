"""m1863 驱动：把 Martin & Moyce 参考表登记进册，独立复核 TensorLBM 已合并的误差数字。

做三件事，全部可机器重跑（不调用任何仿真器，纯读已落盘历史 + 插值）：

1. 登记册校验——参考表带出处（DOI / 数字化来源 / 源文件 sha256 / 源仓 commit）才可作判据；
2. 闸门——TensorLBM ``dam_break_sc`` 的 a=40 / a=80 两条真实前沿曲线 vs 参考表，
   逐点相对误差 + 覆盖率 ⇒ PASS/WITHHELD/WARN/FAIL（平台 15% 精度线）；
3. 复核——在 T=1.0 / 2.0 / 2.96 三个刻度上独立重算有符号误差，
   与 TensorLBM ``result.json`` 里已合并的 ``err_pct_*`` 逐项比对（容差 0.05 个百分点，
   只容许四舍五入差）。不一致就如实记 mismatch，不改数。

产出：
    artifacts/reference_registry/ledger.json
    artifacts/reference_registry/claim_recheck.json
    docs/verification/figs/reference_registry_gate.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from vv import reference_registry as rr  # noqa: E402

REGISTRY = ROOT / "references" / "registry"
SIM = ROOT / "references" / "sim"
OUT = ROOT / "artifacts" / "reference_registry"
FIG = ROOT / "docs" / "verification" / "figs"
DATASET_ID = "martin_moyce_1952_dambreak_front"
CHECKPOINTS = (1.0, 2.0, 2.96)
CLAIM_TOL_PP = 0.05  # 只容许四舍五入差

#: TensorLBM/benchmarks/pending/dam_break_sc/result.json#/official_rerun_correct_convention
#: （retrieved 2026-11-02, commit 0d41766351ae67153544403727859843c981aaf6）
RECORDED_CLAIMS = {
    "a40": {"err_pct_T1": 10.526, "err_pct_T2": -7.579, "err_pct_T2p96": -19.889,
            "X_T1": 1.4656, "X_T2": 2.1811, "X_T2p96": 2.9401},
    "a80": {"err_pct_T1": 2.49, "err_pct_T2": -9.723, "err_pct_T2p96": -19.086,
            "X_T1": 1.359, "X_T2": 2.1305, "X_T2p96": 2.9695},
}
CLAIM_KEYS = ("err_pct_T1", "err_pct_T2", "err_pct_T2p96")
SIM_FILES = {"a40": "tensorlbm_dam_break_sc_a40.csv", "a80": "tensorlbm_dam_break_sc_a80.csv"}


def main() -> int:
    registry = rr.load_registry(REGISTRY)
    ds = registry[DATASET_ID]
    validation = rr.validate_dataset(ds)

    ledger = {"schema": rr.SCHEMA_ID, "dataset": DATASET_ID,
              "validation": validation, "gates": {}}
    recheck = {"dataset": DATASET_ID, "claim_source":
               "TensorLBM/benchmarks/pending/dam_break_sc/result.json"
               "#/official_rerun_correct_convention", "claim_tol_pp": CLAIM_TOL_PP,
               "grids": {}}

    for grid, fname in SIM_FILES.items():
        sim = rr.load_sim_csv(SIM / fname, "T_MM", "X_toe")
        g = rr.gate(ds, sim, tol=float(ds["tolerance"]["gate"]))
        g["sim_file"] = f"references/sim/{fname}"
        g["n_sim_points"] = len(sim)
        ledger["gates"][grid] = g

        cps = rr.checkpoint_errors(ds, sim, CHECKPOINTS)
        claim = RECORDED_CLAIMS[grid]
        rows, mismatches = [], []
        for ck_key in CLAIM_KEYS:
            ck = cps[f"T={dict(zip(CLAIM_KEYS, CHECKPOINTS))[ck_key]:g}"]
            mine = ck.get("signed_err_pct")
            theirs = claim[ck_key]
            delta = None if mine is None else mine - theirs
            agree = delta is not None and abs(delta) <= CLAIM_TOL_PP
            if not agree:
                mismatches.append(ck_key)
            rows.append({"checkpoint": ck_key, "recomputed_err_pct":
                         None if mine is None else round(mine, 4),
                         "recorded_err_pct": theirs,
                         "delta_pp": None if delta is None else round(delta, 4),
                         "agree": agree})
        recheck["grids"][grid] = {"rows": rows, "mismatches": mismatches,
                                  "verdict": "PASS" if not mismatches else "FAIL",
                                  "checkpoints": cps}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ledger.json").write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    (OUT / "claim_recheck.json").write_text(json.dumps(recheck, indent=2, ensure_ascii=False) + "\n",
                                            encoding="utf-8")

    _figure(ds, ledger, recheck)

    for grid, g in ledger["gates"].items():
        print(f"[gate] {grid}: {g['verdict']} — {g['reason']} (coverage {g['coverage']:.0%}, "
              f"max {g['max_rel_err']:.2%})")
    for grid, r in recheck["grids"].items():
        print(f"[recheck] {grid}: {r['verdict']} mismatches={r['mismatches']} "
              f"deltas={[row['delta_pp'] for row in r['rows']]}")
    print(f"[ledger] → {OUT/'ledger.json'}")
    return 0


def _figure(ds: dict, ledger: dict, recheck: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG.mkdir(parents=True, exist_ok=True)
    ref = [(p[0], p[1]) for p in ds["points"]]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))

    ax = axes[0]
    ax.plot([p[0] for p in ref], [p[1] for p in ref], "k^--", ms=5,
            label="Martin & Moyce 1952 (digitised, 14 pts)")
    for grid, style in (("a40", "C0-"), ("a80", "C1-")):
        sim = rr.load_sim_csv(SIM / SIM_FILES[grid], "T_MM", "X_toe")
        ax.plot([a for a, _ in sim], [b for _, b in sim], style, lw=1.3,
                label=f"TensorLBM SCMP a={grid[1:]} (X_toe)")
    ax.set_xlabel("T = t·√(2g/a)")
    ax.set_ylabel("X = x_front/a")
    ax.set_title("(a) front trajectory vs digitised reference")
    ax.set_xlim(0, 5.1)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    ax = axes[1]
    labels, a40, a80 = [], [], []
    for key in CLAIM_KEYS:
        labels.append(key.replace("err_pct_T", "T=").replace("2p96", "2.96"))
        a40.append(next(r["recomputed_err_pct"] for r in recheck["grids"]["a40"]["rows"]
                        if r["checkpoint"] == key))
        a80.append(next(r["recomputed_err_pct"] for r in recheck["grids"]["a80"]["rows"]
                        if r["checkpoint"] == key))
    x = range(len(labels))
    ax.bar([i - 0.18 for i in x], a40, width=0.36, color="C0", label="a=40 (recomputed)")
    ax.bar([i + 0.18 for i in x], a80, width=0.36, color="C1", label="a=80 (recomputed)")
    ax.axhline(15, color="r", ls="--", lw=1.2, label="+/-15% platform accuracy line")
    ax.axhline(-15, color="r", ls="--", lw=1.2)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("signed relative error (%)")
    ax.set_title("(b) three checkpoints, independently recomputed")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)

    fig.suptitle("m1863 reference registry + dam-break gate: TensorLBM SCMP vs Martin & Moyce (1952)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(FIG / "reference_registry_gate.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())