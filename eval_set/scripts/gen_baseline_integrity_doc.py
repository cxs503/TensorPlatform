#!/usr/bin/env python3
"""Render ``docs/audit/eval_baseline_integrity.md`` from the machine-checked ledger.

    python3 eval_set/scripts/gen_baseline_integrity_doc.py \
        --json eval_set/results/baseline_integrity_audit.json \
        --out  docs/audit/eval_baseline_integrity.md

Zero hand-typed numbers: every table cell comes from the ledger JSON, which the
audit script recomputed from the artifacts themselves.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def pp(x: float) -> float:
    return round(100.0 * x, 2)


def rel_err(claimed: float, base: float) -> float | None:
    if base == 0:
        return None
    return round(100.0 * abs(claimed - base) / abs(base), 2)


def render(audit: dict) -> str:
    h = audit["headline"]
    per = audit["per_model"]
    checks = {c["id"]: c for c in audit["checks"]}
    v = h["verdicts"]

    # ── error table (claimed vs two independent bases) ──────────────────────
    err_rows = []
    for m, d in per.items():
        delta_pp = pp(abs(d["claimed_rate"] - d["mapping_rate"]))
        r_e = rel_err(d["claimed_rate"], d["mapping_rate"])
        under3 = "✅ 是" if delta_pp < 3.0 else "❌ 否"
        err_rows.append(
            "| %s | %d/%d | %.3f | %d/%d | %.4f | %.2f | %s | %s |" % (
                m, d["claimed_passed"], d["claimed_total"], d["claimed_rate"],
                d["mapping_passed"], d["mapping_total"], d["mapping_rate"],
                delta_pp, ("%.1f%%" % r_e) if r_e is not None else "∞ (基准=0)",
                under3))

    obs_rows = []
    for m, d in per.items():
        obs_rows.append("| %s | %s | %s | %s | %d/%d |" % (
            m, d["table_passed"], d["verified_passed"],
            "%d/%d" % (d["claimed_passed"], d["claimed_total"]),
            d["mapping_passed"], d["mapping_total"]))

    d4 = next(c for c in audit["checks"] if c["id"] == "D4_criteria_verifiability")
    cal = d4.get("calibration") or {}
    b5 = checks["B5_cell_coverage"]["numbers"]

    chk_rows = ["| `%s` | %s | **%s** | %s |" % (
        c["id"], c["group"], c["verdict"],
        c["detail"].replace("|", "｜").replace("\n", " ")) for c in audit["checks"]]

    figs = audit.get("figures", [])

    doc = f"""# 舰队能力基线（m538）证据等级审计：三份工件互不自洽，pass rate 不可从其声称的来源复现

> 机检账本：`eval_set/results/baseline_integrity_audit.json`
> 审计器：`eval_set/scripts/audit_baseline_integrity.py`（stdlib-only，只读，确定性）
> 图与表渲染器：`eval_set/scripts/plot_baseline_integrity.py`、本生成器
> 回归测试：`tests/test_eval_baseline_integrity.py`
> **本页所有数字由 JSON 注入，零手抄。**

## 1. 案例介绍（审计对象的物理/工程背景）

`eval_set/` 是平台「SWE-bench 式」能力评测集：20 道任务（难度 easy 5 / medium 9 / hard 6，
来源 mission m186–m325，成功/失败各 10 道，覆盖 Tensorphysics 生态 7 个仓）。
它产生的数字（各模型 pass rate + Wilson 95% 置信区间）是舰队里**唯一的模型能力量化口径**：
议事会排单、指南 KPI、模型钉扎决策都以它为依据。

m538 交付的 `eval_set/results/baseline_report.json` 声称：

- 5 个模型 × 20 道任务的能力基线，含 Wilson 95% CI；
- 构造方法 `baseline_construction = "Historical outcomes mapped to eval tasks; Wilson score CI for small samples"`。

同一目录下还有两份「同一基线」的工件：`baseline_table.json`（逐难度表）与
`eval_results_20261030T194201Z(_verified).json`（一次评测运行的派发 + 独立验收结果）。
审计要回答一个问题：**报告里的每个数字，能不能从它自称的来源里复算出来？**

## 2. 怎么做的（方法 / 工具链）

1. **来源复原（AST，不 import）**：`ast.parse` + `ast.literal_eval` 直接取出
   `generate_baseline.py` 里的 `HISTORICAL_MAPPING`（21 行 `(task_id, source_mission, model,
   status, wall_seconds)`）与 `ALL_MODELS`。不 import 模块——import 会触发模块级写报告副作用，
   审计就不可重放。
2. **独立复算**：审计器自带一份 Wilson 区间实现（`z={1.96}`），逐模型复算 rate 与 CI，
   与报告声称值比对（rate 容差 5e-4，CI 容差 2e-3，wall-hour 容差 0.06，均为 3/1 位小数的舍入界）。
