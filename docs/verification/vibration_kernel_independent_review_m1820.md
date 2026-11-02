# 振动噪声域「已合并核」独立复核 + 接线缺口实锤（m1820）

> EDIT_WHITELIST: `tools/validation/`, `tests/`, `docs/`, `artifacts/`
> 席位 tp-infra-2 · 分支 `agent/m1820` · 被审对象 `backend/api/domain/ship_vibration.py`
> 判据账本 `artifacts/m1820/verdicts.json` / `.md` · 接线证据 `artifacts/m1820/wiring_evidence.json`
> 图 `artifacts/m1820/fig_vibration_audit.png` · 回归测试 `tests/test_vibration_kernel_reference_m1820.py`

## ① 案例介绍（物理背景 / 对象 / 参数）

船舶总体性能九学科中，振动噪声域的内核在 m1493 已实现并合并进主干（commit `fac2ee79`，
`backend/api/domain/ship_vibration.py`，713 行、覆盖 Euler–Bernoulli 梁、Kirchhoff 薄板、
螺旋桨/柴油机激励、单自由度隔振、质量定律隔声、吻合频率、dB 叠加与 IEC A 计权）。
但该核的证据等级一直**只有自证**（m1493 自带审计文档 + 自带测试），没有独立席位按
「文献表值 / 异离散 / 时域积分」复核过——这正是 owner 主指标「已合并功能独立复核通过率」的口径。

被审物理量清单（每项都有外部基准，故可机器裁）：

| 类 | 量 | 基准来源 |
|---|---|---|
| 梁 | 无量纲特征值 λ₁…λ₄（C-C / C-F / C-P / P-P） | Blevins (1979) Table 8-1 |
| 梁 | 固有频率 f₁…f₄ | 文献 λ 代闭式（与核自求根独立） |
| 梁 | Hermite FEM 交叉验证的真实收敛阶 | 三次单元 + 一致质量理论阶 O(h⁴) |
| 板 | Laplacian 特征值 μ_mn / 第 1 阶频率 | 5 点有限差分（异离散）+ 解析式 |
| 噪声 | A 计权（34 个频点） | IEC 61672-1 Table 3 标称表 |
| 噪声 | 正入射隔声量 / 场入射工程式 / 吻合频率 | Bies & Hansen 质量定律、f_c≈12/h[mm] kHz |
| 噪声 | dB 能量叠加、倍频程分配能量守恒 | 恒等式 |
| 隔振 | 传递率（3 个工作点 + r=√2 恒等式） | 时域 RK4 积分（不用传递率公式） |
| 激励 | 轴频 / 叶频 / 柴油机点火频 | rpm/60、n_blades·f_s、n_cyl·rpm/(60·2) |
| 振型 | clamped 端 w、w′ 残差 | 数值微分 + Richardson 外推 |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

1. **判据先于动手**：每条判据强制带 `basis`（基准来源）与 `tol`（容差），无基准不许进账本；
   容差按基准性质给（恒等式 1e-9~1e-12、文献表值 1e-5、规范表取整 0.3 dB、异离散离散误差 1~5%）。
2. **参考侧与被测侧物理隔离**：`tools/validation/vibration_reference_m1820.py` **不 import 被审模块**，
   只用①文献表值（Blevins / IEC 61672-1）②不同离散（5 点 FD Laplacian、时域 RK4）
   ③恒等式（r=√2 时 T≡1、两个 60 dB 叠加 = 60+10log10 2）④独立书写的闭式。
3. **被测侧按文件路径加载**（`importlib.util.spec_from_file_location`）——因为 `api/__init__.py`
   会拉起 DB 引擎（`psycopg2` 不在本机），说明该域模块无法经包导入，这一点本身记入注意点。
4. **接线取证三层**（`tools/validation/probe_vibration_wiring_m1820.py`，全只读）：
   静态引用计数 / 模块可加载性 / 在线 HTTP 探针（**带控制组**排除假阴性）。
