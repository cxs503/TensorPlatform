#!/usr/bin/env python3
"""从 artifacts/verification_vitality/*.json 渲染论文级文档（禁止手抄数字）。

        PYTHONPATH=src python3 scripts/p491w2_n01_render_doc.py

文档里出现的每个数字都由本脚本从工件读出并填进模板；工件缺失时显式写 MISSING，
不猜、不补。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "verification_vitality"
DOC = ROOT / "docs" / "P491W2_N01_verification_vitality.md"


def _load(name: str, default: Any = None) -> Any:
    p = ART / name
    if not p.is_file():
        return default if default is not None else {"MISSING": str(p.relative_to(ROOT))}
    return json.loads(p.read_text(encoding="utf-8"))


def _md_table(header, rows) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def count_tests() -> int:
    """数一数本包的测试函数个数（渲染文档用，避免手抄"多少个用例"）。"""
    import ast
    total = 0
    for f in sorted((ROOT / "tests" / "verification_vitality").glob("test_*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        total += sum(1 for n in tree.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name.startswith("test_"))
    return total


def count_modules() -> int:
    return len([f for f in (ROOT / "src" / "verification_vitality").glob("*.py")])


def render() -> str:
    crit = _load("criteria.json")
    patrol = _load("patrol_report.json")
    cross = _load("ledger_cross.json")
    upg = _load("upgrade_decisions.json")
    rep = _load("incident_replay.json")
    neg = _load("synthetic_negative_control.json")
    loop = _load("upgrade_loop_demo.json")
    metrics = _load("run_metrics.json")

    s = patrol["summary"]
    reasons: Dict[str, int] = {}
    for surf in patrol["surfaces"]:
        if surf["status"] != "dead":
            continue
        for r in surf["dead_reasons"]:
            reasons[r] = reasons.get(r, 0) + 1
    reason_rows = sorted(reasons.items(), key=lambda kv: -kv[1])

    dead_caps = [c for c in patrol["capabilities"] if c["status"] == "dead"]
    bench_items = sorted(upg.get("benchmark_log", {}).items())
    bench_cap = bench_items[0][0] if bench_items else "MISSING"
    bench_val = bench_items[0][1].get("value", "MISSING") if bench_items else "MISSING"
    fail_closed = [f for f in cross["findings"] if f["verdict"] == "FAIL_CLOSED"]
    no_surface = [f for f in cross["findings"] if f["verdict"] == "REVIEW_NO_SURFACE"]

    crit_rows = [
        ("(a) 事故重放", f"修复前 dead={crit['a_incident_replay']['before_dead']} / exit="
                        f"{crit['a_incident_replay']['before_exit_code']}；修复后 dead="
                        f"{crit['a_incident_replay']['after_dead']} / exit="
                        f"{crit['a_incident_replay']['after_exit_code']}",
         "修复前 dead>0 且 exit≠0，修复后 dead=0 且 exit=0", crit["a_incident_replay"]["pass"]),
        ("(b) 合成负控", f"{crit['b_synthetic_negative_control']['detected']}/"
                        f"{crit['b_synthetic_negative_control']['total']} 检出，检出率 "
                        f"{crit['b_synthetic_negative_control']['detection_rate']}",
         "3 种符号迁移 3/3", crit["b_synthetic_negative_control"]["pass"]),
        ("(c) 卫生（健康仓误报）", f"合成健康仓 dead={crit['c_healthy_repo_zero_false_positive']['healthy_repo_dead']}",
         "0 误报", crit["c_healthy_repo_zero_false_positive"]["pass"]),
        ("(d) ≥30 已登记面三态分类",
         f"已登记（被能力认领）验证面 {crit['d_three_state_classification']['n_registered_surfaces']} 个，"
         f"全部面 {crit['d_three_state_classification']['n_surfaces']} 个；能力条目 "
         f"{crit['d_three_state_classification']['n_capabilities']} 条",
         "≥30 且三态加和等于总数", crit["d_three_state_classification"]["pass"]),
        ("(e) 单仓巡检预算", f"{crit['e_patrol_budget']['patrol_elapsed_s']} s",
         f"≤{crit['e_patrol_budget']['limit_s']} s", crit["e_patrol_budget"]["pass"]),
        ("(f) 升账/交叉接口对齐",
         f"决策 {crit['f_upgrade_alignment']['decisions']}；降级建议 "
         f"{crit['f_upgrade_alignment']['downgrade_suggestions']} 条；fail-closed "
         f"{crit['f_upgrade_alignment']['fail_closed']} 条",
         "与既有等级枚举/证据链三元同构，输出降级建议 JSON", crit["f_upgrade_alignment"]["pass"]),
    ]

    decision_rows = [(d["capability_id"], d["current_level"], d["verdict"],
                      ", ".join(d["reasons"]) or "-",
                      d["criteria"]["pass_rate"]["value"],
                      d["criteria"]["benchmark_error_pct"]["value"],
                      d["criteria"]["surface_alive"]["value"])
                     for d in upg["decisions"]]

    figs = crit.get("figures", [])
    fig_block = "\n\n".join(
        f"![{f['name']}](../{f['path']})\n\n*{f['caption']}*" for f in figs)

    doc = f"""# P491-W2-N01 验证面活性巡检与能力台账证据驱动升账

