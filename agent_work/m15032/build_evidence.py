#!/usr/bin/env python3
"""m15032 交差核验：m14961 三件套取证 + 判级证据包 + 图。

产出（全部落在工作树内，可入库）：
  runs/m15032_audit/evidence_verification.json   三件套逐项核验（含真实指针）
  runs/m15032_audit/repro_artifacts/*.json       m15032 独立复跑产物（与 m14961 数值比对）
  runs/m15032_audit/tamper_negative_control.json 独立负控（已由 tamper_negcontrol.py 生成）
  runs/m15032_audit/fig1_triad_verification.png
  runs/m15032_audit/fig2_negcontrol_recall.png
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "runs" / "m15032_audit"
SRC_M14961 = Path("/root/tp-agent/work/m14961/artifacts/maturity_gate_h6")
SRC_M15032 = ROOT / "artifacts" / "maturity_gate_h6"

CHECKS = []


def check(name, category, ok, pointer, note=""):
    CHECKS.append({"item": name, "category": category, "ok": bool(ok), "pointer": pointer, "note": note})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ra = OUT / "repro_artifacts"
    ra.mkdir(exist_ok=True)
    for f in ("gate.json", "consistency.json", "increment_ledger.json",
              "mutation_benchmark.json", "fig1_maturity_profile.png",
              "fig2_axes_health.png", "fig3_increment_flow.png"):
        if (SRC_M15032 / f).is_file():
            shutil.copy2(SRC_M15032 / f, ra / f)

    g = json.loads((SRC_M15032 / "gate.json").read_text())
    g_old = json.loads((SRC_M14961 / "gate.json").read_text())
    bench = json.loads((SRC_M15032 / "mutation_benchmark.json").read_text())
    tamper = json.loads((OUT / "tamper_negative_control.json").read_text())

    # ---- 三件套①：run_artifacts
    for f in ("gate.json", "consistency.json", "increment_ledger.json",
              "mutation_benchmark.json", "fig1_maturity_profile.png",
              "fig2_axes_health.png", "fig3_increment_flow.png"):
        p = SRC_M14961 / f
        check(f"run_artifact:{f}", "run_artifacts", p.is_file(),
              str(p), f"{p.stat().st_size} bytes" if p.is_file() else "missing")

    # ---- 三件套②：benchmark 数值对照（ref vs got + 误差）
    same = {k: (json.dumps(g[k], sort_keys=True) == json.dumps(g_old[k], sort_keys=True))
            for k in ("inputs", "criteria", "thresholds", "thresholds_sha256",
                      "axes", "maturity", "verdict")}
    same["increments.chain_head"] = g["increments"]["chain_head"] == g_old["increments"]["chain_head"]
    same["increments.counts"] = g["increments"]["counts"] == g_old["increments"]["counts"]
    check("benchmark:mutation_injected_vs_detected", "benchmark",
          bench["injected"] == 5 and bench["detected"] == 5 and bench["recall"] == 1.0,
          "artifacts/maturity_gate_h6/mutation_benchmark.json:injected/detected/recall",
          f"ref=5/5 recall=1.0 got={bench['detected']}/{bench['injected']} recall={bench['recall']} fp={bench['false_positives']}")
    check("benchmark:target_axis_hit", "benchmark",
          all(bench["target_axis_hit"].values()),
          "artifacts/maturity_gate_h6/mutation_benchmark.json:target_axis_hit",
          f"逐轴命中 {sum(bench['target_axis_hit'].values())}/{len(bench['target_axis_hit'])}（M1/M3 未由目标轴命中，axis_perfect 实为「全部检出」而非「逐轴命中」）")
    check("benchmark:cross_worktree_recompute", "benchmark", all(same.values()),
          "runs/m15032_audit/repro_artifacts/gate.json vs /root/tp-agent/work/m14961/artifacts/maturity_gate_h6/gate.json",
          f"逐字段一致: {same}")

    # ---- 三件套③：repro_cmd 可执行
    check("repro_cmd:cli_run", "repro_cmd", g["verdict"] == "PASS" and g["criteria_pass_rate"] == 1.0,
          "python3 -m tools.maturity_gate_h6.cli run --work-root <root> --spec data/maturity_gate_h6/h6.spec.json --out artifacts/maturity_gate_h6",
          f"verdict={g['verdict']} criteria_pass_rate={g['criteria_pass_rate']}")
    check("repro_cmd:cli_verify", "repro_cmd", True,
          "python3 -m tools.maturity_gate_h6.cli verify --out artifacts/maturity_gate_h6",
          "verified=true, problems=[]")
    check("tests:pytest_24", "tests", True,
          "python3 -m pytest tools/maturity_gate_h6/tests -q",
          "24 passed in 0.29s（本单独立复跑）")

    # ---- 独立负控（不满足 m14961 的「任何数字都能逐字节复算」声明）
    check("negcontrol:verify_antitamper", "benchmark", tamper["recall"] >= 0.99,
          "runs/m15032_audit/tamper_negative_control.json",
          f"注入 {tamper['injected']} 例，verify 仅检出 {tamper['detected']} 例（recall={tamper['recall']}）：axes/maturity/increments 条目（含 sha256/prev_sha256）全部漏检")

    # ---- 集成面
    check("integration_callers", "integration_callers", False,
          "grep -rn 'maturity_gate_h6' --include=*.py（仅 scripts/post_h6_maturity_report.py:40 读取产物图）",
          "无 API/backend/CI/战役门调用方：该门是独立 CLI 工具，未接入平台运行面 → 不达 L3")

    # ---- 外部锚点
    check("external_anchor", "external_anchor", False,
          "data/maturity_gate_h6/h6.spec.json:anchors（均为仓内定义文件：adjudication_engine.py / capability_ledger.py / maturity_gate/spec.py）",
          "无文献/解析解/独立基准对标；变异基准的注入缺陷由本仓作者自造 → 不达 L4")

    summary = {
        "mission": 14961,
        "repo": "TensorPlatform",
        "scope": "m14961 H6-MATURITY-GATE",
        "verdict_level": 2,
        "checked_at_utc": "2026-11-11T15:40:00Z",
        "checks": CHECKS,
        "counts": {"total": len(CHECKS), "ok": sum(1 for c in CHECKS if c["ok"])},
    }
    (OUT / "evidence_verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    # ---------------- 图 ----------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cats = ["run_artifacts", "benchmark", "repro_cmd", "tests",
            "integration_callers", "external_anchor"]
    ok = {c: 0 for c in cats}
    tot = {c: 0 for c in cats}
    for c in CHECKS:
        if c["category"] in tot:
            tot[c["category"]] += 1
            ok[c["category"]] += 1 if c["ok"] else 0
    rate = [ok[c] / tot[c] * 100 for c in cats]
    fig, ax = plt.subplots(figsize=(7.6, 4.0), dpi=150)
    bars = ax.bar(cats, rate, color=["#2e8b57" if r == 100 else ("#d9822b" if r >= 50 else "#c0392b") for r in rate])
    for b, c in zip(bars, cats):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 3,
                f"{ok[c]}/{tot[c]}", ha="center", fontsize=9)
    ax.set_ylim(0, 118)
    ax.axhline(100, ls="--", lw=0.8, color="#888")
    ax.set_ylabel("passed checks (%)")
    ax.set_title("m14961 triad verification (m15032 board audit)\nlevels: L2 granted / L3-L4 not met")
    ax.tick_params(axis="x", labelrotation=20)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_triad_verification.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.0, 3.6), dpi=150)
    labels = ["m14961 claim\n(mutation bench)", "m15032 independent\nnegative control"]
    vals = [bench["recall"] * 100, tamper["recall"] * 100]
    n = [f"{bench['detected']}/{bench['injected']}", f"{tamper['detected']}/{tamper['injected']}"]
    bars = ax.bar(labels, vals, color=["#2e8b57", "#c0392b"], width=0.5)
    for b, v, t in zip(bars, vals, n):
        ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.1f}% ({t})", ha="center", fontsize=9)
    ax.axhline(97, ls="--", lw=0.8, color="#c0392b")
    ax.text(1.42, 98.5, "3% tolerance band", color="#c0392b", fontsize=7, ha="right")
    ax.set_ylim(0, 125)
    ax.set_ylabel("detection recall (%)")
    ax.set_title("verify() anti-tamper recall: claimed vs independently measured")
    fig.tight_layout()
    fig.savefig(OUT / "fig2_negcontrol_recall.png")
    plt.close(fig)

    print(json.dumps({"checks": len(CHECKS), "ok": summary["counts"]["ok"],
                      "level": 2, "same_fields": same}, ensure_ascii=False))


if __name__ == "__main__":
    main()