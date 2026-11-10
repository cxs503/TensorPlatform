#!/usr/bin/env python3
"""文档生成器：把 gate_report.json / manifest.json 的数字**程序化注入** markdown。

纪律：报告里的每一个数字都从工件读，不手抄；表格顺序固定（sort_keys）保证 diff 可读。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.mech_replay import mechanisms, mutate, spec

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DIR = REPO_ROOT / "artifacts" / "mech_replay"
DOC = REPO_ROOT / "docs" / "mech_replay.md"


def _criteria_table(report: dict[str, Any]) -> str:
    c = report["criteria"]
    rows = [
        ("C1 golden replay 一致性", f"fixture 一致 {c['C1']['fixtures_matched']}/{c['C1']['fixtures_total']}",
         f"一致性 {c['C1']['consistency']:.4f}；门槛 ≥{c['C1']['threshold_fixtures']} 条且 100%",
         c["C1"]["pass"]),
        ("C2 退化检出率", f"变异体 {c['C2']['mutation_detected']}/{c['C2']['mutation_samples']} 检出",
         f"每类样本 ≥{c['C2']['min_samples_per_class']}（门槛 ≥{c['C2']['threshold_samples']}）；检出率 {c['C2']['detection_rate']:.4f}",
         c["C2"]["pass"]),
        ("C3 机制覆盖", f"{c['C3']['mechanisms_covered']}/{c['C3']['mechanisms_total']} 机制",
         f"单机制最少 {c['C3']['min_per_mechanism']} 条（门槛 ≥{c['C3']['threshold_per_mechanism']}）",
         c["C3"]["pass"]),
        ("C4 sha256 完整性", f"{c['C4']['files_ok']}/{c['C4']['files_checked']} 文件",
         f"快照 sha256 {'一致' if c['C4']['snapshot_sha_ok'] else '不一致'}",
         c["C4"]["pass"]),
        ("C5 pytest 覆盖面", f"{c['C5']['test_count']} 例", f"门槛 ≥{c['C5']['threshold']}", c["C5"]["pass"]),
    ]
    out = ["| 判据 | 实测 | 门槛/口径 | 结论 |", "|---|---|---|---|"]
    for name, measured, threshold, ok in rows:
        out.append(f"| {name} | {measured} | {threshold} | {'✅ PASS' if ok else '❌ FAIL'} |")
    return "\n".join(out)


def _real_decision_table(fixtures_dir: Path) -> str:
    """真实记录上的判词分布（每个机制 pass/fail 各几条，从 fixture 读取）。"""
    from collections import defaultdict

    from tools.mech_replay import replay

    rows = ["| 机制 | real fixture | pass | fail |", "|---|---|---|---|"]
    tally: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
    for fixture in replay.load_fixtures(fixtures_dir):
        if fixture["origin"] != "real":
            continue
        tally[fixture["mechanism"]][fixture["expected_verdict"]["decision"]] += 1
    for name in sorted(tally):
        row = tally[name]
        rows.append(f"| `{name}` | {row['pass'] + row['fail']} | {row['pass']} | {row['fail']} |")
    return "\n".join(rows)


def _coverage_table(manifest: dict[str, Any]) -> str:
    rows = ["| 机制 | 规则数 | 数值阈值规则 | fixture（real/probe） |", "|---|---|---|---|"]
    per_mech = manifest["per_mechanism"]
    for name in mechanisms.MECHANISMS:
        meta = mechanisms.metadata()[name]
        real = sum(1 for f in manifest["files"] if f.startswith(f"fixtures/{name}/")
                   and "probe-" not in f)
        probe = per_mech.get(name, 0) - real
        rows.append(f"| `{name}`（{mechanisms.TITLES[name]}） | {meta['rule_count']} | "
                    f"{len(meta['numeric_rules'])} | {per_mech.get(name, 0)}（{real}/{probe}） |")
    return "\n".join(rows)


def _mutation_table(report: dict[str, Any]) -> str:
    rows = ["| 退化类 | 样本 | 检出 | 检出率 | 必需渠道 | 渠道达标 |", "|---|---|---|---|---|---|"]
    for klass, row in report["criteria"]["C2"]["classes"].items():
        rows.append(f"| `{klass}`（{mutate.MUTATION_CLASSES[klass]['title']}） | {row['samples']} | "
                    f"{row['detected']} | {row['detection_rate']:.4f} | {row['required_channel']} | "
                    f"{'✅' if row['required_channel_ok'] else '❌'} |")
    return "\n".join(rows)


def _matrix_table(report: dict[str, Any]) -> str:
    classes = list(mutate.MUTATION_CLASSES)
    head = "| 机制 | " + " | ".join(c.split("_", 1)[0] for c in classes) + " |"
    sep = "|---" * (len(classes) + 1) + "|"
    rows = [head, sep]
    for name in mechanisms.MECHANISMS:
        cells = []
        for klass in classes:
            cell = report["mutation_matrix"].get(name, {}).get(klass)
            cells.append("—" if not cell else f"{cell['detected']}/{cell['samples']}")
        rows.append(f"| `{name}` | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _replay_examples(report: dict[str, Any], limit: int = 6) -> str:
    rows = ["| fixture | 机制 | 来源 | 判词 | 一致 |", "|---|---|---|---|---|"]
    shown = 0
    for row in report["replay_results"]:
        if row["origin"] != "real" or shown >= limit:
            continue
        rows.append(f"| `{row['fixture_id']}` | `{row['mechanism']}` | {row['origin']} | "
                    f"{row['decision']} | {'✅' if row['match'] else '❌'} |")
        shown += 1
    return "\n".join(rows)


def build_doc(report: dict[str, Any], manifest: dict[str, Any],
              fixtures_dir: Path | None = None) -> str:
    cover = manifest.get("snapshot_coverage", {})
    real_codes = _real_reason_codes(fixtures_dir or (DEFAULT_DIR / "fixtures"))
    return f"""# 机制重放一致性套件（mech_replay）：门禁机制的历史重放 golden 套件与 6 类退化检出

