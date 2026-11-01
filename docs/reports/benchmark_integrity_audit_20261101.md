# 基准数据集物理自洽性审计与几何闸门（TensorPlatform · tp-arch-3）

> 本文件由 `scripts/gen_report_md.py` 从 `artifacts/benchmark_integrity_audit.json` 程序化生成，文中数字均为工件读取，无手抄。

## ① 案例介绍

**物理背景.** 平台端点 `POST /api/v1/calculations/benchmark_validate` 是平台精度标准（误差 >15% 不可接受）唯一的自动执行点：它把阻力计算与「实验参考值」对比后判 PASS/FAIL。该端点信任 `backend/data/benchmark_reference.json` 的 12 条记录（SUBOFF / DTMB-5415 / KVLCC2 各 4 条），但这份数据集自入库起从未被机检：Ct、Rt、Fn、Cb、排水量五列是否互相自洽，无人验证。

**本案例 = 对参考数据做一次可复算的物理审计，并把审计结论接入端点判决**（验证欠账类）。几何与参数表如下，全部取自数据集自身字段：

| 船型 | L (m) | B (m) | T (m) | 声明排水量 | 声明 Cb | 声明 S (m²) | 工况数 |
|---|---|---|---|---|---|---|---|
| SUBOFF | 4.356 | 0.508 | 0.508 | 未声明 | 未声明 | 5.42 | 4 |
| DTMB-5415 | 5.72 | 0.759 | 0.246 | 466.3 | 0.508 | 未声明 | 4 |
| KVLCC2 | 320.0 | 58.0 | 20.8 | 307000.0 | 0.8098 | 未声明 | 4 |

## ② 怎么做的

1. **判据先于动手**：五条只依赖数据集内字段与代数关系的判据，不引用任何外部文献值（这是防止伪造参考值的硬闸）：
   - **C1** 声明 Fn ≡ V/√(gL)；不等时反解 implied L=(V/Fn)²/g，用于定位尺度混用；
   - **C2** 声明 Cb ≡ ∇/(L·B·T)，并用 ρ=998/1025 两种假设做敏感性判定；
   - **C3** 平台湿表面积闭式 S=πBL(0.5+0.25Cb) vs 声明试验值；Cb 缺失时在几何允许区间 Cb∈(0, π/4] 内取最有利值判定：若最有利仍超差，说明是结构性偏差而非参数问题；
   - **C4** 恒等式 Ct ≡ Rt/(½ρSV²)；
   - **C5** 同船型内 Ct 随 Fn 单调不降（回落 >10% 判两列不同源）。
2. **代码结构**（单一计算源：端点与离线审计调用同一函数，避免「报告里的数 ≠ 端点里的数」）：
   - `backend/services/hull_geometry.py` —— Fn/Cb/湿表面积闭合与几何闸门（纯函数）；
   - `backend/services/resistance_core.py` —— 阻力物理核（ITTC-1957 Cf + 平台经验 Cr），湿表面积作为显式入参；
   - `backend/services/benchmark_integrity.py` —— 五条判据 + 证据分级 + 精度复算；
   - `scripts/audit_benchmark.py` —— 离线审计，产出工件 JSON 与三张图；
   - 端点改造：`/calculations/resistance` 增加几何闸门与 `wetted_surface_m2` 覆盖；`/calculations/benchmark_validate` 接入审计，逐行返回 `evidence` / `trustworthy` / `failed_checks`。
3. **工具链**：Python 3.11 + FastAPI TestClient + matplotlib；全程零网络、纯 CPU（<2 s），在 load15≈117 的高负载机器上安全运行（容量纪律：只做轻量活）。

## ③ 计算结果

**数据集证据分级（12 条记录）**：REAL 0 / WITHHELD 0 / MOCK 12 → 数据集整体判 **MOCK**。

**判据通过情况**：

