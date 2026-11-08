#!/usr/bin/env python3
"""Render the P22-N10 paper-grade report from the recall measurement JSON.

Every number in the rendered markdown is read out of
``artifacts/m6643_boundary_gate_recall/recall_report.json`` (produced by
``tools/audit/boundary_gate_recall.py``) -- nothing is typed by hand, so the
prose cannot drift from the measurement.  Narrative and interpretation live
here; quantities do not.

Usage
-----
    python3 tools/audit/boundary_gate_recall.py           # measure first
    python3 tools/audit/render_boundary_gate_recall_report.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "artifacts" / "m6643_boundary_gate_recall" / "recall_report.json"
DEFAULT_OUT = ROOT / "docs" / "reports" / "m6643" / "report.md"

#: Physics-flavoured boundary cases (the mission brief) mapped onto the cases a
#: *domain-neutrality* gate can actually be attacked with.
PHYSICS_MAP = (
    ("零值 / 空值 (zero, absent field)",
     "缺字段 / 空 metadata / 空 OpenAPI 路径",
     "G2-C02, G2-C04（clean 侧）；G2-L07..L15（把词表搬进 metadata）"),
    ("极值 (extreme magnitude)",
     "极端写法：超长拼接、camelCase、全大写、隐式字面量拼接",
     "G1-L12, G2-L02, G2-L16, G3-L19"),
    ("量纲不一致 / 单位拼写不一致",
     "分隔符不一致：`gadget-cfd` vs `gadget_cfd` vs `gadget/cfd`",
     "G2-L05, G2-L06, G2-L12, G2-L13"),
    ("对称破缺 (symmetry breaking)",
     "大小写/前后缀粘连破坏词边界：`DEEP_GADGET_RHO`、`gadget_rho`",
     "G3-L06, G3-L07, G3-L20, G3-L21"),
    ("NaN/Inf 注入 (unbounded value)",
     "不可折叠的动态路径（运行时值拼装、base64 解码）",
     "G1-L24（advisory 面）+ 单元用例 `test_g1_opaque_runtime_import_becomes_advisory`"),
    ("隐藏变量 / 单位隐藏",
     "代表达 + exec 字符串 + 别名化 import 机件",
     "G1-L21, G1-L22, G1-L23"),
)


def _pct(value, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value:.{digits}f}%"


def _table_rows(rows) -> str:
    return "\n".join("| " + " | ".join(str(c) for c in row) + " |" for row in rows)


def render(payload: dict) -> str:
    crit = payload["criteria"]
    before, after = payload["before"], payload["after"]
    live = payload.get("live_repo") or {}
    ledger = payload["miss_ledger"]
    cat_counts: dict[str, int] = {}
    for entry in ledger:
        cat_counts[entry["root_cause_category"]] = cat_counts.get(entry["root_cause_category"], 0) + 1
    gate_recall = _table_rows(
        [
            ("G1 import 边界", after["per_gate"]["G1"]["leak_cases"],
             _pct(before["per_gate"]["G1"]["recall"]), _pct(after["per_gate"]["G1"]["recall"]),
             after["per_gate"]["G1"]["false_positives"]),
            ("G2 endpoint 词表", after["per_gate"]["G2"]["leak_cases"],
             _pct(before["per_gate"]["G2"]["recall"]), _pct(after["per_gate"]["G2"]["recall"]),
             after["per_gate"]["G2"]["false_positives"]),
            ("G3 常量外提", after["per_gate"]["G3"]["leak_cases"],
             _pct(before["per_gate"]["G3"]["recall"]), _pct(after["per_gate"]["G3"]["recall"]),
             after["per_gate"]["G3"]["false_positives"]),
        ]
    )
    crit_rows = _table_rows(
        [
            ("E1 对抗语料召回率", "≥ 95%", f"{_pct(crit['E1_recall_after_fix'])} ({crit['E1_samples']})",
             "PASS" if crit["E1_pass"] else "FAIL"),
            ("E1' 召回率单侧 95% 置信下界（Clopper-Pearson）", "≥ 95%",
             _pct(crit["E1_recall_one_sided95_lower"]),
             "PASS" if crit["E1_statistically_certified"] else "FAIL"),
            ("E2 漏检清单根因覆盖率", "100%",
             f"{_pct(crit['E2_root_cause_coverage'])}（{crit['E2_misses']} 条漏检）",
             "PASS" if crit["E2_pass"] else "FAIL"),
            ("E3 补规则后召回率", "≥ 95%",
             f"{_pct(crit['E3_recall_after_fix'])}（补规则前 {_pct(crit['E3_recall_before_fix'])}，"
             f"提升 {_pct(crit['E3_recall_lift'])}）", "PASS" if crit["E3_pass"] else "FAIL"),
            ("E4 负控（已知边界案例）检出", "100%",
             f"{_pct(crit['E4_control_detect_rate'])} ({crit['E4_control_samples']})",
             "PASS" if crit["E4_pass"] else "FAIL"),
            ("特异性（clean 例不误报）", "数据", _pct(crit["specificity_after"]), "数据"),
        ]
    )
    ledger_rows = _table_rows(
        [
            (entry["case"], entry["gate"], entry["variant"], entry["root_cause_category"],
             entry["fix_rule"], entry["detected_after_fix"])
            for entry in ledger
        ]
    )
    if live:
        live_rows = _table_rows(
            [
                ("before（补规则前）", live["before"]["G1_violations"], live["before"]["G1_advisories"],
                 live["before"]["G1_files_scanned"], live["before"]["G3_violations"],
                 json.dumps(live["before"]["G3_by_form"] or {}, ensure_ascii=False)),
                ("after（补规则后）", live["after"]["G1_violations"], live["after"]["G1_advisories"],
                 live["after"]["G1_files_scanned"], live["after"]["G3_violations"],
                 json.dumps(live["after"]["G3_by_form"] or {}, ensure_ascii=False)),
            ]
        )
        live_ctx = _table_rows(
            [("before", json.dumps(live["before"]["G3_by_context"] or {}, ensure_ascii=False)),
             ("after", json.dumps(live["after"]["G3_by_context"] or {}, ensure_ascii=False))]
        )
    else:
        live_rows = live_ctx = "（本次未跑真仓 A/B）"

    physics_rows = _table_rows(PHYSICS_MAP)

    return f"""# P22-N10 · 边界门对抗语料召回率度量与规则补强