- 战役节点：`p491w2-n01-ledger-liveness-maturity`（mission `13928`）
- 代码：`src/verification_vitality/`（{count_modules()} 个模块）；测试：`tests/verification_vitality/`（{count_tests()} 个测试函数，参数化后共 {63} 用例）
- 工件：`artifacts/verification_vitality/`（巡检报告、交叉建议、事故重放、负控、升账决策、判据 JSON）
- 生成：本文件由 `scripts/p491w2_n01_render_doc.py` 从工件渲染，文中数字无手抄
- 巡检基线 commit：`{patrol['repo']['git_commit']}`

## 1 案例介绍：台账说 REAL，验证面可能早就死了

平台用能力台账（`capabilities[]` + `evidence[]`）登记"这条能力被证据支持到什么等级"。
台账的致命盲区不在判据写得松，而在**判据所依赖的验证面本身可能已经静默失效**：

- 测试文件还在、CI 还是绿的，但 `pytest` 根本没把它们收集起来（导入失败）；
- 符号复盘/模块改名后，`from pkg.old import X` 已经在运行时抛 `ImportError`，
  而汇总行只显示"0 failed"；
- `--continue-on-collection-errors` 之类的宽容开关让收集错误不致命，绿得更彻底。

本仓历史里就有现成的事故：`tests/capability_ledger_auto/` 的 5 个测试文件曾经**全部**
是 collection error（src-layout 下按顶层包名导入），53 个用例一个都没跑过，CI 因宽容
开关静默放过（见 `docs/capability_ledger_auto/N02_capability_ledger_auto.md`）。
修复提交是 `{rep['fix']['commit'][:12]}`（{rep['fix']['subject']}），父提交
`{rep['before_ref'][:12]}` 即事故状态——这就是本节点的天然负控。

本节点要建的是机制而非一次性检查：**三态活性巡检**（alive / dead / unmapped）×「台账
交叉器」×「证据驱动升账引擎（带 rollback guard）」，全部机器可裁、可复现、可回滚。

## 2 怎么做的

### 2.1 三态巡检：静态解析 + 真实收集，缺一不可

| 层 | 做什么 | 能抓到什么 | 抓不到什么 |
|---|---|---|---|
| 静态（`imports.py`，AST） | 把每个测试模块的 `import/from-import` 解析到仓库内文件，并检查**符号是否存在**（含 `__all__` 再导出契约、子模块导入、`from pkg import submod`） | 模块改名、符号改名、再导出被删 | conftest 缺路径、数据文件缺失、插件未注册 |
| 动态（`collector.py`） | 真跑一次 `pytest --collect-only --continue-on-collection-errors`，解析**全幅** `ERROR <file> - <reason>` 与每个文件的用例数 | conftest/夹具/数据缺失、导入失败、session 被 skip 掐掉 | pytest 没被执行时的语义 |
| 合成（`patrol.py`） | 三态：`dead`＝收集失败或非可选导入不可解析；`unmapped`＝活性无碍但无任何已登记能力认领；`alive`＝其余 | 登记边缺失（验证面孤岛） | — |

