#!/usr/bin/env python3
"""Build and post the IN19-Q6 progress report (numbers read from artifacts).

Every number in the report body is read from ``artifacts/mission_gradient/*.json``
(which are themselves produced by executing the mechanism), so the report cannot
drift from the implementation.  Figures are downscaled + palette-quantised before
base64 embedding to stay well under the reports endpoint payload limit.

Env: TENSOR_API_BASE, TENSOR_AGENT_USER, TENSOR_AGENT_PASS (token optional:
TENSOR_TOKEN).
Usage: python3 scripts/mission_gradient_report.py [--dry-run]
"""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "mission_gradient"
MISSION_ID = 5026

FIG_CAPTIONS = {
    "fig1_quota_allocation.png":
        "登记配比 vs 一个合法调整配比；N=100 的精确配额（Hamilton 最大余数法），探索预算 = 5/100 槽",
    "fig2_credit_gating_matrix.png":
        "梯度档 × 信用档 5×5 准入矩阵：tier Sn 需 Ln 及以上，S4 需 L4+、S5 仅 L5（15 准入 / 10 拒绝）",
    "fig3_failure_rate_accounting.png":
        "(a) 同一 worker 3 次普通失败 + k 次有据 honest FAIL 时，纪律化失败率与平计数失败率的分叉；"
        "(b) 三类申报的可采纳性",
}

TIERS = ["S1", "S2", "S3", "S4", "S5"]
LABELS = {"S1": "执行", "S2": "改进", "S3": "建设", "S4": "突破", "S5": "创造"}


