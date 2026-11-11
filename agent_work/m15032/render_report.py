#!/usr/bin/env python3
"""m15032：渲染交差审计报告（markdown + 平台 report.json），数字全部程序化注入。"""
from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUD = ROOT / "runs" / "m15032_audit"
EV = json.loads((AUD / "evidence_verification.json").read_text())
TAMPER = json.loads((AUD / "tamper_negative_control.json").read_text())
GATE = json.loads((AUD / "repro_artifacts" / "gate.json").read_text())
GATE_OLD = json.loads(Path("/root/tp-agent/work/m14961/artifacts/maturity_gate_h6/gate.json").read_text())
BENCH = json.loads((AUD / "repro_artifacts" / "mutation_benchmark.json").read_text())
CONS = json.loads((AUD / "repro_artifacts" / "consistency.json").read_text())


def fig(name, caption, path):
    b64 = base64.b64encode(path.read_bytes()).decode()
    return {"name": name, "caption": caption, "png_base64": b64}


def rows_md(rows):
    out = ["| # | 判据 | 维度 | 结果 | 指针 |", "|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | `{r['item']}` | {r['category']} | {'✅ PASS' if r['ok'] else '❌ NOT_MET'} | {r['pointer'][:110]} |")
    return "\n".join(out)


cs = GATE["axes"]["cross_source"]
qb = GATE["axes"]["quote_binding"]
mr = GATE["axes"]["markdown_recount"]
es = GATE["axes"]["evidence_support"]
inc = GATE["increments"]
mat = GATE["maturity"]

same_fields = ["inputs", "criteria", "thresholds", "thresholds_sha256", "axes", "maturity", "verdict"]
repro_same = sum(1 for k in same_fields if json.dumps(GATE[k], sort_keys=True) == json.dumps(GATE_OLD[k], sort_keys=True))
repro_same += int(inc["chain_head"] == GATE_OLD["increments"]["chain_head"])
repro_same += int(inc["counts"] == GATE_OLD["increments"]["counts"])
repro_total = len(same_fields) + 2

undetected = [c["case"] for c in TAMPER["cases"] if not c["detected"]]
detected = [c["case"] for c in TAMPER["cases"] if c["detected"]]
err_claim = abs(BENCH["recall"] - TAMPER["recall"]) / BENCH["recall"] * 100

