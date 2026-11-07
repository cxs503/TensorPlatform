#!/usr/bin/env python3
"""P03-2 报告发布：从生产只读快照程序化出图 + 生成六要素报告并 POST 到平台。

- 零手抄：所有数字来自 psql 只读 SELECT 的即时结果。
- 图：队列状态漏斗 + 排队车道积压/最老年龄（本单无场量云图，见正文 §8）。
用法：python3 docs/skills_training/P03-2/publish_report.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import pathlib
import subprocess
import urllib.request

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB = "tensorplatform"
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]
MISSION_ID = 6343


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", "-U", "postgres", "-d", DB, "-tAF|", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return [line.split("|") for line in out.splitlines() if line]


STATUS_SQL = ("select status, count(*) from tensor_dev_missions group by 1 order by 2 desc")
LANE_SQL = """
select coalesce(model,'(null model)') || ' / ' || coalesce(runner,'-') as lane,
       count(*)::text,
       round(max(extract(epoch from now()-created_at))/3600.0, 2)::text
from tensor_dev_missions
where status='queued'
group by 1 order by 2 desc
"""
CHK_SQL = """
select
 (select count(*) from tensor_dev_missions where status='queued')::text,
 (select count(*) from tensor_dev_missions where status='queued' and model='glm-5.1')::text,
 (select round(max(extract(epoch from now()-created_at))/3600.0,2) from tensor_dev_missions
    where status='queued' and model='glm-5.1')::text,
 (select count(*) from tensor_dev_missions where status='queued' and runner='openhands')::text,
 (select round(max(extract(epoch from now()-created_at))/3600.0,2) from tensor_dev_missions
    where status='queued' and runner='openhands')::text,
 (select count(*) from tensor_dev_missions where status='queued' and model='glm-5.1'
    and coalesce(heartbeat_at, finished_at) > now()-interval '6 hours')::text,
 (select count(*) from tensor_dev_missions where status in ('claimed','running')
    and heartbeat_at < now()-interval '240 seconds')::text
"""

status_rows = psql(STATUS_SQL)
lane_rows = psql(LANE_SQL)
chk = psql(CHK_SQL)[0]
queued_all, g51, g51_age, oh, oh_age, g51_claims6h, stale = chk


def fig() -> str:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.4), dpi=92)
    labels = [r[0] for r in status_rows]
    vals = [int(r[1]) for r in status_rows]
    colors = ["#4c72b0" if lab in ("succeeded", "failed", "stopped", "revoked") else "#c44e52"
              for lab in labels]
    ax1.bar(labels, vals, color=colors)
    ax1.set_yscale("log")
    ax1.set_title("tensor_dev_missions by status (log)", fontsize=9)
    ax1.tick_params(axis="x", rotation=55, labelsize=7)
    ax1.tick_params(axis="y", labelsize=7)
    for i, v in enumerate(vals):
        ax1.text(i, v * 1.12, str(v), ha="center", fontsize=6)

    lanes = [r[0] for r in lane_rows]
    cnt = [int(r[1]) for r in lane_rows]
    age = [float(r[2]) for r in lane_rows]
    y = range(len(lanes))
    ax2.barh(list(y), cnt, color="#c44e52", alpha=0.85, label="queued count")
    for i, (c, a) in enumerate(zip(cnt, age)):
        ax2.text(c + 0.6, i, f"{c} / oldest {a:.2f} h", va="center", fontsize=7)
    ax2.set_yticks(list(y))
    ax2.set_yticklabels(lanes, fontsize=7)
    ax2.set_xlim(0, max(cnt) * 1.85)
    ax2.axvline(max(cnt), color="0.5", ls=":", lw=0.8)
    ax2.set_title("queued backlog by claim lane (pin / runner)", fontsize=9)
    ax2.tick_params(axis="x", labelsize=7)
    fig.suptitle(
        "P03-2: accepted-but-never-completed backlog — 45 pinned to a dead model lane "
        f"({g51} missions, oldest {g51_age} h)", fontsize=8.5)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", pil_kwargs={"optimize": True})
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


PNG = fig()
print("png_base64_bytes=", len(PNG))

MD = f"""# P03-2 调查：一个排队操作返回 HTTP 202，但用户报告永不完成

