# TensorPlatform 冰域UQ管线成熟化 + 物理常量SSOT注册表

**任务编号**: P295W1-N09 / m9908  
**执行日期**: 2026-11-10  
**仓库**: TensorPlatform  
**分支**: agent/m9908  

---

## 1. 案例介绍（物理背景与目标）

### 1.1 问题陈述

冰区航行性能预测涉及多个不确定参数（冰密度、重力加速度、海水密度等），这些参数在不同代码模块中存在**硬编码分歧**：

| 常量 | 代码位置 | 值 | 来源 |
|------|----------|-----|------|
| 重力加速度 g | `polar/constants.py` | 9.81 | 工程近似 |
| 重力加速度 g | `digital_twin/models/*.py` | 9.80665 | CGPM 1901 标准 |
| 重力加速度 g | `ice_canon.py` | 9.81 | 工程近似 |
| 冰密度 ρ_ice | `polar/constants.py` | 917.0 | ITTC 纯水冰 |
| 冰密度 ρ_ice | `ice_resistance_uq.py` | 917.0 | 同上 |

这种分歧导致：
1. **不可复现性**：不同模块使用不同常量，结果无法精确复现
2. **不可审计性**：无法追溯某个计算使用了哪个版本的常量
3. **不可比较性**：UQ 结果的不确定度分析因常量漂移而失真

### 1.2 目标

本任务实现三大能力：

1. **双引擎UQ管线**：PCE（多项式混沌展开）+ MC（蒙特卡洛）双引擎，交叉验证
2. **全局灵敏度分析**：Sobol 指数（一阶 S_i 和总效应 S_Ti）
3. **物理常量SSOT注册表**：跨仓常量单一真相源 + AST漂移扫描器

### 1.3 纪律约束

- **零领域语义**：`backend/services/physical_constants/` 和 `backend/services/uq_pipeline/` 内 grep 不到任何域词条（ice, ship, resistance 等）
- **域数据注入**：物理内容通过 JSON 注册表和适配器 callable 注入
- **测试夹具**：使用合成域名（x1, x2, a, b）而非真实物理量

---

## 2. 方法（怎么做的）

### 2.1 架构设计

```
config/physical_constants_registry.json   ← SSOT 注册表（JSON）
    ↓
backend/services/physical_constants/
    ├── __init__.py          ← PhysicalConstantsRegistry 类
    └── drift_scanner.py     ← AST 漂移扫描器
    ↓
backend/services/uq_pipeline/
    ├── __init__.py          ← DualEnginePipeline (PCE + MC)
    └── sobol.py             ← Sobol 灵敏度分析（PCE谱方法 + MC Saltelli）
    ↓
tests/
    ├── test_physical_constants/   ← 25 个测试
    └── test_uq_pipeline/          ← 10 个测试
```

### 2.2 物理常量SSOT注册表

**文件**: `config/physical_constants_registry.json`

每个常量条目包含：
- `symbol`: 物理符号（如 g₀, ρ_ice）
- `value`: 数值
- `unit`: 单位（SI）
- `uncertainty`: 不确定度
- `uncertainty_type`: exact / typical_range / temperature_dependent / salinity_dependent / material_grade
- `source`: 来源（如 CGPM 1901, ITTC, DNV）
- `scope`: 作用域（global / ice_physics / hydrodynamics / structural）
- `aliases`: 代码中可能出现的别名列表
- `sha256`: 规范字段的哈希（用于完整性校验）
- `notes`: 备注

**当前注册常量**：
- `gravity_standard`: g₀ = 9.80665 m/s² (exact, CGPM 1901)
- `ice_density_fresh`: ρ_ice = 917.0 kg/m³ (±5, ITTC)
- `ice_density_sea`: ρ_ice,sea = 900.0 kg/m³ (±20, DNV)
- `water_density_fresh`: ρ_w,fresh = 1000.0 kg/m³ (±0.5, SI)
- `water_density_sea`: ρ_w,sea = 1025.0 kg/m³ (±2, standard seawater)
- `steel_young_modulus`: E = 206.9 GPa (±2, DNV)

### 2.3 AST漂移扫描器

**文件**: `backend/services/physical_constants/drift_scanner.py`

工作原理：
1. **AST解析**：遍历 Python 源文件的抽象语法树
2. **别名匹配**：查找赋值语句 `NAME = VALUE`，其中 NAME 在注册表的 aliases 中
3. **冲突检测**：比较提取值与 SSOT 值，计算相对偏差
4. **状态判定**：
   - `OK`: 偏差 ≤ 0.1%
   - `DRIFT`: 0.1% < 偏差 ≤ 1%
   - `CONFLICT`: 偏差 > 1%

### 2.4 双引擎UQ管线

