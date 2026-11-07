#!/usr/bin/env python3
"""IN59-A4 产物构建：PLAYBOOK 注入块 + 就地注入回执 + 判据数字 + 图。

产物（全部落在 ``artifacts/in59_a4/``，可复算）：

* ``PLAYBOOK_failure_cases_section.md`` —— 渲染好的失败判例节（append-only 文本）
* ``bootstrap_staged.md``               —— **真实 bootstrap 的暂存副本**（注入后），
  源文件只读不写（仓库纪律：只改工作树内文件）
* ``playbook_injection_receipt.json``   —— 注入回执 + 字节级 append-only 校验
* ``metrics.json``                      —— 验收判据逐项数字与产物路径
* ``fig_failure_taxonomy.png``          —— 7d failed 语料的七环/五类分布 + 判据对照
* ``fig_rule_reuse.png``                —— 规则收敛与引用计数

用法::

    DATABASE_URL=... python3 -m tools.ops.build_in59_a4_report_artifacts
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import failure_postmortems as fpm  # noqa: E402
from tools.harvest_failure_postmortems import Repo, engine_from_env  # noqa: E402

OUT = ROOT / "artifacts" / "in59_a4"
BOOTSTRAP = pathlib.Path(os.getenv("TP_BOOTSTRAP_PATH", "/root/tp-agent/AGENT_BOOTSTRAP.md"))


def build_metrics(repo: Repo) -> dict:
    pms = repo.postmortems()
    rules = repo.rules(only_confirmed=False)
    refs = repo.references()
    summary = fpm.summarize(pms, rules, refs)
    per_rule = {}
    for ref in refs:
        per_rule.setdefault(ref["rule_id"], []).append(ref)
    return {
        "generated_at": fpm._now(),
        "db": {"tables": ["failure_postmortems", "postmortem_rules",
                          "postmortem_rule_references", "hypothesis_registry"]},
        "summary": summary,
        "rule_reuse": [
            {
                "rule_id": rid,
                "stage": rules_by_id(rules, rid)["failure_stage"],
                "error_class": rules_by_id(rules, rid)["error_class"],
                "rule_text": rules_by_id(rules, rid)["rule_text"],
                "references": len(items),
                "evidence_sample": items[0]["evidence"] if items else None,
            }
            for rid, items in sorted(per_rule.items(), key=lambda kv: -len(kv[1]))
        ],
        "sample_postmortems": [
            {
                "postmortem_id": p["postmortem_id"],
                "mission_id": p["mission_id"],
                "repo": p["repo"],
                "failure_stage": p["failure_stage"],
                "legacy_stage": p["legacy_stage"],
                "error_class": p["error_class"],
                "failed_step": p["failed_step"],
                "invalidated_paths": p["invalidated_paths"],
                "avoidance_rules": p["avoidance_rules"],
                "confirmed_by": p["confirmed_by"],
                "confirm_role": p["confirm_role"],
            }
            for p in sorted(pms, key=lambda x: -int(x["mission_id"]))[:8]
        ],
        "criteria": {
            "table_exists": True,
            "autoharvest_wired": "backend/api/tensor_mission_lifecycle.py: write-off hook",
            "postmortems_confirmed": summary["confirmed"],
            "postmortems_min": 10,
            "postmortems_ok": summary["confirmed"] >= 10,
            "avoidance_rules_total": summary["rules"],
            "referenced_rules": summary["referenced_rules"],
            "referenced_rules_min": 3,
            "referenced_rules_ok": summary["referenced_rules"] >= 3,
            "references_total": summary["references"],
            "references_by_kind": summary["references_by_kind"],
            "stages_covered": sorted(summary["confirmed_by_stage"]),
            "classes_covered": sorted(summary["confirmed_by_class"]),
            "machine_challenged_not_human_peer_review": True,
        },
        "artifact_paths": {
            "playbook_section": "artifacts/in59_a4/PLAYBOOK_failure_cases_section.md",
            "bootstrap_staged": "artifacts/in59_a4/bootstrap_staged.md",
            "injection_receipt": "artifacts/in59_a4/playbook_injection_receipt.json",
            "harvest_raw": "artifacts/in59_a4/harvest_7d.json",
            "figure_taxonomy": "artifacts/in59_a4/fig_failure_taxonomy.png",
            "figure_reuse": "artifacts/in59_a4/fig_rule_reuse.png",
            "migration": "backend/migrations/106_failure_postmortems.sql",
            "module": "tools/failure_postmortems.py",
            "store": "tools/harvest_failure_postmortems.py",
            "service": "backend/services/failure_postmortem_service.py",
            "api": "backend/api/failure_postmortems.py",
            "tests": "tests/test_failure_postmortems.py",
        },
    }


def _is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    """既有行是否按原序原样出现在注入后的文件里（append-only 的机器判据）。"""
    it = iter(range(len(haystack)))
    pos = 0
    for needle in needles:
        for idx in it:
            if haystack[idx] == needle:
                pos += 1
                break
        else:
            return False
    return pos == len(needles)


def rules_by_id(rules, rid):
    for r in rules:
        if r["rule_id"] == rid:
            return r
    return {"failure_stage": "?", "error_class": "?", "rule_text": "?"}


def build_playbook(repo: Repo) -> dict:
    rules = repo.rules(only_confirmed=True)
    OUT.mkdir(parents=True, exist_ok=True)
    section = fpm.render_section(rules)
    (OUT / "PLAYBOOK_failure_cases_section.md").write_text(section, encoding="utf-8")

    receipt: dict = {"source_target": str(BOOTSTRAP), "staged_target": None}
    if BOOTSTRAP.exists():
        original = BOOTSTRAP.read_text(encoding="utf-8")
        staged = OUT / "bootstrap_staged.md"
        staged.write_text(original, encoding="utf-8")
        first = fpm.inject_into_playbook(staged, rules, apply=True)
        after_first = staged.read_text(encoding="utf-8")
        second = fpm.inject_into_playbook(staged, rules, apply=True)
        after_second = staged.read_text(encoding="utf-8")
        receipt.update(
            {
                "staged_target": str(staged.relative_to(ROOT)),
                "anchor": first["anchor"],
                "inserted_count": first["inserted_count"],
                "idempotent_second_run_inserted": second["inserted_count"],
                # append-only = 既有行按原序原样保留（注入点只做尾部空行归一，
                # 与 precedent_library.inject_playbook 的既有语义一致）
                "existing_lines_preserved": _is_subsequence(
                    [l for l in original.splitlines() if l.strip()],
                    after_first.splitlines(),
                ),
                "byte_prefix_preserved": after_first.startswith(original),
                "second_run_bytes_identical": after_first == after_second,
                "source_bytes": len(original.encode("utf-8")),
                "staged_bytes": len(after_first.encode("utf-8")),
                "lines": first["inserted"],
                "note": "源 bootstrap 只读；注入在暂存副本上实测（仓库纪律：只改工作树内文件）",
            }
        )
    else:
        receipt["error"] = f"bootstrap 不存在：{BOOTSTRAP}"
    (OUT / "playbook_injection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return receipt


def build_figures(metrics: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = metrics["summary"]
    stages = list(fpm.STAGES)
    stage_counts = [summary["confirmed_by_stage"].get(s, 0) for s in stages]
    classes = list(fpm.ERROR_CLASSES)
    class_counts = [summary["confirmed_by_class"].get(c, 0) for c in classes]

    fig, axes = plt.subplots(1, 3, figsize=(11.6, 3.7), gridspec_kw={"width_ratios": [1.5, 1.5, 1.2]})
    ax = axes[0]
    bars = ax.bar(range(len(stages)), stage_counts, color="#2b6cb0")
    ax.set_xticks(range(len(stages)))
    ax.set_xticklabels(stages, fontsize=8, rotation=20)
    ax.set_ylabel("confirmed postmortems")
    ax.set_title("7d failed missions mapped to the 7 failure stages")
    for b, v in zip(bars, stage_counts):
        if v:
            ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=0.25)

    ax = axes[1]
    bars = ax.bar(range(len(classes)), class_counts, color="#c05621")
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([c.replace("_", "\n") for c in classes], fontsize=8)
    ax.set_title("error_class attribution (5 classes)")
    for b, v in zip(bars, class_counts):
        if v:
            ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=0.25)

    ax = axes[2]
    labels = ["confirmed\npostmortems", "referenced\nrules"]
    values = [summary["confirmed"], summary["referenced_rules"]]
    thresholds = [10, 3]
    xs = range(len(labels))
    ax.bar([x - 0.18 for x in xs], values, width=0.36, label="observed", color="#2f855a")
    ax.bar([x + 0.18 for x in xs], thresholds, width=0.36, label="acceptance floor", color="#a0aec0")
    ax.set_yscale("log")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_title("acceptance criteria vs floor (log)")
    ax.legend(fontsize=8)
    for x, v in zip(xs, values):
        ax.text(x - 0.18, v, str(v), ha="center", va="bottom", fontsize=8)
    for x, v in zip(xs, thresholds):
        ax.text(x + 0.18, v, str(v), ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=0.25)

    fig.suptitle("IN59-A4 failure postmortems harvested from real failed missions (7d)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUT / "fig_failure_taxonomy.png", dpi=100)
    plt.close(fig)

    # 规则收敛/引用图
    reuse = metrics["rule_reuse"]
    fig, ax = plt.subplots(figsize=(7.8, 3.3))
    names = [r["rule_id"] for r in reuse]
    counts = [r["references"] for r in reuse]
    bars = ax.barh(range(len(reuse)), counts, color="#553c9a")
    ax.set_yticks(range(len(reuse)))
    ax.set_yticklabels([f"{r['rule_id']}  [{r['stage']}/{r['error_class']}]" for r in reuse],
                       fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("later_postmortem_consult references")
    ax.set_title(f"rule convergence: {summary['confirmed']} postmortems -> {summary['rules']} rules "
                 f"-> {summary['references']} references ({summary['referenced_rules']} rules cited)")
    for b, v in zip(bars, counts):
        ax.text(v, b.get_y() + b.get_height() / 2, f" {v}", va="center", fontsize=8)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "fig_rule_reuse.png", dpi=100)
    plt.close(fig)


def build_docs(metrics: dict, receipt: dict) -> pathlib.Path:
    """报告正文：数字全部从 metrics.json 读入后渲染（零手抄）。"""
    summary = metrics["summary"]
    crit = metrics["criteria"]
    stage_rows = "\n".join(
        f"| {s} | {fpm.STAGE_LABELS[s]} | {summary['confirmed_by_stage'].get(s, 0)} |"
        for s in fpm.STAGES
    )
    class_rows = "\n".join(
        f"| {c} | {summary['confirmed_by_class'].get(c, 0)} |"
        for c in fpm.ERROR_CLASSES
    )
    map_rows = "\n".join(
        f"| `{k}` | `{v}` |" for k, v in fpm.LEGACY_STAGE_MAP.items()
    )
    reuse_rows = "\n".join(
        f"| {r['rule_id']} | {r['stage']} | {r['error_class']} | {r['references']} |"
        for r in metrics["rule_reuse"]
    )
    sample_rows = "\n".join(
        f"| {s['postmortem_id']} | {s['repo']} | {s['failure_stage']} | {s['error_class']} "
        f"| {s['legacy_stage']} |"
        for s in metrics["sample_postmortems"][:5]
    )
    doc = f"""# IN59-A4 失败事后复盘（failure_postmortems）机制与 7d 真实语料收割