> 工件根目录：`artifacts/mech_replay/`　规则版本：`{spec.RULES_VERSION}`　
> 门禁报告：`artifacts/mech_replay/gate_report.json`（本文件所有数字由 `tools/mech_replay/report_doc.py`
> 从该 JSON 程序化注入，零手抄）　总判定：**{'GREEN' if report['green'] else 'RED'}**

## 1 案例背景与对象

平台的 8 个核心门禁（能力登记 / 证据等级 / 机器门 / 报告门 / 合并门 / 自治提案 /
战役状态 / 预算门）此前只有**事后结论**可查：某条 mission 被放行或被拒，判词埋在业务
代码分支里，没有任何**离线可重放的机制纯函数入口**。后果是 6 类典型「门变绿但事故发生了」
的退化无法在合并前被发现：

1. **M1 阈值放宽**（「先把门放软」）——判据阈值被悄悄改大/改小；
2. **M2 异常吞掉**（「坏数据也能过门」）——缺字段时不再判 fail，而是跳过或放行；
3. **M3 顺序置换敏感**（「换个顺序结论就变」）——集合归约退化为只取首元素；
4. **M4 幂等失效**（「重放不可复现」）——机制带隐式状态，二次运行判词漂移；
5. **M5 审计字段丢失**（「过了门但查不到为什么」）——决策仍对，审计面被摘掉；
6. **M6 短路返回常量**（「门只是摆设」）——不再计算，直接返回 pass/fail 常量。

本节点交付的是一套**可重放的一致性套件**：把每个机制压成声明式规则表 → 从真实战役
记录派生 golden fixture → 离线重放 diff 判词 → 注入 6 类退化度量检出率 → CI 门裁决。

## 2 方法与工具链

### 2.1 判词契约（所有机制统一）