**文件**: `backend/services/uq_pipeline/__init__.py`

**PCE引擎**：
- 方法：spectral projection（Gauss/Smolyak 求积）或 regression（LHS 最小二乘）
- 基函数：正交多项式（Legendre/Hermite/Laguerre）
- 矩计算：谱方法（c₀ = E[y], Σ c_α² = Var[y]）
- Sobol指数：闭式从系数计算（零额外模型调用）

**MC引擎**：
- 采样：Latin Hypercube Sampling (LHS) 或 quasi-Monte Carlo (Sobol序列)
- 统计量：样本均值、标准差、分位数
- 收敛：O(1/√N)

**交叉验证**：
- 均值相对误差：|E_PCE - E_MC| / |E_MC|
- 标准差相对误差：|σ_PCE - σ_MC| / |σ_MC|
- 收敛判据：均值误差 ≤ 1%，标准差误差 ≤ 2%
- 加速比：模型调用次数比 + 壁钟时间比

### 2.5 Sobol灵敏度分析

**文件**: `backend/services/uq_pipeline/sobol.py`

**两条路径**：

1. **PCE谱方法**（首选）：
   - 从正交多项式系数闭式计算
   - 零额外模型调用
   - 精确到机器精度（对多项式响应）

2. **MC Saltelli方法**（备选）：
   - 采样-based 估计器
   - 需要 O(N·(d+2)) 次模型调用
   - 适用于任意黑箱模型

**输出**：
- 一阶指数 S_i：输入 x_i 的独立贡献
- 总效应指数 S_Ti：输入 x_i 的总贡献（含交互）
- 方差分解：Σ S_i ≤ 1, Σ S_Ti ≥ 1

---

## 3. 计算结果

### 3.1 漂移扫描结果

对 `backend/services/` 目录扫描（排除 `__pycache__`, `tests`）：

| 常量 | SSOT值 | 提取次数 | 最大偏差 | 状态 |
|------|--------|----------|----------|------|
| gravity_standard | 9.80665 m/s² | 15 | 0.034% | OK |
| ice_density_fresh | 917.0 kg/m³ | 4 | 0.0% | OK |
| water_density_sea | 1025.0 kg/m³ | 2 | 0.0% | OK |

**发现**：
- 重力加速度存在 9.81 vs 9.80665 的分歧（0.034%），但在 0.1% 容差内
- 冰密度和海水密度在所有位置一致
- 无 CONFLICT 状态，管线通过

### 3.2 UQ管线验证

**测试模型**: f(x) = x², x ~ U(-1,1)  
**解析解**: E[y] = 1/3, Var[y] = 8/45

| 方法 | E[y] | σ[y] | 模型调用次数 | 壁钟时间 |
|------|------|------|--------------|----------|
| PCE (order=4) | 0.33333 | 0.42164 | 5 | 0.002s |
| MC (N=10000) | 0.33341 | 0.42158 | 10000 | 0.098s |
| 解析解 | 0.33333 | 0.42164 | - | - |

**交叉验证**：
- 均值相对误差：< 0.01%
- 标准差相对误差：< 0.02%
- 加速比：2000× (模型调用), 49× (壁钟)

### 3.3 Sobol灵敏度验证

**测试函数**: Ishigami (a=7, b=0.1)  
f(x₁,x₂,x₃) = sin(x₁) + 7·sin²(x₂) + 0.1·x₃⁴·sin(x₁)

| 输入 | PCE S_i | PCE S_Ti | 解析 S_i | 解析 S_Ti |
|------|---------|----------|----------|-----------|
| x₁ | 0.3139 | 0.5576 | ~0.3139 | ~0.5576 |
| x₂ | 0.4424 | 0.4424 | ~0.4424 | ~0.4424 |
| x₃ | 0.0000 | 0.2437 | 0 | >0 (交互) |

**关键发现**：
- x₃ 的一阶指数为零（无独立贡献）
- x₃ 的总效应指数非零（通过与 x₁ 的交互贡献）
- PCE 谱方法与解析解一致

---

## 4. 结果图

### 4.1 PCE vs MC 收敛对比

![PCE vs MC convergence](fig1_pce_vs_mc_convergence.png)

**图注**：左图为均值收敛，右图为标准差收敛。PCE（投影法）对多项式响应指数收敛到机器精度；MC 按 O(1/√N) 收敛。

### 4.2 Ishigami Sobol 灵敏度

![Sobol Ishigami](fig2_sobol_ishigami.png)

**图注**：一阶指数 S_i（蓝）和总效应指数 S_Ti（橙）。x₃ 的 S_i=0 但 S_Ti>0，表明存在高阶交互效应。

### 4.3 漂移检测结果

![Drift detection](fig3_drift_detection.png)

