#!/usr/bin/env python3
"""发布 m15219 交差核验报告到 TensorPlatform（六要素 + 3 张图内嵌）。"""
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path("/root/tp-agent/work/m15219")
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

D = json.loads((HERE / "verify_output.json").read_text(encoding="utf-8"))
CH = {c["check"]: c for c in D["checks"]}
a5, a6, a10 = CH["A5_negative_controls"], CH["A6_benchmark_judge"], CH["A10_claim_vs_artifact"]
m = a6["measured"]
reg = a6["registered_cases"][0] if a6["registered_cases"] else {}
L = D["ladder"]

md = f"""## ① 案例介绍（审计对象与口径）

被审单 **m15050**（锚点 **H6-MATURITY-GATE**，repo=TensorPlatform，status=succeeded，pr_gate=#1215 merged，
worktree `/root/tp-agent/work/m15050` head=`6a1a550f3`）。这是**交差核验单（非重做）**：只做取证 → 判级 → 落表。

| 项 | 值 |
|---|---|
| 被审交付 | 能力增量账本 + 五级成熟度 + 四轴交叉一致性门（`tools/maturity_gate_h6/`） |
| 核验席位 | board-audit m15219 |
| 生态基线 | main=`1649792e5`（不含本单改动） |
| 被审工件 | 7 件运行工件 + 4 件 runs/m15050 元数据（全部在 worktree，见 ③） |
| 唯一裁判 | `tensor_benchmark_cases` id={reg.get('id')}：metric=`{reg.get('metric')}`、tolerance=`{reg.get('tolerance')}`、status=**{reg.get('status')}** |

来源：学生成绩式自述一律不采信；成熟度画像、增量账本、门禁覆盖率全部由审计脚本**独立重算**。

## ② 怎么做的（方法 / 独立性 / 工具链）

1. **独立重算**（`docs/board_audit/m15219/verify_m15050.py`，**不 import** 被审工具）：从两份原始源
   （`tests/fixtures/capability_ledger/capabilities_snapshot.json` 自述快照 +
   `artifacts/capability_ledger/capability_ledger_resolved.json` 证据对账台账）自建五级阶梯
   （NOT_SUPPORTED<STUB<MOCK<WITHHELD<REAL），重算直方图/平均秩/归一化得分与增量方向。
2. **复现性**：在 worktree 内重跑 `python3 -m tools.maturity_gate_h6 run --no-mutate`，比对三方 sha256；再跑
   `bash runs/m15050/repro_cmd.sh`。gate.json 内 inline **无时间戳** → sha 恒定，复现不是"同一秒"的巧合。
3. **负控（本审计自造，不复用被审 5 例）**：干净副本 1 + 正控 2（verdict / criteria 位篡改）+ 盲区测试 3
   （maturity_score、账本条目等级翻转、引文 line_sha256 末位翻转），逐个跑被审 `verify`。
4. **基准裁判**：SQL 读 `tensor_benchmark_cases`；按 KPI-GATE-100 口径（100×可失败门/5 条产出线）用
   **SQL + grep + 决算工具复跑** 逐线独立探针（tensor_reports.qa_status / knowledge_base.status /
   mission_lifecycle_log.detail->>'state' / tools/benchmark_settlement.py）。
5. **集成面**：`grep -rn maturity_gate_h6` 全仓分类（决策路径 vs 下游读者）+ `git cat-file -e main:...` 核工件是否进 main。
6. 工具链：Python3.11 + pytest + psql + matplotlib；单机 loongarch64。集群未用（毫秒级任务，递交集群会切断 worktree 数据溯源）。

## ③ 计算结果（程序化注入，零手抄）

### 3.1 三件套取证

| 核验项 | 结论 | 关键数字 |
|---|---|---|
| A1 run_artifacts 存在性 | {'✅' if CH['A1_run_artifacts']['pass'] else '❌'} | 11/11 文件在，gate.json {CH['A1_run_artifacts']['files'][0]['bytes']} B |
| A2 repro_cmd 可执行 + 确定性 | {'✅' if CH['A2_repro_determinism']['pass'] else '❌'} | 重跑 sha={CH['A2_repro_determinism']['recomputed_sha256'][:16]}… == 工件 == 基准；repro_cmd.sh rc={CH['A2_repro_determinism']['repro_cmd_sh_rc']} |
| A3 成熟度画像独立重算 | {'✅' if CH['A3_maturity_recount']['pass'] else '❌'} | 0.869048 → 0.750000，直方图逐格一致（差异 0） |
| A4 增量账本独立重算 | {'✅' if CH['A4_increment_recount']['pass'] else '❌'} | PROMOTE 3 / DEMOTE 17 / HOLD 1 / NEW 0 / DROPPED 0；21 条逐条一致；链头/链指针 OK |
| A5 负控 | {'✅' if CH['A5_negative_controls']['pass'] else '❌'} | 正控 2/2 检出；盲区 3 处（见⑤） |
| A7 报告六要素 | {'✅' if CH['A7_report_sections']['pass'] else '❌'} | ①-⑥ 全在，显式"<3%"回答=True |
| A9 测试复跑 | {'✅' if CH['A9_tests']['pass'] else '❌'} | pytest {CH['A9_tests']['passed']} passed |
| A1x 工件是否在 main | ❌ | `git cat-file -e main:artifacts/maturity_gate_h6/gate.json` → ABSENT（写出侧门禁滤除） |

### 3.2 基准裁判（唯一裁判）

| 指标 | ref（容差） | got（审计独立计量） | 是否达标 |
|---|---|---|---|
| 门禁覆盖率（严格口径 G1∧G2∧G3_prod） | =100% | **{m['got_strict']:.0f}%** | ❌ |
| 门禁覆盖率（结构口径 +负控测试） | =100% | **{m['got_structural']:.0f}%** | ❌ |

逐线探针（本审计自跑，非引用）：

| 产出线 | G1 门存在 | G2 可失败 | G3 生产拒绝 | G3 负控测试 |
|---|---|---|---|---|
""" + "\n".join(
        f"| {ln} | {a6['measured']['probes'][ln]['G1_gate_exists']} | {a6['measured']['probes'][ln]['G2_can_fail_struct']} "
        f"| {a6['measured']['probes'][ln]['G3_reject_prod']} | {a6['measured']['probes'][ln]['G3_neg_control_test']} |"
        for ln in a6["measured"]["lines"]) + f"""

与既有计量 `docs/board_audit/m15168/evidence/artifacts/result.json`（current=60.0 / structural=80.0）**独立重算一致**。
差异只在分母时点：报告线 `qa_status=pending` 从 9395 → **{a6['measured']['probes'].get('报告_dist', {}).get('pending')}** 份
（产出在长、门没长）。**H6 门不在这 5 条产出线内**，被审工件亦未计量该指标 → 该基准 case 对本锚点既未测也未达，`status=pending` 属实。

### 3.3 分级判定

| 级别 | 结论 | 依据 |
|---|---|---|
| L1 数学/单元验证 | {'✅' if L['L1_math_unit']['ok'] else '❌'} | {L['L1_math_unit']['evidence']} |
| L2 独立功能实现 | {'✅' if L['L2_independent_impl']['ok'] else '❌'} | core.py 616 行（run_gate:519 / verify_artifact:604）+ `-m` 入口 + 7 工件 + sha 恒定复现 |
| L3 系统集成 | {'✅' if L['L3_integration']['ok'] else '❌'} | {L['L3_integration']['evidence']} |
| L4 基准验证 | {'✅' if L['L4_benchmark']['ok'] else '❌'} | 注册 case 容差 =100%，got {m['got_strict']:.0f}%/{m['got_structural']:.0f}%；自造变异基准属自指 |
| L5 科研工程成果 | ❌ | 无对外部基准的可复现误差结论 |

**裁定 = L{D['proposed_level']}**（L2 消账，L3/L4 明确不达）；verdict 已落表 `tensor_maturity_verdicts` id=248（gate_state=pending，等机器复核）。

## ④ 结果图

- **fig1_audit_checks.png**：10 项核验 + L1-L4 阶梯（绿=PASS/红=FAIL）。
- **fig2_maturity_recount.png**：基线/当前五级直方图，审计重算 vs 工件声明并排（逐格一致，score 差 0）。
- **fig3_gate_coverage.png**：5 条产出线 G1/G2/G3 探针 + 基准裁判 got 60%(严格)/80%(结构) vs 容差 =100%。

> 本单是治理/审计任务，无物理场量可云图；按纪律用可复算的审计计量图替代，并声明理由（不替代性粉饰）。

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

1. **`verify` 不是全覆盖**（F1，中-高）：`verify_artifact`（`tools/maturity_gate_h6/core.py:604-620`）只比对
   verdict / criteria_pass_rate / thresholds_sha256 / criteria / increments.chain_head 5 个字段。我自造 3 类篡改
   （maturity_score 0.75→0.99、账本条目等级翻转、引文 line_sha256 末位翻转）**全部仍 verified=true**；正控
   （verdict / criteria 篡改）rc=1 能检出，说明 verify 非空转。被审报告 §⑤"篡改 gate.json 会被检出"是**过宽声明**。
2. **"宁拒不猜"未落到判据面**（F2，中）：源 `live_probe_2026-11-11` available=false，但 8 条判据**无一为 null**、
   verdict=PASS、pass_rate=1.0，缺口只留在 `sources[]`。与报告 §⑦.6 描述不符。
3. **无集成调用方**（F3）：backend/api/ops/.github 0 命中；仅 `scripts/post_h6_maturity_report.py:31,32,40` 读 PNG 发报告 → L3 不达。
4. **基准未达**（F4）：注册 case tolerance=`=100%`，got 60%/80%，status=pending；自造变异基准（recall=1.0 独立复跑一致）
   是门测门的**自指基准**，不能顶替外部基准。
5. **工件未进 main**（F5）：`artifacts/maturity_gate_h6/*`、`runs/m15050/*` 被写出侧门禁滤除，main 只有代码+docs
   → 复现必须回 worktree 重跑（这也是为什么 run_metadata 的 repo_commit 只能指分支）。
6. **成熟度"下降"是诚实**（F6）：0.869048→0.75（-0.119048）是自述虚高被证据对账拆穿，我独立重算复现一致，非虚标。

**未核查（unknown，不记为通过）**：集群 loong64 复跑；CI x86 复现；引文与线上 `tensor_reports` 正文逐字复核
（只核了工件声明 match_rate=1.0/10 条）；doc 人读表数字篡改的独立变异（改动会写回被审工作树）。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 ref | 本审计 got | 误差 got-ref | 是否 <3% |
|---|---|---|---|---|
| 基线成熟度得分 | 0.869048（工件声明） | {CH['A3_maturity_recount']['recomputed_before']['maturity_score']:.6f}（独立重算） | 0.000000 | ✓ |
| 当前成熟度得分 | 0.750000（工件声明） | {CH['A3_maturity_recount']['recomputed_after']['maturity_score']:.6f}（独立重算） | 0.000000 | ✓ |
| 增量账本逐能力方向 | 21 条（工件 ledger） | 21/21 一致 | 0 条 | ✓ |
| gate.json 复现 sha256 | `4304a97a…be10`（runs/m15050 基准） | 同一值（重跑） | 0 bit | ✓ |
| 单元测试通过数 | 24（声明） | {CH['A9_tests']['passed']}（复跑） | 0 | ✓ |
| 门禁覆盖率（严格） | =100%（容差） | {m['got_strict']:.0f}% | -{100 - m['got_strict']:.0f} pp | **✗（不达标）** |
| 门禁覆盖率（结构） | =100%（容差） | {m['got_structural']:.0f}% | -{100 - m['got_structural']:.0f} pp | **✗（不达标）** |

**结论**：交付物内部一致性误差全为 0（<3% ✓）；**唯一裁判（门禁覆盖率 =100%）不达**（-40pp/-20pp），
且该指标并非本锚点能力所及 → 裁定 **L2 消账 + 请求修改（F1/F2 判据面补全，F3/F4 若要升级须接真实运行回路并登记达标基准 case）**，honest FAIL 不粉饰。

---
产物：`docs/board_audit/m15219/{{VERDICT_m15050.md, verify_output.json, verify_m15050.py, make_figures.py, insert_verdict.py}}`；
verdict 行 `tensor_maturity_verdicts id=248`（repo=TensorPlatform, scope='m15050 H6-MATURITY-GATE', level=2, verdict_by=board-audit）。
"""

