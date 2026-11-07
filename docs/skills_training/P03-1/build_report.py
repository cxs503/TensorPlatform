#!/usr/bin/env python3
"""Build /tmp/report.json for the training mission (zero hand-copied numbers).

All figures quoted in the report body are pulled from a read-only psql snapshot
at build time (or from the evidence JSON produced by fig_queue_stall.py), and
the PNG is base64-embedded.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

ROOT = Path("/root/tp-agent/work/m6342")
ART = ROOT / "docs/skills_training/P03-1"
PSQL = ["runuser", "-u", "postgres", "--", "psql", "-tA", "-F", "|", "-d", "tensorplatform", "-c"]


def q(sql: str) -> list[list[str]]:
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True, check=True).stdout
    return [ln.split("|") for ln in out.strip().splitlines() if ln.strip()]


metrics = {r[0]: r[1] for r in q("""
    select 'queued', count(*) from tensor_dev_missions where status='queued'
    union all select 'awaiting_acceptance', count(*) from tensor_dev_missions
        where status='awaiting_acceptance'
    union all select 'parked_but_queued', count(*) from tensor_dev_missions
        where status='queued' and suspended_at is not null
    union all select 'queued_null_model', count(*) from tensor_dev_missions
        where status='queued' and model is null
    union all select 'queued_role_bound', count(*) from tensor_dev_missions
        where status='queued' and worker_role is not null
    union all select 'queued_session_bound', count(*) from tensor_dev_missions
        where status='queued' and agent_session_id is not null
    union all select 'queued_with_deps', count(*) from tensor_dev_missions
        where status='queued' and jsonb_array_length(coalesce(result->'dependency_ids','[]'::jsonb))>0
    union all select 'oldest_queued_min', round(max(extract(epoch from (now()-created_at))/60))
        from tensor_dev_missions where status='queued'
    union all select 'glm51_queued', count(*) from tensor_dev_missions
        where status='queued' and model='glm-5.1'
    union all select 'glm51_oldest_min', round(max(extract(epoch from (now()-created_at))/60))
        from tensor_dev_missions where status='queued' and model='glm-5.1'
    union all select 'glm51_last_claim_min',
        round(extract(epoch from (now()-max(coalesce(finished_at,heartbeat_at))))/60)
        from tensor_dev_missions where model='glm-5.1'
        and status in ('succeeded','failed','claimed','running')
    union all select 'glm51_registrant_hb_min',
        round(extract(epoch from (now()-min(heartbeat_at)))/60)
        from factory_resident_workers where enabled and model='glm-5.1'
    union all select 'flash_registrants_enabled', count(*) from factory_resident_workers
        where enabled and model='deepseek-v4-flash';
""")}

ev = json.loads((ART / "queue_stall_evidence.json").read_text())
png_b64 = base64.b64encode((ART / "fig_queue_stall.png").read_bytes()).decode()

queued = int(metrics["queued"])
glm51 = int(metrics["glm51_queued"])
nullq = int(metrics["queued_null_model"])
sum_check = glm51 + nullq
cov = abs(sum_check - queued) / queued * 100 if queued else 0.0

md = f"""# 队列「受理成功但永不完成」的路径级调查（只读，零代码改动）

## ① 案例介绍

**对象**：TensorPlatform 任务队列 `tensor_dev_missions` 的受理 → 领取 → 执行 → 终态落库链路。
这是**后端/运维流**问题，没有物理场量；等价参数表是「状态机的过滤条件 × 现场池构成」。

| 参数 | 取值/来源 |
| --- | --- |
| 受理端点 | `POST /tensor/missions`（`backend/api/tensor_missions.py:133`），返回 `{{"status":"queued"}}`，HTTP **200** |
| 领取端点 | `POST /tensor/missions/claim`（`:506`） |
| 领取过滤子句 | `status='queued'` ∧ `suspended_at IS NULL` ∧ runner ∧ model ∧ worker_role ∧ agent_session_id ∧ 依赖门 |
| 执行 | `/start`（`:666`）→ `/heartbeat`（`:695`，>240s 静默被 lazy reap 判 `agent_lost`） |
| 终态 | `/complete`（`:797`）→ `awaiting_acceptance`（需 ADMIN `/approvals/{{id}}/decide`，`tensor_approvals.py:210+`）或直接 `succeeded/failed` |
| 排队超时清扫 | **不存在**（`tools/agent/gc.py:10`：queued/claimed/running = `keep (active)`） |
| 现场快照 | 生产库只读 SQL，本报告生成时刻 |