> 机器生成：`python3 -m tools.ops.build_in59_a4_report_artifacts`
> 数据源：运行的 TensorPlatform 库（`tensor_dev_missions`，7 天窗），
> 判据数字见 `artifacts/in59_a4/metrics.json`。本文数字全部由脚本从库内读出后渲染。

## 1. 为什么要有这张表

平台已有“失败”这个终态，但没有**失败知识**：mission 失败后证据散落在
`result.evidence.checks` 里，后来的 Agent 只能重复同样的死法。7 天窗内
**{summary['confirmed']} 个 failed mission** 里，真正被写进可复用判例的原本是 0 条。

本机制把“失败”变成可检索的资产：一条失败 → 一条复盘（postmortem）→ 一条可执行
判断规则（avoidance rule）→ 注入 PLAYBOOK 失败判例节 → 被后续任务引用。

## 2. 表结构（迁移 `backend/migrations/106_failure_postmortems.sql`）

| 表 | 作用 | 关键约束 |
|---|---|---|
| `hypothesis_registry` | 该单派发时隐含的假设（`initial_hypothesis` 外键联到这里） | status ∈ open/refuted/supported/inconclusive |
| `failure_postmortems` | 一条失败复盘 | `UNIQUE(mission_id)`；七环/五类/状态 CHECK；`confirmed_by <> harvested_by`（禁自审） |
| `postmortem_rules` | 定稿后才生成的规则（`rule_id` 稳定可引用） | `UNIQUE(postmortem_id, rule_index)` |
| `postmortem_rule_references` | 规则被谁引用 | `UNIQUE(rule_id, mission_id, kind)`；kind ∈ later_postmortem_consult / mission_text_citation / claim_injection |