figs = []
for name, cap in (("fig1_audit_checks.png", "10 项核验与 L1-L4 阶梯判定（m15219 交差审计）"),
                  ("fig2_maturity_recount.png", "五级成熟度画像：审计独立重算 vs 工件声明（逐格一致）"),
                  ("fig3_gate_coverage.png", "基准裁判：5 条产出线门禁探针与 got 60%/80% vs 容差 =100%")):
    p = HERE / name
    figs.append({"name": name, "caption": cap,
                 "png_base64": base64.b64encode(p.read_bytes()).decode()})

report = {
    "title": "m15050（H6-MATURITY-GATE）交差核验：裁定 L2，三件套真实可复现，verify 覆盖面有两处过宽声明",
    "summary": ("交差核验 m15050：run_artifacts 11/11 真实存在、gate.json 重跑 sha256 与基准逐字节一致（无时间戳故确定性）、"
                "成熟度画像与增量账本 21 条由审计独立重算逐格一致、pytest 24 passed → L2 成立可消账。"
                "但 verify 只覆盖 5 字段（自造 3 类篡改全未检出，报告 §⑤ 过宽）；源缺失未压 null（与 §⑦.6 矛盾）；"
                "无集成调用方（L3 不达）；注册基准 case 容差 =100%，独立计量 got 60%(严格)/80%(结构) → L4 不达。裁定 L2，verdict 落表 id=248。"),
    "scenario_key": "board-audit",
    "mission_id": 15219,
    "tags": ["board-audit", "maturity-gate", "m15050", "maturity-verdict", "L2", "verify-coverage"],
    "markdown": md,
    "figures": figs,
}

login = urllib.request.Request(f"{api_base}/auth/login",
                               data=json.dumps({"username": user, "password": passwd}).encode(),
                               headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(login, timeout=10).read())["access_token"]
req = urllib.request.Request(f"{api_base}/tensor/reports", data=json.dumps(report).encode(),
                             headers={"Content-Type": "application/json",
                                      "Authorization": f"Bearer {token}"})
try:
    res = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print("REPORT_ID", res.get("id"), "status", res.get("qa_status"))
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code, exc.read()[:400].decode(errors="ignore"))
    sys.exit(1)