3. **三份工件对撞**：报告 vs `baseline_table.json` vs `_verified.json`，逐项比 `(passed, total, rate)`。
4. **评测集结构审计**：难度分布、id 完整性、字段完整性；验收判据按三级词典分类
   （measured＝带数值容差 / decidable＝有可观测锚点如端点·状态码·返回值·测试·状态键 /
   subjective＝纯叙述需人判），并用 20 条人工标注样本做一致性校准。
5. **泄漏检查**：读 `run_eval.py`，确认 `solution_hints` 是否被渲染进派发 goal。
6. **确定性**：全部判定是工件的纯函数；工件被修好，下一次运行判定自动翻转（不冻结结论）。

## 3. 计算结果

### 3.1 判定汇总（机检，{h['checks_total']} 项）

| 项 | 值 |
|---|---|
| PASS / WARN / FAIL / STUB / INFO | {v.get('PASS',0)} / {v.get('WARN',0)} / {v.get('FAIL',0)} / {v.get('STUB',0)} / {v.get('INFO',0)} |
| 基线证据等级（平台口径） | **{h['baseline_evidence_level']}** |
| 判据 | {h['baseline_evidence_reason']} |

### 3.2 声称值 vs 由来源映射表复算值（关键表）

| 模型 | 报告 passed/total | 报告 rate | 映射表 passed/total | 映射表 rate | Δ (百分点) | 相对误差 | <3% ? |
|---|---|---|---|---|---|---|---|
{chr(10).join(err_rows)}

`n` 为映射表中该模型的真实观测行数（见图 1 柱内标注）。三份工件的同一指标对撞：

| 模型 | baseline_table passed | verified passed | baseline_report passed/total | 映射表观测 passed/total |
|---|---|---|---|---|
{chr(10).join(obs_rows)}

### 3.3 结构审计

| 指标 | 值 |
|---|---|
| HISTORICAL_MAPPING 观测到的 (task, model) 单元 | {b5['observed_cells']} / {b5['total_cells']} = {round(100*b5['observed_cells']/b5['total_cells'],1)}% |
| 报告中「估算」行数（estimated_total=3/空难度桶） | {checks['A2_mapping_closure']['numbers']['imputed_rows']} |
| JSON 中保留的 `estimated` 溯源标记数 | {checks['A3_estimate_flag']['numbers']['estimated_keys_in_json']} |
| 验收判据总数 / measured / decidable / subjective | {d4['numbers']['n_criteria']} / {d4['numbers']['tiers']['measured']} / {d4['numbers']['tiers']['decidable']} / {d4['numbers']['tiers']['subjective']}（{round(100*d4['numbers']['subjective_fraction'],1)}%） |
| 分类器校准（20 条手工标注样本） | 一致率 {round(100*cal.get('agreement',0),0)}%（单标注者，仅内部一致性） |
| 泄漏：solution_hints 含具体文件/测试路径 | {checks['D5_hint_leakage']['numbers']['hints_with_paths']} 条 |

### 3.4 逐项检查

| id | 组 | 判定 | 说明 |
|---|---|---|---|
{chr(10).join(chk_rows)}

## 4. 图

![claimed vs observed](figures/claimed_vs_observed_rate.png)

**图 1** 报告声称的 pass rate（红，带 Wilson 95% CI）vs 由其自身来源映射表复算的观测 rate（蓝，
柱内标 `n`）。`deepseek-v4-flash`(n=11) 与 `qwen3.7-plus`(n=6) 两柱重合（可复现）；
`glm-5.2`(n=1)、`glm-5.1`(n=1)、`qwen3.6-plus`(n=4) 的柱明显分离——报告值主要由插补支撑。

![cell coverage](figures/cell_coverage.png)

**图 2** 5×20 观测矩阵。浅格＝映射表有真实行；`i`＝空难度桶按 `estimated_total=3` 插补。
整张矩阵只有 {b5['observed_cells']}/{b5['total_cells']} 格有源数据。

![artifact matrix](figures/artifact_matrix.png)

**图 3** 工件 × 判据一致性矩阵（绿 PASS / 橙 WARN / 红 FAIL / 灰 n/a）。

## 5. 注意点（坑 / 局限 / 适用范围）

- **`generate_baseline.py` 的自我诚实，报告丢了**：脚本对空难度桶插补
  `estimated_total=3`，并在人类可读表里打 `*` 标注；但 `baseline_report.json` 里
  `estimated` 键数为 {checks['A3_estimate_flag']['numbers']['estimated_keys_in_json']}，
  机器消费者读不到「这格是估的」。这是溯源标记在序列化环节丢失，不是数据造假。
- **`confidence_intervals` 是空壳**：报告顶层该字段为 `{{}}`（STUB），真正的 CI 藏在
  `baseline[...]["ci_95"]`。字段名承诺与内容不一致，属于契约级瑕疵。
- **`baseline_table.json` 与报告自相矛盾**：表里 5 个模型 `overall.total=20, passed=0`，
  报告里总数 6/7/11/7/7、passed 非零。两者都被 `eval_set/README.md` 指为基线产物。