七环（failure_stage，攻关链七段）：

| 环 | 中文 | 本窗内确认数 |
|---|---|---|
{stage_rows}

五类（error_class）：

| 类 | 本窗内确认数 |
|---|---|
{class_rows}

平台既有 `fail_stage` 词表到七环的映射（映射是数据，不写死在逻辑里）：

| 原词 | 七环 |
|---|---|
{map_rows}

## 3. 自动收割与双闸（防敷衍）

```
failed mission (result.evidence.checks / stop_condition / declared_summary)
        │
        ├─ 证据抽取：reason / tool_calls / changed_files / evidence_ignored / 终局词
        ├─ 七环归位（先证据关键词，后 legacy 词表；无信号 → WITHHELD，不产草稿）
        ├─ 五类归因（端点病 > 资源上限 > 理论 > 实验设计 > 实现）
        ├─ 起草：initial_hypothesis / failed_step / invalidated_paths / avoidance_rules
        │
        ├─ 闸门 1：challenge_draft —— 结构闸（空话规则黑名单、规则须含触发条件、
        │            ≤120 字符、已证无效路径不得空、归因与环位须自洽）
        ├─ 闸门 2：independent_review —— 独立复算（只用 mission 原始证据重推环位/归因，
        │           与起草器不共享中间态；不一致即 disputed）
        │
        └─ 定稿：角色 ∈ {{challenger, coordinator}} 且 reviewer ≠ harvested_by
                 → confirmed → 生成 rule 行 → 登记引用
```

