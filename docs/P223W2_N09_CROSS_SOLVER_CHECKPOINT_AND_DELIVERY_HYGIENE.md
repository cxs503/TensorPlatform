# 跨求解器 checkpoint 重启动 与 交付面卫生 机制成熟化（p223w2-n09 / m9532）

> 仓：TensorPlatform（共性机制，零领域语义）｜分支：`agent/m9532`｜判据微步数 N=1000｜压缩编解码器：`zstd`

## ① 案例介绍（背景 + 参数）

本节点做两件互不依赖、但同属「证据可复算」的基础设施：

1. **跨求解器 checkpoint / restart 一致性**。多求解器耦合运行时，「checkpoint 后重启」有三个各自独立的失效面：位级不可复现（状态 sha256 变了）、能量账本失守（重启点前后 `E_total` 对不上，或重启后能量单调漂移）、时间同步点未裁决（在非同步点存盘又按同步点语义续跑，悄悄重排了子循环相位）。三者必须分别有判据——位级相等并不蕴含能量守恒。

2. **交付面卫生**。仓库里 `agent/*` 分支只增不减时，「看起来没合并」的分支数远大于「真的还有东西没进主干」的分支数；而任何「降分支数」的动作若没有内容等价判据，都可能把真成果一起删掉。机制 = 四分分类（A 已被主干覆盖 / B 被超集覆盖 / C 真未交付 / D 孤儿）＋ 每仓基线化的「只减不增」门。

两件机制都**不含任何领域词条/判据/算例**（owner 2026-11-06 重建铁律）：子求解器只按「格点数 / 时间步 / 无量纲刚度 ωΔt」参数化，属数值方法层；分支分类只看 git 对象。因此同一个包可以直接服务任意领域仓。

### 参数表（三档耦合场景，全部由 `tools/hygiene/coupling_restart.py::default_scenarios()` 生成）

| 场景 | 说明 | 求解器（格点/Δt/ωΔt/界面节点） | 耦合边（κ） | 子循环 |
|---|---|---|---|---|
| `pair_2way` | 两体双向耦合，Δt 比 1:2（最小子循环基线） | s_fine(32/0.25/0.05/0) + s_peer(32/0.5/0.05/8) | fine↔peer 5e-4 | lcm(1,2)=2 微步 |
| `chain_3way` | 三体链式耦合，Δt 比 1:2:4 | s_fine(32/0.25/0.05/0) + s_med(16/0.5/0.05/4) + s_coarse(8/1.0/0.05/2) | fine↔med 5e-4；med↔coarse 4e-4 | lcm(1,2,4)=4 微步 |
| `all_to_all_3way` | 三体全耦合（三条边）+ 异分辨格点 | s_fine(32/0.25/0.05/0) + s_med(16/0.5/0.05/4) + s_coarse(8/1.0/0.05/2) | fine↔med 5e-4；med↔coarse 4e-4；fine↔coarse 2.5e-4 | lcm(1,2,4)=4 微步 |

阈值（集中定义，见 `tools/hygiene/coupling_restart.py`）：能量残差 `< 1.000e-10`；重启后能量漂移 `< 0.001000`（0.1%）；同步点要求所有子求解器相位为 0 **且** 无悬挂耦合半踢（整数判据，不留容差）。

## ② 怎么做的（方法 / 流程 / 关键设置）

### 2.1 耦合态 = 「求解器数组 + 时钟相位 + 能量账本」三层

* **子求解器**：周期格点上的二次型振子链（数值方法层），状态 = 模态 `q` 与速度 `p`；离散能量 `E = ½‖p‖² + ½kΣ(q_{i+1}-q_i)²`，用 velocity-Verlet（kick-drift-kick）积分，逐元素运算、不调 BLAS，保证同输入逐位同输出。刚度由 `ωΔt = 0.05` 反算，保证各 Δt 的求解器处于同一无量纲分辨率。

