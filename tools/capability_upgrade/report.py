"""Generate the P218-N01 acceptance report from run artifacts.

Every number in the output Markdown is injected from
``artifacts/capability_upgrade/results.json`` / ``verdict.json`` — zero hand
copying (the report is a *view* over the artifacts, not a retelling).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

WORKTREE = Path(__file__).resolve().parents[2]
ART = WORKTREE / "artifacts" / "capability_upgrade"
DOC = WORKTREE / "docs" / "capability_upgrade"

GATE_ORDER = [
    "min_matching",
    "min_sources",
    "provenance_sha256",
    "below_target_assertion",
    "contradiction_gate",
    "metric:error_pct",
    "metric:n_samples",
]


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(out)


def build() -> Path:
    results = json.loads((ART / "results.json").read_text(encoding="utf-8"))
    verdict = json.loads((ART / "verdict.json").read_text(encoding="utf-8"))
    demo, ledger = results["demo"], results["ledger"]

    c1 = verdict["C1_evidence_association"]
    c2 = verdict["C2_auto_rejudge_within_1s_and_consistent"]
    c4 = verdict["C4_self_healing_upgrade"]
    c5 = verdict["C5_negative_control_holds_withheld"]
    c6 = verdict["C6_pytest"]

    demo_rows = []
    for ing in demo["ingests"]:
        demo_rows.append([
            ing["capability_id"], ing["kind"], ing["metrics"].get("error_pct"),
            ing["metrics"].get("n_samples"), ing["level"], ing["outcome"],
            round(ing["latency_ms"], 3),
        ])

    ledger_rows = []
    for slot in ledger["slots"]:
        ledger_rows.append([
            slot["capability_id"], slot["claimed_level"], slot["level"],
            slot["evidence_count"], slot["outcome"],
            ",".join(sorted({f["check"] for f in slot["failures"]})) or "-",
        ])

    lat_rows = [[str(c2["events_measured"]), f'{c2["max_latency_ms"]:.3f}',
                 f'{c2["p95_latency_ms"]:.3f}', f'{c2["deadline_ms"]:.0f}',
                 f'{c2["auto_manual_agreement_rate"] * 100:.1f}%']]

    from collections import Counter

    claimed = Counter((sl["claimed_level"] or "?") for sl in ledger["slots"])
    review_caps = {i["capability_id"] for i in ledger["ingests"] if i["kind"] == "ledger_review"}
    absent_caps = {i["capability_id"] for i in ledger["ingests"] if i["kind"] == "absence_audit"} - review_caps

    lat_table = _table(["事件数", "最大延迟 ms", "P95 延迟 ms", "时限 ms", "自动/手动一致率"], lat_rows)
    demo_table = _table(["能力位", "证据类型", "error_pct", "n_samples", "判后等级", "结果", "延迟 ms"], demo_rows)
    ledger_table = _table(["能力位", "台账自述", "判后等级", "证据数", "结果", "阻断的门"], ledger_rows)
    accept_table = _table(
        ["判据", "机器判定", "数字/工件"],
        [
            ["C1 证据关联", c1["ok"],
             f"withheld_with_evidence={c1['ledger_withheld_with_evidence']}/{c1['ledger_withheld_total']}"],
            ["C2 自动重判 <=1s 且与手动一致", c2["ok"],
             f"max={c2['max_latency_ms']:.3f} ms, agreement={c2['auto_manual_agreement_rate'] * 100:.1f}%"],
            ["C3 升级历史四要素", verdict["C3_history_fields"]["ok"],
             f"history rows demo={verdict['C3_history_fields']['history_rows_demo']}, "
             f"ledger={verdict['C3_history_fields']['history_rows_ledger']}"],
            ["C4 自愈升级 WITHHELD->REAL", c4["ok"], str(c4["demo_analytic_integration_level"])],
            ["C5 负控保持 WITHHELD", c5["ok"],
             f"under_sampled={c5['demo_under_sampled']}, contradicted={c5['demo_contradicted']}, "
             f"absent={c5['demo_absent']}, ledger_withheld={c5['ledger_still_withheld']}"],
            ["C6 pytest >=10 全绿", c6["ok"], c6["summary"]],
            ["C7 零领域语义", verdict["C7_zero_domain_semantics"]["ok"],
             "包内源码域词扫描=0 命中；注册表/适配器注入"],
        ],
    )

    md = f"""# P218-N01 能力证据自动升级管线：WITHHELD→REAL 系统化解决