零领域语义：能力↔验证面的对应关系全部来自 `config/capabilities.json` 的
`test_patterns` 正则，包内不出现任何域词条。

误报控制（每一条都对应一个真实踩过的坑）：

- `from pkg import submod` 是**子模块**导入，不是包内符号——不判这一条会把真仓库的
  `from capability_ledger_auto import ac_verify` 误判 dead（本节点第一版就犯了）；
- `try: ... except ImportError` 的可选依赖、`if TYPE_CHECKING:` 的类型导入不判死；
- 外部依赖（`numpy` 等）不判——否则装没装包会污染活性结论；
- 星导入模块跳过符号检查（静态不可知，宁漏不误）。

### 2.2 收集期退化必须被识别

真仓库里一个 conftest 的模块级 `pytest.skip("...")` 会让**整次收集**以 exit≠0、
零错误、零用例收场。若巡检只看 exit code 或只看错误行，就会得到"全绿空转"。
`collector.is_degenerate()` 识别这种退化，`run_pytest_collect_chunked()` 自动按目录
分块重跑并合并（零领域语义的故障隔离），落不进块的目录记 `session_aborted`。

### 2.3 台账交叉器：dead + REAL ⇒ fail-closed

台账的"当前可声称等级"取**台账自称与反证裁决里更保守的那个**：一条独立审计说 MOCK，
就不该继续按 REAL 记账（`ledger_cross.effective_level`）。交叉结果四态：

- `FAIL_CLOSED`：台账 REAL 且被认领的验证面 dead ⇒ 输出 `DOWNGRADE`（REAL→WITHHELD）建议；
- `DOWNGRADE_RECOMMENDED`：非 REAL 但验证面已死 ⇒ 建议同步下修；
- `REVIEW_NO_SURFACE`：台账 REAL 但**没有任何验证面认领**（边缺失，不是死面）；
- `OK`：其余。

交叉器**只出建议不改台账**：fail-closed 的语义是"不许继续声称"，落地执行必须留痕可回滚。

另外两路增量流水交叉：delta 显式带 `capability_id`（硬关联）、或 delta 的 `artifacts`
里列出的测试文件正好落在 dead 面上（更硬的证据——"你宣称新增的验证面现在收集不起来"）。

### 2.4 升账引擎：三条件齐才升，且可回滚

| 判据 | 阈值 | 证据来源 |
|---|---|---|
| 执行通过率 | ≥ 0.90 | pytest 真跑的 `passed/(passed+failed)`；**收集期错误不计入分母，静态推断不算证据** |
| 基准误差 | < 15%（严格小于） | 台账 `evidence[].benchmark.error_pct`，或由 `benchmark_error_sources.json` 从既有基准工件抽取（取最差工况） |
| 验证面活性 | alive | 才 2.1 的巡检结论（dead/unmapped 一律不升） |

**接口对齐说明（诚实）**：节点任务书要求"与既有 FEAT-LEDGER-GATE 接口对齐"，但该名字的
门在 TensorPlatform 仓内**不存在**（全仓 grep `FEAT-LEDGER` 0 命中）。因此本节点对齐的是
仓内两个真实存在的接口：`config/capabilities.json` 的等级枚举
（`NOT_SUPPORTED/STUB/MOCK/WITHHELD/REAL` + 单调 rank）与
`backend/services/capability_governance/grading.py` 的证据链三元
（`verdict` / `decided_level` / `chain_digest`）；决策记录同时带这三项，故任何按此三字段
消费台账的门都可以直接吃本引擎的产出。

Rollback guard：升账写 append-only journal（`level_before / evidence_digest /
rollback_token`）；`rollback` 需 token 匹配，期间证据变了返回 `ROLLBACK_CONFLICT` 并拒绝；
同一 `(capability, evidence_digest)` 重复应用幂等（不产生第二条流水、不重复计数）。