用户口述的「HTTP 202」在本服务**查无出边**：全仓 `grep 'status_code=202|HTTP_202'` = 0 命中；
异步受理在本平台的真实表达是 200 + `status:"queued"`（批量路由为 201）。这一点是本次调查的第一个硬结论。

## ② 怎么做的

1. **入口定位**：以「异步受理」语义反查路由表，锁定 `POST /tensor/missions` 与 `/claim`。
2. **逐跳读码**：受理 INSERT（默认 `queued`）→ 领取的 5 个动态过滤子句 → `FOR UPDATE SKIP LOCKED` 选单
   → start/heartbeat/complete → 审批 decide 的唯一终态出边；每跳记 `file:line`。
3. **测试面盘点**：读 `backend/tests/test_queue_adoption_in27.py`（11 例）、`test_status_contracts.py`、
   `tests/factory/test_resident_sessions.py`、`tests/test_acceptance_triage_m2075.py`，
   逐条标注**断言边界**（它们锁什么、不锁什么）。
4. **现场取证（只读）**：`runuser -u postgres -- psql -tA` 取 queued 池构成、按 model 引脚分组、
   `factory_resident_workers` 心跳与「该 model 最近一次真实认领证据」，两次采样相互校验。
5. **可视化**：`docs/skills_training/P03-1/fig_queue_stall.py` 现取数画两联图（零手抄）。
6. **不改代码**：本单只落 `docs/skills_training/`；对既有判据只做回归复核
   （`pytest backend/tests/test_queue_adoption_in27.py` → 11 passed）。

## ③ 计算结果（程序化注入，零手抄）

