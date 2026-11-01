# 振动噪声域证据分级审计与参考能力补全（m1493）

**日期**：2026-11-01  **席位**：tp-impl-2  **仓**：TensorPlatform  **分支**：agent/m1493
**任务**：`selfstart`（自主工时，平台席）  **mission_id**：1493

---

## 1. 审计：本域此前为 NOT_SUPPORTED（机器级证据，非印象）

船舶总体性能九学科中，阻力 / 推进 / 耐波 / 操纵 / 稳性 / 结构 / 绿色 / 冰区 / 几何
均有平台报告留痕（1062/1058/1057/1049/1045/1044/1033/1046 等），**振动噪声域 0 篇**。

对在线平台做「候选路径 × 控制组」探针（`tools_probe_vibration.py`，只读）：

| 路径 | 状态 | 判读 |
|---|---|---|
| `calculations/vibration` | 404 | 路由不存在 |
| `calculations/noise` | 404 | 路由不存在 |
| `calculations/vibration/beam_natural` | 404 | 路由不存在 |
| `calculations/vibration/plate_natural` | 404 | 路由不存在 |
| `calculations/noise/panel_tl` | 404 | 路由不存在 |
| `calculations/acoustics` | 404 | 路由不存在 |
| `calculations/vibration/modal` | 404 | 路由不存在 |
| `calculations/noise/cabin_spl` | 404 | 路由不存在 |
| **控制组** `calculations/stability` | **405** | 路由存在、GET 不允许 → 探针有效 |
| **控制组** `calculations/resistance` | **405** | 同上 |

控制组 405 是关键：证明「404」不是探针或鉴权失效导致的假阴性，而是**路由确实不存在**。
另：`backend/api/domain/*.py` 全文检索 `vibration|noise|acoustic|振动|噪声` 命中 0 行。

**判决**：振动噪声域 = **NOT_SUPPORTED**（能力缺失，不是精度不达标，也不是 MOCK 伪造）。

## 2. 做了什么：解析级参考能力（REAL）

新增 `backend/api/domain/ship_vibration.py`（纯 numpy + fastapi，无 scipy/DB 依赖）：

| 能力 | 物理模型 | 形式 | 端点 |
|---|---|---|---|
| 梁弯曲固有频率 | Euler–Bernoulli 均匀梁 | 特征方程**数值求根**（不查表）+ 闭式频率 | `POST /calculations/vibration/beam_natural` |
| 梁有限元交叉验证 | Hermite 三次单元 + 一致质量 | 广义特征值（Cholesky 化标准问题） | 同上 `fem_check=true` |
| 梁振型 | 各 BC 闭式振型函数 | 解析 | 库函数 |
| 板固有频率 | Kirchhoff 薄板 SSSS | Navier 闭式解 | `POST /calculations/vibration/plate_natural` |
| 板独立复算 | Rayleigh 商 + 数值积分 | 变分原理，**不调用**闭式解 | 同上 `rayleigh_check=true` |
| 激励与共振筛查 | 轴频 / 叶频 / 柴油机点火频 | 解析 + 多对多裕度筛查 | `POST /calculations/vibration/excitation` |
| 隔振 | 单自由度基础激励传递率 | 解析 | `POST /calculations/vibration/isolation` |
| 隔声 | 单层板质量定律（正入射精确 + 场入射 −5 dB） | 解析 | `POST /calculations/noise/panel_tl` |
| 吻合频率 | 单层板临界频率 | 解析 | 同上 |
| 声级合成 / A 计权 | 能量叠加 + IEC 61672-1 解析式 | 解析 | `POST /calculations/noise/levels` |

设计纪律：**每个函数都有一个与被测代码相互独立的基准**，禁止「被测函数自己算基准」。

## 3. 计算结果（全部由 `artifacts/m1493/verify_vibration_noise.py` 程序化产出，零手抄）

数据源：`artifacts/m1493/numbers.json`