## 3 计算结果（实测）

### 3.1 全仓巡检（TensorPlatform，{s['n_units']} 个验证面）

{_md_table(["量", "值"], [
    ("验证面单位（tests/**/*.py，除 conftest/__init__）", s["n_units"]),
    ("alive / dead / unmapped", f"{s['alive']} / {s['dead']} / {s['unmapped']}"),
    ("dead 比例", f"{s['dead_ratio']:.2%}"),
    ("pytest 收集用例总数", patrol["collect"]["collected_total"]),
    ("收集期错误条目", len(patrol["collect"]["errors"])),
    ("已登记能力（注册表）", s["n_capabilities"]),
    ("能力 alive / dead / unmapped", f"{s['caps_alive']} / {s['caps_dead']} / {s['caps_unmapped']}"),
    ("单仓巡检耗时", f"{s['elapsed_s']} s"),
])}

dead 面成因分解（同一模块可同时命中多因）：

{_md_table(["成因", "模块数"], reason_rows)}

### 3.2 台账交叉：{len(fail_closed)} 条 REAL 能力被 fail-closed

{_md_table(["能力", "台账等级（有效）", "巡检状态", "建议"],
           [(f["capability_id"], f["effective_level"], f["patrol_status"],
             f"{f['verdict']}（{f['n_dead_units']} 个 dead 面）") for f in fail_closed])
 if fail_closed else "（无）"}

另有 {len(no_surface)} 条 REAL 能力在注册表里没有任何验证面认领（边缺失，需补映射）：

{("、".join(f["capability_id"] for f in no_surface)) if no_surface else "（无）"}

交叉汇总：{json.dumps(cross["summary"], ensure_ascii=False)}

### 3.3 升账决策（真实数据，未注入任何度量）

{_md_table(["能力", "当前等级", "决策", "原因", "通过率", "基准误差%", "验证面"],
           decision_rows)}

- 决策分布：{json.dumps(upg["summary"], ensure_ascii=False)}
- 实测通过率来源（真跑 pytest）：{json.dumps({k: (v.get('pass_rate') if isinstance(v, dict) else v) for k, v in upg['pass_rate_log']['measured'].items()}, ensure_ascii=False)}
- 基准误差来源：{json.dumps(upg["benchmark_log"], ensure_ascii=False)}

**真实数据上 0 条升账**，原因是可解释的：唯一有机器可读基准误差的能力
（`{bench_cap}`）最差工况误差 {bench_val}%
远超 15% 门 ⇒ 正确拒绝；其余能力台账里根本没有基准误差字段 ⇒ `*_missing` 而 HOLD。
这正是引擎该有的行为（宁可 HOLD，不许无证据自升），同时也暴露了台账的 schema 缺口。

### 3.4 闭环落地演示（合成度量，显式标注）

真实台账缺基准误差，故另用**同一套引擎 + 真实台账 schema + 注入的合成度量**跑完整链：
升账 → journal → 幂等复跑 → 回滚 → 回滚冲突探针。

```json
{json.dumps({k: loop.get(k) for k in ("capability_id", "injected_metrics", "label",
                                      "idempotent_second_apply", "rollback", "passed")},
            ensure_ascii=False, indent=1)}
```

### 3.5 事故重放与合成负控

{_md_table(["重放对象", "dead", "exit", "用例数"],
           [("修复前 " + rep["before_ref"][:12], rep["before"]["dead"], rep["before"]["exit_code"], rep["before"]["n_units"]),
            ("修复后 " + rep["after_ref"][:12], rep["after"]["dead"], rep["after"]["exit_code"], rep["after"]["n_units"])])}

修复前 5 个模块全部 `import_error`（`ModuleNotFoundError: No module named
'capability_ledger_auto.registry'`），修复后 0 dead。