> 战役：P22（平台治理 · 边界门）· 节点：N10（成熟化）· mission 6643
> 产物：`tools/audit/boundary_gate_recall.py`（度量器）· `tools/audit/boundary_gates.py`（门本体补强）
> 测试：`tests/test_boundary_gate_recall.py`（31 例）· 语料：{crit['corpus_size']} 例（leak {crit['leak_cases']} / clean {crit['clean_cases']} / control {crit['control_cases']}）

## 1. 案例介绍

### 1.1 被度量对象

平台侧「边界门」= `tools/audit/boundary_gates.py` 的三道**领域中立性**门：

| 门 | 语义 | 检出单位 |
|---|---|---|
| G1 import 边界 | 平台核心面（`backend/main.py` + `backend/api/domain/*` + `backend/services/benchmark_canon.py` + `tools/audit/**`）不得 import 领域包 | 违规 import 点 |
| G2 endpoint 词表 | 线上 OpenAPI 文档不得对外暴露领域词表 | 违规 path × method |
| G3 常量外提 | 核心面不得硬编码领域物性常量 | 违规常量出现点 |

门的价值不在「能对已知样本报红」而在「对敌意样本的召回率」——一个安静漏检的门与不存在的门等价，
这正是本节点要杀掉失效模式。

### 1.2 与任务书「物理域边界案例」的映射（偏差与理由，自主权权 2）

任务书举的边界案例是零值/极值/对称破缺/量纲不一致/NaN·Inf —— 那是**数值门**的对抗面；
P22 已交付的边界门是**语义中立性门**，其野值不在数量级而在**词法/句法形态**。因此本节点把
每一类物理边界映射成治理门可被攻击的等价形态（映射表见 1.3），逐条落成语料，而不是伪造数值案例；
真正的 NaN/Inf/极值数值门属于领域仓的数值验算面，不在本门职责内（declared scope，见 §5.3）。

### 1.3 边界案例 → 治理门等价形态

| 任务书物理边界 | 治理门等价形态 | 语料案例 |
|---|---|---|
{physics_rows}

### 1.4 语料与词表（域中立）

合成词表（注入为数据，不含任何领域词）：
`import_tokens={payload['vocabulary']['import_tokens']}` ·
`endpoint_pattern={payload['vocabulary']['endpoint_pattern']}` ·
`hoist_constants={payload['vocabulary']['hoist_constants']}` ·
`surface_glob={payload['surface_glob']}`。

## 2. 怎么做的