5. **判决器** `tools/validation/verify_vibration_kernel_m1820.py` 一键产出账本 + 图；
   **回归测试** 25 条把参考侧与"被审核现状"两层都钉住。

复现（各 ≤90 s，纯解析 + 小网格，无重算力）：

```bash
python3 tools/validation/probe_vibration_wiring_m1820.py
python3 tools/validation/verify_vibration_kernel_m1820.py
python3 -m pytest tests/test_vibration_kernel_reference_m1820.py -q
```

## ③ 计算结果

**判据总数 45：PASS 45 / FAIL 0**（分域：beam 14/14, plate 4/4, noise 14/14, isolation 6/6, excitation 7/7）。

被审模块：`backend/api/domain/ship_vibration.py`（713 行），引入 commit：`fac2ee79 m1493: matured mission deliverables（58 个文件被门禁滤除）`。

### 接线取证（infrastructure 层）

- 全仓 `*.py` 中对 `ship_vibration` 的**生产引用点 = 0**（唯一命中为模块自带测试 `backend/tests/feat/test_ship_vibration.py` 与本单 tools/tests）；
- `backend/api/domain/__init__.py` 的 `__all__` 是否含该域：**False**；
- `backend/main.py` 是否出现 vibration：**False**（共 16 个 include_router）；
- 在线探针 :8001：6 条候选路径状态码 [404, 404, 404, 404, 404, 404]，控制组（stability/resistance）[405, 405] → 404 为「路由真的没有」而非鉴权假阴性；
- 模块 sha256 `601d97cbce56c228…`，行数 713，`@router` 端点数 6。

### 判据账本（全量）