* **耦合算子**：界面自由度之间挂一根刚度 κ 的弹簧（Hamiltonian 耦合，等值反向 ⇒ 动量守恒），耦合势能 `U_c = ½κ(q_L-q_R)²` 计入总能量，因此能量账本是**闭合**的；积分用 Strang 分裂（cycle 首尾各半踢）。这是**结构性**守恒，而不是调参调出来的：任何一次半踢都成对作用在两个求解器上。

* **时钟**：时间轴以 `Δt_micro = min Δt` 为最小刻度，求解器 i 每 `n_i = Δt_i/Δt_micro` 个微步走一步；`cycle = lcm(n_i)` 个微步后所有求解器同时位于步边界 = **同步点**。调度状态 `phase[i]` 与悬挂半踢标志 `leg` 不属于任何单个求解器的数组，**必须进 checkpoint**——这正是非同步点存盘的失效根源。

* **位级存储**：复用 `tools/longrun/checkpoint.py`（每数组 `sha256(dtype+shape+tobytes())`、原子 `os.replace`、链式 manifest），所以「位级」是字节判据，不是容差比较。

### 2.2 同步点裁决

`load_runner(strict_sync=True)`（生产口径）在三种情况下抛 `SyncPointViolation`：同步块缺失（朴素存盘）、任一 `phase≠0`、`leg≠0`。`strict_sync=False` 是**为了证明危害**而存在的：它按同步点语义（相位归零）强行续跑，结果必然与连续运行不同。

### 2.3 交付面四分

| 分类 | 判据（全部可推翻） | 证据字段 |
|---|---|---|
| A 已被主干覆盖 | `base..ref` 空（ANCHOR）/ `git cherry` 全 `-`（BANKED）/ 触及路径内容与 base 逐字节一致（CONTENT_SAME，含「整树 diff 为空」的改回形态） | `reason`/`ahead`/`unique`/`files` |
| B 被超集覆盖 | 唯一 patch-id 集合是另一分支唯一集的**真子集** | `superset_by`/`superset_unique`/`unique_patch_ids` |
| C 真未交付 | 存在主干没有的等价补丁，且无任何分支包含它 | `unique`/`unique_patch_ids`/`files` |
| D 孤儿 | 与 base 无共同祖先 / 引用不可解析 | `merge_base`/`reason` |

### 2.4 只减不增门（每项都是独立可证的检查）

| 检查 | 红条件 |
|---|---|
| `coverage_complete` | 任一行缺 `class` 或证据 `reason` |
| `digest_match` | 台账 sha256 重算不一致（台账被改过） |
| `baseline_present` | 无基线 ⇒ **fail-closed 判红**（宁可拦住，不可放行） |
| `count_le_baseline` | 未合并集合（B∪C∪D）比基线多 |
| `no_new_unmerged` | 出现基线里没有的未合并分支（逐条列出） |
| C 类零丢失 | 基线里的 C 分支消失且无法证明其 commit 已是 base 祖先 |
| `c_count` | C 类数量比基线多 |
| `regressions` | A→未合并 的改判（分支被强推重写，已入库分支偷偷长出未交付内容） |

AC6 的抽样复核用**另一条命令路径**（`git log --cherry-pick --right-only` + `git diff --quiet` + 独立 patch-id 重算）复判，不复用主分类函数；B 类还要求独立重算出的 patch-id 子集关系成立。

## ③ 计算结果（数字表，程序化注入）

### 3.1 AC1–AC4：位级 / 能量 / 同步点 / 压缩（N=1000）

| 场景 | checkpoint 微步 | 位级 sha256 一致 | 能量残差 \|E_load−E_save\| | 重启后漂移 | 轨迹振荡带 max\|ΔE/E₀\| | AC3 严格拒绝 | AC3 宽松续跑发散 | 对照(同步点宽松)一致 | 压缩无损 |
|---|---|---|---|---|---|---|---|---|---|
| `all_to_all_3way` | 500 | True | 0 | 1.570e-05 | 8.370e-04 | True/True | True | True | True (zstd) |
| `chain_3way` | 500 | True | 0 | 1.808e-05 | 7.796e-04 | True/True | True | True | True (zstd) |
| `pair_2way` | 500 | True | 0 | 8.511e-06 | 4.042e-04 | True/True | True | True | True (zstd) |