| 判据 | PASS | FAIL | UNAVAILABLE |
|---|---|---|---|
| C1_froude_consistency | 5 | 7 | 0 |
| C2_cb_closure | 0 | 8 | 4 |
| C3_surface_closure | 0 | 4 | 8 |
| C4_ct_rt_identity | 0 | 4 | 8 |
| C5_ct_monotonicity | 0 | 1 | 0 |

**逐条记录的关键量**（C1 rel_err = Fn 相对偏差；implied L 为 Fn 列反解特征长度；C3 bias = 湿表面积闭式相对偏差）：

| case | C1 | C1 rel_err | implied L (m) | C2 | Cb 声明 | Cb 由 ∇ 反算 | C2 rel_err | C3 bias | C4 | Rt_声明 / Rt_恒等式 |
|---|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | PASS | 0.000 | 4.355 | UNAVAILABLE | — | — | — | -0.11 | FAIL | 0.135 |
| SUBOFF-02 | PASS | 0.000 | 4.355 | UNAVAILABLE | — | — | — | -0.11 | FAIL | 0.121 |
| SUBOFF-03 | PASS | 0.000 | 4.355 | UNAVAILABLE | — | — | — | -0.11 | FAIL | 0.111 |
| SUBOFF-04 | PASS | 0.000 | 4.355 | UNAVAILABLE | — | — | — | -0.11 | FAIL | 0.105 |
| DTMB5415-01 | FAIL | 0.822 | 181.223 | FAIL | 0.508 | 0.437484 | 0.16 | — | UNAVAILABLE | — |
| DTMB5415-02 | FAIL | 0.822 | 181.223 | FAIL | 0.508 | 0.437484 | 0.16 | — | UNAVAILABLE | — |
| DTMB5415-03 | FAIL | 0.822 | 181.258 | FAIL | 0.508 | 0.437484 | 0.16 | — | UNAVAILABLE | — |
| DTMB5415-04 | FAIL | 0.822 | 181.246 | FAIL | 0.508 | 0.437484 | 0.16 | — | UNAVAILABLE | — |
| KVLCC2-01 | FAIL | 0.004 | 317.73 | FAIL | 0.8098 | 0.796832 | 0.02 | — | UNAVAILABLE | — |
| KVLCC2-02 | FAIL | 0.002 | 318.764 | FAIL | 0.8098 | 0.796832 | 0.02 | — | UNAVAILABLE | — |
| KVLCC2-03 | FAIL | 0.005 | 316.965 | FAIL | 0.8098 | 0.796832 | 0.02 | — | UNAVAILABLE | — |
| KVLCC2-04 | PASS | 0.001 | 319.596 | FAIL | 0.8098 | 0.796832 | 0.02 | — | UNAVAILABLE | — |

**端点 vs 参考值（Ct 口径，同一物理核复算）**：n=12，最有利情形下最小误差 5.12%，最大误差 602.31%，均值 91.88%；≤3% 的工况 0 条，≤15% 的工况 5 条。

**力口径（Rt，仅 SUBOFF 4 条有声明湿表面积）**：closure S 下误差 293.97%–479.58%，声明 S 下 514.01%–803.28%。两口径都不可能接近 15%，因为参考 Rt 列与参考 Ct 列本身相差一个量级（见 C4）——参考值自身矛盾，任何力口径的精度判定都无法成立。

> **物理注记**：Ct = Rt/(½ρSV²) 且 Rt = ½ρSV²(Cf+Cr) ⇒ **Ct 与湿表面积无关**。因此 S 的闭式偏差只作用于力（Rt ∝ S），不作用于无量纲 Ct。这条决定了本单对端点的改造（透传声明 S）只在力口径上有效，不能宣称提高了 Ct 精度。

## ④ 结果图

![benchmark_fn_consistency](../../artifacts/figs/benchmark_fn_consistency.png)

*图 1｜声明 Fn vs 几何派生 Fn：DTMB-5415 四点整体偏离 1:1 线（Fn 列隐含 L≈181 m，几何仅 5.72 m），SUBOFF/KVLCC2 落在线上。*

