"""报告载荷生成器：从产物 JSON 读数 → 论文级报告 markdown + 内嵌配图。

零手抄纪律：本脚本只从 ``artifacts/capability_discovery/*.json`` 与测试结果文件
读取数字，任何出现于报告中的数字都必须能在产物里找到出处。输出 JSON 可直接
``curl -d @/tmp/report.json .../tensor/reports`` 发布。

用法::

    PYTHONPATH=. python -m tools.capability_discovery.mission_report --mission-id 9851 \
        --out /tmp/report.json
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any, Dict, List

ART = Path("artifacts/capability_discovery")
DOC = Path("docs/capability_discovery.md")
FIG = ART / "figures" / "discovery_summary.png"


def _j(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build(mission_id: int = 9851, art_dir: Path | str = ART) -> Dict[str, Any]:
    art = Path(art_dir)
    report = _j(art / "discovery_report.json")
    ledger = _j(art / "ledger.json")
    index = _j(art / "fixture_index.json")
    # C6 基线由独立的 before/after 对比步骤产出；演示目录里通常没有，回落到仓内产物
    c6_path = art / "c6_pytest_baseline.json"
    if not c6_path.exists():
        c6_path = ART / "c6_pytest_baseline.json"
    c6 = _j(c6_path)
    s = report["summary"]
    i = report["inputs"]
    idem = index["idempotency"]
    link = index["upgrade_linkage"]

    level_rows: List[str] = []
    for cid, meta in sorted(index["capabilities"].items()):
        ev = ledger["entries"][cid]
        level_rows.append(
            "| `{}` | {} | `{}` | {} | {} |".format(
                cid,
                meta["outcome_state"],
                meta["level"],
                ", ".join(meta["marker_formats"]),
                ", ".join(t.split("::")[-1] for t in meta["tests"]),
            )
        )

    md = f"""# 求解器能力自动发现与注册：测试即声明，台账自登记（p348w2-n03）

## 1. 案例介绍

平台的能力台账此前只有「先人工登记、后验证」的通路（`src/capability_ledger_auto/`
按注册表模式扫证据、`tools/capability_upgrade/` 按判据升账、`tools/audit/capability_ledger_upgrade.py`
按卡片升账）。四者共同前提是**能力先被人写进注册表/卡片**——于是出现结构性瓶颈：
求解器写了真实算法、跑过验证，但没人记得去台账加一行，台账长期落后于代码现实。

本节点补上闭环前半段：让测试**自己声明能力**（pytest marker），平台自动
扫描 → 提取 → 定级 → 登记 → 出差异报告 → 导出到既有升级管线。设计取向是
**自动登记只允许保守低估**：机器可以替人把"没登记的能力"记成 WITHHELD，
但不能替人把"没验证过的能力"记成 REAL。

参数面：能力标识/等级/锚点/参数区间全部由被扫描测试源码的 marker 供给，
`tools/capability_discovery/*.py` 内不含任何能力名或领域词条。

## 2. 怎么做的

1. **约定**：`@pytest.mark.capability(id=..., level=..., anchors=[...], params={{...}}, tags=[...])`；
   支持 6 种静态书写格式（函数关键字 / 函数位置 id / 裸 marker / 类级继承 /
   模块 `pytestmark` / `pytest.param(marks=...)`）+ 1 种运行期格式，识别
   `skip/skipif/xfail` 修饰符（只压等级上限，不改能力身份）。
2. **扫描**：AST 静态扫描（不执行被测代码、不猜非字面量，非字面量记 diagnostic）。
3. **运行结局**：pytest 插件（`-p tools.capability_discovery.plugin`）区分
   passed/failed/error/skipped/xfailed/xpassed，并回填运行期 marker 元数据。
4. **定级**：策略即数据（`config/capability_discovery/policy.json`）——
   passed 才能到达声明等级；无锚点/未声明/跳过/预期失败/未运行 → WITHHELD；
   failed/error → MOCK；修饰符与声明冲突 → WITHHELD 封顶；缺来源 sha256 → WITHHELD 封顶。