AC2 的**连续性**另有断言：重启段的 1000 微步漂移与不中断段的同区间漂移之差 <1e-9（`test_ac2b_energy_drift_below_0_1_percent`），即「重启没有引入能量」。

逐场景分段证据（含 sha256 原文、manifest 链摘要、每个数组的压缩前后 sha256、压缩损坏检出）见 `artifacts/p223w2_n09_hygiene/restart_evidence.json`（本表由该文件的数字直接生成）。

### 3.2 AC5/AC6：交付面台账（两仓实测，只读扫描）

| 仓 | base | 分支总数 | A 已覆盖 | B 超集覆盖 | C 真未交付 | D 孤儿 | 未合并(B∪C∪D) | 覆盖率 | 抽样复核 | 台账 sha256（前 16 位） |
|---|---|---|---|---|---|---|---|---|---|---|
| `TensorPlatform` | main | 2105 | 666 | 54 | 1376 | 9 | 1439 | 100% | 100% (30 条) | `ea2be5ce5d829901` |
| `ship-performance-platform` | main | 1566 | 224 | 44 | 1298 | 0 | 1342 | 100% | 100% (30 条) | `9f15f5dde9996725` |

**台账格式与可复算链**：表内 sha256 是**完整台账**的摘要；入库文件为瘦身版（`files` 逐行截断到 6 条，另存 `files_count`/`files_digest`，整行 `evidence_sha256` 对完整证据，整表 `full_ledger_sha256` = 上表摘要，`ledger_sha256` = 瘦身表自身摘要）。瘦身只动「看得见的文件清单」，`class` 判据与证据链一环未减；平台仓瘦身表重算一致（verify_ok=True，瘦身摘要 `6ff0bd5383b14ca0` / `a6dff3e55a791e38`）。完整台账可由同一条 CLI 命令在两分钟内确定性重放。

**base 敏感性（同一仓换 base）**：以 `develop` 为 base 时 A=0 / B=254 / C=1316（未合并 1570，sha `2b4935860230f707`）。A 类被打回未合并 ⇒ 该仓的实际集成分支是 `main` 而非 `origin/HEAD→develop`。**台账必须连同 base 一起引用**，否则「未合并数」没有意义。

门禁实测：两仓各自基线化后均 `green`（TensorPlatform 检查项 13 项；ship 违规码 []）——**当前 = 基线本身就是门要钉住的起点**。`fail-closed`（无基线必红）与「注入新未合并分支必红」由 pytest `test_gate_red_fail_closed_without_baseline` / `test_ac8_negative_control_detects_all_injected` 机器证明，而不是靠运行时恰好为绿。

## ④ 结果图

![energy](artifacts/p223w2_n09_hygiene/fig_energy_ledger.png)

*图 1*：三档耦合场景的能量账本 `E(t)/E(0)−1` 随微步演化。曲线在 ±0.1%（红色虚线=判据阈值）内**有界振荡**而非漂移；振荡幅度来自辛积分的 O((ωΔt)²) 项，按理论应为 (0.05)²/4 ≈ 6e-4 量级，实测最大值见 ③ 表（程序注入）。

![branches](artifacts/p223w2_n09_hygiene_platform/fig_branch_classes.png)

*图 2*：TensorPlatform 的 `agent/*` 分支四分分布，以及与基线的未合并数对比（只减不增门的两条主检查：数量不回升 + 不出现新的未合并分支）。