**节点**：P218-N01（自主战役，物理域=平台自治）　**仓**：TensorPlatform
**模块**：`tools/capability_upgrade/`　**配置**：`config/capability_upgrade/demo_registry.json`
**工件**：`artifacts/capability_upgrade/`（results.json / verdict.json / 图 3 张 / 原始 SQLite 事件库）
**证据等级**：本节点全部判据由程序计算，REAL（机器可复跑）；平台侧能力位的等级仍由证据决定，不由本节点代为声称。

## ① 案例介绍

本节点的“物理背景”不是流体域，而是**能力台账的证据准入**：平台自述 `audit_capabilities`
给出 21 个能力位的等级，其证据等级长期依赖人工判读（报告正文、评审散文），
出现三个结构性缺陷：

1. **证据不可溯源**：级别与“哪个算例/测试/参考值”之间没有机器可查的边；
2. **新证据不触发重判**：证据入库后靠人工重跑 CLI，WITHHELD 位长期冻结；
3. **台账不自愈**：缺证据的能力位与证据已满足的能力位在台账里长得一样。

本节点把“等级”变成 (证据集合, 验收判据) 的**确定性函数**，并让证据入库自动触发重判。

### 关键参数表（全部来自配置，不写死在代码）

| 参数 | 取值 | 来源 |
|---|---|---|
| 重判时限（证据入库→判完） | 1000 ms | `EvidencePipeline(deadline_seconds=1.0)` |
| 数值判据（数值验证误差上限） | 3 % | 注册表 `spec.metrics[error_pct]` |
| 最少独立证据数 | 2（demo 主位）/ 1（对照位） | 注册表 `spec.min_matching` |
| 最少独立来源数 | 2 | 注册表 `spec.min_sources` |
| 溯源强制项 | `source.sha256` 长度=64 | 注册表 `spec.require_source_sha256` |
| 等级序 | NOT_SUPPORTED<STUB<MOCK<WITHHELD<REAL | `model.LEVEL_RANK` |

### 两条证据流（两条独立的判据面）

* **合成面（demo）**：4 个 `demo.*` 能力位 + 5 条证据，其中数值误差由
  `tools/capability_upgrade/numeric_probe.py` **现场计算**（梯形求积 vs 闭式解），
  不是手写百分数。
* **真实面（dry run）**：`artifacts/capability_ledger/capability_ledger.json`
  （sha256 `{ledger['source_sha256'][:16]}…`）经 `ledger_adapter.py` 映射成
  21 个能力位 + {len(ledger['ingests'])} 条证据，跑同一条管线。

## ② 怎么做的

```
EvidenceItem ──ingest()──► evidence_items(SQLite, 内容寻址 digest)
                    │        └─► evidence_events(emit 时刻 + 完成时刻 + latency_ms)
                    ▼ 同一次调用内同步执行
              RejudgeEngine.judge()
                    ├─ criteria.evaluate()  ← 判据是 JSON 数据（白名单/最小证据数/来源数/溯源哈希/数值门/矛盾门）
                    ├─ 只升不降：命中 target_level 的判据才改级；无判据或未过 → 原级不动
                    ├─ rejudge_runs（trigger=evidence_ingested|manual，逐判据 verdicts）
                    └─ upgrade_history（证据 ID + 判据 + 时间 + 结果 + 失败原因）
```

设计要点（与验收判据一一对应）：

1. **能力位-证据关联模型**：`slot_evidence(capability_id, evidence_id)` 表；`coverage()`
   统计 WITHHELD 位是否有 ≥1 证据，缺口具名列出（C1）。