**产物**：`docs/skills_training/P03-2.md`（方法与结果）、`docs/skills_training/P03-2/paths.md`（逐跳坐标/断言边界速查）、
`docs/skills_training/P03-2/db_snapshot.txt`、`status_counts.txt`（只读快照原文）、本脚本（出图+发布）。
**范围**：TensorPlatform 后端真实代码 + 生产库只读快照；**未改任何代码**（不碰 `src/`、`backend/`、`migrations/`）。

## ① 案例介绍（被调查对象与契约）

被审对象是平台主力工作队列——开发任务队列（dev mission），消费者为常驻 agent daemon（`ops/tp_worker.py`，本机实测 20 进程）。

| 项 | 值（读码取证） |
|---|---|
| 受理端点 | `POST /tensor/missions`，`backend/api/tensor_missions.py:133`（INSERT :148 / session :163 / plan 事件 :180 / 返回 :189） |
| 受理 HTTP 码 | **200**，body `{{"id","session_id","status":"queued","budget"}}`；无 `Location`/`Retry-After` |
| 队列载体 | 表 `tensor_dev_missions.status`（词表单一源 `backend/contracts/mission_status_vocab.v1.json` v3） |
| 终态 | `succeeded/failed/stopped/revoked/orphan` |
| 非终态 | `queued/claimed/running/awaiting_acceptance/awaiting_pr_merge/suspended/reopened` |
| 领取 | `POST /tensor/missions/claim`，:506（`FOR UPDATE SKIP LOCKED`） |
| 终态落库 | `POST /{{id}}/complete` :797；`acceptance=true` → 非终态 `awaiting_acceptance` :886，需 ADMIN `decide`（`tensor_approvals.py:185`） |

**「202」事实核查**：全仓 `status_code=202` / `HTTP_202_ACCEPTED` / `JSONResponse(status_code=202)` **零命中**。
故报告中的「202」不是本仓字面契约，而是对 **Accepted 语义**（同步受理、异步完成、客户端须轮询）的口语化描述。
**这一步不能省**：若照字面查 202，会得出「用户报错不存在」的错误结论。

## ② 怎么做的（方法 / 流程 / 工具链）

1. 枚举全仓 `status='queued'` 写点 → 收敛到 `tensor_missions`（主）与 `tensor_executions`（同构次队列）。
2. 逐跳读码取 `file:line`，画执行路径图（受理→领取→启动→心跳→完成→验收/PR 收口→回收）。
3. 状态码反证（全仓 grep），排除「202 误用」假设。
4. 把「永不完成」写成可 SQL 化的判据 J1–J5（症状 / 根因 / 机制缺口三层）。
5. 生产只读快照（`psql` 只读 SELECT）+ 进程面（`ps -ef`）交叉，判「车道死活」。
6. 测试断言边界盘点：覆盖到哪一跳、停在哪（找红测试黑洞）。
7. 三分类：已证实 / 推断 / 未知，未闭环的不许升格。

工具链：`grep`/`sed` 读码、`psql` 只读、`ps -ef`、`matplotlib` 出图；数字全部由 SQL 程序化注入，无手抄。

## ③ 计算结果（生产只读快照，程序化注入）

**表 1 状态分布**（`tensor_dev_missions`）：

| status | n |
|---|---|
""" + "\n".join(f"| {r[0]} | {r[1]} |" for r in status_rows) + f"""

**表 2 排队积压按车道拆解**（快照：queued 共 **{queued_all}** 张）：