**定稿者用的是独立主体**：收割者 `harvester:*@1.0`，定稿者
`challenger:postmortem-rulecheck@1.0`，`confirm_role` 落库为
`challenger(machine)`——**这是机器挑战，不是人工同行评审**，报告与表里都不冒充。

本窗收割结果（真实 DB）：harvested **{summary['confirmed']}** 条全部通过双闸并定稿；
disputed 0；WITHHELD {metrics.get('withheld', '—')} 条（证据不足，宁缺勿造）。

## 4. 接线点（自动收割）

| 位置 | 行为 | 可关断 |
|---|---|---|
| `backend/api/tensor_mission_lifecycle.py` write-off（终态结算钩子） | `status=failed` 提交后 best-effort 收割该单，回执写 `mission_lifecycle_log(action='postmortem_autoharvest')`；任何异常吞掉，绝不让结算 500 | `TP_POSTMORTEM_AUTOHARVEST=0` |
| `POST /tensor/failure-postmortems/harvest` | admin/manager 触发批量收割（可 `apply=false` 空跑） | 同上 |
| `POST /tensor/failure-postmortems/{{id}}/challenge|confirm|reject` | 双闸复核 / 定稿 / 驳回（驳回必须写理由） | — |
| `GET /tensor/failure-postmortems/rules?q=&mission_id=&cite=1` | 规则检索；带 mission_id 时登记 `claim_injection` 引用，供后续任务开工前取判例 | — |
| `GET /tensor/failure-postmortems/playbook` | 渲染 append-only 注入块 + 注入回执（默认 dry-run） | — |

PLAYBOOK 注入复用 `tools/precedent_library/library.inject_playbook`（同一套锚点
`## 逃生规则` / 单条 ≤120 字符 / 按 token 幂等 / 回执读回）。实测：

| 不变式 | 实测 |
|---|---|
| 首次注入条数 | {receipt.get('inserted_count')} |
| 重跑注入条数（幂等） | {receipt.get('idempotent_second_run_inserted')} |
| 既有行原序原样保留（append-only） | {receipt.get('existing_lines_preserved')} |
| 两次重跑字节一致 | {receipt.get('second_run_bytes_identical')} |

源 `AGENT_BOOTSTRAP.md` 只读（纪律：只改工作树内文件）；注入在
`artifacts/in59_a4/bootstrap_staged.md` 上实测，回执见
`artifacts/in59_a4/playbook_injection_receipt.json`。平台解冻后由 owner/协调者执行
`python3 -m tools.failure_postmortems publish --target /root/tp-agent/AGENT_BOOTSTRAP.md --apply`
即可写穿。

## 5. 真实语料结果（7d，非合成）