| 编号 | 域 | 判据 | 服务值 | 参考值 | 偏差 | 容差 | 判决 |
|---|---|---|---|---|---|---|---|
| A.clamped-clamped | beam | lambda_1..4 (clamped-clamped) vs Blevins Table 8-1 | 14.1372 | 14.1372 | 1.143e-14 | 1.0e-05 | **PASS** |
| A.clamped-free | beam | lambda_1..4 (clamped-free) vs Blevins Table 8-1 | 10.9955 | 10.9955 | 7.593e-15 | 1.0e-05 | **PASS** |
| A.clamped-pinned | beam | lambda_1..4 (clamped-pinned) vs Blevins Table 8-1 | 13.3518 | 13.3518 | 1.706e-13 | 1.0e-05 | **PASS** |
| A.pinned-pinned | beam | lambda_1..4 (pinned-pinned) vs Blevins Table 8-1 | 12.5664 | 12.5664 | 0.000e+00 | 1.0e-05 | **PASS** |
| B.clamped-free | beam | f_1..4 (clamped-free) vs 文献 lambda 闭式 | 7.19098 | 7.19098 | 1.619e-13 | 1.0e-05 | **PASS** |
| B.clamped-clamped | beam | f_1..4 (clamped-clamped) vs 文献 lambda 闭式 | 45.758 | 45.758 | 9.612e-14 | 1.0e-05 | **PASS** |
| C.clamped-clamped | beam | FEM 收敛阶 p (clamped-clamped, 8->32 单元) | 3.9965 | 4 | 8.759e-04 | 1.5e-01 | **PASS** |
| C.clamped-free | beam | FEM 收敛阶 p (clamped-free, 8->32 单元) | 4.10098 | 4 | 2.524e-02 | 1.5e-01 | **PASS** |
| D.plate_mu11 | plate | Laplacian 特征值 mu11：FD(32x32) vs 解析 | 0.999245 | 1 | 7.550e-04 | 1.0e-02 | **PASS** |
| D.plate_mu11_extrap | plate | mu11：FD Richardson 一阶外推 vs 解析 | 0.999805 | 1 | 1.948e-04 | 1.0e-03 | **PASS** |
| D.plate_f11 | plate | SSSS 板 f11：模块闭式 vs 独立书写闭式 | 13.6587 | 13.6587 | 1.301e-16 | 1.0e-09 | **PASS** |
| D.plate_mu21_fd | plate | FD 第 2/3 阶 mu vs 解析 mu(2,1)/mu(1,2) | 0.997262 | 1 | 2.738e-03 | 1.0e-02 | **PASS** |
| E.aw_table | noise | A 计权最差频点 (16 Hz) vs IEC 61672-1 Table 3 | -56.4263 | -56.7 | 2.737e-01 | 3.0e-01 | **PASS** |
| E.aw_1000 | noise | A 计权 1000 Hz 归零约定 | 0.000141528 | 0 | 1.415e-04 | 2.0e-02 | **PASS** |
| F.tl_normal_250 | noise | 正入射 TL (250 Hz) vs 教科书精确式 | 37.3108 | 37.3108 | 0.000e+00 | 1.0e-09 | **PASS** |
| F.tl_normal_500 | noise | 正入射 TL (500 Hz) vs 教科书精确式 | 43.3308 | 43.3308 | 0.000e+00 | 1.0e-09 | **PASS** |
| F.tl_normal_1000 | noise | 正入射 TL (1000 Hz) vs 教科书精确式 | 49.3512 | 49.3512 | 0.000e+00 | 1.0e-09 | **PASS** |
| F.tl_normal_2000 | noise | 正入射 TL (2000 Hz) vs 教科书精确式 | 55.3718 | 55.3718 | 0.000e+00 | 1.0e-09 | **PASS** |
| F.tl_field_eng | noise | 场入射 TL (2 kHz) vs 工程式 20log10(m''f)-47.2 | 50.3718 | 50.6974 | 3.256e-01 | 5.0e-01 | **PASS** |
| G.fc_5mm | noise | 吻合频率 (钢 5 mm) vs 弯曲波速匹配式 | 2392.63 | 2392.63 | 0.000e+00 | 1.0e-09 | **PASS** |
| G.fc_rule_5mm | noise | 吻合频率 (钢 5 mm) vs 经验式 12/h[mm] kHz | 2392.63 | 2400 | 3.072e-03 | 5.0e-02 | **PASS** |
| G.fc_8mm | noise | 吻合频率 (钢 8 mm) vs 弯曲波速匹配式 | 1495.39 | 1495.39 | 0.000e+00 | 1.0e-09 | **PASS** |
| G.fc_rule_8mm | noise | 吻合频率 (钢 8 mm) vs 经验式 12/h[mm] kHz | 1495.39 | 1500 | 3.072e-03 | 5.0e-02 | **PASS** |
| H.combine_two | noise | 两个 60 dB 不相干叠加 | 63.0103 | 63.0103 | 0.000e+00 | 1.0e-12 | **PASS** |
| H.combine_ten | noise | 10 个等强 50 dB 叠加 = 60 dB | 60 | 60 | 0.000e+00 | 1.0e-12 | **PASS** |
| H.octave_energy | noise | 倍频程分配的能量守恒（未计权带级回算总级） | 80 | 80 | 0.000e+00 | 1.0e-09 | **PASS** |
| I.T_sqrt2_zeta0 | isolation | T(r=sqrt2, zeta=0) 恒等于 1 | 1 | 1 | 4.441e-16 | 1.0e-12 | **PASS** |
| I.T_sqrt2_zeta0.05 | isolation | T(r=sqrt2, zeta=0.05) 恒等于 1 | 1 | 1 | 4.441e-16 | 1.0e-12 | **PASS** |
| I.T_sqrt2_zeta0.2 | isolation | T(r=sqrt2, zeta=0.2) 恒等于 1 | 1 | 1 | 3.331e-16 | 1.0e-12 | **PASS** |
| I.T_rk4_f20_z0.05 | isolation | T(f=20, fn=10, zeta=0.05) vs 时域 RK4 稳态幅值比 | 0.339182 | 0.339327 | 4.294e-04 | 2.0e-02 | **PASS** |
| I.T_rk4_f5_z0.05 | isolation | T(f=5, fn=10, zeta=0.05) vs 时域 RK4 稳态幅值比 | 1.33204 | 1.3319 | 1.096e-04 | 2.0e-02 | **PASS** |
| I.T_rk4_f30_z0.1 | isolation | T(f=30, fn=10, zeta=0.1) vs 时域 RK4 稳态幅值比 | 0.145366 | 0.14536 | 3.678e-05 | 2.0e-02 | **PASS** |
| J.shaft | excitation | 轴频 (120 rpm) | 2 | 2 | 0.000e+00 | 1.0e-12 | **PASS** |
| J.bpf | excitation | 叶频 (4 叶, 120 rpm) | 8 | 8 | 0.000e+00 | 1.0e-12 | **PASS** |
| J.diesel4 | excitation | 4 冲程 6 缸点火频 (120 rpm) | 6 | 6 | 0.000e+00 | 1.0e-12 | **PASS** |
| J.diesel2 | excitation | 2 冲程 6 缸点火频 (120 rpm) | 12 | 12 | 0.000e+00 | 1.0e-12 | **PASS** |
| K.margin_+0.20 | excitation | 裕度语义 (margin=+0.20, tol=0.15) | 1 | 1 | 0.000e+00 | 1.0e-12 | **PASS** |
| K.margin_+0.10 | excitation | 裕度语义 (margin=+0.10, tol=0.15) | 0 | 0 | 0.000e+00 | 1.0e-12 | **PASS** |
| K.margin_-0.20 | excitation | 裕度语义 (margin=-0.20, tol=0.15) | 1 | 1 | 0.000e+00 | 1.0e-12 | **PASS** |
| L.modeshape_w_clamped-clamped | beam | 第 1 阶振型端点 w 残差 (clamped-clamped) | 0 | 0 | 0.000e+00 | 1.0e-12 | **PASS** |
| L.modeshape_slope_decay_clamped-clamped | beam | 端点斜率残差随加密的衰减指数 (clamped-clamped) | 1 | 1 | 0.000e+00 | 1.0e-12 | **PASS** |
| L.modeshape_slope_limit_clamped-clamped | beam | 端点斜率残差 Richardson 极限 (clamped-clamped) | 8.5237e-08 | 0 | 8.524e-08 | 1.0e-04 | **PASS** |
| L.modeshape_w_clamped-free | beam | 第 1 阶振型端点 w 残差 (clamped-free) | 0 | 0 | 0.000e+00 | 1.0e-12 | **PASS** |
| L.modeshape_slope_decay_clamped-free | beam | 端点斜率残差随加密的衰减指数 (clamped-free) | 1 | 1 | 0.000e+00 | 1.0e-12 | **PASS** |
| L.modeshape_slope_limit_clamped-free | beam | 端点斜率残差 Richardson 极限 (clamped-free) | 3.15003e-09 | 0 | 3.150e-09 | 1.0e-04 | **PASS** |

