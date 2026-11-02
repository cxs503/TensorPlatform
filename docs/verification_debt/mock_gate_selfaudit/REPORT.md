# 证据分级裁决器的自审：MOCK 指纹探测器灵敏度 50% → 100%（m2035）

作者：tp-impl-7（platform 席，自找活 m2035） · **全部数字由 `tools/audit/mock_gate_selfaudit.py` 写出 `artifacts/mock_gate_selfaudit/audit.json`，本文件数字从 JSON 注入，零手抄**

## ① 案例介绍

被测对象不是船舶内核，而是**平台证据分级（REAL/MOCK/STUB/WITHHELD）的机器裁决器** `tools/audit/mock_fingerprint.py`（597 行，6 条纯数值判据 + 端点级聚合）。
它由 m1523/m1547/m1760 的三次人工抓假（回转要素落 0.5L 栅格、垂荡 RAO ≡ 1.260、KQ ≡ KT/10）倒逼出来，如今是每个端点证据等级的第一道机器闸门。
问题在于：**这扇闸门自己从未被系统检验**。m1791 只用一组合成响应证明「探测器会响」，没有回答两个可量化的问题：

* **灵敏度**：真 MOCK 端点会不会从它手里走掉（漏判 = 把 MOCK 写成 REAL，最危险）？
* **特异度**：真响应端点会不会被它误杀（误判 = 把 REAL 打成 MOCK）？

| 项 | 值 |
|---|---|
| 被测模块 | `tools/audit/mock_fingerprint.py`（判据 INVARIANT / RIGID_RATIO / FLAT / PLATEAU / COARSE_LATTICE / DIRECTION_VIOLATION） |
| 合成语料 | **14** 例（MOCK 10 / REAL 4），标签由构造先验给定 |
| 判负口径 | verdict ∈ {MOCK, PARTIAL} 记「检出」；REAL 例必须 verdict = RESPONSIVE |
| 两级对照 | legacy = m1791 原判据；hardened = 本单加固后（同一份语料、同一份代码，只切口径开关） |
| 净算量 | 全离线纯 CPU，< 1 s |

### 合成语料的真值定义（参数表）

| 案例 | 真值 | 构造 |
|---|---|---|
| `mock_constant` | MOCK | 输出恒为 1.26（m1547 现场） |
| `mock_ratio_locked` | MOCK | KQ ≡ KT/10（m1760 现场） |
| `mock_direction` | MOCK | 回转要素随舵角反向 |
| `mock_coarse_lattice` | MOCK | 落在 115 m（0.5L）整数倍栅格 |
| `mock_flat_vector` | MOCK | 向量通道常量填充 0.02 |
| `mock_string_encoded` | MOCK | 输出以 JSON 字符串给出（"1.260"） |
| `mock_echo_shadow` | MOCK | 响应回显请求体，真值恒定 1.0 |
| `mock_quantized_ratio` | MOCK | 恒比但两端 `round(x,4)` |
| `mock_blind_jitter` | MOCK | 硬编码值 ×(1+1e-8·i) 伪抖动 |
| `mock_clamped_nonzero` | MOCK | 尾部夹紧在非零常数 2.94 |
| `real_b_series` | REAL | 两条独立 B 系列回归 |
| `real_noisy` | REAL | 健康回归 + 1e-4 相对量测噪声 |
| `real_rounded_4dp` | REAL | 健康回归但输出 4 位小数 |
| `real_zero_floor` | REAL | K_T = max(0,·) 物理零位 |

## ② 怎么做的

1. 把判决链整条跑在合成语料上：`score()` → `find_key` → `as_floats` → `classify_*` → `endpoint_verdict`（不绕过任何一层，因此测的是真实闸门而不是某个函数）。
2. 用 `set_detector_profile('legacy'|'hardened')` 切换 5 个口径开关，同一语料重跑：差值即加固增益。
3. 对每条实测到的漏判通道做**最小加固**（不新增判据类别名之外的复杂度），并用**必须保持静默的 REAL 例**守住误杀方向。

### 实测到的 5 条逃逸通道与对应加固