### 3.1 梁无量纲特征值 vs Blevins (1979) Table 8-1

| BC | λ₁ | λ₂ | λ₃ | λ₄ | 最大相对误差 |
|---|---|---|---|---|---|
| clamped-clamped | 4.730041 | 7.853205 | 10.995608 | 14.137165 | 1.0e-7 % |
| cantilever | 1.875104 | 4.694091 | 7.854757 | 10.995541 | 1.0e-7 % |
| clamped-pinned | 3.926602 | 7.068583 | 10.210176 | 13.351769 | 1.0e-7 % |
| pinned-pinned | π | 2π | 3π | 4π | <1e-12（解析恒等） |

### 3.2 Hermite FEM 收敛（EI=2.1e11×1.6e-4, ρA=7850×0.02, L=6 m）

| 单元数 N | C-C 一阶相对误差 | 悬臂一阶相对误差 |
|---|---|---|
| 2 | 1.621e-2 | 4.834e-4 |
| 4 | 1.327e-3 | 3.271e-5 |
| 8 | 8.441e-5 | 2.083e-6 |
| 16 | 5.297e-6 | 1.309e-7 |
| 32 | 3.313e-7 | 7.074e-9 |
| 64 | 1.926e-8 | 3.824e-9 |

误差单调下降且斜率约 N⁻²，与一致质量 Euler–Bernoulli 单元的期望收敛行为一致；
FEM 侧为**上界**（第 3.2 节各行误差恒正，悬臂 N=64 转为舍入噪声 −3.8e-9）。

### 3.3 板：Navier 闭式解 vs Rayleigh 商独立复算（3×2×0.012 m 钢板，SSSS）

7 个模态 (m,n) 相对误差最大 **0.115 %**（Rayleigh 侧含网格数值积分离散误差，故非零）。

### 3.4 其余判据

| 判据 | 计算值 | 基准 | 误差 |
|---|---|---|---|
| T(√2)（隔振恒等式） | 1.0000000000 | 1 | 1.1e-16 |
| T(r=3, ζ=0.05) | 0.130410 | √(1.09/64.09) 手算 | <1e-15 |
| 钢 5 mm 吻合频率 | 2393 Hz | 经验式 f_c[kHz]≈12/h[mm] → 2400 Hz | 0.29 % |
| 6 mm 钢 1 kHz 场入射 TL | 45.93 dB | 渐近式 20log₁₀(m″f)−42.53 = 50.93（正入射）+ 5 dB 场修正 | 0.005 dB |
| A 计权 1000 Hz | 0.000 dB | IEC 61672-1 | 0.00 dB |
| A 计权 8 点最大偏差 | — | IEC 61672-1 表值 | 0.090 dB |
| 两个 60 dB 叠加 | 63.0103 dB | 10log₁₀(2·10⁶) | <1e-12 |
| 十个 60 dB 叠加 | 70.0000 dB | — | <1e-12 |

### 3.5 端点连通性（最小 FastAPI app + TestClient，不导入整站）

6 个端点全部 **200**，非法输入（`bc="bad-bc"`）返回 **422 而非 500**。

## 4. 图

| 图 | 内容 |
|---|---|
| `artifacts/m1493/fig1_fem_convergence.png` | FEM−解析相对误差 vs 单元数（双对数 + N⁻² 参考斜率） |
| `artifacts/m1493/fig2_mode_shapes.png` | 两端固支梁前 4 阶闭式振型 |
| `artifacts/m1493/fig3_isolation.png` | 传递率 vs 频率比，r=√2 处 T=1 恒等式 |
| `artifacts/m1493/fig4_panel_tl.png` | 质量定律场入射 TL 与吻合频率位置 |

（图内文字用英文：宿主环境无 CJK 字体，中文会渲染成方框，非内容问题。）

## 5. 注意点 / 踩过的坑