## ④ 结果图

`artifacts/m1820/fig_vibration_audit.png`（4 面板，全部读自 `verdicts.json`，零字面量）：

- (a) 梁 λ₁…λ₄ 相对文献表值的偏差（对数轴，4 个边条件并列）→ 全部在 1e-5 容差下贴着机器精度；
- (b) A 计权：核解析曲线 vs IEC 61672-1 Table 3 的 34 个规范点；
- (c) 板 μ₁₁：有限差分（异离散）随网格加密收敛到解析值，并给出 Richardson 外推点；
- (d) 隔振传递率：核解析式曲线 vs 时域 RK4 稳态幅值比（三个工作点），标注 r=√2 恒等点。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **最重要的发现不是精度，而是接线**：该核在主干中**没有任何生产引用点**——不在
   `backend/api/domain/__init__.py` 的 `__all__`，`backend/main.py` 的 11 个域聚合 router 里也没有它；
   在线探针（:8001）6 条候选路径全 `404`，而控制组 `calculations/{stability,resistance}` 返回 `405`
   （路由存在、方法不允许）——证明 404 不是鉴权/探针失效。**结论：核的数学是 REAL，但能力对用户仍是
   NOT_SUPPORTED（代码 ≠ 能力）**。修复属船舶代码改动，按席位白名单不在本单范围内，建议派单到
   `ship-performance-platform`（两行：`__all__` 增项 + `main.py` include_router）。