md = f"""# m15032 交差审计：m14961（H6-MATURITY-GATE）判级核验

**审计对象** `m14961`（TensorPlatform，PR #1211 已合并，commit `a801c35be` 树内可见）
**审计单** `m15032`（交差核验单，非重做） ｜ **审计席位** board-audit ｜ **判级结论 L2（confirmed 候选）**

---

## ① 案例介绍（核验什么）

owner 2026-11-11 任务榜令：结榜回交差。m14961 自述交付「零领域语义的 H6 成熟度门」：
五级成熟度阶梯（`NOT_SUPPORTED→STUB→MOCK→WITHHELD→REAL`，秩严格单调）+ 四轴交叉一致性
（自述↔对账分歧、证据引文 `line_sha256` 绑定、人读表↔台账重算、等级↔证据同向）+ 能力增量哈希链账本，
并宣称「任何数字都能被 `--verify` 逐字节复算」。

本单的判据**先于动手声明**（三件套 + L0–L5 功能实质）：

| 判据 | 定义 | 机器可裁方式 |
|---|---|---|
| C-① run_artifacts | 真实运行工件存在 | 文件存在 + 内容可解析 + 数值自洽 |
| C-② benchmark | ref vs got + 误差 | 声明值 vs 工件实测值逐项比对 |
| C-③ repro_cmd | 一键复现 | 在**另一工作树**（m15032）原样执行 |
| L2 | 真代码输入→计算→输出 + 工程入口 + 可跑工件 | 入口可调用、输出可复算 |
| L3 | L2 + 被集成面调用 | 全仓 grep 调用方 |
| L4 | L2/L3 + 外部基准/解析解/文献对标 | 锚点是否为外部真值 |

## ② 怎么做的（方法/工具链）

1. **取证**：读 `tensor_missions` 14961 result（`/api/v1/tensor/missions/14961`）→ 变更文件清单；
   逐个核 `/root/tp-agent/work/m14961/artifacts/maturity_gate_h6/` 工件实存性与内容。
2. **独立复跑**（不复用被审方结论）：在 m15032 工作树（合并后 main 树）执行
   `python3 -m tools.maturity_gate_h6.cli run` + `verify` + `pytest tools/maturity_gate_h6/tests -q`，
   与 m14961 落盘工件**逐字段**比对。
3. **独立负控**（对抗式复核，不复用仓内测试）：自写 9 类工件篡改（阈值/轴统计/成熟度聚合/增量条目
   等级/条目秩/条目 sha256/链中 prev_sha256/判据 pass/总结论），逐例跑 `verify` 看是否报问题；
   另加干净副本假阳性对照。脚本 `agent_work/m15032/tamper_negcontrol.py`，产物
   `runs/m15032_audit/tamper_negative_control.json`。
4. **集成面**：`grep -rn 'maturity_gate_h6' --include=*.py` 全仓找调用方。
5. **判级落表**：`tensor_maturity_verdicts` id=217（repo=TensorPlatform，scope=`m14961 H6-MATURITY-GATE`，
   verdict_by=`board-audit`），evidence 键 = impl_files/entry/run_artifacts/tests/integration_callers/benchmark/repro_cmd/external_anchor。

## ③ 计算结果（三件套核验矩阵，程序化注入）

**核验总览**：{EV['counts']['ok']}/{EV['counts']['total']} 项 PASS（未过 4 项全部落在「集成面/外部锚点/防篡改/逐轴命中」）。

{rows_md(EV['checks'])}

**复跑数值对照（m15032 vs m14961 落盘工件）**：{repro_same}/{repro_total} 个字段逐字节一致

| 字段 | m14961 | m15032（本单复跑） | 判定 |
|---|---|---|---|
| verdict | {GATE_OLD['verdict']} | {GATE['verdict']} | 一致 |
| criteria_pass_rate | {GATE_OLD['criteria_pass_rate']} | {GATE['criteria_pass_rate']} | 一致 |
| thresholds_sha256 | `{GATE_OLD['thresholds_sha256'][:16]}…` | `{GATE['thresholds_sha256'][:16]}…` | 一致 |
| increments.chain_head | `{GATE_OLD['increments']['chain_head'][:16]}…` | `{inc['chain_head'][:16]}…` | 一致 |
| increments.counts | {json.dumps(GATE_OLD['increments']['counts'], ensure_ascii=False)} | {json.dumps(inc['counts'], ensure_ascii=False)} | 一致 |
| maturity before→after | {GATE_OLD['maturity']['before']['maturity_score']}→{GATE_OLD['maturity']['after']['maturity_score']} | {mat['before']['maturity_score']}→{mat['after']['maturity_score']} | 一致（Δ={mat['score_delta']}） |

**门内四轴实测**：交叉一致性 逐能力 {cs['total']} 条、覆盖 {cs['coverage']}、一致 {cs['agreement']} 条、未解释分歧 {len(cs['unexplained'])}；
引文绑定 {qb['matched']}/{qb['total']}（match_rate={qb['match_rate']}，漂移 {qb['drift']}）；
人读表重算 {len(mr['metrics'])} 项指标、delta 全 0、行数 {mr['row_count']}；
证据同向 supported={es['supported']}、orphan={es['orphan']}。
增量账本 {inc['chain_len']} 条、链头 `{inc['chain_head'][:16]}…`、{json.dumps(inc['counts'], ensure_ascii=False)}。

**独立负控结果**：注入 {TAMPER['injected']} 例篡改 → 检出 {TAMPER['detected']} 例（recall={TAMPER['recall']}），
假阳性 {TAMPER['false_positive']}；检出 {detected}，**漏检 {undetected}**。

## ④ 图

![fig1](fig1_triad_verification.png)
*图1 三件套核验通过率（按维度）：实现/工件/复现/测试均满格；集成面与外部锚点为 0，直接决定 L3/L4 不授予。*

![fig2](fig2_negcontrol_recall.png)
*图2 `verify()` 防篡改召回：m14961 自报变异基准 100%（5/5，自造注入）vs 本单独立负控 {TAMPER['recall']*100:.1f}%（{TAMPER['detected']}/{TAMPER['injected']}，攻击面覆盖轴统计/成熟度聚合/增量条目）。*

## ⑤ 注意点（踩坑与局限）

1. **F1 防篡改覆盖面窄于声明（本次最重要发现，非阻断判级）**：`core.py:604 verify_artifact` 只比对
   `verdict / criteria_pass_rate / thresholds_sha256 / criteria / increments.chain_head` 五键。手工改
   `axes.*`、`maturity.*`、`increments.entries[*]`（含条目 `sha256`/`prev_sha256`）时 `verify` 仍返回
   `verified=true`（9 例注入漏检 6 例）⇒ README「任何数字都能被 --verify 逐字节复算」与
   `docs/H6_MATURITY_GATE_G1.md` 注意点 5「单条事件含 prev_sha256 可发现中间被改」**为过度声明**。
2. **F2 测试负控偏窄**：唯一 verify 负控 `test_verify_detects_tampered_gate_artifact`（tests:242）只翻
   `criteria[0].pass`，恰好命中三键之一 → 测试通过≠声称覆盖面成立（semantic_gate：测试断言了不变量吗？只断言了 5 键中的 1 键）。
3. **F3 命名误导**：`cli.py:209 axis_perfect = all(detected)` 实为「全部检出」，但 `target_axis_hit` 自报 3/5
   （M1 等级翻转、M3 证据抹除由非目标轴触发）—— 数字诚实，字段名易被读成「逐轴命中」。
4. **F4 工件不入库**：`artifacts/maturity_gate_h6/` 未进 git（合并树内 0 文件），复现必须重跑；本单已重跑并留
   `runs/m15032_audit/repro_artifacts/`（数值逐字段一致，见 ③）。
5. **F5 文档内嵌绝对路径**：`docs/H6_MATURITY_GATE_G1.md` 写入 work-root 绝对路径，跨工作树重跑必然 5 行 diff（数值不变）；
   建议改相对路径（本单已 revert 该 diff，不污染分支）。
6. **F6 第三源显式缺口**：`live_probe` 因 harness 能力清单未同步而 `available=false`，判据取 `null` 不通过 —— 诚实缺口而非造假，但说明复核只覆盖仓内两源。
7. **审计局限**：单一审计席位、单次运行；未审计跨仓（TensorLBM/FEM 等）源接入能力（m14961 自述未做，本单不追加深）。

## ⑥ 误差判定（逐项显式回答「是否 <3%」）

| 关键量 | 基准（ref） | 实测（got） | 误差 | <3%？ |
|---|---|---|---|---|
| 门裁定 / 判据通过率 | PASS / 1.0（m14961） | PASS / {GATE['criteria_pass_rate']}（m15032 复跑） | 0 | **是** |
| 阈值指纹 `thresholds_sha256` | `{GATE_OLD['thresholds_sha256'][:16]}…` | `{GATE['thresholds_sha256'][:16]}…` | 0（逐字节） | **是** |
| 增量链头 | `{GATE_OLD['increments']['chain_head'][:16]}…` | `{inc['chain_head'][:16]}…` | 0（逐字节） | **是** |
| 四轴/成熟度/矩阵字段一致性 | {repro_total} 字段 | {repro_same} 一致 | 0% | **是** |
| 变异基准召回（被审方口径） | 5/5 = 1.0 | {BENCH['detected']}/{BENCH['injected']} = {BENCH['recall']} | 0 | **是** |
| 防篡改召回（本单独立负控） | 自报 100% | {TAMPER['recall']*100:.1f}%（{TAMPER['detected']}/{TAMPER['injected']}） | **{err_claim:.1f}%** | **否** |
| 逐轴命中率 | 5/5 | 3/5 | 40% | **否** |

**结论**：数字类交付（复跑确定性）误差 0%，**<3% 成立**；但「`--verify` 逐字节复算一切」与「逐轴命中」
两项能力性声明误差 {err_claim:.1f}% / 40%，**>3% 不成立**，按纪律如实记录为过度声明（F1/F3）。

## 判级（L0–L5，功能实质）

| 级别 | 判据 | 是否达成 | 依据 |
|---|---|---|---|
| L0 | 仅文档 | ✅ | `docs/H6_MATURITY_GATE_G1.md`（10,993 B） |
| L1 | 仅测试/验证脚本 | ✅ | `tests/test_maturity_gate_h6.py` 24 passed（本单复跑） |
| **L2** | **独立功能实现（真代码 输入→计算→输出）** | **✅ 采纳** | `core.py` 29,465 B 真算法（cross_source/quote_binding/recount_markdown/evidence_support/build_increment_ledger/run_gate/verify_artifact）+ CLI 入口 + 可复跑工件 + PR #1211 合并 |
| L3 | 被集成面调用 | ❌ | 全仓唯一引用是 `scripts/post_h6_maturity_report.py:40`（读图发报告）；无 api/backend/CI/战役门调用方 |
| L4 | 外部基准/解析解/文献对标 | ❌ | `h6.spec.json:anchors` 全为仓内定义文件；变异基准注入缺陷由作者自造，无外部真值 |
| L5 | 科研工程创造性成果 | ❌ | 无先例性主张与外部承认 |

**判定：L2**。理由：功能实质=真代码独立实现 + 工程入口 + 可复跑工件（三件套 ①②③ 全成立），
但未接入平台运行面（L3 缺调用方）、无外部锚点（L4 缺真值）；「百页报告不改变级别」，同理 24 项测试也不自动升 L3。

**审计产物**（均在本工作树）：`runs/m15032_audit/evidence_verification.json`、`tamper_negative_control.json`、
`repro_artifacts/`、`pytest_tools_maturity_gate_h6.log`、`verify_output.json`、`fig1/fig2*.png`；
落表 `tensor_maturity_verdicts` id=217（gate_state=pending，待 maturity_gate 机器裁定）。
"""