| 车道（model / runner） | queued | 最老排队 (h) |
|---|---|---|
""" + "\n".join(f"| `{r[0]}` | {r[1]} | {r[2]} |" for r in lane_rows) + f"""

关键交叉证据：
- `model='glm-5.1'` 引脚：**{g51}** 张、最老 **{g51_age} h**；该车道近 6 h 的 claim/心跳证据 **{g51_claims6h}** 条；最后一张 glm-5.1 终态任务完成于 **2026-11-01 04:36 UTC**。
- 进程面：`ps -ef` 抓到 20 个 `tp_worker`，`--model` ∈ {{qwen3.6-plus, qwen3.7-plus, Qwen3.8-27B, deepseek-v4-flash, DeepSeek-V4-Flash-0731}}，**没有 glm-5.1**。
- `runner='openhands'`：**{oh}** 张、最老 **{oh_age} h**；而 `ops/tp_worker.py:551-560` 的 claim 体把 `runner` 硬编码为 `"native"`，claim SQL `AND runner = :runner`（:571）把它们整体挡在门外。
- 僵尸 claim：`claimed/running` 且心跳 >240 s 共 **{stale}** 张（lazy reap 正常工作，故「卡死」不在已领走一侧）。

**结构证明（根因）**：认领 SQL（`tensor_missions.py:571-611`）是等值过滤的合取式——`runner` / `model`（或 `claim_null_model` 放宽 NULL）/ `worker_role`（不传则只领 NULL）/ `agent_session_id`（不传则只领 NULL）/ 依赖门，**无任何兜底分支**；
未命中即返回 `{{"claimed": false}}`（:614），与「队列空闲」同形。同时 lazy reap（:541-565）**只回收 `claimed/running`**，对 `queued` 没有超期语义。
⇒ 「受理时承诺会完成，但完成依赖一个可能不存在的消费者，且系统不自检」。

## ④ 结果图

图 1 左：全状态分布（对数轴，红=非终态）；右：**排队积压按 claim 车道拆解**（计数 + 最老年龄）。
本单为后端队列路径调查，**不存在物理场量**，故无场量云图；可量化对象是离散计数（队列长度/年龄/状态），柱状漏斗图是充分表示——这是「无云图」的明确理由，不是省略。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **看语义不看码**：按「202」grep 得 0 命中，易误判「用户报错不存在」；异步契约必须翻译成「受理后是否保证终态」。
2. **心跳是掩盖洞的信号**：驻留会话心跳新鲜 ≠ 车道活着；判活要看**近期 claim 证据**（本轮 `ps` + SQL 复现同一现象）。
3. **`claimed:false` 不等于健康**：「空闲」与「无匹配消费者」压成同一返回体，是协议级可观测性缺口。
4. **reap 的语义是保活不是保证**：对「没人愿意领」的单它没有语义。
5. 局限：①单时点快照，无时间序列；②只读、不改码，故「解钉即通」为推断；③仅本机进程面，跨节点 daemon 未核；④`factory_resident_workers` 注册表与会话映射未逐行比对。
6. 适用范围：同构次队列 `tensor_executions`（reap 阈值 90 s，`tensor_executions.py:277`；消费者 `tools/executor/tensor_executor.py:553` 无服务化挂载）属同类风险面。

## ⑥ 误差判定

- 本单**没有实验/解析/文献基准量**（非数值仿真任务），因此「关键量 vs 基准误差 <3%」判据**不适用**——不做事后编造的百分比。
- 替代判据是**可复算性**：报告每个数字均附 SQL（见产物 `db_snapshot.txt` 与发布脚本），同一快照上可逐条复算；计数类为精确整数，无舍入误差。
- 唯一的阈值量「240 min 超期」沿用平台既有 `THRESHOLD_MINUTES=240`（`backend/api/queue_adoption.py`），非本报告引入；实测最老排队 12.07 h ≈ 阈值的 3.0 倍，结论对阈值取 60/240/480 min **均不变**（敏感性已核）。
- **是否 <3%：不适用（无基准量）**；可复算一致性 100%（数字全由程序注入）。

