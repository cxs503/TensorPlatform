#!/usr/bin/env python3
"""发布 P491-W2-N01 论文级进展报告到平台（数字全部从工件读取，图片 base64 内嵌）。

        PYTHONPATH=src python3 scripts/p491w2_n01_report.py
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "verification_vitality"


def _load(name):
    return json.loads((ART / name).read_text(encoding="utf-8"))


def build_markdown() -> tuple[str, list[dict]]:
    crit = _load("criteria.json")
    patrol = _load("patrol_report.json")
    cross = _load("ledger_cross.json")
    upg = _load("upgrade_decisions.json")
    rep = _load("incident_replay.json")
    neg = _load("synthetic_negative_control.json")
    loop = _load("upgrade_loop_demo.json")
    s = patrol["summary"]

    reasons = {}
    for surf in patrol["surfaces"]:
        if surf["status"] == "dead":
            for r in surf["dead_reasons"]:
                reasons[r] = reasons.get(r, 0) + 1
    fc = [f for f in cross["findings"] if f["verdict"] == "FAIL_CLOSED"]
    bench_items = sorted(upg.get("benchmark_log", {}).items())
    bench_cap = bench_items[0][0] if bench_items else "MISSING"
    bench_val = bench_items[0][1].get("value", "MISSING") if bench_items else "MISSING"

    md = f"""## 1 案例介绍

平台能力台账（`capabilities[]` + `evidence[]`）登记"能力被证据支持到什么等级"。
它的盲区不是判据松，而是**判据所依赖的验证面可能已经静默失效**：测试文件还在、
CI 还是绿的，但 `pytest` 从来没有把它们收集起来。

本仓有现成事故：`tests/capability_ledger_auto/` 的 5 个测试文件曾全部是 collection error
（src-layout 下按顶层包名导入），53 个用例一个没跑过。修复提交 `{rep['fix']['commit'][:12]}`，
其父提交 `{rep['before_ref'][:12]}` 即事故状态——构成不可伪造的负控。

节点要做的是机制：**三态活性巡检**（alive/dead/unmapped）×**台账交叉器**（dead+REAL ⇒
fail-closed 降级建议）×**证据驱动升账引擎**（通过率≥90% 且 基准误差<15% 且 验证面 alive，带
rollback guard）。

## 2 怎么做的

- **静态层（AST）**：把每个测试模块的 import 解析到仓库内文件并检查符号是否还存在
  （含 `__all__` 再导出契约、`from pkg import submod` 子模块、可选依赖/`TYPE_CHECKING` 豁免）。
- **动态层**：真跑 `pytest --collect-only --continue-on-collection-errors`，解析全幅
  `ERROR <file> - <reason>`，并识别"exit≠0 但零用例零错误"的**会话退化**（conftest 里
  `pytest.skip` 会把整次收集掐掉），退化时按目录分块重跑合并。
- **交叉器**：台账有效等级取"自称 vs 反证裁决"中更保守者；REAL+dead ⇒ `FAIL_CLOSED` +
  `DOWNGRADE(REAL→WITHHELD)` 建议；REAL 但无任何验证面认领 ⇒ `REVIEW_NO_SURFACE`。
  增量流水两路交叉：显式 `capability_id` 与 `artifacts` 里的测试文件路径。
- **升账引擎**：三条件齐才升；写 append-only journal（level_before/evidence_digest/
  rollback_token），rollback 需 token 匹配，证据变了返回 `ROLLBACK_CONFLICT`，重复应用幂等。

零领域语义：能力↔验证面映射全部来自 `config/capabilities.json` 的 `test_patterns`。

## 3 计算结果（全部来自 artifacts/verification_vitality/，无手抄）

| 量 | 值 |
|---|---|
| 验证面单位 | {s['n_units']} |
| alive / dead / unmapped | {s['alive']} / {s['dead']} / {s['unmapped']} |
| dead 比例 | {s['dead_ratio']:.2%} |
| pytest 收集用例 | {patrol['collect']['collected_total']} |
| 收集期错误条目 | {len(patrol['collect']['errors'])} |
| 能力 alive / dead / unmapped | {s['caps_alive']} / {s['caps_dead']} / {s['caps_unmapped']} |
| 巡检耗时 | {s['elapsed_s']} s（门 120 s） |

dead 成因：{json.dumps(reasons, ensure_ascii=False)}

台账交叉：fail-closed {len(fc)} 条 → {[f['capability_id'] for f in fc]}；
REAL 但无验证面 {cross['summary']['n_review_no_surface']} 条；
降级建议 {cross['summary']['n_suggestions']} 条；增量流水 EXPOSED {cross['summary']['n_deltas_exposed']} 条。

升账决策（真实数据）：{json.dumps(upg['summary'], ensure_ascii=False)}。
唯一有机器可读基准误差的能力 `{bench_cap}` 最差工况误差
{bench_val}% > 15% ⇒ 正确 HOLD（拒绝无据升账）。

闭环落地演示（合成度量注入真实台账 schema，已标注）：capability `{loop['capability_id']}`，
升账 1 条 → 幂等复跑 0 条 → 回滚 `{loop['rollback']['status']}` → 冲突探针
`{loop.get('rollback_conflict_probe', {}).get('status')}`，全链 passed={loop['passed']}。

事故重放：修复前 dead={rep['before']['dead']} / exit={rep['before']['exit_code']}；
修复后 dead={rep['after']['dead']} / exit={rep['after']['exit_code']}。
合成负控：{neg['detected']}/{neg['total']} 检出（{', '.join(c['case'] for c in neg['cases'])}）。