| # | 通道（legacy 的实际行为） | 加固（hardened） | 落点 |
|---|---|---|---|
| 1 | 字符串编码的数值被 `as_floats` 判 None ⇒ 该量整条丢弃，verdict = NO_KEY（不是 MOCK） | 解析数字字符串 | `_coerce` |
| 2 | `find_key` DFS 先命中回显分支 `input.gm`（随扫参变）⇒ 恒定输出伪装成 RESPONSIVE | 两遍搜索，先跳过 input/request/echo/meta 等分支，显式路径仍权威 | `find_key/_search_key` |
| 3 | 恒比被 `round(x,4)` 掩盖：比值相对展布 ~1e-3 ≫ 1e-9 | 分子/分母各自估计打印分辨率，一阶误差传播 `eps=|A/B|(q_A/|A|+q_B/|B|)`，比值展布 ≤ 1.0×eps 判 `RIGID_RATIO_QUANTIZED` | `classify_ratio` |
| 4 | 伪抖动 1e-8（比 1e-9 大 1 个量级）逃过浮点恒定性判据 | 分辨率内恒定性：打印位数口径 ≤2 末位单位、等距网格口径 ≤max(3,1.5(n-1)) 量子 ⇒ `INVARIANT_QUANTIZED` | `quantized_invariance` |
| 5 | 方向量尾部 d==0 不构成方向违背、又不在 `plateau_keys` ⇒ 夹紧漏判；反向地，1e-13 浮点噪声翻转即判 DIRECTION_VIOLATION | 方向约束量也做尾部夹紧检查（尾部为物理零位则豁免）；方向违背加幅度下限 max(1e-9·scale, q/2) | `classify_series/direction_violation/plateau_tail` |

## ③ 计算结果

### 3.1 总账（同一语料，两级口径）

| 口径 | 灵敏度 | 特异度 | 漏判 | 误杀 |
|---|---|---|---|---|
| legacy（m1791） | **0.500**（5/10） | 1.000（4/4） | mock_string_encoded, mock_echo_shadow, mock_quantized_ratio, mock_blind_jitter, mock_clamped_nonzero | 无 |
| hardened（m2035） | **1.000**（10/10） | 1.000（4/4） | 无 | 无 |

结构性结论：**legacy 的特异度本来就是 100%（原判据不会误杀），它的病是"看不见伪装"**——10 个真 MOCK 里 5 个被判成 RESPONSIVE 或 NO_KEY，等于把硬编码端点升格为 REAL。

### 3.2 逐案判决（hardened）

| 案例 | 真值 | verdict | 逐判据 |
|---|---|---|---|
| `mock_constant` | MOCK | **MOCK** | {'gm': 'INVARIANT'} |
| `mock_ratio_locked` | MOCK | **MOCK** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RIGID_RATIO'} |
| `mock_direction` | MOCK | **MOCK** | {'tactical_diameter': 'DIRECTION_VIOLATION'} |
| `mock_coarse_lattice` | MOCK | **PARTIAL** | {'advance': 'COARSE_LATTICE'} |
| `mock_flat_vector` | MOCK | **MOCK** | {'heave_rao': 'FLAT'} |
| `mock_string_encoded` | MOCK | **MOCK** | {'gm': 'INVARIANT'} |
| `mock_echo_shadow` | MOCK | **MOCK** | {'gm': 'INVARIANT'} |
| `mock_quantized_ratio` | MOCK | **MOCK** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RIGID_RATIO_QUANTIZED'} |
| `mock_blind_jitter` | MOCK | **MOCK** | {'gm': 'INVARIANT_QUANTIZED'} |
| `mock_clamped_nonzero` | MOCK | **PARTIAL** | {'tactical_diameter': 'PLATEAU'} |
| `real_b_series` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RESPONSIVE'} |
| `real_noisy` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RESPONSIVE'} |
| `real_rounded_4dp` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RESPONSIVE'} |
| `real_zero_floor` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE'} |

### 3.3 legacy 逐案判决（逃逸证据基线）