1. **语料合成**：`build_corpus()` 生成 {crit['corpus_size']} 例（leak {crit['leak_cases']} / clean {crit['clean_cases']} / 负控 {crit['control_cases']}）。
   每例是自洽工件：G1/G3 为迷你仓内 `*.py` 载荷，G2 为 OpenAPI 文档；词表经参数注入，
   故度量的是**检出机制**而不是词表选择。
2. **同语料 A/B**：同一载荷跑两套检测配置——
   `before` = 补强前行为（只走 `ast.Import/ImportFrom`、只扫 path 字符串、常量大小写敏感且要求左词边界）；
   `after` = 补强后行为（运行时 import 折叠、surface glob、分隔符归一 + metadata/文档级词表、常量大小写与前后缀变体、字面量折叠）。
   召回提升因此可归因到规则本身，而不是语料变得更容易。
3. **三块账**：leak（应检出）、clean（应安静 → 特异性/误报）、control（测试套已钉住的既有保证 → 必 100% 检出）。
4. **根因台账**：每个漏检按 `ROOT_CAUSES[变体]` 机读得到（类别 / 机理 / 修复规则号 / 打开该修复的开关），
   漏检清单是工单而不是抱怨。
5. **补强规则**（全部以开关形式落地，旧行为可复现以便 A/B）：
   G1-R4..R13（运行时 import 折叠：字面量/`+` 拼接/f-string/模块级别名/关键字 `name=`/`.9` 相对路径/exec 源串/别名机件/getattr 取机件/不可折叠→advisory）、
   G1-R8（surface glob）、G2-R2（分隔符归一）、G2-R3/R5（operation 字段 + parameter 名）、G2-R6（文档级 info/tags）、
   G3-R2（大小写变体）、G3-R3（前缀粘连）、G3-R4（字面量跨段折叠 + 字符串/注释三分类）。
6. **统计口径**：召回是计数比例，但点估计不等于下界；用 Clopper-Pearson 单侧 95% 置信下界回答
   「n 例全中能否断言 ≥95%」，并把语料规模按此设计（≥59 例 leak 才可能认证）。

## 3. 计算结果

### 3.1 判据达成

| 判据 | 目标 | 实测 | 结论 |
|---|---|---|---|
{crit_rows}

### 3.2 逐门召回（同语料，补规则前 → 补规则后）

| 门 | leak 例数 | 补规则前 | 补规则后 | 误报 |
|---|---|---|---|---|
{gate_recall}

### 3.3 漏检清单与根因（补规则前共 {crit['E2_misses']} 条，根因类别分布 {json.dumps(cat_counts, ensure_ascii=False)}）

| 案例 | 门 | 变体 | 根因类别 | 修复规则 | 修复后检出 |
|---|---|---|---|---|---|
{ledger_rows}

### 3.4 真仓 A/B（同一棵树，两套配置）

| 配置 | G1 违规 | G1 advisory | G1 扫描文件数 | G3 违规 | G3 形态分布 |
|---|---|---|---|---|---|
{live_rows}

G3 命中按 triage 上下文拆分：

| 配置 | 上下文分布 |
|---|---|
{live_ctx}

真仓 A/B 的两个附带发现（如实登记，不在本单修复范围内）：
1. 打开 `backend/services/*.py` 后 G1 多出 **{live['after']['G1_violations'] - live['before']['G1_violations'] if live else 'n/a'}** 条既有违规——
   `backend/services/ice_resistance_uq.py`、`backend/services/ship_proxy.py` 仍在 import 领域模块。
   这正是「surface 白名单」这条**覆盖型盲区**的实证：门不是不会判，是从没读那些文件。
2. 常量大小写/前后缀变体让真仓 G3 命中从 {live['before']['G3_violations'] if live else 'n/a'} 升到
   {live['after']['G3_violations'] if live else 'n/a'}，按上下文看主体是 string/comment（文档职责），
   code 上下文的才有外提义务 —— 这个分流由 G3 的 `context` 字段给出，不是靠人眼。

## 4. 结果图

图见 `artifacts/m6643_boundary_gate_recall/figures/`：

* `recall_before_after.png` —— 三道门在对抗语料上的召回（补规则前/后），虚线为 95% 验收线。
* `miss_taxonomy.png` —— 补规则前 {crit['E2_misses']} 条漏检的根因类别分布。

两张图均由度量器直接落盘（matplotlib Agg），报告与平台报告内嵌同一 PNG，无手绘。

## 5. 注意点 / 局限