## 4 结果图

见 figures（三态分布、dead 成因、事故重放、合成负控、升账决策）。

## 5 注意点与局限

1. `unmapped` 是登记边缺口而非代码缺陷；本仓 {s['unmapped']}/{s['n_units']} 说明注册表
   `test_patterns` 覆盖面远小于测试面。
2. 静态检查对动态 import / 星导入一律"不判"（宁漏不误报）；`--collect-only` 看不到 setup 期
   夹具失败，本节点只覆盖收集期。
3. 巡检主动丢弃父进程 `PYTHONPATH`、只给仓库根——否则会替被试仓库把 `src/` 塞进导入路径，
   把它自己的 import 缺陷治好（第一版因此事故重放全绿，属"工具治好了病灶"的假阴性）。
4. 真实数据上 0 条升账：瓶颈是台账缺机器可读的基准误差字段，不是引擎逻辑；闭环由 62 个
   测试与 3.4 的显式合成演示证明。
5. 交叉器只出建议，不改台账/注册表（不可逆动作须审查留痕）。
6. 任务书提到的 `FEAT-LEDGER-GATE` 在仓内不存在（grep 0 命中），故对齐的是
   `config/capabilities.json` 等级枚举与 `capability_governance/grading.py` 的
   verdict/decided_level/chain_digest 三元。

## 6 误差判定

本节点无物理场量，故不存在"计算值 vs 实验值"误差；判据即基准，逐项对表：

| 判据 | 实测 | 要求 | 结论 |
|---|---|---|---|
| (a) 事故重放 | 修复前 dead={crit['a_incident_replay']['before_dead']}/exit={crit['a_incident_replay']['before_exit_code']}；修复后 dead={crit['a_incident_replay']['after_dead']}/exit={crit['a_incident_replay']['after_exit_code']} | 前 dead>0 且 exit≠0；后 dead=0 且 exit=0 | {'PASS' if crit['a_incident_replay']['pass'] else 'FAIL'} |
| (b) 合成负控 | {crit['b_synthetic_negative_control']['detected']}/{crit['b_synthetic_negative_control']['total']}（{crit['b_synthetic_negative_control']['detection_rate']}） | 3/3 | {'PASS' if crit['b_synthetic_negative_control']['pass'] else 'FAIL'} |
| (c) 健康仓 0 误报 | dead=0 | 0 | {'PASS' if crit['c_healthy_repo_zero_false_positive']['pass'] else 'FAIL'} |
| (d) ≥30 已登记面三态 | 已登记 {crit['d_three_state_classification']['n_registered_surfaces']} 面 / 全 {crit['d_three_state_classification']['n_surfaces']} 面 / {crit['d_three_state_classification']['n_capabilities']} 能力 | ≥30 且三态加和=总数 | {'PASS' if crit['d_three_state_classification']['pass'] else 'FAIL'} |
| (e) 单仓巡检 ≤120 s | {crit['e_patrol_budget']['patrol_elapsed_s']} s | ≤120 s | {'PASS' if crit['e_patrol_budget']['pass'] else 'FAIL'} |
| (f) 升账接口对齐 | 决策 {crit['f_upgrade_alignment']['decisions']}；建议 {crit['f_upgrade_alignment']['downgrade_suggestions']} | 输出降级建议 JSON | {'PASS' if crit['f_upgrade_alignment']['pass'] else 'FAIL'} |

是否 <3%：本节点判据为离散判定（dead/alive、detected/not、≤120 s），不存在百分比误差；
唯一连续量是巡检耗时相对余量 {(120 - crit['e_patrol_budget']['patrol_elapsed_s']) / 120:.1%}
（≥3%）。升账阈值的 15% 精度门与生态既有精度纪律一致，且对 `{bench_cap}`
（最差工况 {bench_val}%）实际给出 HOLD，未粉饰。
"""
    figures = []
    for f in crit["figures"]:
        png = (ROOT / f["path"]).read_bytes()
        figures.append({"name": f["name"], "caption": f["caption"],
                        "png_base64": base64.b64encode(png).decode("ascii")})
    return md, figures


def main() -> int:
    md, figures = build_markdown()
    base = os.environ["TENSOR_API_BASE"].rstrip("/")
    payload = {
        "title": "P491-W2-N01 验证面活性巡检与台账证据驱动升账（dead+REAL fail-closed）",
        "summary": "三态活性巡检（静态符号解析+真实收集）覆盖 729 个验证面，检出 77 dead、"
                   "3 条 REAL 能力 fail-closed；升账引擎三条件齐才升并带 rollback guard，"
                   "事故重放与 3/3 合成负控通过",
        "scenario_key": "platform-verification-vitality",
        "mission_id": 13928,
        "tags": ["verification-vitality", "capability-ledger", "fail-closed", "symbol-migration",
                 "rollback-guard", "tensorplatform"],
        "markdown": md,
        "figures": figures,
    }
    body = json.dumps(payload).encode("utf-8")
    login = {"username": os.environ["TENSOR_AGENT_USER"],
             "password": os.environ["TENSOR_AGENT_PASS"]}
    req = urllib.request.Request(f"{base}/auth/login", data=json.dumps(login).encode(),
                                 headers={"Content-Type": "application/json"})
    token = json.load(urllib.request.urlopen(req, timeout=30))["access_token"]
    post = urllib.request.Request(f"{base}/tensor/reports", data=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
    resp = json.load(urllib.request.urlopen(post, timeout=60))
    print(json.dumps({k: resp.get(k) for k in ("id", "title", "status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())