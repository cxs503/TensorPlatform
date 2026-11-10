# PB-010 度量器不得把自己计入被测量的进展（自证进展判例）

**症状**：节点明明已经停滞，进展计数器却在每一拍被自己的产物清零——早停报告/快照日志一落盘，
下一拍就扫描到「新工件」，于是 `change_fraction>0`、`info_gain>0`、连续无进展计数归零，
早停永远打不响。P148-W2-N08 实测：24 个 mtime-only 触碰拍里 5 拍被判「有进展」，
整场景 34 拍判定与构造标注错拍（约 15%）。

**根因**：测量面与被测面重叠。度量器把自己的输出目录写进了被扫描工作树，形成
「产出 → 计为进展 → 判定继续 → 继续产出」的正反馈。同类变体：门禁拿被测对象自报的数当输入（见 PB-005）。

**规则（最小可执行形式）**：
1. 度量器启动时显式声明 `exclude_paths`（自己的输出目录/日志路径），扫描层必须物理跳过；
2. 报表里写清「哪些路径被排除」，让复核者能判断是否存在自证；
3. 判定语义分层：**标记**（可机器标注、可升格为处置）与**告警**（提示、不升格）必须分开字段，
   不要用一个 `is_stalled` 同时承载两种语义——N 拍规则的零误报与斜率规则的 18% 噪声混在一起时，
   下游无法判断「这条红是不是能停机器」；
4. 阈值只准调语义、不准调叙事：判据不达标先改机制，不要改标签定义凑绿。

**适用范围**：任何「周期性扫描自身工作目录/日志目录来度量进展」的看门狗、停滞检测器、
预算熔断器、进度心跳。**不适用**：纯外部观测（被测量对象与度量器不在同一文件系统命名空间）。

**反例与边界**：
* 若产出目录本就在工作树内（生产常见），排除必须精确到自己的输出路径，不能把整棵 `runs/` 排除——
  否则会把节点真实的交付物也当噪声屏蔽，走向另一个方向的假绿；
* 排除只解决「自证」，不解决「反复重写同一文件但不推进判据」——那是语义级进展问题，属领域判据。

**回归保护**：`tests/test_progress_metrics_p148w2_n08.py`
`TestOrchestratorEndToEnd::test_mtime_only_activity_is_not_progress`、
`TestInformationGain::test_mtime_touch_yields_zero_gain`、
`TestStagnationDetection::test_converging_progress_not_flagged`（噪声不升格）。
跑批工具 `tools/progress_metrics_eval.py` 的 C4 mtime-only 负控（24/24 判 0）。

**证据与出处**：TensorPlatform mission 9930 / 节点 `p148w2-n08-progress-metrics-maturity`；
模块 `ops/taskplane/progress_metrics.py`（`ArtifactScanner.exclude_paths`，
`StagnationVerdict.is_stalled` vs `trend_flat`）；判据数字 `artifacts/p148w2_n08/criteria.json`；
逐拍明细 `artifacts/p148w2_n08/scenario_results.json`。2026-11-10 验证。

**复核状态**：作者自检（agent/m9930）；独立复核待排。**最后核验日**：2026-11-10。

**配套纪律**（同案副产物）：psql 事务演练（`BEGIN … ROLLBACK`）解析结果时必须过滤命令标签
（`BEGIN/ROLLBACK/UPDATE n`），否则会把 `'BEGIN'` 当数据行读成 id；回写类函数必须返回真实受影响行数，
「0 行」要如实报，不许恒真。