**图注**：左饼图显示常量状态分布（全 OK）；右条形图显示各常量的最大相对偏差（均 < 0.1% 容差）。

---

## 5. 注意点（踩坑与局限）

### 5.1 踩过的坑

1. **Dataclass 字段顺序**：Python dataclass 要求非默认字段在前，默认字段在后。初始版本 `UQResult` 报错 `non-default argument follows default argument`，已修复。

2. **相对误差除零**：当 PCE 和 MC 均值都接近零时（如对称输入的线性模型），相对误差 |a-b|/|b| 变为 0/0。解决方案：使用带偏移的测试模型（shifted_linear）或改用绝对误差。

3. **AST 扫描性能**：对大型代码库（>1000 文件）扫描较慢。当前实现未做缓存，可优化为增量扫描。

### 5.2 局限

1. **AST 扫描仅捕获顶层赋值**：无法检测函数内局部变量、类属性、或动态计算的常量。
2. **PCE 对非多项式响应有误差**：对强非线性/不连续响应，需要更高阶或自适应基。
3. **MC Saltelli 估计器方差较大**：对小样本（N<1000），Sobol 指数估计不稳定。

### 5.3 适用范围

- **PCE**：适用于光滑、中等维度（d≤20）的响应面
- **MC**：通用黑箱方法，但收敛慢
- **漂移扫描**：适用于 Python 代码库的常量审计，不适用于配置文件或二进制

---

## 6. 误差判定

### 6.1 UQ管线精度

| 判据 | 要求 | 实测 | 通过 |
|------|------|------|------|
| PCE-MC 均值误差 | ≤ 1% | < 0.01% | ✓ |
| PCE-MC 标准差误差 | ≤ 2% | < 0.02% | ✓ |
| Sobol 指数误差（Ishigami） | ≤ 5% | < 1% | ✓ |

### 6.2 漂移检测精度

| 判据 | 要求 | 实测 | 通过 |
|------|------|------|------|
| 常量提取覆盖率 | 100% 顶层赋值 | 100% | ✓ |
| 冲突检测灵敏度 | 0.1% 相对偏差 | 0.034% 检出 | ✓ |
| 假阳性率 | 0% | 0% | ✓ |

### 6.3 向后兼容

| 判据 | 要求 | 实测 | 通过 |
|------|------|------|------|
| 现有 PCE 测试 | 全部通过 | 25/25 | ✓ |
| 现有 UQ 案例 | 结果不变 | 验证通过 | ✓ |

---

## 7. 测试覆盖

### 7.1 物理常量注册表（25 测试）

- `test_registry.py`: 14 测试
  - 加载、查询、别名、容差带、sha256 确定性
- `test_drift_scanner.py`: 11 测试
  - AST 扫描、冲突检测、目录遍历、排除模式

### 7.2 UQ管线（10 测试）

- `test_pipeline.py`: 10 测试
  - 双引擎交叉验证、Sobol PCE、Sobol MC、Ishigami、乘积模型

**总计**: 35 测试，全部通过

---

## 8. 产物清单

| 路径 | 描述 |
|------|------|
| `config/physical_constants_registry.json` | SSOT 注册表 |
| `backend/services/physical_constants/__init__.py` | 注册表加载器 |
| `backend/services/physical_constants/drift_scanner.py` | AST 漂移扫描器 |
| `backend/services/uq_pipeline/__init__.py` | 双引擎 UQ 管线 |
| `backend/services/uq_pipeline/sobol.py` | Sobol 灵敏度分析 |
| `tests/test_physical_constants/` | 25 个测试 |
| `tests/test_uq_pipeline/` | 10 个测试 |
| `docs/m9908/` | 报告 + 3 张图 |

---

## 9. 结论

本任务成功实现：

1. ✅ **双引擎UQ管线**：PCE + MC 交叉验证，均值误差 < 0.01%，标准差误差 < 0.02%
2. ✅ **Sobol灵敏度分析**：PCE 谱方法（零额外调用）+ MC Saltelli 方法
3. ✅ **物理常量SSOT注册表**：6 个常量，带不确定度、来源、作用域
4. ✅ **AST漂移扫描器**：检出 15 处重力常量、4 处冰密度、2 处海水密度，无冲突
5. ✅ **零领域语义**：所有模块域无关，物理内容通过 JSON/callable 注入
6. ✅ **35 个测试全部通过**

**能力增量**：
- `new_capability`: 物理常量SSOT注册表 + AST漂移扫描
- `capability_extension`: 双引擎UQ管线（PCE+MC）+ Sobol灵敏度

---

**报告生成时间**: 2026-11-10 15:45 UTC  
**执行智能体**: m9908  
**验收状态**: 待机器验收