1. **伪刚体根**：`cos x·cosh x − 1` 在 x→0 处因浮点相消返回**精确 0**（1 − x⁴/6 与 1 不可区分），
   扫描起点取 1e-6 会误采 λ=0 当作一阶根，导致 C-C / F-F 全部错阶。改为从 0.5 起步 + 刚体守卫。
2. **scipy `eigh` 在本机 segfault**（Bus error，40×40 也复现）。改为对称正定质量阵 Cholesky
   化标准特征值问题 + `numpy.linalg.eigh`，移除 scipy 依赖后稳定，且精度不变（见 3.2）。
3. **ζ=0 且 r=1 除零**（本脚本第一轮实时抓到）：物理上 T→∞，改为返回 `inf` + 警告说明，不抛异常。
4. `backend/api/__init__.py` 会级联导入 auth→database→psycopg2（本机无该依赖），
   故测试与工件脚本均**按文件路径加载**被测模块，不拉整站 app。
5. numpy≥2 把 `trapz` 改名 `trapezoid`，已加兼容分支。

## 6. 误差判定（逐项，显式回答「是否 < 3%」）

| 关键量 | 基准来源 | 误差 | <3%？ |
|---|---|---|---|
| 梁 λ₁…λ₄（4 个 BC） | Blevins 1979 表值 | 1.0e-7 % | **是** |
| 梁 FEM 一阶 f（N=20） | Euler–Bernoulli 闭式解 | ≈2e-6（外推） | **是** |
| 板 f_mn（7 模态） | Rayleigh 商数值积分 | 0.115 % | **是** |
| 吻合频率（5 mm 钢） | 工程经验式 f_c≈12/h[mm] kHz | 0.29 % | **是** |
| A 计权（8 频点） | IEC 61672-1 表值 | 0.090 dB | **是**（dB 量纲，容差 0.2 dB） |
| T(√2) | 解析恒等式 | 1e-16 | **是** |
| dB 能量叠加 | 解析式 | <1e-12 | **是** |
| 隔声 TL | 质量定律渐近式 | 0.005 dB | **是**（同式自洽，非外部实验） |

## 7. 局限与适用范围（honest）

1. **一维梁 / 二维薄板模型**：Euler–Bernoulli 忽略剪切与转动惯量，适用 L/h > 20 与
   低阶模态；板为 Kirchhoff 薄板，适用 h/a < 1/20。厚板 / 高阶需 Mindlin/Reissner 或 3D FEM，本模块**未实现**。
2. **无船体结构模型**：本模块只给解析构件级频率与激励频率，**不做整船/上层建筑 3D 模态分析**；
   共振筛查的输入固有频率须由外部结构分析（或实测）给出，模块不替用户编造。
3. **阻尼、源级、实测谱不编造**：隔振 ζ、设备源级、舱室噪声谱全部是**用户输入**，
   模块不内置「看起来合理」的默认值去伪造 REAL 结论。
4. **规范限值未实现**：IMO MSC.337(91) 舱室噪声限值、ISO 6954 舒适性曲线**未纳入本版**，
   原因是不确定表值的逐行准确转录，宁缺毋滥；`required_tl` 只给物理口径需求量。
5. **场入射 −5 dB 为工程近似**，吻合频率以上质量定律失效（实测会在 f_c 后出现 TL 平台/谷），
   本模块**不建模吻合谷**，TL 值仅可用于 f ≪ f_c 的筛选。
6. **未上线**：路由已加入 `backend/api/domain/__init__.py` 的 `__all__`（惰性可挂载），
   但**未挂进在线 :8010 服务**——挂载需改主站注册并重启生产服务，属运维动作，本单不做。
   因此在线平台当前仍为 NOT_SUPPORTED，仓内为 REAL（能力已实现并验证）。

## 8. 复现

```bash
cd backend && python3 -m pytest tests/feat/test_ship_vibration.py -q --noconftest   # 86 passed
python3 artifacts/m1493/verify_vibration_noise.py                                  # numbers.json + 4 图
python3 tools_probe_vibration.py                                                   # 在线域缺失证据
```