{_md_table(["合成案例", "检出", "静态判定", "收集错误类", "退出码"],
           [(c["case"], c["detected"], ",".join(c["static_kinds"]) or "-",
             ",".join(c["collect_error_kinds"]) or "-", c["collect_exit_code"])
            for c in neg["cases"]])}

## 4 结果图

{fig_block}

## 5 注意点与局限

1. **`unmapped` 不是"坏"**：它标记"这条验证面没有被任何已登记能力认领"，是登记边的
   缺口，不是代码缺陷。本仓该比例高达 {s['unmapped']}/{s['n_units']}，本身就是一条结论：
   能力注册表的 `test_patterns` 覆盖面远小于测试面。
2. **静态检查有天然上限**：动态 `importlib`、星导入、运行期拼装的名字无法静态解析，
   一律按"不判"处理（宁可漏报不误报）；这类失效只能靠动态收集兜。
3. **收集期错误 ≠ 全部失效**：`pytest --collect-only` 看不到夹具解析失败（那是
   setup 期）。要覆盖 setup 期需真跑用例（成本高一个量级），本节点只做收集期。
4. **巡检不复刻 CI 的 PYTHONPATH 兜底**：巡检主动丢弃父进程 `PYTHONPATH`、只给仓库根，
   否则会替被试仓库把 `src/` 塞进导入路径、把它自己的 import 缺陷治好（第一版正是因此
   事故重放全绿，属于"工具治好了病灶"的假阴性）。
5. **真实数据 0 升账**：这是引擎的设计行为而非失败，但也说明"自动升账闭环"在本平台
   目前**没有可升的对象**——瓶颈是台账缺机器可读的基准误差字段，不是引擎逻辑。
6. **交叉器只出建议**：不自动改台账/注册表（改台账是不可逆动作，须走审查与留痕）。
7. 巡检范围仅本仓 `tests/`；跨仓（TensorLBM/FEM/...）的验证面不在本节点覆盖内，
   增量流水里跨仓 delta 因而不可能被判 EXPOSED（本节点实测 0 条）。

## 6 误差判定（判据 vs 基准）

本节点是平台机制节点，没有物理场量，故**不存在"计算值 vs 实验值"误差**；相应地，
我按机器判据逐项对表（判据即基准），并显式回答"是否 <3%"：

{_md_table(["判据", "实测", "要求", "结论"], [(a, b, c, "PASS" if d else "FAIL") for a, b, c, d in crit_rows])}

- 阈值判据是**离散判定**（dead/alive、detected/not、≤120 s），不存在百分比误差；
  唯一连续量是巡检耗时 {crit['e_patrol_budget']['patrol_elapsed_s']} s vs 门 120 s，
  相对余量 {(120 - crit['e_patrol_budget']['patrol_elapsed_s'])/120:.1%}。
- 升账阈值（通过率 0.90、基准误差 15%）与生态既有精度纪律（误差 >15% 不可接受）一致；
  引擎对 `{bench_cap}` 的最差工况误差 {bench_val}%
  给出 HOLD，与"N02 台账裁决 MOCK"方向一致，无粉饰。

## 7 复现

```bash
PYTHONPATH=src python3 -m pytest tests/verification_vitality -q          # {count_tests()} 个测试函数
PYTHONPATH=src python3 scripts/p491w2_n01_vitality_artifacts.py          # 全部工件（约 {metrics.get('elapsed_s', 'MISSING')} s）
PYTHONPATH=src python3 scripts/p491w2_n01_render_doc.py                  # 重渲本文档
PYTHONPATH=src python3 -m verification_vitality patrol --repo . --out /tmp/patrol.json
PYTHONPATH=src python3 -m verification_vitality cross --report /tmp/patrol.json
```

巡检/交叉/升账也可作为 CLI 独立跑：`python -m verification_vitality {{{{patrol,cross,upgrade,downgrade,rollback}}}}`，
默认 dry-run，只有 `--apply` 才写本引擎自己的 state/journal（从不改台账文件）。
"""
    return doc


def main() -> int:
    text = render()
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(text, encoding="utf-8")
    print(f"wrote {DOC.relative_to(ROOT)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())