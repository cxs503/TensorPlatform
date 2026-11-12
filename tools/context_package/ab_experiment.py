"""ab_experiment —— 干货包 A/B 实证（可复跑，数字全部程序化，零手抄）。

设计（预注册，见 ``PREREG`` 常量与 ``runs/ab_ctxpkg_2026-11-12/prereg.md``）
------------------------------------------------------------------------
- 决策：干货包默认模式取 ``report``（并发 A/B）还是 ``enforce``（每单必注入）？
- 两臂（同任务族、同任务目标，唯一变化变量=是否附干货包）：
  - A 裸单：现状注入面（系统提示 + 席位记忆 + 技能块 + 任务目标）。
  - B 干货包：A + ``build_package`` 产物（as_of 取该单 ``created_at``，时间上只用
    该单开工前的知识 —— 无未来信息泄漏）。
- 单元：一个历史任务（mission）。
- 质量指标（机器可裁）：**held-out 失败召回** —— 该单后来登记进
  ``negative_knowledge_ledger`` 的失败画像（failure_kind + 关键词），干货包是否在
  开工前就含"同型失败"条目。A 臂按定义无知识槽（bootstrap 陷阱非按单命中），记 0。
- 成本指标：包体渲染 token（确定性估计器，两臂同口径）、占任务目标文本比例。
- 检出力局限：离线回放只能测"该防的有没有提前给出"，测不到"给了是否真避免"；
  后者需前瞻成对派单（``live_plan.json`` 给出 payload，未执行，见报告诚实声明）。

用法::

    python -m tools.context_package.ab_experiment            # 跑并落 runs/…/
    python -m tools.context_package.ab_experiment --n 60
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .assembler import build_package, covered_by
from .ledger import record
from .loader import ROOT, est_tokens, load_config
from .render import render_block
from .sources import PgReader
from .taxonomy import detect_class

PREREG = {
    "decision": "干货包默认注入模式：report（并发，注入+落账，不阻塞） vs enforce（必需槽源不可用即记红）",
    "alternatives": ["不注入（裸单，现状）", "注入干货包", "注入但只落账不渲染"],
    "hypothesis": "在 token 成本增幅受限（≤ 目标文本 40%）的前提下，干货包能把『同型失败』提前送达"
                  "（held-out 失败召回显著高于裸单），且注入项 100% 带决策键（密度判据）。",
    "metrics": {
        "held_out_failure_recall": "该单后来登记的失败画像在开工前包内的命中率（主质量指标）",
        "injected_tokens": "渲染 token（确定性估计器）",
        "token_ratio_to_goal": "注入 token / 任务目标文本 token",
        "density_yield": "带决策键条目 / 注入条目（必须 = 1.0）",
        "drops": "被丢弃条目原因分布（审计用）",
    },
    "baseline_control": "A 臂=现状注入面（无知识槽）；同任务族同目标，唯一变化=干货包",
    "thresholds": {
        "density_yield_min": 1.0,
        "recall_lift_min": 2.0,
        "token_ratio_max": 0.40,
        "n_min": 40,
    },
    "stopping_rule": "样本达到 n_min 即停；某任务族不足 1 条即如实记该族缺失，不补造",
    "exclusions": ["任务目标文本为空/无法分类→归 generic（不剔除，但单独报）",
                   "包的 as_of 晚于失败登记时间的样本→剔除（防泄漏）"],
    "confounders": ["不同模型/轮次的知识库密度不同", "negative_knowledge 由失败单自身登记→"
                    "失败画像与知识库互为因果（召回偏高）", "离线回放无真实模型行为"],
    "analysis": "按任务族分层报均值/中位数；召回差用绝对差与倍数并报；不做事后调阈值。",
}


def _iso(dt) -> str:
    if dt is None:
        return ""
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def load_corpus(reader: PgReader, since: str = "2026-11-04", limit: int = 600) -> list[dict]:
    sql = """
        SELECT id, goal, repo, status, fail_stage, task_key, created_at, finished_at,
               worker_role, parent_mission_id
        FROM tensor_dev_missions
        WHERE created_at >= %(since)s
          AND status IN ('succeeded','failed','awaiting_pr_merge','stopped')
          AND goal IS NOT NULL AND length(goal) > 40
        ORDER BY id DESC
        LIMIT %(lim)s
    """
    return reader.query(sql, {"since": since, "lim": limit})


def load_needles(reader: PgReader) -> dict[int, list[dict]]:
    sql = """
        SELECT mission_id, entry_id, title, failure_kind, keywords, applicable_conditions, registered_at
        FROM negative_knowledge_ledger
        WHERE mission_id IS NOT NULL
    """
    out: dict[int, list[dict]] = {}
    for r in reader.query(sql, {}):
        kw = r.get("keywords")
        if isinstance(kw, str):
            kws = [k.strip() for k in kw.replace("；", ",").replace("，", ",").split(",") if k.strip()]
        elif isinstance(kw, (list, tuple)):
            kws = [str(k) for k in kw]
        else:
            kws = []
        kws.append(str(r.get("title") or ""))
        out.setdefault(int(r["mission_id"]), []).append({
            "failure_kind": r.get("failure_kind"), "keywords": kws,
            "entry_id": r.get("entry_id"), "registered_at": _iso(r.get("registered_at")),
        })
    return out


def load_population(reader: PgReader) -> dict:
    """总体基线（无干预）：任务族通过率 / task_key 返修率。"""
    rows = reader.query("""
        SELECT id, goal, status, task_key FROM tensor_dev_missions
        WHERE created_at >= %(since)s AND goal IS NOT NULL
    """, {"since": "2026-11-01"})
    per: dict[str, dict] = {}
    tk = Counter(r.get("task_key") for r in rows if r.get("task_key"))
    rework = sum(1 for k, v in tk.items() if v > 1)
    for r in rows:
        cls, _ = detect_class(r.get("goal"))
        d = per.setdefault(cls, {"n": 0, "succeeded": 0})
        d["n"] += 1
        d["succeeded"] += int(str(r.get("status")) == "succeeded")
    for d in per.values():
        d["pass_rate"] = round(d["succeeded"] / max(1, d["n"]), 3)
    return {"by_class": per, "task_keys": len(tk),
            "rework_task_keys": rework,
            "rework_rate": round(rework / max(1, len(tk)), 3),
            "n_missions": len(rows)}


def run(n_target: int = 48, per_class_cap: int = 16, since: str = "2026-11-04",
        out_dir: Path | None = None) -> dict:
    cfg = load_config()
    reader = PgReader()
    out_dir = out_dir or (ROOT / "runs" / "ab_ctxpkg_2026-11-12")
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus(reader, since=since)
    needles = load_needles(reader)
    population = load_population(reader)

    # 分层抽样：按任务族取前 per_class_cap 条（不按结果挑样本，取最近优先=无选择偏倚）
    buckets: dict[str, list[dict]] = {}
    for m in corpus:
        cls, _ = detect_class(m.get("goal"))
        buckets.setdefault(cls, []).append(m)
    sample: list[dict] = []
    for cls in sorted(buckets):
        sample.extend(buckets[cls][:per_class_cap])
    sample.sort(key=lambda m: m["id"])
    sample = sample[:max(n_target * 2, n_target)]

    rows = []
    excluded = []
    for m in sample:
        cls, ev = detect_class(m.get("goal"))
        as_of = _iso(m.get("created_at")) or None
        pkg = build_package(m.get("goal"), repo=m.get("repo") or "", mission_id=m.get("id"),
                            as_of=as_of, cfg=cfg, reader=reader)
        block = render_block(pkg, cfg.get("decisions") or {})
        goal_tok = max(1, est_tokens(m.get("goal"), cfg))
        pkg_tok = int((pkg.get("budget") or {}).get("used_tokens", 0))
        ns = needles.get(int(m["id"]), [])
        # 防泄漏：只用该单开工前已登记的失败画像（登记时间未知的按保守剔除）
        usable = [x for x in ns if x.get("registered_at") and x["registered_at"] > (as_of or "")]
        covered = [covered_by(pkg, x) for x in usable]
        if ns and not usable:
            excluded.append({"mission_id": m["id"], "reason": "NEEDLE_PREDATES_OR_UNDATED"})
        rows.append({
            "mission_id": m["id"], "task_class": cls, "class_reason": ev.get("reason"),
            "status": m.get("status"), "fail_stage": m.get("fail_stage"),
            "repo": m.get("repo"), "as_of": as_of,
            "package_id": pkg.get("package_id"),
            "tokens_goal": goal_tok, "tokens_pkg": pkg_tok,
            "tokens_rendered": est_tokens(block, cfg),
            "token_ratio_to_goal": round(pkg_tok / goal_tok, 4),
            "density_yield": (pkg.get("audit") or {}).get("density_yield"),
            "injected_items": (pkg.get("audit") or {}).get("injected_items"),
            "candidate_pool": (pkg.get("audit") or {}).get("candidate_pool"),
            "drops": (pkg.get("audit") or {}).get("dropped_by_reason"),
            "slot_status": {s["slot"]: s["status"] for s in pkg.get("slots") or []},
            "degraded": pkg.get("degraded_sources"),
            "needles": len(usable), "needles_covered": sum(1 for c in covered if c["covered"]),
            "arm_A": {"tokens_knowledge": 0, "recall": 0.0 if usable else None},
            "arm_B": {"tokens_knowledge": pkg_tok,
                      "recall": round(sum(1 for c in covered if c["covered"]) / len(usable), 4)
                      if usable else None},
            "ledger": record(pkg, rendered=block, mode="report", ledger_dir=out_dir / "injection_ledger",
                             consumer="ab_experiment"),
        })

    def agg(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return {"n": 0}
        return {"n": len(vals), "mean": round(statistics.fmean(vals), 4),
                "median": round(statistics.median(vals), 4),
                "min": round(min(vals), 4), "max": round(max(vals), 4)}

    by_class: dict[str, dict] = {}
    for r in rows:
        d = by_class.setdefault(r["task_class"], {"n": 0, "tokens_pkg": [], "ratio": [],
                                                  "recall_B": [], "density": [], "items": []})
        d["n"] += 1
        d["tokens_pkg"].append(r["tokens_pkg"])
        d["ratio"].append(r["token_ratio_to_goal"])
        d["density"].append(r["density_yield"])
        d["items"].append(r["injected_items"])
        if r["arm_B"]["recall"] is not None:
            d["recall_B"].append(r["arm_B"]["recall"])
    for d in by_class.values():
        d["tokens_pkg"] = agg(d["tokens_pkg"])
        d["ratio"] = agg(d["ratio"])
        d["density"] = agg(d["density"])
        d["items"] = agg(d["items"])
        d["recall_B"] = agg(d["recall_B"])

    needle_rows = [r for r in rows if r["needles"] > 0]
    recall_B = [r["arm_B"]["recall"] for r in needle_rows]
    recall_A = [r["arm_A"]["recall"] for r in needle_rows if r["arm_A"]["recall"] is not None]
    all_density = [r["density_yield"] for r in rows if r["density_yield"] is not None]
    all_ratio = [r["token_ratio_to_goal"] for r in rows]
    drops = Counter()
    for r in rows:
        for k, v in (r["drops"] or {}).items():
            drops[k] += v
    summary = {
        "n_missions": len(rows),
        "n_by_class": {k: v["n"] for k, v in sorted(by_class.items())},
        "recall_B_overall": round(statistics.fmean(recall_B), 4) if recall_B else None,
        "recall_A_overall": round(statistics.fmean(recall_A), 4) if recall_A else None,
        "recall_lift": (round((statistics.fmean(recall_B) / statistics.fmean(recall_A)), 3)
                        if recall_B and recall_A and statistics.fmean(recall_A) > 0 else None),
        "n_missions_with_needles": len(needle_rows),
        "density_yield_min": min(all_density) if all_density else None,
        "density_n_below_1": sum(1 for x in all_density if x is not None and x < 1.0),
        "token_ratio": agg(all_ratio),
        "tokens_pkg": agg([r["tokens_pkg"] for r in rows]),
        "drops_total": dict(drops.most_common()),
        "excluded": excluded,
        "population_baseline": population,
        "evidence_state_counts": dict(Counter(r["status"] for r in rows)),
    }
    th = PREREG["thresholds"]
    checks = {
        "n_ok": summary["n_missions"] >= th["n_min"],
        "density_ok": (summary["density_yield_min"] == 1.0),
        "recall_ok": (summary["recall_lift"] is not None and summary["recall_lift"] >= th["recall_lift_min"]),
        "token_ok": (summary["token_ratio"]["n"] > 0 and summary["token_ratio"]["mean"] <= th["token_ratio_max"]),
    }
    verdict = "ENFORCE" if all(checks.values()) else "REPORT"
    result = {"generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
              "prereg": PREREG, "checks": checks, "verdict": verdict,
              "summary": summary, "by_class": by_class, "rows": rows,
              "live_plan": _live_plan(rows[:8])}
    (out_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "prereg.md").write_text(_prereg_md(), encoding="utf-8")
    (out_dir / "report.md").write_text(_report_md(result), encoding="utf-8")
    reader.close()
    return result


def _live_plan(sample_rows: list[dict]) -> dict:
    """前瞻成对派单计划（未执行）：同目标两臂，测真实 gate 通过/返修/一次通过。"""
    pairs = []
    for r in sample_rows:
        base = {"repo": "TensorPlatform", "budget": {"max_tool_calls": 60, "max_wall_seconds": 3600}}
        pairs.append({"task_key": f"ctxpkg-ab-{r['mission_id']}",
                      "arm_A": {**base, "goal": f"[CTXPKG_OFF] {r.get('goal_hint') or ''} 复现单 {r['mission_id']}"},
                      "arm_B": {**base, "goal": f"[CTXPKG_ON] {r.get('goal_hint') or ''} 复现单 {r['mission_id']}"},
                      "mission_id": r["mission_id"]})
    return {"executed": False,
            "why_not": "需平台把 hook 装进运行中的 tp_worker 并给成对派单额度；本单只交付可执行 payload",
            "payload_endpoint": "POST /api/v1/tensor/missions",
            "pairs": pairs}


def _prereg_md() -> str:
    p = PREREG
    lines = ["# 干货包 A/B 预注册（context-package A/B preregistration）", "",
             f"- 决策：{p['decision']}",
             f"- 备选：{' / '.join(p['alternatives'])}",
             f"- 假设：{p['hypothesis']}", "",
             "## 指标", ""]
    for k, v in p["metrics"].items():
        lines.append(f"- `{k}`：{v}")
    lines += ["", f"- 基线/对照：{p['baseline_control']}", "",
              "## 预声明阈值与停止规则", ""]
    for k, v in p["thresholds"].items():
        lines.append(f"- `{k}` = {v}")
    lines += [f"- 停止规则：{p['stopping_rule']}",
              f"- 剔除：{'；'.join(p['exclusions'])}",
              f"- 混杂：{'；'.join(p['confounders'])}",
              f"- 分析：{p['analysis']}", ""]
    return "\n".join(lines)


def _report_md(res: dict) -> str:
    s, b = res["summary"], res["by_class"]
    L = ["# 干货包 A/B 实证报告（离线回放 + 前瞻计划）", "",
         f"生成时间：{res['generated_at']}　判定：**{res['verdict']}**", "",
         "## 1. 样本与总体基线", "",
         f"- 对照单数 N = **{s['n_missions']}**（按任务族分层：{s['n_by_class']}）",
         f"- 总体基线（2026-11-01 起，无干预）：任务族通过率 "
         + ", ".join(f"{k}:{v['pass_rate']}(n={v['n']})" for k, v in s["population_baseline"]["by_class"].items())
         + f"；task_key 返修率 {s['population_baseline']['rework_rate']}",
         "", "## 2. 成本（注入 token，确定性估计，两臂同口径）", "",
         f"- 包体 token：均值 {s['tokens_pkg'].get('mean')}／中位 {s['tokens_pkg'].get('median')}"
         f"／区间 [{s['tokens_pkg'].get('min')}, {s['tokens_pkg'].get('max')}]",
         f"- 占任务目标文本比例：均值 {s['token_ratio'].get('mean')}／中位 {s['token_ratio'].get('median')}"
         f"／最大 {s['token_ratio'].get('max')}（预声明上限 {PREREG['thresholds']['token_ratio_max']}）",
         "", "## 3. 质量（held-out 失败召回，主指标）", "",
         f"- 有失败画像的单数 {s['n_missions_with_needles']}",
         f"- B 臂召回 {s['recall_B_overall']}　A 臂召回 {s['recall_A_overall']}　倍数 {s['recall_lift']}",
         "", "## 4. 密度判据", "",
         f"- density_yield 最小 {s['density_yield_min']}；<1.0 的单数 {s['density_n_below_1']}（必须 0）",
         f"- 丢弃原因分布：{s['drops_total']}", "",
         "## 5. 分族明细", "",
         "| 任务族 | n | 包 token 均值 | 占目标文本 | 注入条数均值 | 召回(有画像单) |",
         "|---|---|---|---|---|---|"]
    for k, v in sorted(b.items()):
        L.append(f"| {k} | {v['n']} | {v['tokens_pkg'].get('mean')} | {v['ratio'].get('mean')} | "
                 f"{v['items'].get('mean')} | {v['recall_B'].get('mean')} (n={v['recall_B'].get('n')}) |")
    L += ["", "## 6. 判定与局限（honest）", "",
          f"- 阈值检查：{res['checks']}",
          "- 局限①：离线回放只能证明『该防的有没有提前送到』，不能证明『送到就避免了』；"
          "后者要前瞻成对派单（`live_plan` 已给出 payload，未执行）。",
          "- 局限②：negative_knowledge 由失败单自身登记，失败画像与知识库互为因果 → 召回偏高，"
          "故只作『可达性』证据，不作效应量。",
          "- 局限③：A 臂召回按定义 = 0（裸单无按单知识槽；bootstrap 陷阱不分单命​中），"
          "倍数只表示可达性差异，不等于收益倍数。",
          "- 局限④：token 为确定性代理估计（无第三方 tokenizer），绝对值仅可比不可外推。",
          ""]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=48)
    ap.add_argument("--per-class", type=int, default=16)
    ap.add_argument("--since", default="2026-11-04")
    a = ap.parse_args()
    r = run(n_target=a.n, per_class_cap=a.per_class, since=a.since)
    print(json.dumps({"verdict": r["verdict"], "checks": r["checks"],
                      "summary": {k: r["summary"][k] for k in
                                  ("n_missions", "n_by_class", "recall_B_overall",
                                   "recall_A_overall", "density_yield_min", "token_ratio",
                                   "drops_total")}}, ensure_ascii=False, indent=2))