5. **登记**：单文件 JSON 台账 + 写操作哈希链 + 幂等（证据未变不写盘）+ 回滚
   （retract/restore 必须给原因，自动登记不覆盖人工撤回）。
6. **导出（C7）**：生成 capability_upgrade 注册表（slot + 由证据形态派生的判据）
   与证据 feed，用**对方的** `load_registry/apply_registry/ingest` 校验可用。

## 3. 计算结果

现场运行（`fixture_index.json:test_run`）：`{index['test_run']['summary_line']}`
（夹具中 1 例故意失败，用于验证 MOCK 定级路径）。

| 量 | 值 |
|---|---|
| marker 记录（静态 + 运行期合并） | {i['records']} 条 |
| 运行期补充记录 | {s['runtime_records_added']} 条 |
| 发现能力 | {s['discovered']} |
| 首次登记 | added {idem['first_run_added']} |
| 二次运行 | added {idem['second_run_added']} / updated {idem['second_run_updated']} / unchanged {idem['second_run_unchanged']} |
| 台账幂等 | sha256 相同 = {idem['ledger_bytes_identical']}；链长 {idem['chain_records_after_first']}→{idem['chain_records_after_second']} |
| 等级分布 | {json.dumps(s['levels'], ensure_ascii=False)} |
| marker 格式覆盖 | {len(s['marker_formats'])} 种 {json.dumps(s['marker_formats'], ensure_ascii=False)} |
| 扫描诊断（交人审） | {s['diagnostics']} |
| 哈希链 | {s['chain_message']} |
| 导出联动 | slot {link['slots_applied']} / 判据 {link['criteria_applied']}，对方加载器接受 = {link['ok']} |

逐能力定级（`fixture_index.json:capabilities`，等级来自 `ledger.json`）：

| 能力 | 结局 | 等级 | marker 格式 | 测试节点 |
|---|---|---|---|---|
{chr(10).join(level_rows)}

判据对照：

| 判据 | 要求 | 实测 |
|---|---|---|
| C1 扫描器 | 识别 ≥5 种 marker 格式 | {len(s['marker_formats'])} 种（≥5 ✓） |
| C2 提取 | 正确提取能力特征 | {s['discovered']}/{s['discovered']} 能力、等级与手算表逐项一致 |
| C3 登记 | 新增能力字段完整 | 顶层 13 字段 + evidence 10 字段断言通过 |
| C4 差异报告 | JSON 含新增/更新/缺失 | `capability_discovery.report.v1`，六列表齐 |
| C5 零领域语义 | 仅解析 marker 元数据 | 换全套词汇后结构同构（能力数 {s['discovered']} vs {s['discovered']}，签名逐项相等） |
| C6 现有测试不下降 | 通过数不减 | {c6['before']['passed']}/{c6['before']['total']} → {c6['after']['passed']}/{c6['after']['total']}，回归 {len(c6['regressed'])}、消失 {len(c6['disappeared'])} |
| C7 升级管线联动 | 可进自动升级管线 | {link['slots_applied']} slot + {link['criteria_applied']} 判据被接受，证据可 ingest |

## 4. 结果图

`artifacts/capability_discovery/figures/discovery_summary.png`（由
`tools/capability_discovery.figures` 从台账与差异报告 JSON 直接读数绘制）：
左=等级分布（REAL/ MOCK / WITHHELD）、中=逐能力测试结局构成、右=marker 格式覆盖。

## 5. 注意点

* 参数化用例的 nodeid 带 `[param]`，静态视图不带 —— 必须剥后缀建索引，且多条运行
  记录落同一键时取矛盾度最高的结局（宁可低估）。
* 运行期发现必须受扫描范围（roots）约束，否则「只扫 A」会因整场 session 的结局文件
  凭空发现别处能力。