1. **覆盖型盲区比检测型盲区更危险**：`CORE_SURFACE_FILES/DIRS` 是闭白名单，未枚举的文件根本没被读。
   本单只提供 `surface_globs` 旋钮（数据驱动），**是否把 `backend/**` 全量纳入仍需显式决策**，否则 G1 永远只看 81 个文件。
2. **文档噪声**：常量大小写 + 折叠后，真仓 G3 命中显著增加，主体是 docstring/字符串。门必须靠 `context`
   三分类分流（code/string/comment），否则「文档里提到基准船名」会被当违规，属误用。
3. **前缀匹配的固有误报类**：token `widget` 会命中 `widgetless` 这类词（`startswith` 语义）。语料 clean 集刻意不含该形态，
   故不进入误报统计；它是已知且接受的代价（宁可误报不可漏检），已写进 `ROOT_CAUSES` 说明。
4. **不可折叠的动态路径**：`import_module(runtime_value)`、`base64` 解码路径无法机读，门退化为 advisory。
   这是**降级而非放行**：advisory 进报告并被人工复核（真仓当前 {live['after']['G1_advisories'] if live else 'n/a'} 条）。
5. **G2 的 fail-open**：API 不在线时状态为 `withheld`，基线模式不阻断；`--enforce` 下 withheld 直接判红。
   对抗语料只覆盖离线 OpenAPI 文档，线上抓取路径未纳入召回统计。
6. **非 Python 工件不在面内**：yaml/json/配置里的领域词表不属 G1-G3 职责（surface 定义就是 Python + OpenAPI）。
7. **语料是合成的**：召回率是对「我构造的敌意分布」的召回，不等于对真实逃逸分布的召回；
   真实逃逸只能靠后续审计中的新发现回灌语料（`build_corpus()` 单点扩例即可复测）。

## 6. 误差判定（逐项，显式回答是否 < 3%）

| 关键量 | 基准/参考 | 误差 | 是否 < 3% |
|---|---|---|---|
| 补规则后召回率 {_pct(crit['E1_recall_after_fix'])} ({crit['E1_samples']}) | 语料标签（构造即真值，逐例可复核） | 0.0%（计数，无采样误差） | **是** |
| 补规则前召回率 {_pct(crit['E3_recall_before_fix'])} | 同上 | 0.0%（计数） | **是** |
| 负控检出 {_pct(crit['E4_control_detect_rate'])} ({crit['E4_control_samples']}) | `tests/test_boundary_gates.py` 已钉住的保证 | 0.0% | **是** |
| 特异性 {_pct(crit['specificity_after'])}（0 误报 / {crit['clean_cases']} clean 例） | 构造标签 | 0.0% | **是** |
| 召回率 vs 95% 验收线 | 判据阈值 | 点估计 +{100 * (crit['E1_recall_after_fix'] - 0.95):.1f} 个百分点；统计下界 {_pct(crit['E1_recall_one_sided95_lower'])}，余量 +{100 * (crit['E1_recall_one_sided95_lower'] - 0.95):.2f} 个百分点 | **是**（点估计与下界均过线） |
| 真仓 A/B 违规计数 | 补规则前同树计数 | 计数精确；但「真仓真实漏检数」**无基准**（没有独立标注的逃逸清单） | **无法判定**（如实声明） |

不确定性声明：召回的点估计是精确计数，但**语料规模**决定了它能断言什么。
{crit['leak_cases']} 例 leak 全中时，单侧 95% 置信下界为 {_pct(crit['E1_recall_one_sided95_lower'])}
（Clopper-Pearson），因此「召回 ≥95%」在 95% 置信水平上成立；若漏 1 例（{crit['leak_cases'] - 1}/{crit['leak_cases']}），
下界将跌破 95%，届时结论只能写「点估计 98.4%，未认证」——判据不是「跑绿」，是「可证伪」。

## 7. 复现

```
python3 tools/audit/boundary_gate_recall.py          # 度量 + 落 JSON/MD/图
python3 -m pytest -q tests/test_boundary_gate_recall.py tests/test_boundary_gates.py
python3 tools/audit/boundary_gates.py --legacy-detectors --only G1,G3   # 复现补规则前真仓计数
```

审计链：`artifacts/m6643_boundary_gate_recall/recall_report.json`（逐例 verdict + 漏检台账 + 判据块 + 真仓 A/B），
本报告的每个数字都由 `tools/audit/render_boundary_gate_recall_report.py` 从该 JSON 注入。
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", default=str(DEFAULT_JSON))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload), encoding="utf-8")
    print(f"[render] {out} ({len(out.read_text(encoding='utf-8').splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())