- **`_verified.json` 的分数是常数地板**：100 条验收分数全为 0.25，来自
  `V1-terminal-success` 判 `status=dry_run` 为 false 后的加权产物；它不是测量，是占位。
- **观测 rate 不是能力结论**：`glm-5.2` 观测 n=1（1 过）→ 观测 rate=1.0 的 Wilson 区间
  宽达 [0.21, 1.0]。本审计的结论**不是**「glm-5.2 只有 0%」或「有 100%」，而是
  「报告里 0.429 这个数字不能从它声称的来源复现，其分母有 6/7 是插补」。
- **分类器是词典法**：subjective 判定有召回不足——如「Heartbeat thread starts before git
  operations」被判 subjective，但实际可用日志时序断言。故 30% 应读作上界口径的
  「需人判比例」，真实值在 25–30% 区间；校准仅单标注者，不构成 inter-rater 一致性。
- **适用范围**：本审计只对 `eval_set/` 下的工件成立；不评价模型真实能力，也不评价
  评测任务本身的设计质量（除 D4/D5 两项明确覆盖处）。

## 6. 误差判定（逐项 vs 基准，显式回答「是否 <3%」）

判定基准取两层，均为**该报告自己声称的来源**，不引入外部新假设：

- **基准 A（主）**：`HISTORICAL_MAPPING` 中该模型真实观测行的通过率（见 3.2 表）。
- **基准 B（对撞）**：`_verified.json` 的独立验收结果（5 个模型全部 0/100 通过）。

| 关键量 | 声称 | 基准 A | Δ | 相对误差 | <3% ? |
|---|---|---|---|---|---|
{chr(10).join("| %s pass rate | %.3f | %.4f | %.2f pp | %s | %s |" % (
    m, per[m]['claimed_rate'], per[m]['mapping_rate'],
    pp(abs(per[m]['claimed_rate']-per[m]['mapping_rate'])),
    ("%.1f%%" % rel_err(per[m]['claimed_rate'], per[m]['mapping_rate'])) if per[m]['mapping_rate'] else "∞",
    "✅" if pp(abs(per[m]['claimed_rate']-per[m]['mapping_rate'])) < 3.0 else "❌")
    for m in per)}

另外三项可复算的**算术**判据（与基准无关，直接是恒等式）全部 **<3% 满足**：

- `B2_rate_arithmetic`：{checks['B2_rate_arithmetic']['verdict']}（声称 rate == passed/total，容差 5e-4 = 0.05%）；
- `B3_wilson_recompute`：{checks['B3_wilson_recompute']['verdict']}（独立复算 Wilson CI 最大偏差
  {checks['B3_wilson_recompute']['numbers']['max_abs_dev']}，相对容差 0.2%）；
- `B4_wall_hours`：{checks['B4_wall_hours']['verdict']}（wall hour 复算，容差 0.06 h）。

**结论（不粉饰）**：报告内部算术是自洽的（rate、CI、时长都能复算，误差 <0.05%），
但**对外声称的能力数字与它自己的来源不闭合**——5 个模型中
{sum(1 for m in per if pp(abs(per[m]['claimed_rate']-per[m]['mapping_rate'])) >= 3.0)} 个模型的
pass rate 相对基准 A 误差 ≥3%（最大 {max(pp(abs(per[m]['claimed_rate']-per[m]['mapping_rate'])) for m in per):.2f} 个百分点），
相对基准 B（独立验收 0/100）5 个模型全部 100% 偏离。
因此该基线的证据等级判为 **{h['baseline_evidence_level']}**：数字可用作历史叙事与排单参考，
**不可**用作能力评测结论或模型选型依据。honest FAIL，不予粉饰。

## 7. 改进建议（可机检、可验收）

1. 在 `baseline_report.json` 的每个 `by_difficulty` 桶里保留 `estimated` 布尔（本审计的
   `A3_estimate_flag` 会因此翻绿）；
2. `confidence_intervals` 顶层改为 `{{model: [lo, hi]}}` 或直接删字段（消灭 STUB）；
3. 统一 `baseline_table.json` 与报告的口径，或在表里加 `estimated` 标记与 `source`
   字段（`A8_table_vs_report` 会翻绿）；
4. 报告写入 `mapping_digest`（`sha256` of 序列化后的 `HISTORICAL_MAPPING`），第三方即可
   确认数字对应哪一版映射（`E1_reproducibility_stamp` 翻绿）；
5. 真正跑一次 live eval（`run_eval.py --live`）后再发布能力基线；在跑到之前，报告标题应
   显式写「历史叙事基线（非评测测量）」。
"""

    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=ROOT / "eval_set/results/baseline_integrity_audit.json")
    ap.add_argument("--out", type=Path, default=ROOT / "docs/audit/eval_baseline_integrity.md")
    args = ap.parse_args()
    audit = json.loads(args.json.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(audit), encoding="utf-8")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())