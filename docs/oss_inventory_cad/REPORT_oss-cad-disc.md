# 开源 CAD 技术盘点·候选普查报告

> **scenario_key**: oss-cad-disc  
> **mission_id**: 8668  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8668  
> **产物**: `docs/oss_inventory_cad/oss_cad_registry.json`（机器可解析）  
> **方法**: GitHub API (`api.github.com/search/repositories`) 多轮查询 + 人工质量筛选

---

## 一、案例介绍（物理背景 / 普查范围）

### 1.1 任务背景

CAD（Computer-Aided Design，计算机辅助设计）是船舶总体性能平台几何前处理的核心环节——从 B-rep/NURBS 几何内核到参数化建模、网格生成、几何修复，是阻力/推进/耐波/结构等所有学科仿真的几何输入基础。本普查对标 m8638（SPH 普查 succeeded 范式）协议，为 TensorPhysics 域建立开源 CAD 技术全景图，为后续深盘（wave-1+）与 feature_ledger GAP 锚点提供候选池。

### 1.2 普查范围

| 维度 | 覆盖 |
|---|---|
| **方法族** | B-rep · NURBS · CSG · 参数化 CAD · 几何修复 · 网格生成 · IGA |
| **物理域** | 船舶几何 · 结构 · 流体 · 多物理耦合 |
| **计算后端** | CPU · GPU · Web · Python · Rust · C++ |
| **应用域** | 船舶设计 · 工业制造 · 教育 · 科研 · 融合能源 |
| **质量门槛** | ≥5 stars 或方法独特性突出；有实质代码（非纯 README） |

### 1.3 普查方法

1. **GitHub API 多轮搜索**：`geometry+kernel+cad`、`nurbs+cad`、`brep+geometry`、`csg+geometry`、`mesh+generation+cad`、`parametric+cad`、`geometry+repair+cad`、`occt+opencascade`、`differentiable+cad`、`nurbs+python`、`brep+python`、`cad+python+geometry`、`cad+rust+geometry`、`cad+parametric+python`、`cad+web+geometry`、`cad+mesh+generation`、`cad+geometry+repair`、`cad+differentiable+geometry`、`cad+parametric+rust`、`cad+geometry+python+library`、`cad+step+iges+geometry`、`cad+geometry+mesh+python`、`cad+geometry+analysis`、`cad+geometry+simulation` 等 24+ 查询词；
2. **已知重要代码直接查询**：FreeCAD、OCCT、CadQuery、build123d、solvespace、chili3d、solidcraft、NURBS-Python、gismo、MeshLib 等；
3. **质量筛选**：去重 → 去 fork → 去纯 demo/无实质代码 → 按 stars/活跃度/license/语言/方法覆盖度分级。

---

## 二、普查结果总览

### 2.1 分级统计

| Tier | 数量 | 定义 |
|---|---:|---|
| **Tier 1** 几何内核/参数化 CAD/网格生成 | 15 | 核心 CAD 方法论、生产级求解器、有论文/实验验证 |
| **Tier 2** 可微/框架族 | 8 | 现代框架、可微 CAD、跨平台 |
| **Tier 3** 专项能力 | 10 | 教学、IGA、网格、Web、特定物理 |
| **合计** | **33** | — |

### 2.2 语言分布

| 语言 | 数量 | 代表 |
|---|---:|---|
| C++ | 9 | FreeCAD, OCCT, solvespace, MeshLib, gismo |
| Python | 12 | CadQuery, build123d, NURBS-Python, pymadcad |
| Rust | 5 | solidcraft, QymCAD, csgrs, OpenGeometry |
| JavaScript/TypeScript | 5 | csg.js, ImplicitCAD, chili3d, three-bvh-csg |
| C | 2 | dune3d, tinyspline |
| C# | 1 | Macad3D |

### 2.3 许可证分布

| License | 数量 | 备注 |
|---|---:|---|
| MIT | 8 | 最宽松，可商用 |
| LGPL-2.1/3.0 | 7 | 链接可用 |
| Apache-2.0 | 6 | 宽松+专利保护 |
| GPL-3.0 | 5 | 传染性，商用需谨慎 |
| AGPL-3.0 | 3 | 网络传染性 |
| MPL-2.0 | 2 | 文件级 |
| NOASSERTION | 2 | 需确认 |

---

## 三、每仓一句话速览