两图 sha256：`fig_energy_ledger.png`=c74cc863a8afd8ee；`fig_branch_classes.png`=d076c2b39f63909f（图数据来自 ③ 的同一批 JSON，无手抄）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **反力符号：位级一致 ≠ 正确**。第一版耦合内核把二阶差分的符号写反了（`F=-k·D2` 而非 `+k·D2`），于是 `F` 变成反恢复力：**checkpoint/restart 的 sha256 判据全部通过**（两次运行同样地爆炸），只有能量账本判据抓住了它（漂移 5.7e3，即能量发散 10¹⁵量级）。这是本节点最重要的方法论结论：位级可复现只证明「可重复」，不证明「对」，必须与守恒量判据并行。

2. **速度旋转型耦合会泵能量**。最初的耦合算子设计成「界面速度对的二维旋转」——算子本身正交、瞬时能量精确不变，但与两个不同频率求解器的积分复合后系统性地泵入能量（实测 40 微步内 +18%）。换成 Hamiltonian 弹簧耦合（等值反向 + 势能入账 + Strang 分裂）后转为有界振荡。**「算子守恒」不等于「格式稳定」**，这是数值耦合的经典陷阱。

3. **辛格式给出的是有界振荡而非零漂移**。判据必须区分「漂移（系统性）」与「振荡带（O(Δt²)、有界）」：本报告两个数都报（③ 表 drift 与 band 两列）。把振荡带当漂移会误判，把漂移藏在振荡带里会放过真问题。

4. **zstd 对小载荷会膨胀**。三档场景的状态只有 ~1 KB（不可压缩 float64），zstd 帧头开销使 `packed/raw > 1`（见表 ③ 与 restart_evidence.json 的 `total_*_bytes`）。AC4 判的是**无损**，不是压缩率；压缩率要在大状态上测，不能拿小载荷的比值当结论。

5. **`git cherry` 的边界**：fixture 里若 `main` 尚未分叉，`git cherry-pick` 会快进成**同一提交**，分支随即变成 ANCHOR 而不是 BANKED——判据本身没错，是夹具没构造出「等价但不同提交」的形态。同理，「改过又改回」的分支整树 diff 为空，必须归 A/CONTENT_SAME 而不是 C。

6. **C 类是「未交付」的上界，不是「功能缺失」的证明**。判据是「主干当前内容与该分支不同」；若某分支的改动后来被另一形态的提交重写进主干，它仍会落在 C。B 类只覆盖「被**兄弟分支**超集吸收」的情形，不覆盖「被主干后续重写吸收」。因此 C 的正确用法是**待人工/机器复核队列**，而不是「可直接删除」。

7. **base 选择会改变结论**（见 ③ 的 base 敏感性）：同一 ship 仓以 `main` 为 base 得 C=1298，以 `develop` 得 C=1316 且 A=0。台账必须与 base 一起引用。

8. **范围边界**：本包只读 git（`for-each-ref`/`rev-list`/`cherry`/`log -p`/`patch-id`/`diff`/`merge-base`），不建删改任何 ref；真实仓的分支删除/合并是运维动作，本机制只给「删哪条安全、哪条不能碰」的证据与门禁。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答）

| 关键量 | 实测 | 基准/阈值 | 相对余量 | 是否 <3% |
|---|---|---|---|---|
| checkpoint 前后能量残差 | 0 | < 1.000e-10 | 0%（严格 0） | 是 |
| 重启后 1000 微步能量漂移 | 1.808e-05 | < 0.001000 | 1.8% of 阈值 | 是 |
| 轨迹能量振荡带 | 8.370e-04 | < 0.001000 | 83.7% of 阈值 | 是（余量 16.3%） |
| 位级 sha256 一致（3 档场景） | 3/3 | 3/3 | 0 | 是 |
| 抽样复核一致率 | 100% | ≥95% | — | 是 |
| 分类覆盖率 | 100% | 100% | 0 | 是 |