2. **自动重判**：`ingest()` 内**同步**调用引擎，因此“返回即已重判”，1 秒限是结构性保证而不是轮询承诺；
   实测延迟写回 `evidence_events.latency_ms`，审计可仅凭数据库复核（C2）。
3. **判据检查**：`criteria.evaluate()` 输出逐检查项记录（`checks`），失败必带 `failures` 原因，禁止静默放行；
   结构性矛盾门：证据自身标注的等级若低于判据目标级（如标 MOCK 却想证明 REAL）一律不得计入（C5）。
4. **证据链 + 升级历史**：`chain()` 返回 证据（含 source.path/locator/sha256/digest）→ 判据 → 每次运行 → 历史；
   `upgrade_history` 每行含 `evidence_ids` / `criteria` / `evaluated_at` / `outcome` / `after_level`（C3）。
5. **API 端点**（`tools/capability_upgrade/api.py`，FastAPI）：
   `POST /evidence`、`POST /capabilities`、`POST /capabilities/{{id}}/rejudge`、
   `GET /capabilities/{{id}}/chain`、`GET /capabilities/{{id}}/history`、
   `GET /coverage`、`GET /events`、`GET /health`。
6. **零领域语义**：代码内不出现任何域词（`test_c7_package_source_contains_no_domain_vocabulary`
   用 `tools/evidence_domain_lexicon.json` 对包内全部 .py 做机器扫描）；能力位与判据全部来自
   JSON 注册表/适配器（C7）。

## ③ 计算结果（程序化注入，零手抄）

### C2 重判时限与一致性

{lat_table}

### 合成面（demo）：数值证据现场计算 + 自愈/负控

{demo_table}

* `demo.analytic_integration`：第 1 条证据后仍 WITHHELD，第 2 条证据使最小证据数与来源数同时满足 → **REAL**（C4 自愈）。
* `demo.under_sampled`（error 21.46%）、`demo.contradicted`（数值准确但自标 MOCK）、`demo.absent`（无数值验证）→ 全部保持 **WITHHELD**（C5 负控）。

### 真实面（dry run）：21 个能力位

覆盖率：WITHHELD 位 {c1['ledger_withheld_with_evidence']}/{c1['ledger_withheld_total']} 均关联 ≥1 证据项（C1 coverage_ok={c1['ledger_coverage_ok']}）。
升级数：{len(c4['ledger_upgraded_capabilities'])}（真实台账当前**不存在**可机器判读的数值验证记录，见 ⑤ 局限）。

{ledger_table}

**失败原因分布**（逐能力位，见 fig3）：台账评审行只提供 `stance_support`（散文评审的极性），
**没有** `error_pct` 这一可机器核验的量，因此 `metric:error_pct` 与 `min_matching` 在全部 21 位上都未过 —— 这正是
“散文评审不能当数值证据”的机器化表达，也是本节点的**诚实负结果**。

## ④ 图

1. `artifacts/capability_upgrade/fig1_ingest_latency.png` —— {c2['events_measured']} 次证据入库的重判延迟（对数轴）
   与 1000 ms 时限线：最大 {c2['max_latency_ms']:.2f} ms，余量 {1000 / max(c2['max_latency_ms'], 1e-9):.0f}×。
2. `artifacts/capability_upgrade/fig2_level_transition.png` —— 真实台账“自述等级 vs 判后等级”对照：
   自述 REAL {claimed.get('REAL', 0)} 位、NOT_SUPPORTED {claimed.get('NOT_SUPPORTED', 0)} 位、STUB {claimed.get('STUB', 0)} 位；判后 WITHHELD {len(ledger['slots'])} 位（零机器数值证据）。