2. **判据自身的两处返工（诚实披露）**：①FEM 收敛阶最初把「8→32 单元」当成 2 倍加密归一化，
   得出 p≈3.0 的假 FAIL；改为 4 倍后观测 p≈4.00，与三次 Hermite + 一致质量的理论阶一致（核无缺陷）。
   ②振型端点斜率残差最初按固定 5e-3 判，实为 `np.gradient` 端点单边差分的截断量级；改为
   「随加密单调下降 + Richardson 外推极限 < 1e-4」后通过。两处都是**判据缺陷，不是物理缺陷**，
   修改已写进脚本注释，未放宽任何物理容差。
   （另：C-P 文献值只按渐近式 λ_n≈(n+1/4)π 的窄带校验，因该边条件存在 tan λ=±tanh λ 两种书写约定。）
3. **不是自证**：参考侧完全不含被审模块的公式，但两侧共享「薄板/梁理论」这一物理前提——若前提本身错
   （例如考虑剪切变形或大挠度），本复核不覆盖。适用域：细长梁（L/h>10）、薄板（h/a<1/10）、
   单自由度隔振、扩散声场质量定律；船体实阻尼、设备源级这类**没有基准的量不编造**，仍为 WITHHELD。
4. **场入射 TL 是工程约定**：核用 `TL_n − 5`，与常规工程式 `20log10(m″f) − 47.2` 相差 0.33 dB（判据
   容差 0.5 dB），属约定差异而非错误；跨平台对比时须统一口径。
5. **机器容量**：本单为轻量解析审计（load15≈84–101，nproc=128），未起任何重算力进程；探针与判决
   全量重跑 <3 min。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

**回答：是（关键物理量全部 <3%，绝大多数贴机器精度）。**

- 有基准的可裁量共 45 项，其中 24 项为恒等式级（容差 ≤1e-9），实测最大相对偏差 2.524e-02（`C.clamped-free`）；
- 相对偏差 **超过 3% 的判据数为 2**；超过 3% 的项目清单：['E.aw_table', 'F.tl_field_eng']；
- 最松的一项是场入射 TL 的工程约定差异（核 `TL_n−5` vs 工程式 `20log10(m″f)−47.2`），量级 0.3 dB / 判据 0.5 dB 内——属**约定**差异，非精度缺陷；
- 无基准的量（船体实阻尼比、设备源级、实际舱室 SPL）**不编造**，保持 WITHHELD，故不存在「误差」可报。

逐项数字见上表与 `artifacts/m1820/verdicts.json`（零手抄，均由判决器写入）。

## ⑦ 交付物

| 文件 | 作用 |
|---|---|
| `tools/validation/vibration_reference_m1820.py` | 独立参考（文献表 / FD / RK4 / 恒等式），不 import 被审模块 |
| `tools/validation/verify_vibration_kernel_m1820.py` | 判决器：产出账本 + 图 |
| `tools/validation/probe_vibration_wiring_m1820.py` | 接线取证（静态 + 在线，带控制组） |
| `artifacts/m1820/verdicts.json` / `.md` | 判据账本（served / reference / 偏差 / 容差 / 判决 / 基准） |
| `artifacts/m1820/wiring_evidence.json` | 模块 sha256、引用点、`__all__`、`include_router` 清单、在线状态码 |
| `artifacts/m1820/fig_vibration_audit.png` | 4 面板复核图 |
| `tests/test_vibration_kernel_reference_m1820.py` | 25 条回归（参考侧 + 被审核现状 + 接线缺口） |