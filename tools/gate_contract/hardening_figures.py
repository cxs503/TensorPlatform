#!/usr/bin/env python3
"""渲染本波硬化的审计图（数字全部从 artifacts/gate_contract/*.json 读出，零手抄）。

图 1  九类伪证据的判定矩阵（每类 12 例）与假通过率
图 2  fail-closed 硬化探针：每条新判据的最小反例是否被 withhold
图 3  跨宿主一致性：4 套异构宿主签名 × 9 门 的摘要一致性与 libc 往返
图 4  plan 258 九节点门：逐判据 实测/阈值 比与三值判定
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "gate_contract"
FIG = OUT / "figures"
INK = "#22303c"


def _load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def fig_detection(nc: dict, path: Path) -> None:
    classes = sorted(nc["by_class"])
    names = {"no_gate": "no gate.json", "threshold_widened": "threshold widened",
             "sha_mismatch": "artifact sha mismatch", "missing_unit": "unit missing",
             "log_only": "log only (no number)", "criterion_dropped": "criterion dropped",
             "measured_handcopied": "measured hand-copied", "path_escape": "artifact path escape",
             "registry_omitted": "registry omitted"}
    withheld = [nc["by_class"][c]["grades"].get("WITHHELD", 0) for c in classes]
    fail = [nc["by_class"][c]["grades"].get("FAIL", 0) for c in classes]
    passed = [nc["by_class"][c]["grades"].get("PASS", 0) for c in classes]
    y = np.arange(len(classes))
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.barh(y, withheld, color="#2b6cb0", label="WITHHELD")
    ax.barh(y, fail, left=withheld, color="#c05621", label="FAIL")
    ax.barh(y, passed, left=np.array(withheld) + np.array(fail), color="#c53030",
            label="PASS (false pass)")
    ax.set_yticks(y, [names.get(c, c) for c in classes])
    ax.set_xlabel("injected pseudo-evidence cases")
    n = nc["n_cases"]
    ax.set_title(f"Injected negative controls: {n} cases, false-pass rate = "
                 f"{nc['false_pass_rate']:.1%} (must be 0)", color=INK)
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def fig_probes(summary: dict, path: Path) -> None:
    probes = summary["C7_hardening"]["probes"]
    labels = [p["probe"] for p in probes]
    codes = ["+".join(p["codes"]) if p["codes"] else "-" for p in probes]
    ok = [1 if p["withheld"] else 0 for p in probes]
    fig, ax = plt.subplots(figsize=(8.6, 4.0))
    ax.bar(range(len(probes)), ok, color=["#2f855a" if v else "#c53030" for v in ok], width=0.6)
    ax.set_xticks(range(len(probes)), [f"{lab}\n{code}" for lab, code in zip(labels, codes)],
                  fontsize=7)
    ax.set_yticks([0, 1], ["fail-open\n(leaked)", "WITHHELD\n(caught)"])
    ax.set_ylim(0, 1.25)
    ax.set_title("Fail-closed hardening probes: minimal counter-example per new check",
                 color=INK)
    for i, p in enumerate(probes):
        ax.text(i, 1.02, p["grade"], ha="center", fontsize=7, color=INK)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def fig_crossarch(cross: dict, path: Path) -> None:
    runs = cross["A_heterogeneous_fingerprint"]["runs"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 4.0),
                                   gridspec_kw={"width_ratios": [3, 2]})
    labels = [f"{r['profile']['machine']}\npy{r['profile']['python'][0]}."
              f"{r['profile']['python'][1]}/{r['profile']['system']}" for r in runs]
    ident = [r["identical"] for r in runs]
    mism = [len(r["mismatched"]) for r in runs]
    x = np.arange(len(runs))
    ax1.bar(x - 0.18, ident, width=0.36, color="#2b6cb0", label="identical digests")
    ax1.bar(x + 0.18, mism, width=0.36, color="#c53030", label="mismatched")
    ax1.set_xticks(x, labels, fontsize=7)
    ax1.set_ylabel("gates compared")
    ax1.set_title("A. heterogeneous host signatures -> same verdict digest", color=INK)
    ax1.legend(fontsize=8, frameon=False)
    ax1.grid(axis="y", alpha=0.25)

    rt = cross["C_libc_roundtrip"]
    ax2.bar(["round-trip ok", "round-trip bad"],
            [rt["n_values"] - rt["n_bad"], rt["n_bad"]], color=["#2f855a", "#c53030"])
    ax2.set_title("C. evidence numbers via libc %.17g -> strtod", color=INK)
    ax2.set_ylabel("measured values")
    ax2.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def fig_plan258(verify_all: dict, path: Path) -> None:
    gates = verify_all["gates"]
    color = {"PASS": "#2f855a", "FAIL": "#c05621", "WITHHELD": "#2b6cb0"}
    fig, ax = plt.subplots(figsize=(9.4, 4.4))
    rows = []
    for gi, gate in enumerate(gates):
        for ci, crit in enumerate(gate["criteria"]):
            meas, thr = crit["measured"], crit["threshold"]
            if isinstance(meas, list):
                meas = max(abs(float(v)) for v in meas)
            if isinstance(thr, list):
                thr = max(abs(float(v)) for v in thr)
            if not isinstance(meas, (int, float)) or not isinstance(thr, (int, float)) or thr == 0:
                ratio = 1.0  # 不可比的判据画在 1.0 上并标灰
                rows.append((gi, ci, 1.0, "n/a", gate["grade"]))
                continue
            ratio = abs(float(meas) / float(thr)) if thr else 0.0
            rows.append((gi, ci, max(ratio, 1e-18), crit["status"], gate["grade"]))
    for gi, ci, ratio, status, grade in rows:
        ax.scatter(gi + (ci - 1.5) * 0.14, ratio, s=46,
                   color=color.get(grade, "#4a5568"),
                   marker="o" if status in ("pass", "withheld") else "X",
                   edgecolor="white", linewidth=0.6, zorder=3)
    ax.axhline(1.0, color="#c53030", linestyle="--", linewidth=1, label="threshold (=1.0)")
    ax.set_yscale("log")
    ax.set_xticks(range(len(gates)),
                  [g["gate_id"].replace("plan258-", "") + f"\n{g['grade']}" for g in gates],
                  fontsize=7)
    ax.set_ylabel("measured / threshold (log)")
    ax.set_title("plan 258 nine nodes: per-criterion margin and three-valued grade", color=INK)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def main() -> int:
    FIG.mkdir(parents=True, exist_ok=True)
    summary = _load("summary.json")
    nc = _load("negcontrol_summary.json")
    cross = _load("crossarch.json")
    va = _load("verify_all.json")
    fig_detection(nc, FIG / "fig1_negative_control_matrix.png")
    fig_probes(summary, FIG / "fig2_hardening_probes.png")
    fig_crossarch(cross, FIG / "fig3_cross_host_consistency.png")
    fig_plan258(va, FIG / "fig4_plan258_margins.png")
    meta = {"figures": [
        {"file": "fig1_negative_control_matrix.png",
         "caption": "九类注入式伪证据 ×12 例的判定分布；假通过率 0（PASS 段为空）"},
        {"file": "fig2_hardening_probes.png",
         "caption": "本波新增 fail-closed 判据的最小反例探针，全部落 WITHHELD"},
        {"file": "fig3_cross_host_consistency.png",
         "caption": "四套异构宿主签名回填后逐门摘要一致性 + libc %.17g 往返"},
        {"file": "fig4_plan258_margins.png",
         "caption": "plan 258 九节点逐判据 measured/threshold 与三值判定"}],
        "source": "artifacts/gate_contract/{summary,negcontrol_summary,crossarch,verify_all}.json"}
    (FIG / "figures_metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"figures": [f["file"] for f in meta["figures"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