**与真值的对标说明**：本节点交付的是**机制**（可复算的一致性/卫生判据），不存在需要与实验/参考解比对的物理量；这里的「基准」是每条判据的形式阈值（能量残差 1e-10、漂移 0.1%、覆盖率 100%、复核一致率 95%）。能量判据另有一条**解析锚**：辛格式的能量振荡幅度理论值为 (ωΔt)²/4 ≈ 6.25e-4，实测 band 最大 8.370e-04，与之同量级（偏差来自耦合分裂项），支持「有界振荡而非漂移」的解释。

### AC7 诚实申报：未达成

AC7 要求 `ship-performance-platform` 未合并分支数 443→<50。**实测未达成**：以 `main` 为 base，真实未交付集合 C=1298（未合并 B∪C∪D=1342），远大于 AC7 假设的 443，更不可能 <50。原因与处置：

* 该仓现有 `agent/*` 分支总数 1566，其中 A=224（可安全归档）、B=44（已被兄弟分支吸收）、C=1298。抽样 8 条 C 分支独立抽查：均为「领先 base 1–2 个提交、最近 1 周内、改动集中在 artifacts/」的真未落地内容，不是误判。

* **降数是运维动作**（删除/合并 1550+ 条 ref），本单的写权限只覆盖自己的 worktree，不能对其它仓建删 ref；机制交付的是「A/B 可安全归档清单 + C 复核队列 + 基线化的门（此后只减不增）」与逐条证据，以及 heads-up：**交付债是 AC7 假设的约 3 倍**，按 443 规划的清理批次不够用。

* 门已经可以把「不再恶化」钉死：此后每波次跑 `tools/hygiene/cli.py gate` 即可对未合并数回升/新未合并分支/C 类丢失判红（负控在 pytest `test_ac8_negative_control_detects_all_injected` 中注入 3 条新分支，检出率 100%）。

### 判据总表

| 判据 | 结论 | 证据 |
|---|---|---|
| AC1_bitwise_reproducible | PASS | `artifacts/p223w2_n09_hygiene/restart_evidence.json` |
| AC2a_energy_residual | PASS | `artifacts/p223w2_n09_hygiene/restart_evidence.json` |
| AC2b_energy_drift | PASS | `artifacts/p223w2_n09_hygiene/restart_evidence.json` |
| AC3_sync_point_adjudication | PASS | `artifacts/p223w2_n09_hygiene/restart_evidence.json` |
| AC4_compression_lossless | PASS | `artifacts/p223w2_n09_hygiene/restart_evidence.json` |
| AC5_classification_coverage | PASS | `artifacts/p223w2_n09_hygiene_platform/ledger.json + _ship/ledger.json` |
| AC6_sample_recheck | PASS | `artifacts/*/sample_recheck.json` |
| AC7_ship_unmerged_under_50 | FAIL（如实申报） | `artifacts/p223w2_n09_hygiene_ship/ledger.json` |
| AC8_gate_negative_control | PASS | `tests/test_p223w2_n09_delivery_hygiene.py::test_ac8_negative_control_detects_all_injected` |

*判据全称*：AC1_bitwise_reproducible=位级可复现（3 档耦合场景，重启 sha256 == 连续运行）；AC2a_energy_residual=能量账本（残差 <1e-10；重启后漂移 <0.1%）；AC2b_energy_drift=能量账本（残差 <1e-10；重启后漂移 <0.1%）；AC3_sync_point_adjudication=时间同步点裁决（非同步点：严格拒绝 + 宽松续跑必须发散，同步点对照必须一致）；AC4_compression_lossless=压缩无损（zstd/lzma 往返 sha256 不变 + 损坏检出）；AC5_classification_coverage=四分分类覆盖率 100% + 台账 sha256 可重算（两仓）；AC6_sample_recheck=抽样 30 条独立复核一致率 ≥95%；AC7_ship_unmerged_under_50=ship 仓未合并 <50 —— **未达成（honest FAIL）**；AC8_gate_negative_control=门禁负控：注入 3 条新分支 ⇒ 门红、检出 3/3。