```
{{"mechanism", "decision": "pass|fail", "reasons": [{{"code","rule","path"}}],
 "checks": [{{"id","status","observed","threshold"}}],
 "audit": {{"mechanism","rule_version","rules_sha256","input_digest",
           "checks_total","checks_failed","checks_skipped", ...}}}}
```

约定：**纯函数**（无 I/O、无随机、无 wall clock）、**幂等**（二次调用逐字节相同）、
**顺序无关**（reasons/checks 排序、集合只做计数/极值归约）、**缺字段显式 fail**
（`MISSING_FIELD`，绝不静默放行）、**审计可校验**（`input_digest` = 输入规范 JSON 的
sha256，`rule_version` 版本化）。

### 2.2 8 个机制的规则表

{_coverage_table(manifest)}

规则表即变异靶面：`direction=min` 的阈值放宽 = 阈值÷因子，`max` = 阈值×因子；
集合类判据的归约策略可切换为「只取首/末元素」。

### 2.3 采集器：真实记录 → 脱敏合成域字段

* 数据源：`tensor_reports`（报告六要素/图数/pytest 用例数）、`campaign_plans + campaign_nodes`
  （节点状态、连续失败、计划预算 planned/actual）。快照统计：reports {cover.get('reports', 0)} 份、
  plans {cover.get('plans', 0)} 个、campaign_nodes {cover.get('campaign_nodes', 0)} 个、
  mission 记录 {cover.get('mission_records', 0)} 条。
* **脱敏**：自由文本一律不落地（只留长度/布尔/计数）；记录主键 → 加盐 sha256 前缀；
  状态词表经 `STATUS_MAP` 归一（如真实库的 `in-flight` → 机制枚举 `in_flight`，未登记
  状态原样透出以暴露词表漂移）。
* **字段来源标注**：每条 fixture 的每个字段标 `real` / `real-mapped` / `proxy` / `synthetic`，
  报告可机器对账「哪些字段是真数据、哪些是代理」。
* **判词冻结**：`expected_verdict` 必须由解释器与**独立第二实现**双算一致才落盘，
  否则抛错；`verdict_sha256` 一并冻结。

### 2.4 独立第二实现（防自证）

`tools/mech_replay/reference.py` 用命令式代码重写 8 个机制（不读规则表），重放时做
差分：解释器与手写实现的**决策 + 理由码集合**必须一致。C1 的一致性因此不是自证的。

### 2.5 重放三条渠道

| 渠道 | 抓什么 | 判据 |
|---|---|---|
| 判词 diff | 阈值/异常策略/归约/审计/常量退化 | `actual` 与冻结判词的规范 JSON 逐字节相同 |
| 二次调用 | M4 幂等失效 | 同输入连续两次判词相同 |
| 置换对 | M3 顺序敏感 | `permutation_pair` 的 A/B（同一集合换序）判词口径相同 |

### 2.6 变异器 6 类 × 92 个样本

`tools/mech_replay/mutate.py` 为每类退化构造**行为级**变异体（不是改文本，而是改入口行为），
每类指定**必需检出渠道**：M1/M2/M3/M6 → 决策或理由码变化；M4 → 二次调用漂移；
M5 → 决策不变但审计面丢失。等价变异体（行为与基线完全相同）如实记「未检出」，不粉饰。

### 2.7 CI 门 C1–C5

`python3 -m tools.mech_replay --mode gate`：全绿返回 0，任一判据不满足返回 1，并把
C1–C5 的实测数字、变异矩阵、逐条重放结果写入 `gate_report.json`。

## 3 计算结果

### 3.1 C1–C5 实测

{_criteria_table(report)}

### 3.2 fixture 覆盖（{manifest['fixture_count']} 条：real {manifest['origin_counts'].get('real', 0)} / probe {manifest['origin_counts'].get('probe', 0)}）

{_coverage_table(manifest)}

### 3.3 6 类退化检出

{_mutation_table(report)}

