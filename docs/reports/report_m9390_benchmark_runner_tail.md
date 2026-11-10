## ④ 结果图

图由 `tools/benchmark_runner/artifact.py:render_suite_figure` 与 `report_figure.py` 生成，
图上的每个数字都直接读自 `result.json`（不重算、不手输）；PNG 的 sha256 在各自 `manifest.json` 里。

| 图 | 文件（worktree 内路径）| 说明 |
|---|---|---|
| 汇总图 | `artifacts/benchmark_runner/summary.png` | (a) 15 个算例的实测相对误差（绿=PASS / 红=FAIL）与预声明阈值（蓝短横）；(b) 判据余量 `阈值/实测`，>1 通过。负对照两例的余量 ≪1，一眼可见门不是摆设 |
| 积分族 | `artifacts/benchmark_runner/fx_integration/run_fx_integration/figures/fx_integration_verdict.png` | (a) 逐算例误差 vs 阈值；(b) 误差随子区间数 n 的下降曲线；同族 n=32→128 的实测收敛阶在「关键结论」块里由两算例误差直接相除得到（理论 4 阶） |
| 级数族 | `artifacts/benchmark_runner/fx_series/run_fx_series/figures/fx_series_verdict.png` | (a) 逐算例误差 vs 阈值；(b) 误差随项数的下降 |
| 求根族 | `artifacts/benchmark_runner/fx_rootfinding/run_fx_rootfinding/figures/fx_rootfinding_verdict.png` | (a) 逐算例误差 vs 阈值；(b) 误差随容差 tol 的下降 |
| 负对照 | `artifacts/benchmark_runner/fx_negative_control/negctl_run1/figures/fx_negative_control_verdict.png` | 两例均为红色（欠分辨率 + 无判据 fail-closed）|

> 这些图是**判据门裁决的可视化**，不是场量云图——本单是平台机制节点，被检验对象是引擎而非某个物理场，
> 故不存在场量云图；对照基线曲线即"测量值 vs 闭式参考解"的逐算例误差（§3.1 表 + 图 (a)/(b)）。

## ⑤ 注意点（踩过的坑、局限、适用范围）

### 踩过的坑（都是"跑绿≠修好"的实例）
1. **机器零基线会造出假回归告警**：Simpson 对三次式精确 → `fx_int_cubic_n64` 的 `rel_error` 是 `0.0`；
   朴素相对漂移 `|cur−base|/|base|` 在 base=0 处爆成天文数字，把一次 `0.0 → 2e-16` 的浮点噪声报成回归。
   处置：引入分母地板 `ZERO_FLOOR=1e-12`（`regression.drift_of`），并用单测钉死该行为
   （`test_regression_zero_baseline_does_not_false_alarm`）。假告警与漏报一样是不诚实。
2. **默认策略把显式参数吃掉了**：首版 `_NON_TRACKED`（分辨率类指标不参与漂移）无差别生效，
   连 `--track evals` 也被压掉 → 回归注入演示"全绿"。是端到端测试
   （`test_end_to_end_drift_exit_code_2`）把它逼出来的：**显式 track 必须优先于默认策略**。
   这正是本单要求"回归检测器要有判别力控制"的价值——没有 +5%/+15% 的正对照，这个 bug 会静默存在。
3. **suite 无聚合判据时语义含糊**：首版让 `gate.evaluate([])` 返回 FAIL，于是"没写 suite_criteria"的
   整批直接失败，却与"算例失败"混在同一个退出码里。处置：**suite 级判据在配置期强制非空**
   （退出码 3=配置非法），运行期 fail-closed 只负责算例级。两类失败必须能区分。
4. **工件自己把出处写脏**：`git status` 会把本次运行刚写出的 `artifacts/**` 算进 `git_dirty`。
   处置：`collect_provenance(ignore_paths=...)` 排除输出目录，`git_dirty` 才真正表示"源码是否偏离 HEAD"。
5. **漂移告警是非方向性的**：+5% 与 −5% 都会报同一等级，因为"哪边更好"是领域判断，引擎不该内置。
   引擎只报幅度与方向（`baseline → current`），等级按幅度（>3% warn，>3×3% critical）。

### 复现审计命令（worktree 根目录，0 命中 = 引擎与配置无应用域词条）

```bash
grep -rniE 'ship|hull|seakeep|propuls|froude|wigley|suboff|kvlcc|resistance|ice_|冰区|阻力|船体|螺旋桨' \
     tools/benchmark_runner/*.py tools/benchmark_runner/configs/*.json | wc -l
# 输出：0
```