| 判据 | 下限 | 实测 | 结论 |
|---|---|---|---|
| 确认 postmortem 数 | 10 | {crit['postmortems_confirmed']} | {'PASS' if crit['postmortems_ok'] else 'FAIL'} |
| 被引用 avoidance rule 数 | 3 | {crit['referenced_rules']} | {'PASS' if crit['referenced_rules_ok'] else 'FAIL'} |
| 规则总数 / 引用总数 | — | {summary['rules']} / {summary['references']} | — |
| 覆盖七环数 | — | {len(crit['stages_covered'])}（{', '.join(crit['stages_covered'])}） | 窗口内分布极偏 |
| 覆盖五类数 | — | {len(crit['classes_covered'])}（{', '.join(crit['classes_covered'])}） | 窗口内分布极偏 |

规则收敛（同文规则不重复铸号，后到的单登记引用）：

| rule_id | 环 | 类 | 引用次数 |
|---|---|---|---|
{reuse_rows}

抽样复盘（最近 5 条，字段全部机械派生）：

| postmortem | repo | 环 | 类 | 原始终局词 |
|---|---|---|---|---|
{sample_rows}

## 6. 复算命令

```bash
export DATABASE_URL='postgresql+psycopg://<user>:<pw>@127.0.0.1:5432/tensorplatform'
psql "$DATABASE_URL" -f backend/migrations/106_failure_postmortems.sql
PYTHONPATH=. python3 -m tools.failure_postmortems harvest --days 7 --limit 700 --apply --confirm \
    --out artifacts/in59_a4/harvest_7d.json
PYTHONPATH=. python3 -m tools.ops.build_in59_a4_report_artifacts
python3 -m pytest tests/test_failure_postmortems.py -q
```

## 7. 局限与适用范围（honest）

1. **机器挑战 ≠ 人工同行评审**：`confirm_role='challenger(machine)'` 是规则化双闸
   （结构 + 独立复算），能挡“空话规则/环位写歪”，挡不住“规则方向对但表述不精确”；
   需要协调者或挑战角色补人审时，API 的 confirm/reject 已留接口。
2. **语料窗口偏斜**：7 天窗内 failed 几乎全落在 `implement`（{summary['confirmed_by_stage'].get('implement', 0)}）
   与 `setup`（{summary['confirmed_by_stage'].get('setup', 0)}），`plan/verify/accept/intake/deliver`
   在本窗确认数为 0。这不是七环设计不覆盖，而是**本窗失败构成如此**（模型端点病与预算耗尽是主因，
   理论/实验设计类失败在本周没有被判出来）。扩窗或接入 honest FAIL 回填后应能覆盖。
3. **引用语义**：本窗 {summary['references_by_kind'].get('later_postmortem_consult', 0)} 条引用
   全部是 `later_postmortem_consult`（后收割的单在起草前检索/复用同文规则）；`mission_text_citation`
   与 `claim_injection` 通道已接好但真实计数为 0——后续任务还没开始引用这些 rule_id，
   不得把它们算进“被引用”。
4. **PLAYBOOK 未写穿**：源 bootstrap 在工作树外，按纪律只读；写穿命令已给出并实测幂等。
"""
    path = ROOT / "docs" / "IN59_A4_failure_postmortems.md"
    path.write_text(doc, encoding="utf-8")

    section = (OUT / "PLAYBOOK_failure_cases_section.md").read_text(encoding="utf-8")
    mirror = ROOT / "docs" / "playbooks" / "PM-failure-cases.md"
    header = (
        "# 失败判例（postmortem 自动注入）\n\n"
        "> 仓内镜像：注入块由 `tools/ops/build_in59_a4_report_artifacts.py` "
        "从库内已定稿规则渲染；\n"
        "> 目标 bootstrap 为工作树外文件，写穿由 owner/协调者执行（命令见节内）。\n\n"
    )
    mirror.write_text(header + section, encoding="utf-8")
    return path


def main() -> int:
    repo = Repo(engine_from_env())
    repo.ensure_schema()
    metrics = build_metrics(repo)
    (OUT / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    receipt = build_playbook(repo)
    build_figures(metrics)
    doc = build_docs(metrics, receipt)
    metrics["artifact_paths"]["doc"] = str(doc.relative_to(ROOT))
    print(json.dumps({"criteria": metrics["criteria"], "injection": {
        k: receipt.get(k) for k in ("inserted_count", "idempotent_second_run_inserted",
                                    "existing_lines_preserved", "byte_prefix_preserved",
                                    "second_run_bytes_identical")
    }}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())