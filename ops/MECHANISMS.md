# 运行优化机制沉淀（机制 · 实测数据 · 教训）

本文件沉淀 agent 舰队从「手动运维」到「自主运行」的全部优化机制，附实测数据与可复用配方。

## 1 舰队与动态编制

- 编制表 fleet.json：32 席 = {id, role, model, sdaa}，七角色（impl/ver/arch/doc/mod/app/infra）一人一卡
- supervisor 30s 自愈：只补缺员、从不杀存活进程 → 模型变更必须显式换血（防误杀在飞任务）
- **动态编制引擎**（2h 周期 daemon）：
  - 度量：48h 滚动 succeeded/failed、**耗尽型失败**（tool_calls 达预算）/**弃疗型失败**（预算充足仍放弃）、均调用数
  - **归因链**（关键设计）：mission 钉扎 model 优先；未钉扎按 created_at 时刻编制表反查；
    每次换血前后两张表入 `fleet_change.json` 账本。没有账本时换血会污染历史归因
    （实测：无账本版本 qwen3.7 席位历史虚高 117→148）
  - 判定阈值：strong（n≥8 且成功率≥70%）/ probation / eliminate（n≥15 且<40%，
    或 n≥10 且弃疗比>60% 且<50%）
  - 执行：**tmp+replace 原子改写**编制表；只杀「模型变更且无在飞任务」的 worker
- 首轮换血实测（48h 窗口，n=279）：

| 模型 | n | 成功率 | 判定 | 席位变化 |
|---|---|---|---|---|
| qwen3.7-plus | 120 | 79% | strong | 8→13（进全部角色做影子冗余） |
| qwen3.6-plus | 76 | 80% | strong | 8→9 |
| glm-5.2 | 23 | 74% | strong | 4→3 |
| deepseek-v4-flash | 26 | 54% | probation | 4→3 |
| glm-5.1 | 14 | 43% | probation | 4→2 |
| MiniMax-M2.5-AWQ | 9 | 33% | probation | 2→1 |
| Qwen3.8-27B | 11 | 9%（弃疗比 80%） | eliminate | 2→0 |

## 2 预算校准（最大单项杠杆）

- 实测：补报线 v1（18 次调用）成功率 **11%** → v2（40 次 + 工件清单/标题嵌入 goal）**88%**，同一批模型
- 根因：模型每轮发约 2 个并行工具调用 → 18 次调用≈9 轮，探索完就没有余量写产物+POST+finish
- 配方四条：
  1. 预算按「2 调用/轮」折算可用轮数，收尾至少留 1/4
  2. 派单端先 ls，把工件清单嵌进 goal（省掉 agent 自探索的调用）
  3. goal 内嵌阶段红线（「第 N 次调用前必须进入写产物阶段」）
  4. 燃料表注入：剩余≤15 次时每次工具结果附预算提醒，≤3 次时强令收尾
- 现行标准：常规单 80–140 调用 / 2700–5400s / 2.5–4M tokens

## 3 失败归因分类学（230 单样本）

- 总体成功率 69%（158 成功 / 72 失败）
- **harness 侧 ~75%**：预算耗尽 33%、协议门失败（工作树无改动/未 POST 报告）19%、
  派单配方事故 13%、历史 bug 10%
- **模型侧 ~15%**：弃疗型（预算充足仍放弃）7%
- **任务侧 ~10%**
- 推论：「模型未调用 finish」绝大多数=预算耗尽而非违抗——先修预算再评模型；
  耗尽型/弃疗型二分能预测「加预算谁受益、谁没救」

## 4 三层报告体系

1. **纪律层**：worker 首消息 footer（finish 前必须 POST 报告，未发=未完成）
2. **补报层**：report_watch 常设线（v2.1 成功率 88%；重试上限 2、在飞去重、防双 POST）
3. **质检层**：report_qa（PASS/MINOR/RESUBMIT；抽 3 个数字回工件 grep 复核；
   RESUBMIT 自动派返工单）
- **六要素论文级报告标准**：案例介绍 / 怎么做的 / 数字表（零手抄、程序化提取）/
  结果图（云图+比较曲线，无图必须写明理由）/ 注意点 / 误差判定（显式回答关键量
  是否<3%，无基准时声明原因）

## 5 多模型协作机制

- **模型钉扎路由**：MissionCreate 带 `model` 字段，claim 侧 `(model=X OR model IS NULL)`
  过滤——议事/角色任务精确卡位领取
- **议事会三轮**：R1 三模型并行提案 → R2 双模型质询 → R3 综合主席出计划（呈 owner 批后派发）
- **值班长班次**：班次即 mission；班内自主巡检/补货/派诊断，硬护栏内建
  （每班≤3 单、只准扫荡类任务、里程碑≥10 单出合并报告）
- **影子冗余**：每角色埋 1 席强模型影子位，主力淘汰/降级时无缝接管

## 6 可复用教训（每条都付过学费）

1. 改写配置文件必须 tmp+replace 原子化——rename 先行=崩在中间时配置文件消失，守护断粮
2. `pkill -f` 的目标路径若出现在自身命令行会自杀——用 `[x]` 字符类或只在目标机内执行
3. 任务表可能没有 claimed_at 列——时间归因用 created_at + 钉扎 model 字段
4. goal/markdown 文本含 `%` 时禁用 % 格式串——用占位符 + replace
5. 报告 figure 的 name 不带扩展名（后端存取两侧自动补 .png，带扩展名会落成 .png.png）
6. 单次 >240s 的工具调用会被 agent_lost 收割——worker 起后台心跳线程 60s 兜底
7. token 过期会 401 僵尸空转——api() 层自动重登
8. bounce/换血前必查在飞任务（status IN running/queued）——只杀确无 session 的
9. 容器 matplotlib 可能无 CJK 字体——图内文字用英文或预装字体
10. 「工作树无改动」一票否决——goal 第 0 步就要求建产物文件

## 7 效能量表（持续跟踪）

| 指标 | 实测 |
|---|---|
| 舰队在编 / 自愈时延 | 32/32，<30s |
| 补报线成功率 | 88%（v2.1 配方） |
| 质检数字抽查命中 | 3/3（首批评审） |
| 跨卡复现 | Couette 2D L2 1.8e-4 跨卡逐位一致 |
| 动态编制首轮换血 | 10 席变更、daemon 自动巡检 |

## 8 停摆自检自愈（stall detector，IN20-R5）

- 病案：2026-11-07 00:52→03:18 停摆 147.55 min（`saturation_pulse` 实测断档
  00:52:35→03:20:08），无人报警——检测器是人的注意力，这是单点。
- 机制：`ops/taskplane/stall_detector.py`（daemon，独立于任何会话）三规则——
  R1 断粮（pending=0 且 idle/online≥0.5 持续 15 min → 触发补给器 selfstarter）、
  R2 舰队心跳丢失（失联比≥0.5 持续 5 min → fleet_supervisor 重启路径）、
  R3 脉搏停滞（老化≥30 min → 自证存活 beacon）。
- 判据：历史时间线回放 MUST 报警+触发（R1 01:10 / R3 01:23，延迟 ≤1 采样周期），
  健康态负例 0 误报；`tests/test_stall_detector_in20_r5.py` 16 项。
- 纪律：只读台账 + hash 链审计 + `--rollback` 清闸闩；动作 dry/live 分离，
  dry 只探活不改系统（「不动作」≠「不检查」）。

## 自主续航验收门（IN20-G21）

`tools/autonomy/acceptance.py`：把 R1–R7 的机制替身装进同一条时间线真跑一遍，产出
`artifacts/autonomy_acceptance/autonomy_acceptance.json`（六条判据 + 证据 sha256）。判据：

- C1 补给器停摆回放自动触发（真 Postgres 沙箱库，真 INSERT + 真回滚）
- C2a 放门自动化真释放 + 幂等 + 回滚（可丢弃 schema，纯函数离线复算审计）
- C2b 合并自动化真合并 1 例 PR（审计链哈希 + git ancestry 双验）
- C3 propose 通道积压 stuck=0（宽限内在途不计）
- C4 退出仪表盘三指标基线（全 REAL + chronicle 快照）
- C5 垫池四单终态 + 能力增减账本入账

聚合规则：**WITHHELD 不得当 PASS**（沙箱/DB 不可达 → 整体不通过）。复跑：
`python3 -m tools.autonomy.acceptance && python3 -m pytest -q tests/test_autonomy_acceptance_gate.py`。

## 10 放门自动化（gate_auto_release，IN20-R2）

**问题**：战役门（campaign gate）靠人工 UPDATE 放行——owner 不在场则门永 blocked，
放了不留证据、放错不可逆。

**机制**：`ops/taskplane/gate_auto_release.py`（决策纯函数 + PG 原子 CTE 写路径 +
`gate_release_audit` 审计表 + JSONL 流 + `--rollback`），systemd/容器常驻，
注册进任务平面三方一致清单；无 LLM、无平台令牌，独立于任何人类/协调者会话。

**判据**：C1 上游依赖全 done；C2 判据工件可解析（非空 + json 合法 + 仓归属匹配）；
C3 非治理门（`owner_only`/治理关键词 → 只标不放）。不满足只记 `wait`，绝不写 `failed`。

**G13–G20 六门演练**（真实 DB）：生产只读矩阵 6 门 = 0 放 / 5 等 / 1 owner_only，
全表快照 sha256 前后一致；沙箱 `gate_drill` schema 真 UPDATE 4 门→pending 后全量
rollback 还原，release↔rollback 4/4 配对、审计字段完整；治理门 G20 生产 metadata
已写 `owner_only=1`。证据：`artifacts/gate_auto_release_drill.json`、图
`artifacts/gate_auto_release_drill.png`、pytest `tests/test_gate_auto_release.py`（10 条）。

**教训**：①「可解析」要按字面执行（json 损坏即不满足），否则自动化会放空门；
②等待态审计必须去重（以审计表最近原因为锚，跨进程重启不膨胀）；
③治理门判定要偏保守——误伤代价是「门等人 review」，误放代价是不可逆的治理越权；
④沙箱演练用同形目录（`<root>/<repo>/artifacts/…`）才能触发仓归属校验，否则
repo_mismatch 会让演练假阴性。