| 案例 | 真值 | verdict | 逐判据 |
|---|---|---|---|
| `mock_constant` | MOCK | **MOCK** | {'gm': 'INVARIANT'} |
| `mock_ratio_locked` | MOCK | **MOCK** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RIGID_RATIO'} |
| `mock_direction` | MOCK | **MOCK** | {'tactical_diameter': 'DIRECTION_VIOLATION'} |
| `mock_coarse_lattice` | MOCK | **PARTIAL** | {'advance': 'COARSE_LATTICE'} |
| `mock_flat_vector` | MOCK | **MOCK** | {'heave_rao': 'FLAT'} |
| `mock_string_encoded` | MOCK | **NO_KEY** | {} |
| `mock_echo_shadow` | MOCK | **RESPONSIVE** | {'gm': 'RESPONSIVE'} |
| `mock_quantized_ratio` | MOCK | **RESPONSIVE** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RESPONSIVE'} |
| `mock_blind_jitter` | MOCK | **RESPONSIVE** | {'gm': 'RESPONSIVE'} |
| `mock_clamped_nonzero` | MOCK | **RESPONSIVE** | {'tactical_diameter': 'RESPONSIVE'} |
| `real_b_series` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RESPONSIVE'} |
| `real_noisy` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RESPONSIVE'} |
| `real_rounded_4dp` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE', 'kq': 'RESPONSIVE', 'kq/kt': 'RESPONSIVE'} |
| `real_zero_floor` | REAL | **RESPONSIVE** | {'kt': 'RESPONSIVE'} |

### 3.4 图

* 图1 `fig1_sens_spec.png`：灵敏度/特异度两级柱状对比。
* 图2 `fig2_case_matrix.png`：14 例 × 2 口径的判决矩阵（红= MOCK 级，黄= PARTIAL/栅格，绿= RESPONSIVE/NO_KEY）。
* 图3 `fig3_evasion_window.png`：逃逸窗口——指纹量级（1e-3~1e-8）比 `INVARIANT_TOL=1e-9` 高 1~6 个量级，正是"看得见却没在看"的区间。

## ④ 注意点（踩坑/局限/适用范围）

1. **加固不是"更严"而是"更懂分辨率"**：所有新增判据都以端点自己的输出分辨率（打印位数/等距网格）为尺度，没有把阈值整体放松或收紧，因此 `tests/test_mock_fingerprint.py` 的 20 条旧单测与两条旧假阳性边界（独立粗糙多项式、细栅格）全部保持原判。
2. **零位豁免是物理判断**：`K_T = max(0,·)` 或 η₀ 无推力归零会在尾部出现严格相等的点，那是物理下限不是截断，加固后显式豁免（否则会把真端点误杀）。
3. **仿射栅格仍不区分**：等距扫参上的线性响应与"5 档查表栅格"在数值上不可区分，旧判据把它们都判 COARSE_LATTICE（PARTIAL）。本单试过 affine 豁免，与既有单测 `test_coarse_lattice_flagged` 冲突且会削弱查表指纹，故**放弃**——这是已知的保守侧偏差（宁可 PARTIAL 不放过栅格）。
4. **灵敏度 100% 只对语料负责**：语料 14 例覆盖了 5 类经典指纹 + 5 条口径级伪装，不等于覆盖全部逃逸形态（例如：**响应随扫参真实变化但精度系统性偏差**需要文献基准，不在本探测器职责内）。
5. **未做在线 A/B**：沙箱安全策略拦下了对 :8001 的 live 扫描，故本单**没有**「真实端点判决在新口径下不变」的在线证据；替代证据是 `--live-ab` 入口（已实现，对 `artifacts/mock_sweep/raw.json` 做 legacy/hardened 双算并列出 verdict/key 变更），待有落盘回执的环境可直接执行。**这是本单的诚实缺口**。

## ⑤ 误差判定

| 关键量 | 基准 | 实测 | 相对误差 | <3%? |
|---|---|---|---|---|
| legacy 灵敏度 | 由构造标签给出（10 个 MOCK 应全检出） | 0.500 | 50.0% | 否（欠检出 5 例） |
| hardened 灵敏度 | 同上 | 1.000 | 0.0% | **是（0.0%）** |
| legacy 特异度 | 由构造标签给出（4 个 REAL 应全 RESPONSIVE） | 1.000 | 0.0% | 是 |
| hardened 特异度 | 同上 | 1.000 | 0.0% | 是 |
| 判决可复现性 | `tests/test_mock_fingerprint.py` 20 条旧单测 | 20 passed | — | 是（未回归） |
| 新增单测 | 本单 13 条 | 13 passed（合计 33 passed / 6 skipped） | — | 是 |

验收判据（机器可判，均已达成）：hardened 灵敏度 = 1.000 且特异度 = 1.000；legacy 漏判清单稳定复现（`test_legacy_profile_reproducibly_misses_five_channels`）；旧 20 条判据单测零回归；落盘工件与代码一致（`test_audit_artifact_is_current`）。