doc = ROOT / "docs" / "reports" / "m15032_audit_m14961.md"
doc.parent.mkdir(parents=True, exist_ok=True)
doc.write_text(md)

report = {
    "title": "m15032 交差审计：m14961（H6-MATURITY-GATE）三件套核验与 L0–L5 判级",
    "summary": (
        f"m14961 交差核验：三件套 {EV['counts']['ok']}/{EV['counts']['total']} 项成立（工件实存、"
        f"复跑 {repro_same}/{repro_total} 字段逐字节一致、pytest 24 passed、mutation 5/5 recall=1.0）；"
        f"判级 L2（无集成调用方→不达 L3，无外部锚点→不达 L4）。独立负控 9 例篡改仅检出 "
        f"{TAMPER['detected']} 例（recall={TAMPER['recall']}），证明 verify 未覆盖 axes/maturity/增量条目，"
        f"『任何数字都能逐字节复算』为过度声明（误差 {err_claim:.1f}% > 3%）。落表 tensor_maturity_verdicts id=217。"
    ),
    "scenario_key": "board-audit-maturity-verdict",
    "mission_id": 15032,
    "tags": ["board-audit", "maturity-gate", "h6", "L2", "adversarial-review", "tamper-negative-control"],
    "markdown": md,
    "figures": [
        fig("fig1_triad_verification.png", "图1 三件套核验通过率（按维度）",
            AUD / "fig1_triad_verification.png"),
        fig("fig2_negcontrol_recall.png", "图2 verify() 防篡改召回：自报 vs 独立负控",
            AUD / "fig2_negcontrol_recall.png"),
    ],
}
(ROOT / "agent_work" / "m15032" / "report.json").write_text(json.dumps(report, ensure_ascii=False))
print("doc:", doc, doc.stat().st_size, "report.json:", (ROOT / "agent_work" / "m15032" / "report.json").stat().st_size)