![benchmark_geometry_closure](../../artifacts/figs/benchmark_geometry_closure.png)

*图 2｜逐条 C1/C2 几何闭合残差（对数轴；阈值 Fn 0.1%、Cb 1%）。*

![benchmark_ct_rt_identity](../../artifacts/figs/benchmark_ct_rt_identity.png)

*图 3｜左：端点 Ct vs 参考 Ct 误差（含 Cb 误差带端点与 3%/15% 判据线）；右：C4 恒等式检验，全部记录落在 1:1 线下方 5.6–9.5 倍。*

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

- **C4 是致命项，C1 只是线索**：C1 说明 DTMB-5415 的 Fn 列与几何不同尺度（航速像实船节数、长度像模型），但真正让数据集不可用的是 C4——同一记录的 Ct 与 Rt 相差 5.6–9.5 倍，无法判断哪一列错，所以不能靠改一列「修好」，必须回到原始试验报告重新录入。
- **Cb 缺失时拒绝点估计**：SUBOFF 的 geometry 块没有排水体积，旧实现用 cb=0.508（= 直径数值）与 displacement=0.508 顶上，属于无来源编造；本单改为 Cb=None + 几何允许区间，端点对这类请求返回 `cb_source=band_over_geometry` 与力误差带。
- **闭式的结构性偏差与 Cb 取值无关**：SUBOFF 声明 S=5.42 m²，而 Cb∈(0, π/4] 内平台闭式的最有利情形仍偏小 10% 以上，说明该闭式对回转体不可用，必须由调用方传声明 S。
- **KVLCC2 的 Cb 闭合失败与 ρ 假设无关**：ρ=998 与 1025 下相对偏差均 >1%，说明数据集中 `displacement_t` 与 `cb` 不同源，不能靠换密度凑上。
- **未做**：不修改数据集里的 `experimental_ct` / `experimental_rt_n` / `froude_number` 三列——它们只能由原始报告重新录入，任何「修正」都会变成新的伪造。本单只做审计与闸门，数据集仍待重录。另：`backend/tests/conftest.py` 依赖 psycopg2/cryptography，本环境无此依赖、整站 app 无法加载，故新测试用 `--noconftest` 独立运行（旧 `backend/tests/feat/test_ship_benchmark.py` 同法验证 25/25 通过）。

## ⑥ 误差判定

| 关键量 | 基准 | 结果 | 是否 <3% |
|---|---|---|---|
| 端点 Ct（12 工况，最有利 Cb） | 数据集 experimental_ct | 最小 5.12%，最大 602.31% | **否**（0/12 达标） |
| 端点 Rt（SUBOFF 4 工况，声明 S） | 数据集 experimental_rt_n | 514.01%–803.28% | **否**（0/4） |
| 判据非自证性：合成自洽记录 | 应判 REAL | `test_consistent_synthetic_case_graded_real` 通过 | 是 |
| 判据灵敏度：单字段篡改（Ct×3） | 应判 FAIL | `test_single_field_corruption_is_detected` 通过 | 是 |
| 物理不变量 Ct 与 S 无关 | 解析恒等 | 相对差 1e-12 内相等 | 是 |
| 物理不变量 Rt ∝ S | 解析比例 | 相对差 1e-6 内成比例 | 是 |

**结论（honest）**：端点对数据集的精度**既不能**判为 <3%，**也不能**判为 <15%——因为基准自身 C4 不成立（12/12 条记录至少一条判据不闭合，数据集整体证据等级 MOCK）。这不是算法失败，而是验证欠账：在数据集按原始试验报告重录之前，`benchmark_validate` 的任何 PASS 都不能作为精度证据。本单的交付就是把这条限制从隐性变成端点返回值里显式可见（`evidence` / `trustworthy` / `evidence_note`），并修掉两处编造几何与一处 S 口径不可覆盖。