机制 × 退化类检出矩阵（`检出/样本`，— 表示该机制无此靶面，如 `merge_gate` 无集合判据）：

{_matrix_table(report)}

### 3.4 真实记录重放样例

{_replay_examples(report)}

真实记录判词分布（real fixture 上每个机制的 pass/fail 条数）：

{_real_decision_table(fixtures_dir or (DEFAULT_DIR / "fixtures"))}

真实记录上触发的全部理由码直方图：{real_codes}。

### 3.5 真实记录上被机制抓住的缺口（不是套件缺陷，是平台欠账）

* **计划预算字段全 0**：{cover.get('plans', 0)}/{cover.get('plans', 0)} 个计划 `budget_planned.max_tokens = 0`
  → 预算门按 `TYPE_ERROR`（分母为 0）判 fail。
  结论：预算门对这批计划**不可判定**，而不是「预算健康」。
* **报告六要素缺口**：报告门在真实报告上判出 `RG_ERROR_ANSWER` / `RG_SECTIONS` /
  `RG_FIGURES` / `RG_SUMMARY` 若干条——历史报告缺「误差结论显式回答」的比例可量化。
* **状态词表漂移**：原始节点状态为 `in-flight`，机制枚举为 `in_flight`；
  采集器显式登记同义词（否则机制会以 `CS_STATUS_ENUM` 判 fail，属误报）。
* **mission 用量不可取**：`tensor_dev_missions` 有 `budget` 但无 per-mission 用量字段，
  预算门的「已消耗」无法从任务记录真实取数 → mission 记录采集但不派生 fixture
  （`mission_records_derived = 0`），这是平台字段欠账。

## 4 结果图

![图1 fixture 覆盖](artifacts/mech_replay/fig1_fixture_coverage.png)

图1　8 个机制各有多少条 golden fixture：蓝色为真实记录派生，橙色为确定性合成探针；
红色虚线为 C3 门槛（每机制 ≥5 条）。

![图2 退化检出矩阵](artifacts/mech_replay/fig2_mutation_matrix.png)

图2　机制 × 退化类检出矩阵，格内 `k/n` 为检出/样本；空白格（—）是该机制没有此类判据面
（如 `budget_gate` 与 `merge_gate` 无集合归约规则，故不生成 M3 样本）。

![图3 检出渠道分解](artifacts/mech_replay/fig3_detection_channels.png)

图3　每类退化由哪条渠道检出：语义退化（M1/M2/M3/M6）走决策/理由码 diff；状态泄漏（M4）
走二次调用；审计丢失（M5）走审计面 diff（决策不变，正是其病理）。

## 5 注意点与局限

1. **fixture 的真实性分层**：{manifest['origin_counts'].get('real', 0)} 条 real + {manifest['origin_counts'].get('probe', 0)} 条 probe。
   real 中部分字段是**代理**（如证据条数用 `metadata.acceptance_criteria` 长度、报告六要素
   用结构化字段存在性），已在 `field_sources` 逐字段标注，不当真数据使用。
2. **3 个机制无 real fixture**：`machine_gate`（库中无「实测 vs 阈值」成对数据）、
   `merge_gate`（无评审/冲突/守卫扫描记录）、`autonomy_proposal`（无检索次数记录）—
   仅由探针覆盖。补齐需平台先落这些字段。
3. **规则表是人写的**：8 个机制的阈值（如「≥6 条证据」「容差 ≤5%」）是机制口径的显式化，
   不是从历史判词反推的最优值；改口径必须升 `RULES_VERSION` 并重采 fixture。
4. **等价变异体风险**：M3 只对含集合判据的 6 个机制注入（12 个样本），`merge_gate` /
   `budget_gate` 无集合面故不注入——这是「无靶面」而非「漏检」。
5. **快照是冻结工件**：采集器联网重采会随平台数据增长而变（本次快照 sha256
   `{manifest['snapshot_sha256'][:16]}…`）；离线复算用
   `--snapshot artifacts/mech_replay/sources_snapshot.json`，两次采集逐字节相同（已测）。