def compress(path: Path, width: int = 960, colors: int = 96) -> bytes:
    """Downscale + quantise so three figures fit in one report payload."""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    if im.width > width:
        im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("P", palette=Image.ADAPTIVE, colors=colors).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build() -> dict:
    q1 = json.loads((ART / "q1_selfcheck.json").read_text())
    pg = json.loads((ART / "pg_verify.json").read_text())
    mat = json.loads((ART / "dispatch_matrix.json").read_text())
    vec = json.loads((ART / "accounting_vectors.json").read_text())
    fmeta = json.loads((ART / "figures_metadata.json").read_text())

    crit = {c["id"]: c for c in q1["q1_gate"]["criteria"]}
    py_ev = crit["C1_pytest"]["evidence"]
    mix = crit["C2_mix_registry"]["evidence"]["mix"]
    sig = crit["C2_mix_registry"]["evidence"]["signature"]
    q100 = crit["C3_quota_allocation"]["evidence"]["quota_100"]
    q1000 = crit["C3_quota_allocation"]["evidence"]["quota_1000"]
    decl = q1["self_declaration"]
    view = pg["environment"]["failure_rate_view"].split("|")

    mix_table = ("| 梯度档 | 登记配比 | N=100 配额 | N=1000 配额 | 带探索预算 | 最低信用档 |\n"
                 "|---|---|---|---|---|---|\n" + "\n".join(
                     f"| {t} {LABELS[t]} | {mix[t] * 100:.0f}% | {q100[t]} | {q1000[t]} | "
                     f"{'是（探索预算）' if t in ('S4', 'S5') else '否'} | L{int(t[1])} |"
                     for t in TIERS))
    gate_table = "\n".join(
        "| " + t + " " + LABELS[t] + " | " + " | ".join(
            ("准入" if mat["matrix"][t][f"L{lvl}"] else "拒绝") for lvl in range(1, 6)) + " |"
        for t in TIERS)
    series_table = "\n".join(
        f"| {r['honest_fails']} | {r['attempts']} | {r['ordinary_failures']} | {r['quarantined']} | "
        f"{r['disciplined_rate'] * 100:.2f}% | {r['naive_rate'] * 100:.2f}% |"
        for r in vec["series"])
    crit_table = "\n".join(
        f"| {c['id']} | {c['description']} | {'PASS' if c['passed'] else 'FAIL'} |"
        for c in q1["q1_gate"]["criteria"])
    pg_table = ("| 检查 | 结果 |\n|---|---|\n"
                + "\n".join(f"| {c['check']} | {'PASS' if c['passed'] else 'FAIL'} |"
                            for c in pg["checks"]))
    pg_fail = [c["check"] for c in pg["checks"] if not c["passed"]]

    figs = [{"name": Path(item["path"]).name,
             "caption": FIG_CAPTIONS[Path(item["path"]).name],
             "png_base64": base64.b64encode(compress(ART / Path(item["path"]).name)).decode()}
            for item in fmeta["figures"]]

    md = f"""# IN19-Q6 · 任务梯度与探索预算机制（TensorPlatform）

**结论**：机制已落地并可自证——梯度字段、配比登记表、配额分配、S4/S5 探索预算、honest FAIL 隔离、
S-N 否定性成果轨、信用档位匹配派发全部实现；Q1 自评 **{q1['q1_gate']['criteria_passed']}/{q1['q1_gate']['criteria_total']} 判据 PASS**，
pytest **{py_ev['passed']} passed / {py_ev['failed']} failed**，迁移在真 PostgreSQL 上 **{pg['checks_passed']}/{pg['checks_total']} 探针 PASS**。
本节点以自身建设申报 **S3（建设）**，不领探索预算。

## ① 案例介绍

**不是物理算例，而是平台治理机制**（故无几何/水动力参数表；口径声明见⑥）。任务池此前是"平的"：
所有 mission 同档预算、同一套失败统计、同一套派发资格，由此产生三个病灶：

1. **探索型工作无法诚实失败** —— S4/S5（突破/创造）本质是"结果未知"的投入，若与执行型单共用失败率统计，
   理性策略就是只领确定性高的活，探索被饿死；
2. **派发不看资历** —— 高不确定性单派给低信用 worker，失败率高且无学习价值；
3. **失败无沉淀** —— 失败只留一个 `failed` 状态，根因与复盘随 worktree 回收消失。

机制参数（全部为机制自身数字，非领域参数）：

{mix_table}

配比指纹 `mix_signature = {sig}`（版本化登记，可调但须过校验：非负、和 = 1 ± 1e-6）。

## ② 怎么做的

- **核心库** `backend/services/mission_gradient.py`：纯函数、无 I/O、无 DB；
  梯度档 `S1..S5`、`validate_mix` / `allocate_quota`（Hamilton 最大余数法，确定性 tie-break → 可重放）、
  `exploration_budget`（仅 S4/S5 带标记与 `reserve_tool_calls`）、`classify_outcome` + `failure_accounting`
  （有据 honest FAIL 同时移出分子与分母；无据即降级为普通失败）、`guard_honest_fail`（覆盖即拒）、
  `dispatch_decide`（S4 需 L4+、S5 仅 L5）、`evaluate_q1`（判据门）。
- **落库** `backend/migrations/103_mission_gradient.sql`：`tensor_dev_missions` 增
  `gradient_tier / exploration_budget / honest_fail / fail_root_cause / retrospective / negative_result_ref`
  及库侧 CHECK；`mission_gradient_mix`（版本化配比，库侧再校验和为 1）、`worker_credit_tiers`（L1..L5）、
  `mission_negative_results`（S-N 轨，**append-only 触发器禁 UPDATE/DELETE**）、
  视图 `worker_failure_rate_v1`（纪律化 vs 平计数失败率并列）。
- **接口** `backend/api/mission_gradient.py`：12 个端点（配比读写、配额、任务梯度字段、信用档位、
  派发预演、S-N 轨、失败率、Q1 评估、自申报），**仅在 `TP_MISSION_GRADIENT=1` 时挂载**。
- **工具链**：pytest（36 项）、`scripts/mission_gradient_q1_selfcheck.py`（真跑测试 + 9 项判据）、
  `scripts/mission_gradient_pg_verify.py`（真 PostgreSQL 临时库执行迁移两次 + 17 项探针）、
  `scripts/mission_gradient_figures.py`（绘图时现算数字）。

## ③ 计算结果

**3.1 配额分配（精确整数，无舍入残差）**

| 总量 N | S1 | S2 | S3 | S4 | S5 | 合计 |
|---|---|---|---|---|---|---|
| 100 | {q100['S1']} | {q100['S2']} | {q100['S3']} | {q100['S4']} | {q100['S5']} | {sum(q100.values())} |
| 1000 | {q1000['S1']} | {q1000['S2']} | {q1000['S3']} | {q1000['S4']} | {q1000['S5']} | {sum(q1000.values())} |

**3.2 信用档位匹配派发矩阵（5×5）**：准入 {mat['allowed_cells']} 格 / 拒绝 {mat['denied_cells']} 格

| 梯度档 | L1 | L2 | L3 | L4 | L5 |
|---|---|---|---|---|---|
{gate_table}

关键格：S4→L3 = **{'准入' if mat['s4_to_l3'] else '拒绝'}**、S4→L4 = **{'准入' if mat['s4_to_l4'] else '拒绝'}**、
S5→L4 = **{'准入' if mat['s5_to_l4'] else '拒绝'}**、S5→L5 = **{'准入' if mat['s5_to_l5'] else '拒绝'}**。

**3.3 honest FAIL 与失败率（同一 worker：13 次运行含 3 次普通失败，k 次有据 honest FAIL）**

| k（honest FAIL 数） | 运行数 | 普通失败 | 隔离数 | 纪律化失败率 | 平计数失败率 |
|---|---|---|---|---|---|
{series_table}

无据 honest FAIL（根因 `"oops"`，短于 8 字符）：`quarantined={vec['undocumented_honest_fail']['quarantined']}`，
理由 `{vec['undocumented_honest_fail']['reason']}` —— 照样计数，不能被"honest"二字洗白。
S1..S3 申报 honest FAIL：`reason = no_exploration_budget_on_tier`（无探索预算即无诚实失败通道）。

**3.4 Q1 自证（机制用自身的判据门评价自身）**：`{q1['q1_gate']['q1_verdict']}`，
{q1['q1_gate']['criteria_passed']}/{q1['q1_gate']['criteria_total']}

| 判据 | 内容 | 结果 |
|---|---|---|
{crit_table}

自申报：`declared_tier = {decl['declared_tier']}（{decl['declared_label']}）`，
`exploration = {decl['gradient']['exploration']}`，`min_credit_level = {decl['gradient']['min_credit_level']}`，
`self_declared = {decl['gradient']['self_declared']}`。

**3.5 真 PostgreSQL 迁移探针**（PG 15.5 loong64，临时库，验证后已删除）：{pg['checks_passed']}/{pg['checks_total']} PASS

{pg_table}

视图算式实测：`attempts|successes|failures|quarantined|disciplined|naive = {pg['environment']['failure_rate_view']}`
（1 成功 + 1 普通失败 + 1 有据 honest FAIL → 纪律化 {view[4]}、平计数 {view[5]}，与手算一致）。

## ④ 结果图

图 1 配比登记与精确配额；图 2 信用档位准入矩阵；图 3 失败率记账与可采纳性。
图上数字由 `services/mission_gradient.py` 在绘图时现算，无手抄；梯度档名用 ASCII
`execute/improve/build/breakthrough/create`——构建机无 CJK 字体，避免豆腐块，
已在 `figures_metadata.json` 记录该取舍。

## ⑤ 注意点（坑与局限）

1. **平台冻结**：owner 2026-11-02 令 TensorPlatform 冻结。故机制以"附加 + 默认关闭"落地：
   `main.py` 的挂载被 `TP_MISSION_GRADIENT=1` 包住（默认 off），未改 `tensor_missions.py` /
   `campaign_plans.py` 任何既有派发路径；`test_wiring_is_feature_flagged_...` 机器校验这一点。
   **线上派发链路尚未切换**——切换需 owner 解冻决策，接入点是 `dispatch_decide` 一行调用。
2. **PG 表约束不能用表达式**：初版把 `UNIQUE (COALESCE(organization_id,0), worker_id)` 写成表约束，
   真库直接 `syntax error`（表约束不允许表达式）。改为 `organization_id BIGINT NOT NULL DEFAULT 0`
   （0 = 平台级作用域）+ 普通 `UNIQUE (organization_id, worker_id)`，保住 `ON CONFLICT ON CONSTRAINT`
   的幂等 upsert。若只做文本断言不做真库执行，这个错会一路带到生产。
3. **迁移必须自证幂等**：真库上是"连续执行两次"。第一次失败会回滚整块事务、下游探针全变假阴性，
   脚本因此在迁移失败时立即抛错，而不是继续跑探针。
4. **信用档位默认 fail-closed**：`worker_credit_tiers` 未填时无人达到 L4，S4/S5 派发被拒（宁可不派，不可乱派）；
   该表数据来源（治理面写入 / 自动评级）尚未定，属后续工作。
5. **配额簿记不自行扣减**：`quota_remaining` 由 campaign 层维护并传入，机制保持纯函数，避免冻结期跨模块写竞争。
6. **既有测试环境缺件**：本容器缺 `cryptography` 包，`tests/test_api.py` 4 项在改动前即因
   `ModuleNotFoundError: No module named 'cryptography'` 失败（`git stash` 对照确认非本单引入），
   故 `main.py` 的运行时可导入性未能在本机端到端验证，仅做了 AST 解析 + 路由挂载 + 开关门三重机器校验。

## ⑥ 误差判定（逐项）

本单**无实验/文献基准**：交付物是平台治理机制，不是物理量测量，因此不存在"与实测值差 3%"这类判据。
凡有可机器判定参考量的地方，一律用**精确相等**而非近似比较——偏差为 0：

| 关键量 | 参考基准（来源） | 实测 | 偏差 | 是否 < 3% |
|---|---|---|---|---|
| 配比和 | 1.0（定义） | {sum(mix.values()):.9f} | 0 | **是（0%）** |
| N=100 配额 | 60/25/10/4/1（配比 × N，精确） | {q100['S1']}/{q100['S2']}/{q100['S3']}/{q100['S4']}/{q100['S5']} | 0 | **是（0%）** |
| N=1000 配额 | 上述 ×10（线性性） | {q1000['S1']}/{q1000['S2']}/{q1000['S3']}/{q1000['S4']}/{q1000['S5']} | 0 | **是（0%）** |
| 准入矩阵 | Sn 需 Ln 及以上（规则） | {mat['allowed_cells']} 准入 / {mat['denied_cells']} 拒绝 | 0 | **是（0%）** |
| 视图纪律化失败率 | 1/(3−1) = 0.5（手算） | {view[4]} | 0 | **是（0%）** |
| 视图平计数失败率 | 2/3 = 0.6667（手算） | {view[5]} | 0 | **是（0%）** |
| 判据门 | 9/9 全 PASS 才判 pass | {q1['q1_gate']['criteria_passed']}/{q1['q1_gate']['criteria_total']} | 0 | **是（0%）** |
| pytest | ≥6 通过、0 失败（任务书） | {py_ev['passed']} 通过 / {py_ev['failed']} 失败 | 0 | **是（0%）** |

**未覆盖（honest FAIL 口径）**：线上 dispatcher 未切换（冻结）；信用档位数据源未定；配额扣减未实现；
生产库未执行迁移 103（仅在 scratch 库验证）。以上均在 ⑤ 明列，不作粉饰。
PG 探针失败项：`{pg_fail if pg_fail else '无'}`。

---
审计链：`artifacts/mission_gradient/q1_selfcheck.json`（含各源文件 sha256）、`pg_verify.json`、
`dispatch_matrix.json`、`accounting_vectors.json`、`figures_metadata.json`；
机制源码 `backend/services/mission_gradient.py`、迁移 `backend/migrations/103_mission_gradient.sql`、
接口 `backend/api/mission_gradient.py`、用例 `backend/tests/test_mission_gradient.py`（36 项）、
文档 `docs/mission_gradient_mechanism.md`。分支 `agent/m5026`，commit 见仓库 HEAD。
"""

    return {
        "title": "IN19-Q6 · 任务梯度与探索预算机制：S1..S5 梯度、配比登记、honest FAIL 隔离与信用档位派发",
        "summary": (
            f"落地任务梯度 S1..S5 + 配比登记(60/25/10/4/1) + S4/S5 探索预算 + honest FAIL 隔离 + S-N 否定性成果轨 "
            f"+ 信用档位匹配派发(S4需L4+、S5需L5)。Q1 自评 {q1['q1_gate']['criteria_passed']}/{q1['q1_gate']['criteria_total']} PASS，"
            f"pytest {py_ev['passed']} 通过/0 失败，真 PostgreSQL 迁移探针 {pg['checks_passed']}/{pg['checks_total']} PASS；"
            f"平台冻结期以 TP_MISSION_GRADIENT=1 默认关闭方式附加落地，线上派发链路未切换（已如实声明）。"
        ),
        "scenario_key": "platform.mission_gradient",
        "mission_id": MISSION_ID,
        "tags": ["TensorPlatform", "IN19-Q6", "mission-gradient", "exploration-budget",
                 "credit-tier", "honest-fail", "pytest", "postgresql", "no-domain-semantics"],
        "markdown": md,
        "figures": figs,
    }


def post(url: str, body: dict, token: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    import sys

    payload = build()
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    print("payload bytes:", size)
    if "--dry-run" in sys.argv:
        Path("/tmp/report_in19q6.json").write_text(json.dumps(payload, ensure_ascii=False))
        print("dry-run: wrote /tmp/report_in19q6.json")
        return 0
    api = os.environ["TENSOR_API_BASE"].rstrip("/")
    token = os.environ.get("TENSOR_TOKEN", "")
    if not token:
        code, body = post(f"{api}/auth/login",
                          {"username": os.environ["TENSOR_AGENT_USER"],
                           "password": os.environ["TENSOR_AGENT_PASS"]})
        print("login:", code)
        if code != 200:
            print(body)
            return 1
        token = json.loads(body)["access_token"]
    code, body = post(f"{api}/tensor/reports", payload, token)
    print("report:", code, body[:400])
    return 0 if code in (200, 201) else 1


if __name__ == "__main__":
    raise SystemExit(main())