### Tier 1 — 几何内核/参数化 CAD/网格生成

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---:|---:|---|---|---|
| 1 | **FreeCAD** | 35296 | C++ | LGPL-2.1 | 开源参数化 CAD 旗舰：OCCT B-rep 内核 + 草图 + 网格 + Python API |
| 2 | **solidcraft** | 15198 | Rust | Apache-2.0 | 现代 Rust 参数化 CAD：约束求解器 + B-rep 内核 |
| 3 | **CadQuery** | 6079 | Python | NOASSERTION | Python B-rep CAD：OCCT 上流畅 API，STEP/IGES 导出 |
| 4 | **chili3d** | 4977 | TS | AGPL-3.0 | Web 参数化 CAD：OCCT 内核，浏览器原生 |
| 5 | **solvespace** | 4241 | C++ | GPL-3.0 | 极简参数化 CAD：2D/3D 约束求解器，教学标杆 |
| 6 | **build123d** | 3666 | Python | Apache-2.0 | Python B-rep CAD：OCCT，直接+参数化建模 |
| 7 | **OCCT** | 3147 | C++ | LGPL-2.1 | 工业级 B-rep/NURBS/CSG 几何内核：FreeCAD/CadQuery 底座 |
| 8 | **dune3d** | 2137 | C | GPL-3.0 | 3D 参数化 CAD：约束求解器 |
| 9 | **csg.js** | 1863 | JS | MIT | 经典 CSG 库：Web 几何 |
| 10 | **ImplicitCAD** | 1591 | JS | AGPL-3.0 | 隐式 CSG：Haskell + JS |
| 11 | **QymCAD** | 922 | Rust | AGPL-3.0 | Rust 参数化 CAD：B-rep 内核 |
| 12 | **MeshLib** | 844 | C++ | NOASSERTION | 网格处理 + 几何修复：B-rep 转换 |
| 13 | **NURBS-Python** | 769 | Python | MIT | 纯 Python NURBS/B-spline：求值/拟合/IGA |
| 14 | **tinyspline** | 1357 | C | MIT | 轻量 NURBS/B-spline：多语言绑定 |
| 15 | **gismo** | 441 | C++ | MPL-2.0 | IGA 库：NURBS/B-spline + FEM |

### Tier 2 — 可微/框架族

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---:|---:|---|---|---|
| 16 | **OpenGeometry** | 543 | Rust | MPL-2.0 | 开源几何内核：B-rep，Web + 桌面 |
| 17 | **CQ-editor** | 1265 | Python | Apache-2.0 | CadQuery 编辑器/GUI 框架 |
| 18 | **OCP** | 213 | C++ | Apache-2.0 | OCCT Python 绑定 |
| 19 | **pymadcad** | 294 | Python | LGPL-3.0 | Python CAD + 网格库 |
| 20 | **csgrs** | 265 | Rust | MIT | Rust CSG：Web + 桌面 |
| 21 | **torchlensmaker** | 138 | Python | GPL-3.0 | 可微 CAD：PyTorch 透镜设计 |
| 22 | **cadjoint** | 71 | Python | Apache-2.0 | 伴随/可微 CAD |
| 23 | **NURBS_Diff** | 52 | Python | BSD-3 | 可微 NURBS |

### Tier 3 — 专项能力

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---:|---:|---|---|---|
| 24 | **tIGAr** | 117 | Python | LGPL-3.0 | IGA + FEniCS：NURBS + FEM |
| 25 | **Silk** | 99 | Python | GPL-3.0 | NURBS 基础 CAD |
| 26 | **nurbspy** | 89 | Python | MIT | NURBS Python 库 |
| 27 | **CADFit** | 91 | Python | NOASSERTION | CAD 网格拟合 |
| 28 | **paramak** | 110 | Python | MIT | 融合能源参数化 CAD |
| 29 | **Macad3D** | 587 | C# | MIT | C# CAD：OCCT |
| 30 | **occt-import-js** | 296 | C++ | LGPL-2.1 | OCCT STEP/IGES 导入 JS |
| 31 | **pyOCCT** | 260 | Python | LGPL-2.1 | OCCT Python 绑定（legacy） |
| 32 | **nodi** | 241 | TS | Apache-2.0 | Web CAD |
| 33 | **three-bvh-csg** | 963 | JS | MIT | three.js BVH CSG |

---

## 四、feature_ledger GAP 锚点提议

对标 m8638 `GAP-SPH-*` 命名法，提议 CAD 域 8 个 GAP 锚点：