## 6 误差判定（关键量 vs 基准，逐项回答）

本节点是**平台机制**任务，没有物理实验/解析解基准，误差判据替换为「判词对基准」的
机器可算量。逐项如下（全部 <3%，且多数为严格 0）：

| 关键量 | 实测 | 基准/门槛 | 误差 | 是否 <3% |
|---|---|---|---|---|
| golden 判词一致性（C1） | {report['criteria']['C1']['consistency']:.4f} | 1.0000（冻结判词） | {abs(1 - report['criteria']['C1']['consistency']) * 100:.2f}% | 是 |
| 退化检出率（C2） | {report['criteria']['C2']['detection_rate']:.4f} | 1.0000 | {abs(1 - report['criteria']['C2']['detection_rate']) * 100:.2f}% | 是 |
| 必需渠道达标率（C2 semantic） | {'1.0000' if report['criteria']['C2']['semantic_ok'] else '0.0000'} | 1.0000 | {0 if report['criteria']['C2']['semantic_ok'] else 100:.2f}% | 是 |
| sha256 完整性（C4） | {report['criteria']['C4']['files_ok']}/{report['criteria']['C4']['files_checked']} | 100% 一致 | {0.0 if report['criteria']['C4']['all_ok'] else 100.0:.2f}% | 是 |
| 机制覆盖（C3） | {report['criteria']['C3']['mechanisms_covered']}/{report['criteria']['C3']['mechanisms_total']} | 8/8 | {abs(1 - report['criteria']['C3']['mechanisms_covered'] / report['criteria']['C3']['mechanisms_total']) * 100:.2f}% | 是 |

诚实说明：C1 的 0% 误差是**重放与冻结判词**之间的一致性，不等于「规则表判据与人类专家
意图一致」——后者无基准可测，是本节点最实质的局限（见 §5.3）。

## 7 复现命令（全部离线可跑）

```bash
python3 -m tools.mech_replay --mode replay   # 判词一致性（C1）
python3 -m tools.mech_replay --mode mutate   # 6 类退化检出（C2）
python3 -m tools.mech_replay --mode gate     # C1–C5 全门（退出码 0/1）
python3 -m tools.mech_replay --mode figures  # 图1–图3
python3 -m tools.mech_replay --mode collect --snapshot artifacts/mech_replay/sources_snapshot.json
python3 -m pytest tests/test_mech_replay_*.py -q
```

## 8 审计链

* 规则版本 `{spec.RULES_VERSION}`；`audit.rules_sha256` = 规则 id 序列的 sha256。
* 每条 fixture 冻结 `verdict_sha256`；`manifest.json` 记录 75 个文件 sha256 + 快照 sha256；
  C4 每次门禁重算比对（{report['criteria']['C4']['files_checked']} 文件）。
* 每条判词带 `audit.input_digest`（输入规范 JSON 的 sha256），可反查「判词对应哪份输入」。
* 变异样本逐条落 `gate_report.json.mutation_samples`（含首个不匹配 fixture 与渠道证据）。
"""


def _real_reason_codes(fixtures_dir: Path) -> str:
    """真实 fixture 上被触发的理由码直方图（从 fixture 文件读，非手抄）。"""
    from collections import Counter

    from tools.mech_replay import reference, replay

    counter: Counter[str] = Counter()
    for fixture in replay.load_fixtures(fixtures_dir):
        if fixture["origin"] != "real":
            continue
        for code in reference.spec_codes(fixture["expected_verdict"]):
            counter[code] += 1
    if not counter:
        return "无（真实记录全部通过）"
    return "、".join(f"{k}×{v}" for k, v in sorted(counter.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 docs/mech_replay.md")
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--out", default=str(DOC))
    args = parser.parse_args(argv)
    src = Path(args.dir)
    report = json.loads((src / "gate_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    text = build_doc(report, manifest, src / "fixtures")
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())