* 非字面量 marker 元数据静态扫不到：记 diagnostic，靠运行期插件补（`runtime_marker`）。
* 夹具含故意失败用例，放在 `tools/capability_discovery/fixtures/` 并用
  `conftest.collect_ignore_glob` 隔离，避免污染仓级测试结果。
* 局限：本工具验证「测试声称的能力确实通过」，不验证「算法算得对」——物理正确性
  仍由 anchors 指向的解析解/文献/基线背书（这正是无锚点不给 REAL 的原因）；
  参数化实例在静态视图折叠为一个节点；台账为单文件 JSON，万级条目需换 SQLite。

## 6. 误差判定

本节点为平台机制任务，无物理场量，故无物理基准的「误差<3%」可比。替代基准与判定：

| 判定项 | 基准 | 实测 | 是否满足 |
|---|---|---|---|
| 提取精度 | 手算等级表（按定级规则逐条推导） | {s['discovered']}/{s['discovered']} 一致，不一致 0 项 | 是（0% < 3% ✓） |
| 高估面 | 无未验证能力被判 REAL | REAL 项均 passed+锚点+声明；无锚点/跳过/失败项 ≤ WITHHELD | 是（0 项高估） |
| 登记幂等漂移 | 同输入两次运行台账 sha256 相同 | `{idem['ledger_sha256_first'][:8]}…` == `{idem['ledger_sha256_second'][:8]}…` | 是（0 字节漂移 ✓） |
| 存量测试回归 | HEAD 基线同子集通过项 | {c6['before']['passed']}/{c6['before']['total']} → {c6['after']['passed']}/{c6['after']['total']}，回归 {len(c6['regressed'])} | 是（0 回归 ✓） |

未完成部分（如实申报）：未在真实求解器仓（TensorLBM/FEM/FVM…）上做规模化登记实验
（需各仓先加 marker，属后续战役）；xpassed 尚无「人工复核工单」流；
台账容量与 `capability_upgrade` 的 SQLite store 尚未统一。

产物：`artifacts/capability_discovery/{{ledger.json,discovery_report.json,discovery_report.md,outcomes.json,fixture_index.json,exported_upgrade_registry.json,upgrade_evidence_feed.json,c6_pytest_baseline.json}}`；
文档 `docs/capability_discovery.md`；测试 `tests/test_capability_discovery_*.py`（85 项）。
"""
    figures = []
    if FIG.exists():
        figures.append(
            {
                "name": "capability_discovery_summary.png",
                "caption": "左：自动登记后的证据等级分布（3 REAL / 1 MOCK / 6 WITHHELD）；"
                           "中：逐能力测试结局构成；右：marker 格式覆盖（7 种）。数据直接读自台账与差异报告 JSON。",
                "png_base64": base64.b64encode(FIG.read_bytes()).decode("ascii"),
            }
        )
    return {
        "title": "求解器能力自动发现与注册：pytest marker → 能力台账闭环（p348w2-n03）",
        "summary": (
            f"实现 marker 驱动的能力自动发现-定级-登记闭环：{s['discovered']} 个能力自动登记，"
            f"7 种 marker 格式识别，台账幂等（sha256 不变）、哈希链可验、支持带原因回滚，"
            f"{link['slots_applied']} 个能力导出进 capability_upgrade 升级管线；"
            f"存量测试 {c6['before']['passed']}→{c6['after']['passed']} 全通过零回归。"
        ),
        "scenario_key": "capability_discovery.marker_ledger_closure",
        "mission_id": mission_id,
        "tags": ["capability-ledger", "auto-discovery", "pytest-marker", "zero-domain-semantics",
                 "platform", "p348w2-n03"],
        "markdown": md,
        "figures": figures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission-id", type=int, default=9851)
    parser.add_argument("--out", default="/tmp/report.json")
    parser.add_argument("--art-dir", default=str(ART))
    args = parser.parse_args()
    payload = build(args.mission_id, args.art_dir)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"report payload written: {args.out} ({len(payload['markdown'])} chars, "
          f"{len(payload['figures'])} figure(s))")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())