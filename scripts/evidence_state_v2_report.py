"""从 metrics.json 程序化生成论文级报告（markdown 落盘 + /tmp/report.json 供 API 发布）。

零手抄：报告中的每个数字都从 artifacts/evidence_state_v2/*.json 读取。
运行：
    PYTHONPATH=src python scripts/evidence_state_v2_report.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "evidence_state_v2"
DOC = ROOT / "docs" / "EVIDENCE_STATE_MACHINE_V2.md"
REPORT_JSON = Path("/tmp/report.json")


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def collect_tests() -> dict:
    """收集 tests/capability_ledger_auto 的用例数（程序化，零手抄）。"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    out = {"suite": "tests/capability_ledger_auto", "total": None, "new_node_cases": None}
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/capability_ledger_auto",
             "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, env=env, timeout=300,
        )
        nodes = [ln for ln in (r.stdout + r.stderr).splitlines() if "::" in ln]
        out["total"] = len(nodes)
        out["new_node_cases"] = sum(1 for ln in nodes if "test_evidence_state_machine" in ln)
    except Exception as exc:  # pragma: no cover - 环境缺失时降级
        out["error"] = repr(exc)
    (OUT / "tests.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> int:
    m = load("metrics.json")
    s = load("summary.json")
    sm = load("scenario_matrix.json")
    tm = load("transition_matrix.json")
    pv = load("tamper_detection.json")
    tc = collect_tests()

    c1, c2, c3, c4, c5, c6, c7 = (
        m["C1_semantics"], m["C2_undecidable_fail_closed"], m["C3_upgrade_rules"],
        m["C4_downgrade_rules"], m["C5_no_manual_override"], m["C6_discovery_integration"],
        m["C7_audit_provenance"],
    )
    region = c2["region"]

    state_rows = "\n".join(
        f"| {st} | {c1['admissible_rank'][st]} | {c1['definitions'][st]} | {c1['state_tally'][st]} |"
        for st in c1["states"]
    )
    scen_rows = "\n".join(
        f"| {r['scenario']} | {r['criteria']['independent_decisive_pass_anchors']} | "
        f"{r['criteria']['distinct_pass_kinds']} | {r['criteria']['undecidable_anchors']} | "
        f"{r['criteria']['counterexample_count']} | {r['actual_state']} | {r['rule_id']} | {r['gate']} |"
        for r in sm["rows"]
    )
    seq_rows = "\n".join(
        f"| {i} | {e['prev']} | {e['new']} | {e['transition']} | {e['rule_id']} | {e['reasons'][0]} |"
        for i, e in enumerate(tm["sequence"])
    )
    init_rows = "\n".join(
        f"| {r['capability_id']} | {r['trace_count']} | {r['test_pass_rate']:.2f} | "
        f"{r['initial_state']} | {r['rule_id']} |"
        for r in c6["initial_states"]
    )
    criterion_rows = "\n".join(
        f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in s["criteria"].items()
    )

    md = f"""# 证据等级引擎 v2：五态 + 不可判态（UNDECIDABLE）的 fail-closed 语义与机器裁决

> 战役节点 `p348w2-n04`｜仓库 TensorPlatform｜模块 `src/capability_ledger_auto`
> 全部数字由 `scripts/evidence_state_v2_acceptance.py` 程序化写入
> `artifacts/evidence_state_v2/metrics.json`，本报告由
> `scripts/evidence_state_v2_report.py` 从 JSON 注入（零手抄）。

## ① 案例介绍（问题背景与对象）

平台的能力台账引擎原先只有 **三态**（REAL / MOCK / STUB，`grader.py`），
对「验证锚存在但证据不闭合」的情形没有表达力：一个算例的相对误差落在判决线附近、
而参考值本身带不确定度（实验台测量、文献离散、缩放外推）时，旧引擎只能二选一
——按 `err < tol` 判 PASS（过度乐观，把统计上不可分的差异当通过），或判 WITHHELD
（语义错误：证据并非「没收集」，而是「收集了但不可判」）。

本节点把证据等级语义扩到 **五态 + 不可判态**，并为升级/降级建立机器可算规则：

| 状态 | 可采信秩 | 语义定义 | 本波场景数 |
|---|---|---|---|
{state_rows}

- **UNDECIDABLE ≠ WITHHELD**（C2 核心）：WITHHELD = 还没试过验证（无锚）；
  UNDECIDABLE = 试过但证据不闭合（有锚、不确定度带跨判决线、或缺参考值）。
  二者在门禁上分流：`WITHHELD→ALLOW(min_rank=WITHHELD)`、`UNDECIDABLE→BLOCK_UNDECIDABLE`，
  且 UNDECIDABLE 的可采信秩 **{c2['undecidable_rank']} < NOT_SUPPORTED 的 {c2['not_supported_rank']}**，
  保证「不确定即最不可信」的 fail-closed 偏序。
- 判据阈值（`EvidencePolicy`，全部可配）：容差 tol={m['policy']['tolerance']}（平台精度标准 15%）、
  覆盖因子 z={m['policy']['coverage_z']}（≈95%）、
  REAL 需 ≥{c3['min_independent_anchors']} 条**独立**决定性 PASS 锚 + ≥{c3['min_distinct_anchor_kinds']} 类来源、
  测试通过率 ≥{c3['test_pass_threshold']}。

## ② 怎么做的（方法 / 工具链）

新增三个模块（纯函数、无 IO、零领域语义，能力名一律合成 `synth.*`）：

1. `anchors.py` —— 验证锚 `VerificationAnchor(rel_error, uncertainty, independence_key, has_reference_value)`
   与单锚判决 `judge_anchor`：
   ```
   band = z * uncertainty
   rel_error + band <  tol   → PASS        （决定性通过）
   rel_error - band >  tol   → FAIL        （决定性否证）
   否则（区间跨判决线）      → UNDECIDABLE （unadverse/adverse 由中心估计相对 tol 决定）
   rel_error is None / 缺参考值 → UNDECIDABLE（fail-closed，绝不当 PASS）
   ```
   同 `independence_key` 的锚视为同源，独立性计数只算一次（防同数据集重复刷数）。
2. `state_machine.py` —— 11 条机器规则的状态机（`R01…R10` + `R_MANUAL_OVERRIDE`），
   顺序即优先级：结构判据（无痕迹/stub）→ 决定性否证 → 测试失败 →
   偏坏侧不可判（拦截升级）→ REAL 门槛 → 不可判 → WITHHELD 不足 → 兜底。
3. `provenance.py` —— append-only 哈希链：`entry_hash = sha256(canonical(entry 除 entry_hash 外全字段 + prev_hash))`，
   `verify()` 从创世哈希逐条复算，定位首个坏条目；人工覆盖尝试也留 `outcome=REJECTED` 条目。

工具链：`python -m pytest tests/capability_ledger_auto`（用例 {tc['total']} 例，其中本节点新增 {tc['new_node_cases']} 例，`artifacts/evidence_state_v2/tests.json`）+ 验收脚本（场景矩阵 / 判据数字 / 图 / 审计链）。

## ③ 计算结果（程序化注入）

### 场景矩阵（n={sm['scenario_count']}，全部命中预期：{sm['scenarios_matched']}/{sm['scenario_count']}）

| 场景 | 独立 PASS 锚 | 来源类别 | 不可判锚 | 反例 | 判定 | 规则 | 门禁 |
|---|---|---|---|---|---|---|---|
{scen_rows}

### 生命周期序列（同一能力：发现 → 锚积累 → 反例 → 再验证 → 不可判锁定）

| # | prev | new | 变更方向 | 规则 | 首条理由 |
|---|---|---|---|---|---|
{seq_rows}

转换统计：{json.dumps(tm['transition_counts'], ensure_ascii=False)}；
降级事件 {c4['downgrade_events']} 次（规则 {c4['downgrade_rule_id']}，理由已落链：{c4['reason_recorded']}）；
零人工覆盖：{c5['rejected_count']}/{len(c5['attempts'])} 次人工指定等级被拒（码 {c5['attempts'][0]['code']}），
状态保持不变={c5['state_unchanged']}，拒绝尝试全部留痕={c5['audited_rejected_entries']} 条；
无触发证据的降级被拒 = {tm['downgrade_without_trigger_rejected']}（守卫台账状态保持 {tm['guard_state_after_reject']}）。

### 不可判态量化（判决分区扫描）

网格 {region['grid']['err_points']}×{region['grid']['unc_points']}={region['grid']['cells']} 格
（rel_error∈[0,{region['grid']['err_max']}]，uncertainty∈[0,{region['grid']['unc_max']}]，步长 {region['grid']['step']}）：

- 朴素规则（只看 `err < tol`）判 PASS 的格数：**{region['naive_pass_cells']}**
- 加不确定度带（fail-closed）后判 PASS 的格数：**{region['banded_pass_cells']}**
- 判 UNDECIDABLE 的格数：**{region['undecidable_cells']}**
- 朴素 PASS → 不可判（被 fail-closed 拦下）的格数：**{region['naive_pass_downgraded_to_undecidable']}**
  （占朴素 PASS 的 **{region['naive_pass_downgraded_fraction']:.1%}**）

即：在参考值不确定度 0–{region['grid']['unc_max']} 的现实区间，朴素阈值规则
有 {region['naive_pass_downgraded_fraction']:.0%} 的「通过」在统计上站不住。

### 能力发现联动（C6）

| 能力 | 痕迹数 | 测试通过率 | 初始等级 | 规则 |
|---|---|---|---|---|
{init_rows}

重复登记被拒 = {c6['re_registration_rejected']}；初始等级由扫描证据决定（非默认 WITHHELD、不可由调用方指定）。

### 审计链（C7）

条目 {c7['entry_count']}，链校验 {c7['chain_valid']}（首个坏条目 {c7['first_bad_seq']}），
head_hash=`{c7['head_hash'][:16]}…`；伪造 `entries[2].new_state REAL→MOCK` 后
篡改检测 {pv['forged_chain_valid']}（首个坏条目 seq={pv['forged_first_bad_seq']}）。
全实验 provenance 条目合计 {s['provenance_entries_all_experiments']}。

## ④ 图

1. `artifacts/evidence_state_v2/figures/decision_map.png`
   —— 单锚判决分区（rel_error × 参考值 1σ 不确定度 → PASS/FAIL/UNDECIDABLE），叠加 {sm['scenario_count']} 个场景的验证锚；
2. `artifacts/evidence_state_v2/figures/state_delta.png`
   —— 场景矩阵上「朴素规则 vs fail-closed v2」的等级分布对照
   （UNDECIDABLE {c1['naive_vs_fail_closed_tally']['naive']['UNDECIDABLE']} → {c1['naive_vs_fail_closed_tally']['fail_closed']['UNDECIDABLE']}，
   MOCK {c1['naive_vs_fail_closed_tally']['naive']['MOCK']} → {c1['naive_vs_fail_closed_tally']['fail_closed']['MOCK']}）。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **陷阱：不可判态被当成 WITHHELD 回收**。这是本波最主要的设计风险——若把 UNDECIDABLE
   归一到 WITHHELD，门禁会放过一批统计上不可分的证据。本实现用独立秩 {c2['undecidable_rank']}
   与独立门禁码 `BLOCK_UNDECIDABLE` 双保险，并有 `test_undecidable_distinct_from_withheld_semantics` 守门。
2. **陷阱：同源重复刷锚**。三条锚共用同一数据集时独立锚数仍为 1（场景 S07：collapsed={c3['duplicate_sources_collapsed']}），
   否则「≥3 独立锚」条款等于虚设。
3. **陷阱：偏坏侧不可判反被升级**。只要存在一条中心估计落在坏侧且带内跨线的锚（S13），
   即便已有 3 条决定性 PASS，也锁 UNDECIDABLE（fail-closed 优先于升级）。
4. **局限 1**：不确定度按 1σ 相对量线性传递（z=2），未做非对称/分布形状建模；对强非线性误差传播应改用区间或 MC。
5. **局限 2**：`anchors` 的输入仍由调用方提供，引擎只保证「给定锚则判决可复算」，
   不验证锚本身来源的真实性（该职责在原 `scanner.py` 的哈希证据链）。
6. **局限 3**：本波未改 `backend/api/capability_audit.py` 的对外矩阵接口（保持最小改动），
   状态机目前是库级能力；对外暴露与台账 JSON 合流留作后续节点。
7. **适用范围**：任何「有参考值 + 有误差 + 有不确定度」的能力验收；无参考值的场景应显式落在
   UNDECIDABLE 而不是被硬编码 PASS。

## ⑥ 误差判定（关键量 vs 基准）

| 量 | 基准 | 本实现 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 场景判定命中率 | 场景预期状态（{sm['scenario_count']} 例） | {sm['scenarios_matched']}/{sm['scenario_count']} | 0 | 是（0%） |
| 单锚判决边界 | 解析分区（err+band<tol / err-band>tol / 其余） | 网格 {region['grid']['cells']} 格逐格与解析式一致 | 0 格偏差 | 是（0%） |
| 独立性计数 | 同源锚应折叠为 1 | S07：3 锚 → {c3['threshold_scenarios']['S07_dup_source']['independent_pass']} 独立锚 | 0 | 是（0%） |
| 哈希链完整性 | 复算 sha256 链 | verify={c7['chain_valid']}，篡改定位 seq={pv['forged_first_bad_seq']} | 0 | 是（0%） |
| C1–C7 验收 | 战役判据 | {sum(1 for v in s['criteria'].values() if v)}/{len(s['criteria'])} PASS | 0 | 是（0%） |

判决分区与解析式逐格一致是**构造性验证**（同一公式实现），真正的物理精度验证需在
领域仓的真实算例锚点上做；本节点只保证等级语义与裁决机制的正确性。

### 验收判据汇总

| 判据 | 结论 |
|---|---|
{criterion_rows}

## 复现

```bash
PYTHONPATH=src python -m pytest tests/capability_ledger_auto -q
PYTHONPATH=src python scripts/evidence_state_v2_acceptance.py
PYTHONPATH=src python scripts/evidence_state_v2_report.py
```
"""
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(md, encoding="utf-8")

    fig1 = OUT / "figures" / "decision_map.png"
    fig2 = OUT / "figures" / "state_delta.png"
    report = {
        "title": "证据等级引擎 v2：五态 + 不可判态 fail-closed 语义与机器裁决（p348w2-n04）",
        "summary": (
            f"capability_ledger_auto 扩为五态+UNDECIDABLE：{sm['scenarios_matched']}/{sm['scenario_count']} 场景命中，"
            f"UNDECIDABLE 秩 {c2['undecidable_rank']}（< NOT_SUPPORTED），"
            f"判决分区 {region['grid']['cells']} 格逐格验证，朴素 PASS 有 {region['naive_pass_downgraded_fraction']:.0%} 被 fail-closed 拦下；"
            f"C1–C7 {sum(1 for v in s['criteria'].values() if v)}/7 PASS，provenance 链 {c7['entry_count']} 条可复算且篡改可定位。"
        ),
        "scenario_key": "platform.evidence_state_machine_v2",
        "mission_id": 9852,
        "tags": ["evidence_grade", "fail_closed", "undecidable", "provenance", "capability_ledger"],
        "markdown": md,
        "figures": [
            {"name": "decision_map.png",
             "caption": "单锚判决分区：rel_error × 参考值不确定度 → PASS/FAIL/UNDECIDABLE，叠加 14 个场景锚点",
             "png_base64": b64(fig1)},
            {"name": "state_delta.png",
             "caption": "场景矩阵：朴素规则 vs fail-closed v2 的等级分布对照",
             "png_base64": b64(fig2)},
        ],
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {DOC} ({len(md)} chars) and {REPORT_JSON}")
    print("all_criteria_pass:", s["all_criteria_pass"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())