# Knowledge Factory 试点调度器（TensorPlatform 侧）· 首批真实跑通

## ① 案例介绍
报告→知识产线的平台侧适配：复用平台既有 Mission API / Agent 身份 / 工作树 / 执行日志 / 验收机制，
不另建平台。产线把近万份 tensor_reports（当前 9682 份）按
「标准工作包 YAML」拆成五道工序（01 资产登记 / 02 分类解析 / 03 知识抽取 / 04 独立审核 / 05 综合），
每包只碰一份 report_id。本单交付调度器 daemon + 双门校验器 + 看板 + 首批真实跑通。

试点批：m15462 分层清单 200 份（repo×content_type×tier 131 层）。对账 tensor_reports 结果：
sha256 直配 22、标题匹配 13、stem 匹配 4 → 唯一绑定 35 份（35/200 = 17.5%），
其余 165 份按 owner 允许的「repo×scenario_key×长度」预分层补齐（回后校正）。

## ② 怎么做的
- 存储面：`backend/migrations/121_knowledge_factory_work_packages.sql` 建 tk_work_packages / tk_ingest_drafts /
  tk_knowledge_uses / tk_metric_snapshots。draft 表对 verification_status 加 CHECK(=unverified)，平台侧结构性无法写 verified；
  mission 状态 / KO 审核状态 / 科学验证状态三列分离。
- 契约面：`ops/knowledge_factory/contracts.template.json` 存 owner YAML 骨架（01/02 active，03/05 pending_spec，04 platform_skeleton）；
  `contract.py` 把契约 YAML **全文注入** mission goal，并给出原文获取方式与唯一结果信道（标题 `KF-RESULT <package_id>` + tag `kf-wp:<package_id>` + 唯一 ```json 块）。
  工序 03 契约（KBM-RCI-F1-SPEC）未回 → 调度器拒派（status=blocked），落表留痕。
- 派单面：闲位驱动（idle=工厂工位数−在飞 mission；idle≥8 才派，帽=min(12, idle//4)），flock 单实例，
  task_key=`wp-{report_id}-{stage}`，泳道 A/B/C 由 config 驱动（C 必审必 Skill、B 免审免 Skill、A 单强 Agent 整批由 ABC 实验单自派）。
- 验收面（双门）：结构门 g1 合法 JSON / g2 schema / g3 身份（package_id/report_id/task_type 三者一致，越界碰别人报告即拒）；
  证据门 g4 sha256 独立重算 / g5 quote 字节级反查原文 / g5b 无证据须显式 unresolved+理由 / g6 claim_id 去重（包内+跨包）/ g7 claim 命名空间。
  不合格 → rework 自动重派（帽 2 次，再败转 failed 留痕）。
- 审核/入库：双门通过 → 建工序 04 独立审核单（审核者上下文=原文+KO，reviewer mission ≠ producer mission 为硬门）；
  pass → 落 draft 外发（verification_status=unverified, ko_review_status=passed, science_verification_status=not_verified）。

## ③ 计算结果（程序化产出，零手抄）
- 已建工包：211 个；已派 mission 36 个（id 15501–15537）。
- 双门通过：7 个（wp-174-01, wp-297-01, wp-459-01, wp-542-01, wp-611-01, wp-771-01, wp-79-01）；门失败转 rework：2 个。
- 门自检（真语料报告 #14327 重放 6 个 payload）：控制组通过，5 个植入缺陷分别被对应门拦下表：
  - control_all_good: ok=True failed=[]
  - defect_fake_quote: ok=False failed=['g5_source_locator_resolvable']
  - defect_hash_mismatch: ok=False failed=['g4_source_hash_matches']
  - defect_duplicate_claim_id: ok=False failed=['g6_no_duplicate_ids']
  - defect_no_evidence: ok=False failed=['g5b_all_claims_located_or_unresolved']
  - defect_wrong_report: ok=False failed=['g3_identity']
- 工包状态矩阵：

| lane | stage | status | count |
|---|---|---|---|
| A | 01 | pending | 5 |
| B | 01 | pending | 21 |
| C | 01 | dispatched | 27 |
| C | 01 | pending | 142 |
| C | 01 | review | 7 |
| C | 01 | rework | 2 |
| C | 04 | pending | 7 |

看板八指标（owner 原文）：待处理报告数: 177 ｜ 已分发任务数: 36 ｜ 成功加工数: 7 ｜ 失败与返工数: 2 ｜ 独立审核通过率: None ｜ 证据定位成功率: None ｜ 每份报告平均成本: tokens=0.0 compute_s=330.4 (metered=False) ｜ 知识复用次数: 0

## ④ 结果图
`docs/figures/kf_pilot_dashboard.png`：左=双门重放（真语料+植入缺陷）逐例判定；右=工包状态矩阵。

## ⑤ 注意点与局限
- **未闭环**：本轮结束时工序 04 审核单已建 7 张但尚未回执，
  因此独立审核通过率 / 入库 draft 数为 0 —— 如实记，不粉饰。审核与入库路径由单元测试 + 门自检覆盖，未经真实回执验证。
- **03 未派**：KBM-RCI-F1-SPEC 契约未回，抽取工序一律 blocked；A 臂整批由 KBM-RCI-F1-ABC 自派，平台只建包+导出分片。
- **成本口径**：metered_usage 表当前 0 行 → tokens 记 0，成本只能用 mission wall_seconds 折算；这是计量缺口，不是零成本。
- **清单对账率仅 17.5%**：m15462 语料是仓库文档快照，与 tensor_reports 行不是一一对应；补齐部分来自预分层，代表性以「层覆盖」为准。
- 预分层首批按 id 倒序取，命中若干短报告（信息密度低）；发现后改为分层内按长度降序优选，下一批起生效。

## ⑥ 误差判定
本单是平台机制交付，无物理基准量，故按「门判定符合预期率」度量：门自检 6/6 符合预期（控制组放行、5 个缺陷各被对应门拦下），
误差项 0%；工包验收正确性尚缺真实回执样本（n=0），不宣称 <3% 或任何精度结论。

## repro_cmd
```
cd /root/tp-agent/work/m15495
python3 -m pytest tests/test_knowledge_factory.py -q              # 20 passed
python3 -m ops.knowledge_factory.scheduler --discover-only       # 发现/分层/建包
python3 -m ops.knowledge_factory.scheduler --once --limit 6      # 闲位驱动派单
python3 -m ops.knowledge_factory.scheduler --once --limit 0      # 收敛回执/双门/审核单
python3 -m ops.knowledge_factory.admin gate-replay --report-id 14327
python3 -m ops.knowledge_factory.admin status && python3 -m ops.knowledge_factory.admin metrics
```
