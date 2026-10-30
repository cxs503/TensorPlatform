# TensorPlatform Fleet Ops —— 自主运行优化套件

把「LLM agent 舰队执行科研任务」从手动运维变成常设自动机制：自愈 worker 舰队、
数据驱动动态编制、三层报告质控、多模型议事与值班长调度、预算校准。

## 架构

```
              +---------------------------------------------+
              |  TensorPlatform (FastAPI + PostgreSQL)       |
              |  missions / sessions / approvals / reports   |
              +-------+--------------------------+-----------+
      claim/heartbeat  |                          |  REST
  +-------------------+--------+      +----------+---------------------+
  | tp_worker.py x N  |        |      | 常设线（每条独立进程/cron）     |
  | fleet.json 编制表  |        |      | report_watch    补报线         |
  | fleet_supervisor   |        |      | report_qa       质检+返工线    |
  |   30s 自愈拉起     |        |      | operator_shift  值班长班次     |
  | 401 自动重登        |        |      | sdaa_sweep      案例扫荡补货   |
  | 后台心跳线程        |        |      | council         多模型议事会   |
  | 预算燃料表注入      |        |      | fleet_optimizer 动态编制引擎   |
  +---------+----------+        |      | auto_approver   审批线         |
            |  LLM 网关          |      +--------------------------------+
            v  (OpenAI 兼容)     |
```

## 文件清单

| 文件 | 用途 |
|---|---|
| tp_worker.py | 原生 agent worker：登录/领单/LLM 工具循环/心跳/finish；401 自动重登；预算燃料表注入；报告纪律 footer |
| fleet_supervisor.sh | 舰队守护：30s 重读 fleet.json，缺员即拉起（--agent-id/--model/--sdaa 一人一卡） |
| fleet.example.json | 编制表 schema：`[{"id","role","model","sdaa"}]` |
| fleet_optimizer.py | 动态编制引擎：48h 滚动战绩 → strong/probation/eliminate → 原子改写编制表 → 只杀空闲变更 worker |
| report_watch.py | 补报线：succeeded 却无报告的 mission → 自动派补报单（工件清单嵌入 goal） |
| report_qa.py | 质检线：新报告抽检（五节结构/数字回溯工件/图件/误差判定）→ PASS/MINOR/RESUBMIT → 自动返工单 |
| operator_shift.py | 值班长：20min 班次即 mission；班内自主巡检/补货/派诊断，硬护栏内建 |
| council.py | 议事会：R1 三模型并行提案 → R2 双模型质询 → R3 综合主席出阶段计划 |
| sdaa_sweep.py | 扫荡补货：维持 in-flight 深度，verified 案例逐个上加速卡 |
| auto_approver.py | 审批线：合规交付自动 approve（带 OFF 总开关） |
| worker_loop.sh / auto_approver_loop.sh | 单实例循环包装（flock） |
| env.example | 环境变量模板（凭证全部走环境变量，不入库） |

## 部署

1. 依赖：python3.11+、psql 客户端、TensorPlatform 后端（PG）、OpenAI 兼容 LLM 网关
2. `cp env.example agent.env` 并填入 `TP_API_USER / TP_API_PASS / TP_PG_PASSWORD / TP_LLM_API_KEY`
3. 编制表 `fleet.json`（schema 见 fleet.example.json；`sdaa` = 加速卡号，一人一卡）
4. 启动：

```bash
nohup bash fleet_supervisor.sh &                    # 舰队守护
for d in report_watch report_qa operator_shift sdaa_sweep fleet_optimizer council; do
  setsid --fork nohup python3 $d.py >>/var/log/tp-$d.log 2>&1
done
nohup bash auto_approver_loop.sh &                  # 审批线
```

5. 观测：`/var/log/tp-*.log`、平台 missions/reports 页面、`fleet_change.json`（换血账本）

## 安全

- 所有凭证经 `TP_*` 环境变量注入，仓库零密钥（grep 审计通过）
- agent 席位用最低权限账号；审批/晋级终审保留人工位（auto_approver 有 OFF 开关）