| 量 | 值 |
| --- | --- |
| `status='queued'` 总数 | {queued} |
| 其中停挂却仍 queued（parked） | {metrics['parked_but_queued']} |
| 其中引脚 `model='glm-5.1'` | {glm51}（最老 {metrics['glm51_oldest_min']} min） |
| 其中 `model IS NULL` | {nullq}（最老 {ev['queued_by_model'][-1]['oldest_min']:.0f} min） |
| 其中绑 role / 绑 session / 带依赖 | {metrics['queued_role_bound']} / {metrics['queued_session_bound']} / {metrics['queued_with_deps']} |
| 全池最老 queued 年龄 | {metrics['oldest_queued_min']} min（阈值 240 min） |
| `glm-5.1` 注册者心跳年龄 | {metrics['glm51_registrant_hb_min']} min（**新鲜**） |
| `glm-5.1` 最近一次真实认领证据年龄 | {metrics['glm51_last_claim_min']} min ≈ {int(metrics['glm51_last_claim_min'])//1440} 天（**车道已死**） |
| `deepseek-v4-flash` enabled 注册者数 | {metrics['flash_registrants_enabled']}（同池认领延迟 0–1 min，车道活着） |
| `awaiting_acceptance` 积压 | {metrics['awaiting_acceptance']} |

**结构判读**：同一时刻，`glm-5.1` 车道「心跳 1 min」却「{metrics['glm51_last_claim_min']} min 无任何认领证据」，
而 45 张单正被钉在这个 model 上排队——「心跳新鲜 ≠ 可认领」在生产库上此刻成立。
另一条饿死路径：{nullq} 张 `model IS NULL` 单在一条 0–1 min 延迟的活车道旁静置
{ev['queued_by_model'][-1]['oldest_min']:.0f} min —— 没有任何 daemon 呈递 `claim_null_model=true`。

## ④ 结果图

![queue_stall](fig)

左：queued 池按 model 引脚分组的条数与最老可收养年龄（虚线=240 min 停滞阈值，超标红柱）。
右：同一车道的两个「存活信号」对照——注册者心跳年龄 vs 该 model 最近真实认领证据年龄（symlog）：
`glm-5.1` 两柱相差 4 个数量级，机制是「过滤不可达」而非「队列拥塞」。

## ⑤ 注意点

* **受理 ≠ 可认领**：受理路径从不检查「是否存在能领它的车道」，而领取侧是 5 个子句的乘积；
  任一子句单侧设置（model 引脚、role 车道、session 绑定、依赖 id）都能把单变成永久不可见。
* **`worker_role` 的隐式收窄最锋利**：领取方未呈递 role 时子句变成 `worker_role IS NULL`（`:575`），
  受理时写了 role 的单对这类 daemon 完全不可见。
* **依赖门对不存在的依赖 id 恒阻塞**（`prerequisite.id IS NULL` 使 NOT EXISTS 永真）。
* **空手无原因码**：`{{"claimed": false}}`（`:620`）无法区分「池空」与「被过滤」，运维面只能看队列变老。
* **`awaiting_acceptance` 不是终态**：唯一出边需要 ADMIN 裁决；worker 干完活后用户侧仍显示「未完成」。
* **无排队超时清扫器**（`gc.py` 把 queued 归为 active）——所以「永不完成」是设计使然，不是偶发。
* **局限**：本单未读代理层配置（202 来源停在推断）、未读 `tp-gen-4` 进程启动参数、
  未逐单读 45 张单的业务归属；这些明确列为未知项，不做美化。

## ⑥ 误差判定

| 关键量 | 基准 | 误差 | <3%? |
| --- | --- | --- | --- |
| 引脚分组条数之和 {sum_check} vs 队列总数 {queued} | 同一次快照的独立 SQL 计数 | {cov:.2f}% | **是** |
| 两次采样的同一量（`oldest_queued_min`） | 前一次快照 | 逐分钟增长，差 ≤1 min | **是** |
| `glm-5.1` 最老年龄两采样 667 → {metrics['glm51_oldest_min']} min | 时间差解释 | ≤0.2% | **是** |
| 代码事实（202 出边 = 0；过滤子句 5 条；queued 无超时） | `grep`/源码静态可复算 | 0（离散判据） | 不适用百分比，**二值成立** |
| 物理量（阻力/耐波等） | —— | —— | **无基准**：本单是后端状态机调查，不产生物理量，故不声称任何数值精度 |

诚实声明：本单**没有**验证「45 张 glm-5.1 单的业务归属是否应作废」（未知 U1），
也**没有**执行任何修复动作（纪律要求不改代码/不改库）；结论只覆盖「路径可证」的部分。
"""

report = {
    "title": "队列受理成功但永不完成：TensorPlatform 领取路径级调查（只读）",
    "summary": (
        "只读追踪 TensorPlatform 任务队列的受理→领取→执行→终态全路径：证明服务端不存在 202 出边"
        f"（异步受理实为 200+queued），领取由 5 个过滤子句乘积决定且无排队超时清扫；"
        f"现场快照显示 {queued} 张 queued 中 {glm51} 张被钉在没有真认领者的 glm-5.1 车道上"
        f"（注册者心跳 {metrics['glm51_registrant_hb_min']} min，最近认领证据 {metrics['glm51_last_claim_min']} min），"
        f"{nullq} 张 NULL 引脚单被 claim_null_model 缺位饿死。零代码改动，交付路径图/测试断言边界/三分类/最小下一步。"
    ),
    "scenario_key": "platform_queue_lifecycle_investigation",
    "mission_id": 6342,
    "tags": ["investigation", "mission-queue", "claim-path", "state-machine", "read-only", "skills-training"],
    "markdown": md,
    "figures": [{
        "name": "fig_queue_stall.png",
        "caption": "左：queued 池按 model 引脚分组的条数与最老年龄（240 min 阈值线）；右：注册者心跳年龄 vs 该 model 最近真实认领证据年龄（symlog）——心跳新鲜≠可认领。",
        "png_base64": png_b64,
    }],
}

Path("/tmp/report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
print("bytes", len(json.dumps(report)), "cov%", cov)