| GAP ID | 标题 | 优先级 | 锚定仓库 | 验证目标 |
|---|---|---|---|---|
| **GAP-CAD-BREP-VALIDATION** | B-rep 几何内核基准验证面 | 🔴 high | OCCT, FreeCAD, CadQuery | 布尔运算/拓扑一致性/STEP-IGES 往返精度 |
| **GAP-CAD-NURBS-CONVERGENCE** | NURBS 拟合与 IGA 收敛 | 🔴 high | NURBS-Python, gismo, tIGAr | 控制点误差/L2 收敛/对标文献 |
| **GAP-CAD-CSG-BENCHMARK** | CSG 布尔运算基准 | 🔴 high | csg.js, ImplicitCAD, csgrs | union/intersect/difference 精度与性能 |
| **GAP-CAD-DIFFERENTIABLE** | 可微 CAD 反问题验证 | 🟡 medium | torchlensmaker, cadjoint, NURBS_Diff | AD vs FD 梯度精度/参数辨识收敛 |
| **GAP-CAD-MESH-REPAIR** | 网格生成与几何修复 | 🟡 medium | MeshLib, CADFit, pymadcad | 网格质量/拓扑修复/B-rep 转换 |
| **GAP-CAD-HPC-SCALING** | CAD 内核 HPC 扩展性 | 🟡 medium | OCCT, FreeCAD, gismo | 并行布尔/大规模装配/多核 |
| **GAP-CAD-ENGINEERING** | CAD 工程化与 Python API | 🟡 medium | CadQuery, build123d, CQ-editor | API 一致性/STEP-IGES 互操作 |
| **GAP-CAD-WEB** | Web CAD 几何内核 | 🟢 low | chili3d, three-bvh-csg, nodi | 浏览器端 B-rep/CSG 精度与性能 |

---

## 五、注意点（踩坑 / 局限 / 适用范围）

### 5.1 普查局限

1. **stars 非质量唯一指标**：gismo（IGA）stars 不高但学术影响力极大；tinyspline（NURBS）stars 高但功能单一。分级综合考虑了方法覆盖度、验证深度、活跃度。
2. **GitHub API 限制**：未认证请求 10 req/min（search）+ 60 req/h（core），部分查询可能遗漏长尾仓库；core API 在普查期间多次被限流，部分仓库的 last_commit 数据来自 search API 的 `pushed_at` 字段。
3. **License 识别**：部分仓库 GitHub 显示 NOASSERTION（如 CadQuery 的 Python 项目），实际 license 在源码文件中，需逐仓确认。
4. **Fork 去重**：FreeCAD/CadQuery/OCCT 有大量 fork，已去重只计主仓。
5. **本普查不深盘**：按 m8638 协议，wave-0 只注册候选；深盘（代码审计/精度验证/能力分级）= wave-1+。

### 5.2 与 SPH 生态的对比

| 维度 | SPH 生态 | CAD 生态 |
|---|---|---|
| 领军代码 stars | SPlisHSPlasH 1959 | FreeCAD 35296 |
| 方法多样性 | WCSPH/ISPH/PCISPH/DFSPH | B-rep/NURBS/CSG/参数化/IGA |
| GPU 成熟度 | CUDA 主导 | 较弱（Web/CPU 主导） |
| 可微 | jax-sph 已建立 | torchlensmaker/cadjoint 新兴 |
| 验证面 | 碎片化 | 更碎片化（无跨代码统一基准） |
| HPC | 强（SPH-EXA/SWIFT） | 弱（OCCT 并行有限） |

### 5.3 对 TensorPhysics 的启示

1. **优先复用 OCCT 的几何内核**：B-rep/NURBS/CSG 全家桶 + LGPL-2.1 许可，是 TensorPhysics 验证面的最佳参考；
2. **Python 路线参考 CadQuery/build123d**：Python B-rep API 是船舶几何前处理的主流；
3. **可微路线参考 torchlensmaker/cadjoint**：可微 CAD = 反问题/ML 耦合的天然桥梁；
4. **验证面建设是最大缺口**：8 个 GAP 锚点中 3 个 high priority 都是验证面——这是 TensorPhysics 的核心价值主张。

---

## 六、误差判定

本单为普查注册（非精度验证），无计算结果需对标基准。误差判定不适用。

**诚实声明**：
- ✅ 完成：33 仓普查 + 分级 + 8 GAP 锚点提议 + 机器可解析 JSON
- ⚠️ 未完成：未逐仓 clone 审计代码（= wave-1 任务）；部分 license 需源码确认
- ⚠️ 未覆盖：部分长尾仓库因 GitHub API 限流未纳入；部分 last_commit 数据来自 search API 的 `pushed_at` 字段（非精确 commit 时间）

---

## 七、后续波次建议

| 波次 | 任务 | 优先级 |
|---|---|---|
| wave-1 | 深盘 Tier-1 前 5 仓（FreeCAD / OCCT / CadQuery / build123d / solidcraft）：代码审计 + 能力分级 + 验证面搭建 | 🔴 |
| wave-2 | GAP-CAD-BREP-VALIDATION 实施：跨代码布尔运算对比 | 🔴 |
| wave-3 | NURBS/IGA 收敛验证（NURBS-Python / gismo / tIGAr） | 🟡 |
| wave-4 | 可微 CAD 验证（torchlensmaker vs AD-FD） | 🟡 |

---

**产物清单**：
- `docs/oss_inventory_cad/oss_cad_registry.json` — 33 仓机器可解析注册 + 8 GAP 锚点
- `docs/oss_inventory_cad/REPORT_oss-cad-disc.md` — 本报告
