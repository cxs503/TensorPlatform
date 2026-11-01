#!/usr/bin/env python3
"""报告门禁验证欠账审计（v1 keyword-only vs v2 六要素）。

做三件事，全部可复算：
 1. 从 ``git show HEAD:backend/api/tensor_reports.py`` 取**生产 v1 门禁源码**，
    与 ``backend/tests/test_report_gate_standalone.py`` 的手抄副本逐单比对
    （验证"测试副本是否等于生产实现"——副本漂移 = 绿灯与生产脱钩）。
 2. 取库里全部已发布报告（tensor_reports.markdown + figures），分别过 v1 / v2，
    量化 v1 的**误放行率**（v1 判过、v2 判否）并分解到每个要素。
 3. 输出 JSON 统计 + matplotlib 柱状图，供 docs 报告直接引用（数字零手抄）。

用法：
    python3 tools/audit/report_gate_audit.py [--out docs/reports/report_gate_audit_2026-11-01.json]
纯只读：只 SELECT，不写库；不建 engine（psql CLI）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
V1_PATH = "backend/api/tensor_reports.py"
STANDALONE = BACKEND / "tests" / "test_report_gate_standalone.py"


def load_v1_from_git(rev: str = "HEAD") -> dict:
    """从 git 历史里取出生产版 v1 门禁，exec 成纯函数（无 DB 依赖）。"""
    src = subprocess.run(
        ["git", "show", f"{rev}:{V1_PATH}"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    start = src.index("REQUIRED_SECTIONS = {")
    end = src.index("class ReportQACheck")
    ns: dict = {"re": re}
    exec(compile(src[start:end], f"<{rev}:{V1_PATH}:gate>", "exec"), ns)
    return ns


def load_standalone() -> object | None:
    if not STANDALONE.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_rpt_gate_standalone", STANDALONE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, "_check_report_quality", None)


def load_v2():
    sys.path.insert(0, str(BACKEND))
    from services import report_gate  # noqa: WPS433
    return report_gate


def fetch_reports() -> list[dict]:
    """psql 只读导出（不依赖 psycopg2）。"""
    sql = (
        "select coalesce(json_agg(t), '[]'::json) from ("
        " select id, title, mission_id, coalesce(markdown,'') as markdown,"
        " coalesce(figures,'[]'::jsonb) as figures, qa_status"
        " from tensor_reports order by id) t"
    )
    out = subprocess.run(
        ["psql", "-U", "postgres", "-d", "tensorplatform", "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def normalise_figures(figs) -> list:
    if figs is None:
        return []
    if isinstance(figs, str):
        try:
            figs = json.loads(figs)
        except json.JSONDecodeError:
            return []
    return figs if isinstance(figs, list) else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/reports/report_gate_audit_2026-11-01.json")
    ap.add_argument("--figure", default="docs/reports/figs/report_gate_false_accept.png")
    args = ap.parse_args()

    v1 = load_v1_from_git()["_check_report_quality"]
    rg = load_v2()
    v2 = rg.check_report_quality
    standalone = load_standalone()
    reports = fetch_reports()

    twin_diff = 0
    v2_only_pass = 0
    ev_cause = Counter()
    check_pass = Counter()
    check_total = 0
    strict_verdict_pass = 0
    calibrated_verdict_pass = 0
    stats = Counter()
    issue_counter: Counter[str] = Counter()
    v1_pass_v2_fail: list[dict] = []
    examples: list[dict] = []

    for r in reports:
        md = r.get("markdown") or ""
        figs = normalise_figures(r.get("figures"))
        a = v1(md, figs)
        b = v2(md, figs)
        stats["total"] += 1
        stats["v1_pass"] += int(bool(a["passed"]))
        stats["v2_pass"] += int(bool(b["passed"]))
        if not a["passed"] and b["passed"]:
            v2_only_pass += 1
        if "missing_error_verdict" in [i.split(":")[0] for i in b["issues"]]:
            det = b.get("details", {})
            if det.get("error_number") and not det.get("verdict_word"):
                ev_cause["number_but_no_verdict"] += 1
            elif not det.get("error_number"):
                ev_cause["no_error_number"] += 1
        if a["passed"] and not b["passed"]:
            stats["v1_pass_v2_fail"] += 1
            codes = [i.split(":")[0] for i in b["issues"]]
            issue_counter.update(codes)
            v1_pass_v2_fail.append({"id": r["id"], "codes": codes})
            if len(examples) < 8:
                examples.append({
                    "id": r["id"], "title": (r["title"] or "")[:70],
                    "mission_id": r.get("mission_id"), "codes": codes,
                })
        # 逐判据通过率（v1 只有 5 项，v2 六要素）
        check_total += 1
        for k, val in b["checks"].items():
            check_pass[k] += int(bool(val))
        # 第 ⑥ 要素：严格阈值词 vs 实测校准语义词
        det = b.get("details", {})
        if det.get("no_baseline_declared"):
            strict_verdict_pass += 1
            calibrated_verdict_pass += 1
        else:
            has_num = det.get("error_number", False)
            core = any(re.search(p, md) for p in rg.ERROR_VERDICT_PATTERNS_CORE)
            ext = any(re.search(p, md) for p in rg.ERROR_VERDICT_PATTERNS_EXTENDED)
            strict_verdict_pass += int(has_num and core)
            calibrated_verdict_pass += int(has_num and (core or ext))
        if standalone is not None and standalone is not v2:
            c = standalone(md, figs)
            if bool(c["passed"]) != bool(a["passed"]):
                twin_diff += 1

    # ── 对抗样本：每类 v1 误放行各 1 例 + 1 例合规对照（specificity 控制）──
    FIG_OK = [{"name": "fig1", "caption": "阻力曲线 vs 基线"}]
    adversarial = [
        {
            "name": "A_param_table_only",
            "why": "v1 组间 AND 被拍平成 OR：只写「参数表」即算有物理背景",
            "expect": "reject",
            "md": "## 参数表\nL=10m, B=2m, Cb=0.6。\n## 方法\nLBM。\n## 验证\n与 ITTC 对标。"
                  "\n## 局限\n仅低速。\n## 误差\n关键量误差 1.8%<3%。",
            "figs": FIG_OK,
        },
        {
            "name": "B_figure_word_only",
            "why": "v1 只要正文出现「图」字即算有图（figures 为空也过）",
            "expect": "reject",
            "md": "## 物理背景\n圆柱绕流，几何 D=1m。\n## 方法\nLBM D2Q9。\n## 验证\n与 DNS 对标。"
                  "\n## 结果\n阻力见图1曲线。\n## 局限\nRe=100。\n## 误差\nCd 误差 1.2%<3%。",
            "figs": [],
        },
        {
            "name": "C_numbers_without_verdict",
            "why": "v1 完全没有第 ⑥ 要素检查：列了误差数字就算合规",
            "expect": "reject",
            "md": "## 物理背景\n圆柱绕流，几何 D=1m。\n## 方法\nLBM D2Q9。\n## 验证\n与 DNS 对标。"
                  "\n## 局限\nRe=100。\n## 结果\nCd 误差 0.57%, Cl 误差 2.03%。",
            "figs": FIG_OK,
        },
        {
            "name": "D_compliant_control",
            "why": "具体性控制：六要素齐全的报告不得被误拒",
            "expect": "accept",
            "md": "## ① 案例介绍\n物理背景：低速兴波主导，几何 L=180m, B=28m, Cb=0.62。"
                  "\n## ② 方法\nRANS k-omega SST，网格 1200 万。\n## ③ 验证\n与 ITTC-1957 对标。"
                  "\n## ④ 结果\nCt=4.32e-3，见图。\n## ⑤ 注意点\n仅 Fr=0.20 单工况。"
                  "\n## ⑥ 误差判定\n总阻力误差 2.4%<3%。",
            "figs": FIG_OK,
        },
    ]
    adv_rows = []
    for c in adversarial:
        in1 = bool(v1(c["md"], c["figs"])["passed"])
        in2 = bool(v2(c["md"], c["figs"])["passed"])
        correct = (in2 is False) if c["expect"] == "reject" else (in2 is True)
        adv_rows.append({
            "name": c["name"], "why": c["why"], "expect": c["expect"],
            "v1_pass": in1, "v2_pass": in2, "v2_correct": correct,
        })
    adv_reject = [r for r in adv_rows if r["expect"] == "reject"]
    adv_accept = [r for r in adv_rows if r["expect"] == "accept"]
    adv_stats = {
        "cases": adv_rows,
        "recall_on_false_accept": round(
            sum(r["v2_correct"] for r in adv_reject) / max(len(adv_reject), 1), 4),
        "specificity_control": round(
            sum(r["v2_correct"] for r in adv_accept) / max(len(adv_accept), 1), 4),
        "v1_missed": sum(1 for r in adv_reject if r["v1_pass"]),
    }

    total = max(stats["total"], 1)
    summary = {
        "generated_at": subprocess.run(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True
        ).stdout.strip(),
        "corpus_max_id": max((r["id"] for r in reports), default=0),
        "repo": str(REPO),
        "v1_source": f"git show HEAD:{V1_PATH}",
        "corpus_size": stats["total"],
        "v1_pass": stats["v1_pass"],
        "v1_pass_rate": round(stats["v1_pass"] / total, 4),
        "v2_pass": stats["v2_pass"],
        "v2_pass_rate": round(stats["v2_pass"] / total, 4),
        "v1_pass_v2_fail": stats["v1_pass_v2_fail"],
        "v1_false_accept_rate": round(stats["v1_pass_v2_fail"] / total, 4),
        "false_accept_share_of_v1_passes": round(
            stats["v1_pass_v2_fail"] / max(stats["v1_pass"], 1), 4),
        "issue_breakdown_among_false_accepts": dict(issue_counter.most_common()),
        "error_verdict_reject_causes": dict(ev_cause.most_common()),
        "v2_check_pass_rates": {k: round(v / max(check_total, 1), 4) for k, v in check_pass.items()},
        "error_verdict_strict_pass_rate": round(strict_verdict_pass / max(check_total, 1), 4),
        "error_verdict_calibrated_pass_rate": round(calibrated_verdict_pass / max(check_total, 1), 4),
        "v2_only_pass": v2_only_pass,
        "adversarial": adv_stats,
        "test_twin_imports_shared_impl": standalone is not None and standalone is v2,
        "test_twin_mismatches_vs_v1": twin_diff,
        "examples": examples,
    }
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ── 图：误放行率分解 + 通过率对比 ──────────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        print(f"[warn] matplotlib unavailable: {exc}", file=sys.stderr)
        return 0

    figp = REPO / args.figure
    figp.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))

    ax = axes[0]
    labels = ["v1 (keyword-only)", "v2 (six elements)"]
    vals = [summary["v1_pass_rate"] * 100, summary["v2_pass_rate"] * 100]
    bars = ax.bar(labels, vals, color=["#c0504d", "#4f81bd"])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.1f}%", ha="center", fontsize=10)
    ax.set_ylim(0, 110)
    ax.set_ylabel("pass rate on published reports (%)")
    ax.set_title(f"Gate pass rate, n={summary['corpus_size']}")

    ax = axes[1]
    items = list(summary["issue_breakdown_among_false_accepts"].items())
    if items:
        codes = [k.replace("missing_", "").replace("figures_", "fig_") for k, _ in items]
        cnts = [v for _, v in items]
        ax.barh(codes[::-1], cnts[::-1], color="#9bbb59")
        for y, v in enumerate(cnts[::-1]):
            ax.text(v * 1.01, y, str(v), va="center", fontsize=9)
        ax.set_xlabel("reports")
    ax.set_title("Why v1 false-accepted (issue codes)")
    ax = axes[2]
    names = ["phys", "method", "valid", "figures", "limit", "error_v"]
    keys = ["physics_statement", "method", "validation", "figures",
            "limitations", "error_verdict"]
    rates = [summary["v2_check_pass_rates"].get(k, 0) * 100 for k in keys]
    bars = ax.bar(names, rates, color="#8064a2")
    for b, v in zip(bars, rates):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f}", ha="center", fontsize=8)
    ax.set_ylim(0, 110)
    ax.set_ylabel("v2 per-element pass rate (%)")
    ax.set_title("Where the corpus falls short (v2)")
    fig.suptitle("Report QA gate: validation debt audit", fontsize=12)
    fig.tight_layout()
    fig.savefig(figp, dpi=130)
    print(f"[ok] figure -> {figp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())