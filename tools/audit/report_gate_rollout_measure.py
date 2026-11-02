#!/usr/bin/env python3
"""报告门禁灰度上线测量：v1（线上 historical）/ v2（六要素）在全库报告语料上的对账。

只读：psql 只 SELECT；产物写 artifacts/report_gate_rollout/。

量什么（数字全部程序化注入报告，零手抄）
----------------------------------------
1. 语料规模 / v1 通过率 / v2 通过率 / v1 误放行率（v1 过 & v2 否）。
2. 灰度三档的状态翻转：off/warn 翻转数必须为 0（与灰度前逐位同构）；
   enforce 翻转数 = 将来真正会打回的报告数（这是 enforce 的成本上界）。
3. 六要素逐项通过率 + 误放行报告的 issue 分解。
4. v1 真身对账：共享档案 ``check_report_quality_v1`` 与灰度前内联版（git 历史）逐单比对，
   保证 warn 期判决不是"新写的 v1"。

用法：
    python3 tools/audit/report_gate_rollout_measure.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OUT = REPO / "artifacts" / "report_gate_rollout"
V1_ARCHIVE_REV = "57e72968"  # 灰度前最后一个内联 v1 的提交
V1_PATH = "backend/api/tensor_reports.py"

sys.path.insert(0, str(BACKEND))
from services import report_gate as RG  # noqa: E402


def fetch_reports() -> list[dict]:
    sql = (
        "select coalesce(json_agg(t), '[]'::json) from ("
        " select id, title, mission_id, coalesce(markdown,'') as markdown,"
        " coalesce(figures,'[]'::jsonb) as figures, coalesce(qa_status,'') as qa_status"
        " from tensor_reports order by id) t"
    )
    out = subprocess.run(
        ["psql", "-U", "postgres", "-d", "tensorplatform", "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def normalise_figures(figs) -> list:
    if isinstance(figs, str):
        try:
            figs = json.loads(figs)
        except json.JSONDecodeError:
            return []
    return figs if isinstance(figs, list) else []


def legacy_v1_from_git(rev: str = V1_ARCHIVE_REV):
    """灰度前内联 v1 的源码级复刻（只在 git 归档里还留有 REQUIRED_SECTIONS 时可用）。"""
    try:
        src = subprocess.run(
            ["git", "show", f"{rev}:{V1_PATH}"], cwd=REPO,
            capture_output=True, text=True, check=True,
        ).stdout
        start = src.index("REQUIRED_SECTIONS = {")
        end = src.index("class ReportQACheck")
    except (subprocess.CalledProcessError, ValueError):
        return None
    ns: dict = {"re": __import__("re")}
    exec(src[start:end], ns)  # noqa: S102 —— 只读归档源码，无副作用
    return ns["_check_report_quality"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    reports = fetch_reports()
    legacy = legacy_v1_from_git()

    stats = Counter()
    issues_v2 = Counter()
    false_accept_examples: list[dict] = []
    twin_mismatch = 0
    flips = {"off": 0, "warn": 0, "enforce": 0}
    checks_pass = Counter()

    for r in reports:
        md = r.get("markdown") or ""
        figs = normalise_figures(r.get("figures"))
        v1 = RG.check_report_quality_v1(md, figs)
        v2 = RG.check_report_quality(md, figs)
        stats["total"] += 1
        stats["v1_pass"] += int(v1["passed"])
        stats["v2_pass"] += int(v2["passed"])
        for k, ok in v2["checks"].items():
            checks_pass[k] += int(ok)
        if legacy is not None:
            twin_mismatch += int(bool(legacy(md, figs)["passed"]) != bool(v1["passed"]))
        for mode in flips:
            ev = RG.evaluate_report(md, figs, mode=mode)
            flips[mode] += int(ev["passed"] != v1["passed"])
        if v1["passed"] and not v2["passed"]:
            stats["false_accept"] += 1
            codes = [i.split(":")[0] for i in v2["issues"]]
            issues_v2.update(codes)
            if len(false_accept_examples) < 10:
                false_accept_examples.append(
                    {"id": r["id"], "title": (r["title"] or "")[:70], "codes": codes}
                )

    total = max(stats["total"], 1)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate_mode_env": RG.GATE_MODE_ENV,
        "default_mode": RG.DEFAULT_GATE_MODE,
        "corpus_size": stats["total"],
        "v1_pass": stats["v1_pass"],
        "v2_pass": stats["v2_pass"],
        "v1_pass_rate": round(stats["v1_pass"] / total, 4),
        "v2_pass_rate": round(stats["v2_pass"] / total, 4),
        "false_accept": stats["false_accept"],
        "v1_false_accept_rate": round(stats["false_accept"] / total, 4),
        "false_accept_share_of_v1_passes": round(
            stats["false_accept"] / max(stats["v1_pass"], 1), 4
        ),
        "status_flips_by_mode": flips,
        "v2_check_pass_rate": {k: round(v / total, 4) for k, v in checks_pass.items()},
        "false_accept_issue_breakdown": dict(issues_v2.most_common()),
        "v1_twin": {
            "archive_rev": V1_ARCHIVE_REV,
            "compared": legacy is not None,
            "mismatches": twin_mismatch,
        },
        "false_accept_examples": false_accept_examples,
    }
    (OUT / "rollout.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    _figures(payload)
    print(json.dumps({k: payload[k] for k in (
        "corpus_size", "v1_pass_rate", "v2_pass_rate", "v1_false_accept_rate",
        "status_flips_by_mode", "v1_twin")}, ensure_ascii=False, indent=1))
    return 0


def _figures(p: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from matplotlib import font_manager as fm

    cjk = None
    for cand in ("Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans SC", "WenQuanYi Zen Hei"):
        try:
            cjk = fm.findfont(fm.FontProperties(family=cand), fallback_to_default=False)
            break
        except Exception:  # noqa: BLE001 —— 只是探测字体，缺了就换下一个
            continue
    if cjk:
        fm.fontManager.addfont(cjk)
        plt.rcParams["font.sans-serif"] = [fm.FontProperties(fname=cjk).get_name(), "DejaVu Sans"]
    else:
        print("WARN: 无 CJK 字体，图表标题将缺字（不影响数值）")
    plt.rcParams["font.sans-serif"] = plt.rcParams["font.sans-serif"] + ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    # 图 1：通过率与误放行率
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    labels = ["v1 pass\n(production, pre-rollout)", "v2 six-element pass\n(if enforce)", "v1 false-accept\n(v1 pass & v2 fail)"]
    vals = [p["v1_pass_rate"] * 100, p["v2_pass_rate"] * 100, p["v1_false_accept_rate"] * 100]
    bars = ax.bar(labels, vals, color=["#4c78a8", "#f58518", "#e45756"])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.2, f"{v:.2f}%", ha="center", fontsize=10)
    ax.set_ylabel("share of corpus (%)")
    ax.set_ylim(0, max(vals) * 1.25)
    ax.set_title(f"Report gate v1 vs v2 (six elements): corpus n={p['corpus_size']}")
    ax.grid(axis="y", ls=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(OUT / "fig_pass_rates.png", dpi=150)
    plt.close(fig)

    # 图 2：误放行报告的要素缺失分解 + 灰度三档状态翻转
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    br = p["false_accept_issue_breakdown"]
    keys = list(br)
    a1.barh([k.replace("missing_", "") for k in keys], [br[k] for k in keys], color="#e45756")
    a1.set_xlabel("reports (v1 pass, v2 reject)")
    a1.set_title("Missing elements among false accepts")
    a1.grid(axis="x", ls=":", alpha=0.5)

    modes = list(p["status_flips_by_mode"])
    flips = [p["status_flips_by_mode"][m] for m in modes]
    bars = a2.bar(modes, flips, color=["#54a24b", "#f2cf5b", "#b279a2"])
    for b, v in zip(bars, flips):
        a2.text(b.get_x() + b.get_width() / 2, v + 0.3, str(v), ha="center", fontsize=10)
    a2.set_ylabel("qa_status flips vs pre-rollout")
    a2.set_title("Status flips by mode (off/warn must be 0)")
    a2.grid(axis="y", ls=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(OUT / "fig_rollout.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())