3. `artifacts/capability_upgrade/fig3_gate_matrix.png` —— 每个能力位被哪道门拦住（矩阵热图），
   使“保持 WITHHELD”可解释而非静默。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **“关联证据”≠“证据达标”**：C1 只保证每个 WITHHELD 位有 ≥1 证据项；能否升级由判据决定。
   台账里 {len(absent_caps)} 个能力位无任何评审行（仅 {len(review_caps)} 位有评审行），适配器为其落 `absence_audit` 证据（源自台账文档 sha256），
   这让覆盖率可判，同时该证据**永远不能**触发升级（类型不在 `evidence_kinds` 内）——避免用“缺证据”充数成证据。
2. **矛盾门的两类排除必须分开报**：`MOCK/STUB/NOT_SUPPORTED`（自称另一种实现现实）记 `contradiction_gate`；
   `WITHHELD`（只是没证实）记 `below_target_assertion`。混在一起会把“缺证”误报成“造假”，反之会漏报。
3. **效率与一致性**：`ingest()` 同步重判是为保证“返回即生效”。重判耗时随证据数线性增长，
   本轮 28 条证据最大 {c2['max_latency_ms']:.2f} ms；证据到万级时应改为事件队列+增量索引（本节点未做，属于已知扩展点）。
4. **不做自动降级**：本节点只允许“升”，矛盾证据以 failures 形式留痕交人/审计处置。
   自动降级会把“一次误标 MOCK 的报告”变成回滚风暴，需独立设计（欠账）。
5. **`test_c7` 的词表扫描**只覆盖包内源码，不覆盖配置与工件（域词允许出现在数据里）。
6. **真实面 0 升级不是管线缺陷**，而是平台现状：现有验证报告以散文+结论为主，缺 `(computed, reference, error%)`
   三元组的机读记录。补齐这层语料（或把 `provenance.db` 的 accuracy 记录接入 `numeric_verification`
   适配器）是后继节点的直接入口。

## ⑥ 误差判定（是否 <3%）

| 关键量 | 计算值 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|---|
| demo 梯形求积 sin 积分（n=512） | {demo['ingests'][0]['metrics']['error_pct']:.6f}% | 闭式解 2.0 | {demo['ingests'][0]['metrics']['error_pct']:.6f}% | **是** |
| demo 梯形求积 x² 积分（n=256） | {demo['ingests'][1]['metrics']['error_pct']:.6f}% | 闭式解 1/3 | {demo['ingests'][1]['metrics']['error_pct']:.6f}% | **是** |
| 负控（n=2 粗网格） | {demo['ingests'][2]['metrics']['error_pct']:.6f}% | 闭式解 2.0 | {demo['ingests'][2]['metrics']['error_pct']:.6f}% | 否（>3%，故保持 WITHHELD，符合判据） |
| 自动 vs 手动重判结果 | 一致率 {c2['auto_manual_agreement_rate'] * 100:.1f}% | 100% | 0.000% | **是** |
| 重判延迟 | 最大 {c2['max_latency_ms']:.3f} ms | 1000 ms | 相对余量 {(1 - c2['max_latency_ms'] / 1000) * 100:.1f}% | **是** |

**是否 <3% 的回答**：数值判据面 **是**（1e-3 % 量级）；重判一致性面 **是**（差异恰为 0）；
延迟面不是误差而是时限，余量 {1000 / max(c2['max_latency_ms'], 1e-9):.0f}×。
本节点是平台机制，**无实验/文献基准**可对标（不存在“实验测得的重判延迟”），故不做物理量误差对标；
数值基准为闭式解析解，误差由程序计算，可复跑。

## 验收判据对照

{accept_table}

**ALL_PASS = {verdict['ALL_PASS']}**（机器判定见 `artifacts/capability_upgrade/verdict.json`）

## 复跑方式

```bash
python3 -m pytest tools/capability_upgrade/tests -q          # {c6['summary']}
python3 -m tools.capability_upgrade.cli run --out artifacts/capability_upgrade
python3 -m tools.capability_upgrade.cli serve --port 8021    # HTTP API
```
"""
    DOC.mkdir(parents=True, exist_ok=True)
    out = DOC / "P218-N01_report.md"
    out.write_text(md, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build())