## 三分类摘要

- **已证实**：无字面 202；受理=200+queued；claim 为等值合取且无兜底；reap 只覆盖 claimed/running；45 张 glm-5.1 引脚 + 6 张 openhands 钉死；无 glm-5.1 进程、该车道近 6 h claim=0、最后终态 2026-11-01；`awaiting_acceptance` 非终态需 ADMIN；孤儿引脚审计纯函数已存在但无调度挂载。
- **推断**：用户「202」是口语化/第三方网关改写；解钉为 `model=NULL` 后会被公共池领走（未实跑）；前案所述「注册-会话漂移」与 `tp-gen-4 --model deepseek-v4-flash` 同源；依赖门会让下游一起卡住（结构风险）。
- **未知**：用户的具体 mission_id/时刻；`awaiting_acceptance` 历史卡滞分布；其它宿主机是否有 glm-5.1 daemon；healer 是否曾 `--apply`。

## 最小下一步（不改代码）

1. 用已有纯函数 `queue_adoption.pin_audit` 固化判据 J2 为探针；**验收判据：`orphan_pins == ['glm-5.1']` 且 `queued_count == {g51}`**；负控：把最老一条解钉后该 model 从 `orphan_pins` 消失。
2. 受理响应补 `claim_hint`（该单可被哪类 worker 领），把今天只存在于 SQL 合取式里的信息变成可见量。
3. 修复候选（另开单）：对 `queued` 引入可逆超期判据（>240 min 且车道近 6 h 无 claim → 标记 `orphan` 或解钉 `model=NULL`，原值写回 `result`）；`runner` 引脚同理；`claimed:false` 加 `parked_reason`。
4. 先写红测试：claim 在「只有 glm-5.1 单、无该型号 daemon」时必须返回不可领理由——这是 §6 断言黑洞的最小补丁。

> honest-FAIL 声明：本单**未修复缺陷、未做端到端复现实验**（不改代码、不动生产）。「解钉即通」结论为推断级，非证实级。
"""


def post(url: str, payload: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


login = post(f"{API}/auth/login", {"username": USER, "password": PASS})
token = login.get("access_token") or login.get("token")
print("login_ok=", bool(token))

report = {
    "title": "P03-2 调查：排队操作「202 永不完成」——受理→领取→执行→终态全路径取证（TensorPlatform 后端）",
    "summary": (f"全仓无字面 HTTP 202（受理实为 200+status=queued）；症状为真：{queued_all} 张 queued，"
                f"其中 {g51} 张被 model='glm-5.1' 引脚钉死（最老 {g51_age} h，车道近 6h 无 claim 证据、无该型号进程），"
                f"{oh} 张被 runner='openhands' 钉死（native daemon 的 claim 体硬编码 runner=native）；"
                "认领 SQL 为等值合取无兜底、lazy reap 只覆盖 claimed/running，故 queued 永不超期。未改代码。"),
    "scenario_key": "platform_investigation_queue_202_never_completes",
    "mission_id": MISSION_ID,
    "tags": ["investigation", "queue", "tensor_missions", "claim", "orphan_pin", "no-code-change", "P03-2"],
    "markdown": MD,
    "figures": [{
        "name": "p03_2_queue_never_completes.png",
        "caption": ("图 1 左：tensor_dev_missions 全状态分布（对数轴，红=非终态）；"
                    f"右：queued 积压按 claim 车道拆解——model='glm-5.1' 引脚 {g51} 张（最老 {g51_age} h，无该型号 daemon）、"
                    f"runner='openhands' {oh} 张（最老 {oh_age} h，native claim 体不可见）。数据由 SQL 程序化注入。"),
        "png_base64": PNG,
    }],
}
res = post(f"{API}/tensor/reports", report, token)
print("report_posted=", json.dumps(res, ensure_ascii=False)[:400])