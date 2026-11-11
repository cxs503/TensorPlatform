# m13872 · p474w1-n01 证据链 + 统计计量学门（可复用教训）

## 结论一句话

跨仓证据链可用，但**度量口径比算法更容易错**：关联率、普查来源、功效解析式三处口径/公式
缺陷各让头条数字偏差 3×/自反馈膨胀/4×；全部由回归测试锁定。

## 陷阱（按危害排序，含机制）

| # | 陷阱 | 机制 | 回归护栏 |
|---|---|---|---|
| T1 | 关联率把"A 仓的度量键"算成"B 仓工件的证据" | 正则匹配不带仓作用域 → 分母虚高、分子虚低（33 % vs 92 %） | `associate()` 内 `label_repo` 作用域 + 合成双仓夹具（现值 1.0） |
| T2 | `max_files` 按字典序截断整段目录 | 目录名序 `artifacts/ < benchmarks/ < docs/` → 预算耗尽后 docs 从未被扫描；且被样本上限截掉的文件被误判未绑定 | 上限大于全量 + `GroupSample.accepted_files` 与截断样本分离 |
| T3 | 普查读取自身输出 → 声明数膨胀/digest 不复现 | 工件与 `docs/<node>/acceptance.json` 均含 `status: PASS`，被下一次扫描当来源（67 KB → 166 KB） | 两处都写进 `reclassification.exclude` + 幂等性回归测试 |
| T4 | 普查单位不一致 → 假阳性 INCONSISTENT | 通用键名把 `value=6e8`（Re 数）与 `tolerance_pct=2` 对比，误报 9 条 | `value_threshold_pairs` 单位配对 + 双用例 |
| T5 | 功效解析式用观测 `s` 当总体 `σ` | 条件功效里 `x̄` 的尺度是 `σ/√n` 而非 `s/√n`；margin≫离散度时功效低估 4 倍 | 蒙特卡洛对照（4 SE）+ `test_mean_power_matches_monte_carlo` |
| T6 | MDE 二分容差写死 1e-4 | 对 1e-6 级守恒 margin 直接返回 0（"完美数据却 MDE=0"） | 容差随 margin 缩放 + `test_mde_is_not_zero_for_small_margins` |
| T7 | 自实现 `**` 段匹配递归错误 | `**` 分支递归传了 `rest` 而非原模式 → 嵌套目录整类不可见（某仓识别 0 文件） | 段匹配单测 + `deep/**` 夹具 |
| T8 | `Path.glob` 按 pattern 逐条走树 | 7 仓 × 3 pattern 时扫描从 ~30 s 涨到 >6 min（超工具 120 s 时限被整组收割） | 单次 `os.walk` + 段匹配器（3250 文件 ~25 s 冷、100 s 含解析） |
| T9 | 后台长跑被工具超时整组收割 | `nohup ... &` 仍属同进程组；`sleep` 型轮询也会被算进 120 s | `setsid nohup ... & disown` + 分段轮询 |
| T10 | `pkill -f <关键字>` 自杀 | 关键字节串出现在自身命令行 → 连 shell 一起杀 | 用 `ps -ef \| awk '{print $2}' \| while read pid; do kill $pid; done` |

## 方法（可搬运）

1. **等价检验三件套**：点判据 → 双单侧检验 + 1−2α 对偶区间 + `power@μ=0` / MDE 覆盖度。
   MDE 定义取"目标功效下仍可认证的最大真实偏差"，**不要**用 post-hoc power 当设计属性。
2. **解析功效可验证**：条件于 `s` 的拒绝域是区间 `[c·s/√n−δ, δ−c·s/√n]`，对 χ² 分布做中点求积
   即得精确功效；与蒙特卡洛对照 4 SE 内即可放心用于 10^4 量级条目的扫描。
3. **自助法 KS 等价**：对 sup-CDF 距离做分组重采样，`1−2α` 上界 < margin 即判等价；决策用
   2000 次，灵敏度（power/MDE）用 ≤400 次以控成本。
4. **普查可复算**：摘要只放"计数 + 标记率 + 对全部声明规范化的 SHA-256"，另给 `recompute()`
   独立重算，判据才可执行。
5. **零领域语义**：用仓内既有 `tools/evidence_domain_lexicon.json` 做机械扫描，命名配置化，
   测试夹具用 `synth_solver_a` 一类合成名——该门真的抓到过配置里一个 `displacement`。

## 适用边界

* 能力样本是**跨案例合并**，不是独立重复实验；区间描述工件总体，不描述单次运行；
* 跨仓方差/分布条目是证据流之间的**描述性**比较，不是物理等价性声明；
* 历史普查只覆盖同时给出数值+阈值+样本量+离散度的声明（本次 341 条 PASS 无一满足 → 标记率 1.0）；
* 值域/文件上限限定总体，被排除数量必须逐条报告。

## 溯源

mission 13872；产物 `artifacts/p474w1_n01/evidence_chain.json`（digests：scan `33bc9e66…`、
capabilities `95e40a6f…`、mde `8349f2ce…`、census `b8f9d232…`）；报告 tensor_reports id 13511；
71 例 pytest 全绿（`tools/capability_evidence_chain/tests`）。最后核验：2026-11-11。