### 局限（如实申报）
1. **判据算子只有四类半**：标量比较（`<= < >= > == !=`）、区间（`in_range`）、单调性（`monotonic`）、
   有限性（`finite`）。分布检验、收敛阶、守恒量残差、CDF/K-S 这类判据尚未内建——当前靠 SUT 侧先折算成
   标量指标再判（可行但会丢掉一部分信息）。扩展点明确：`gate.SUPPORTED_OPS` + 一个 `_check_*` 函数。
2. **回归只按 `case_id` 配对**：算例改名等价于"消失 + 新增"（报 warn/info，不报漂移）。改名要同步改基线。
3. **KPI 口径未闭环（honest 缺口）**：`tools/guide_kpi_ledger.py` 里 `unattended_cases` 的 SQL 口径是
   「DB 里 mission goal 命中『案例 X（Tensor…』的去重计数」（10-31 看板口径 65 案例级）。本单的 13 个
   算例落在工件账本 `artifacts/benchmark_runner/unattended_cases.json`（只读 KPI 桥产出），
   **没有**写进 DB、也没有改 KPI ETL 的口径公式。所以「DB 侧 65→≥75」这一推进需要平台侧把
   账本并入口径（或由上游按算例铺任务单）——本单只保证引擎侧的算例是**可计数的、带哈希链的**。
4. **引擎不含并发/超时/资源限额**：无人值守的调度语义交给外部 scheduler（本波在控制面顺序执行，
   14 个算例端到端 <1 s；大算例应走集群）。引擎不重试、不做统计显著性检验。
5. **仅验证了确定性 SUT**：合成 SUT 无随机、无跨 ISA 差异；真实求解器若带随机性或 loong64/x86 差异，
   需叠加仓库已有的 `tools/repro_digest` 跨 ISA 协议，本单未合并该协议。

### 适用范围
任何「有参考解或既有基线、需要重复跑、需要机器裁」的算例集合：数值内核回归、API 契约回归、
参数扫描守门、以及后续把领域仓的 reference case 以点分路径注入即可复用的场景（引擎本身不改）。

## ⑥ 误差判定

口径：`rel_error = |实测 − 闭式参考| / max(|参考|, 1e-300)`，逐项数字见 §3.1（程序化注入）。

* **正向 13 个算例是否 <3%？** 是。最大相对误差见「关键结论（全部由工件注入）」块的
  `正向算例集最大相对误差` 一行：约 1e-3 量级（最差的是项数最少的级数算例 `fx_series_r050_n10`，
  其预声明容忍为 1e-2，余量约 10 倍；其余算例 1e-7~1e-13），远优于 3% 的验收线；
  且每个算例都通过了自己的**预声明**容忍（逐项 margin 见 §3.1）。
* **参考值来源**：全部为闭式解（Sin/Cos/Exp/多项式原函数、几何级数极限 r/(1−r)、√c），
  由机器精度级公式给出，无实验不确定度、无拟合参数 ⇒ 本单不涉及"对标实验误差>15% 不可接受"的问题；
  误差是**算法离散误差**（Simpson O(h⁴)、二分 O(2⁻ⁿ)），可随分辨率单调下降（图 (b) 已证）。
* **负对照是否被拦住？** 两例全部 FAIL（欠分辨率例 1.1e-4 相对误差 vs 预声明 1e-12；
  无判据例被判 `__none__:__no_criteria__`）⇒ 判据门有否决权，不是空转。
* **回归检测是否既灵敏又不误报？** +2% 注入：`clean`、退出码 0（不误报）；+5%：warn、退出码 2；
  +15%：critical、退出码 2；且这三例的判据门始终 PASS（§3.3）——说明"门通过"与"有回归"是两个
  独立的裁决维度，不能互相代替。
* **未达成的部分（honest）**：DB 侧 `unattended_cases` 从 65 推进到 ≥75 未完成（见 ⑤ 局限 3）；
  另有多判据类型、并发调度、跨 ISA 复现协议三项未纳入本单。

## 修订记录（诚实申报）

| 报告 id | 内容 | 说明 |
|---|---|---|
| 11073 | v1 首次发布 | 工件提交 509c4150 之前的正文；缺源码域词审计命令与 KPI 口径对齐说明 |
| 11075 | v2 | 正文含上述补充；**该条 `summary` 字段在一次接口探针测试中被误写为占位串**，以 v3 的 summary 为准（报告接口不可改/不可删，只能重发新版本，故留痕于此）|
| 本版（v3） | 最终 | 与工件提交 3a83de33 一致；summary 完整 |
