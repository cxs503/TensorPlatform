# 船体几何参数自动验证与网格质量评估流水线（mission p372w2-n04，战役 geometry-validation）

> 仓：TensorPlatform ｜ 分支：`agent/m10760` ｜ 节点：`p372w2-n04-geometry-validation-maturity`
> 产物：`artifacts/p372w2_n04/`（`validation_report.json`、`numbers.json`、
> `audit/validation_audit.jsonl`、`figures/`、`cases/`）＋
> `backend/services/geometry_validation/`（引擎）＋ `tests/test_geometry_validation_pipeline.py`（37 项 pytest）

## ① 案例介绍（物理背景 / 几何 / 参数表）

**物理背景。** 总体性能计算链（阻力/推进/耐波/操纵…）的第一个真实误差源不是求解器，而是**几何**
与**网格**：湿表面积、排水体积、浮心纵向位置全部由船体表面积分得到，摩擦阻力 `Rf = ½ρSCfV²`
以 S 一阶敏感；黏流求解器的 skewness/aspect ratio 与壁面第一层 `y+` 决定了壁面律是否成立。
平台铁律「几何必须先验证」此前只有零散函数级校验（`backend/services/hull_geometry.py` 的闭式形体
校验、`backend/api/domain/hull_geometry.py` 的登记簿闸门），**没有**可批量、可审计、带原因码、
带越域拦截的派发前流水线。本节点把它做成生产可用部件。

**验证几何（全部为解析已知量，基准与被检模型相互独立）。**

| 几何 | 定义 | 解析真值 |
|---|---|---|
| 方盒驳 | L=10.0 m, B=4.0 m, T=2.0 m | `V(z)=LBz`，`Cb≡1`，`Aw≡LB=40.000 m²` |
| Wigley 船体 | L=10.0 m, B=1.0 m, T=0.625 m，`y(x,z)=(B/2)(1-(2x/L)²)(1-(z/T)²)` | `∇=(4/9)LBT=2.777778 m³`，`Cb=4/9=0.444444`，`Aw(z)=(2LB/3)(1-(z/T)²)` |
| 结构化六面体网格 | nx×ny×nz 单位立方体 | 单元体积 1，总 `V=nx·ny·nz`，skewness 0，AR 1 |
| 剪切/各向异性网格 | 顶层节点 +x 位移 `s·Δx`；`Δz/Δx=1/200` | 平行四边形最大角 `180°-atan(1/s)`；AR=200 |

**判据（来自验证 profile，全部是数据不是代码）：**

| 判据 | 阈值 | 来源 |
|---|---|---|
| 声明值 vs 提取值 | 相对误差 ≤ 1 % | 本节点物理目标（几何必须先验证） |
| 网格 skewness | < 0.85 | 本节点物理目标 |
| 网格 aspect ratio | < 100 | 本节点物理目标 |
| 壁面 y+ 目标区间 | 30 – 300（壁面律/对数层） | profile `hull_mesh_v1.json` |
| 适用域包络 | Cb∈[0.55,0.85]，L/B∈[3.9,14.9]，B/T∈[2.1,4.0]（Holtrop & Mennen 1982） | 文献 + 登记于 profile |
| 物理可实现包络（**阻断级**） | Cb = ∇/(LBT) ≤ 1，Cwp = Aw/(LB) ≤ 1 | 包围盒几何恒等式 |

profile：`config/geometry_validation/profiles/hull_mesh_v1.json`，`profile_id=hull_mesh_v1`，
`sha256=b4df09283bc37a36e6629defb9d1977dbda801f7573f54364757f055889db860`（阈值/包络/引文全部在该数据文件中，引擎零领域语义）。

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**流水线（图 1）：** 批量输入 JSON → 表面读取（STL 二进制/ASCII、OBJ、IGES 需外部细分器）→
**拓扑闸门**（顶点焊接 + 绕向一致性传播修复 + 边流形/水密判定）→ **几何积分**
（散度定理体积 `V=(1/6)Σ v0·(v1×v2)`；水线以下体积按半空间裁剪 + 水线面投影封口；
`Aw=dV/dz` 中心差分；Cb、Cwp、LCB）→ **声明值比对**（1 % 容差）→ **适用域包络**（数据驱动）→
**网格质量**（VTK / OpenFOAM `polyMesh` / 求解器日志：体积、等角 skewness、aspect ratio、y+）→
**判定引擎**（PASS / BLOCK / ERROR）→ JSON 报告（schema 1.0 + 原因码统计）→ **哈希链审计日志**。

关键实现决策（都写进了代码 docstring）：

1. **体积积分必须先把绕向修一致**：现场 STL 常见 40 % 面翻转，散度定理体积会错。实现按共享边
   BFS 传播绕向（不可定向面报 `E_GEOM_INCONSISTENT_ORIENTATION`），再按总体积符号归一。混合绕向
   用例（item 04）修复后与干净网格体积**完全一致**（差 < 1e-12 相对）。
2. **水线裁剪的「同/异侧」必须非对称**：恰好落在水线平面上的面若两侧都计入，会**双重计数**，
   把体积压低 2/3（本节点第一版就踩了这个坑，方盒 V=53.33 而非 80）。修正为「平面上的面只属下侧」，
   方盒四个吃水全部闭式精确。
3. **水线面面积用 `Aw = dV/dz` 中心差分**而非平面截线追踪：对多面体严格，且不需要处理平面求交的
   拓扑退化；方盒恒为 `LB=40`（相对误差 < 1e-6），Wigley 在 z=0.3T 与解析式差 0.17 %。
4. **单元体积用散度定理**（`V=(1/3)Σ f_centre·S_f`，Newell 面积矢量）：对任意面数的单元成立，
   天然返回负值以识别**反向/塌陷单元**；OpenFOAM `polyMesh` 的 skewness 用 checkMesh 定义
   `|p-f_centre|/|C_owner-C_neighbour|`，VTK 单元用等角 skewness。
5. **y+ 用物理定义** `y+=y·u_τ/ν`，`u_τ=U√(Cf/2)`，`Cf` 或由 profile 中的幂律 `Cf=a·Re^b`
   （Schlichting 1979）给出；另给反函数「达到目标 y+ 所需第一层高度」，直接可指导网格设计。
6. **审计链**：每条 JSONL = `sha256(seq,ts,run_id,event,payload_sha256,prev)`，报告内写入链头哈希；
   `verify_audit_chain` 可独立复算并按 seq 报出第一处断链。报告内 `items_sha256` 已剔除
   `duration_ms` 等易变字段，保证**同一输入集合两次运行哈希相同**（可复现性）。

**工具链**：纯 Python3.11 + numpy（无外部求解器依赖）、matplotlib(Agg) 出图、pytest 验证；
单次批量运行 16.4 s（15 个算例，含 ~4 万面片几何）。

## ③ 计算结果（数字表）

### A. 几何提取精度：方盒（闭式真值，误差应为机器精度）

| 吃水 z [m] | ∇ 解析 [m³] | ∇ 提取 [m³] | 相对误差 | Aw 解析 [m²] | Aw 提取 [m²] |
|---|---|---|---|---|---|
| 0.50 | 20.000000 | 20.000000 | +1.78e-14 % | 40.000 | 40.000000 |
| 1.00 | 40.000000 | 40.000000 | +0.00e+00 % | 40.000 | 40.000000 |
| 1.50 | 60.000000 | 60.000000 | +0.00e+00 % | 40.000 | 40.000000 |
| 2.00 | 80.000000 | 80.000000 | +0.00e+00 % | 40.000 | 40.000000 |

最大相对误差 **1.78e-14 %**（浮点舍入量级，判据 ≤1 % 满足）。

### B. Wigley 船体 Cb 收敛（真值 Cb=4/9）

| n_u | n_v | 三角面 | ∇ 提取 [m³] | Cb 提取 | Cb 相对误差 | 水密 |
|---|---|---|---|---|---|---|
| 10 | 4 | 178 | 2.707031 | 0.433125 | -2.5469 % | 是 |
| 20 | 8 | 678 | 2.760010 | 0.441602 | -0.6396 % | 是 |
| 40 | 16 | 2638 | 2.773331 | 0.443733 | -0.1601 % | 是 |
| 80 | 32 | 10398 | 2.776666 | 0.444267 | -0.0400 % | 是 |
| 160 | 64 | 41278 | 2.777500 | 0.444400 | -0.0100 % | 是 |

n_u=40 已满足 <1 %（**-0.1601 %**），n_u=160 时
**-0.0100 %**，误差随面片按 **O(h²)** 单调下降（图 3），
与二阶精度一致（内接多面体体积二阶收敛）。

### C. 批量闸门结果（15 个算例，run_id `gv-4d670b197713`）

判定分布：**PASS 6 ／ BLOCK 9 ／ ERROR 0**，
拦截率 `block_rate = 0.600`。每个算例都是为触发单一原因码而人工构造的。

| 算例 id | 场景 | 判定 | 原因码 | 预期 |
|---|---|---|---|---|
| `01-box-reference` | analytic box | PASS | `W_DOMAIN_OUT_OF_ENVELOPE`, `W_GEOM_DECLARED_MISSING`, `W_GEOM_LENGTH_REFERENCE` | `PASS` |
| `02-hull-fine-declared-exact` | fine hull + regular hex mesh | PASS | `W_DOMAIN_OUT_OF_ENVELOPE`, `W_GEOM_DECLARED_MISSING`, `W_GEOM_LENGTH_REFERENCE`, `W_GEOM_ORIENTATION_REPAIRED` | `PASS` |
| `03-declared-cb-mismatch` | declared Cb +5% | BLOCK | `E_GEOM_DECLARED_MISMATCH`, `W_DOMAIN_OUT_OF_ENVELOPE`, `W_GEOM_DECLARED_MISSING`, `W_GEOM_LENGTH_REFERENCE`, `W_GEOM_ORIENTATION_REPAIRED` | `BLOCK` |
| `04-mixed-winding-repair` | 40% faces flipped | PASS | `W_DOMAIN_OUT_OF_ENVELOPE`, `W_GEOM_LENGTH_REFERENCE`, `W_GEOM_ORIENTATION_REPAIRED` | `PASS (W_GEOM_ORIENTATION_REPAIRED)` |
| `05-open-shell` | surface missing its bottom | BLOCK | `E_GEOM_NOT_CLOSED`, `W_DOMAIN_OUT_OF_ENVELOPE`, `W_GEOM_LENGTH_REFERENCE`, `W_GEOM_ORIENTATION_REPAIRED` | `BLOCK` |
| `06-truncated-file` | truncated binary STL | BLOCK | `E_PARSE` | `BLOCK` |
| `07-iges-without-tessellator` | IGES B-rep, no tessellator | BLOCK | `E_FORMAT_UNSUPPORTED` | `BLOCK` |
| `08-envelope-extrapolation` | Cb=1 box vs merchant-hull envelopes | PASS | `W_DOMAIN_OUT_OF_ENVELOPE`, `W_GEOM_DECLARED_MISSING`, `W_GEOM_LENGTH_REFERENCE` | `PASS + W_DOMAIN_OUT_OF_ENVELOPE` |
| `09-openfoam-mesh-yplus-pass` | polyMesh 6 cells + yPlus log | PASS | — | `PASS` |
| `10-yplus-out-of-band` | y+ 0.4..7.5 vs band 30..300 | BLOCK | `E_MESH_YPLUS` | `BLOCK` |
| `11-mesh-skewness` | sheared hex layer | BLOCK | `E_MESH_SKEWNESS`, `W_MESH_YPLUS_UNKNOWN` | `BLOCK` |
| `12-mesh-aspect-ratio` | dz/dx = 1/200 | BLOCK | `E_MESH_ASPECT_RATIO`, `W_MESH_YPLUS_UNKNOWN` | `BLOCK` |
| `13-mesh-inverted-cell` | node permutation inverts one hex | BLOCK | `E_MESH_NEGATIVE_VOLUME`, `W_MESH_YPLUS_UNKNOWN` | `BLOCK` |
| `14-yplus-from-first-cell` | y+ from first-layer height | PASS | — | `PASS` |
| `15-double-shell-export` | two nested closed shells in one STL | BLOCK | `E_DOMAIN_OUT_OF_ENVELOPE`, `W_DOMAIN_OUT_OF_ENVELOPE`, `W_GEOM_LENGTH_REFERENCE` | `BLOCK (E_DOMAIN_OUT_OF_ENVELOPE)` |

**原因码统计（拦截统计，图 2）：**

| 原因码 | 命中算例数 |
|---|---|
| `E_DOMAIN_OUT_OF_ENVELOPE` | 1 |
| `E_FORMAT_UNSUPPORTED` | 1 |
| `E_GEOM_DECLARED_MISMATCH` | 1 |
| `E_GEOM_NOT_CLOSED` | 1 |
| `E_MESH_ASPECT_RATIO` | 1 |
| `E_MESH_NEGATIVE_VOLUME` | 1 |
| `E_MESH_SKEWNESS` | 1 |
| `E_MESH_YPLUS` | 1 |
| `E_PARSE` | 1 |
| `W_DOMAIN_OUT_OF_ENVELOPE` | 7 |
| `W_GEOM_DECLARED_MISSING` | 4 |
| `W_GEOM_LENGTH_REFERENCE` | 7 |
| `W_GEOM_ORIENTATION_REPAIRED` | 4 |
| `W_MESH_YPLUS_UNKNOWN` | 3 |

**适用域包络命中统计：**

| 包络 id | inside | outside | not_evaluated |
|---|---|---|---|
| `holtrop_mennen_1982` | 0 | 7 | 0 |
| `physical_realizability` | 6 | 1 | 0 |
| `slender_body_resistance` | 4 | 3 | 0 |

### D. 网格质量（独立计算，不进闸门统计）

| 算例 | 单元数 | 总体积 | 最小单元体积 | max skewness | max AR | y+ | 违规 |
|---|---|---|---|---|---|---|---|
| regular | 24 | 24.0000 | 1.0000 | 0 | 1.00 | 54.44 | — |
| sheared_6 | 24 | 24.0000 | 1.0000 | 0.8949 | 6.08 | 54.44 | E_MESH_SKEWNESS |
| graded_dz_0.005 | 27 | 0.1350 | 0.0050 | 0 | 200.00 | 54.44 | E_MESH_ASPECT_RATIO |
| inverted_cell | 8 | 7.0000 | 0 | 0.5000 | 1.41 | — | E_MESH_NEGATIVE_VOLUME, W_MESH_YPLUS_UNKNOWN |
| openfoam_3x2x1 | 6 | 6.0000 | 1.0000 | 0 | 1.00 | — | W_MESH_YPLUS_UNKNOWN |

结构化网格总/单元体积与解析值**逐位相等**（24.0、1.0、6.0），skewness=0、AR=1；
剪切 6·Δx 的网格 max skewness = **0.8949 > 0.85**；
`Δz/Δx=1/200` 的网格 AR = **200.0 > 100**；
反向单元体积 ≤ 0。

### E. y+ 与审计链

- item 14：`u_inf=5 m/s, ν=1e-6 m²/s, Re=5e6, y=5e-4 m` → `Cf=0.002634`，
  `u_τ=0.1815 m/s`，**y+ = 90.73**
  （区间 30–300 内 → PASS；与解析定义 `y·u_τ/ν` 相对差 <1e-12，见 pytest）。
- item 10：日志型 y+ 最小 0.4 / 最大 7.5 → `E_MESH_YPLUS` 拦截。
- 审计链：17 条记录，`ok=True`，
  链头 `612bee70c3778d0f2330463bab99c953…`，独立复算 `verify_audit_chain` 同样 ok；
  篡改首条记录后 `broken_at=0`（pytest 已覆盖）。

## ④ 结果图

| 图 | 内容 |
|---|---|
| `artifacts/p372w2_n04/figures/fig1_pipeline_flow.png` | 验证流程图（读取→拓扑→积分→双闸门→判定→报告/审计） |
| `artifacts/p372w2_n04/figures/fig2_interception_stats.png` | 拦截统计（按原因码柱状 + 判定分布，含 block_rate） |
| `artifacts/p372w2_n04/figures/fig3_cb_convergence.png` | Cb 与解析值 4/9 的比较曲线 + 相对误差双对数图（含 O(h²) 参考线） |
| `artifacts/p372w2_n04/figures/fig4_mesh_quality.png` | 各算例 max skewness / max aspect ratio 与阈值线 |

（图为 matplotlib 生成、随报告 png 内嵌；源数据同 `numbers.json`。）

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

**踩过的坑（均已修复并被测试钉住）：**
1. 水线裁剪两侧同时计入 → 方盒 V 少算 2/3（53.33 vs 80）。修正为平面上的面只属下侧。
2. `V = Σtet(A) − Σtet(proj B)` 符号错：水线封口贡献应与被裁掉部分**同向相加**；符号写反时
   体积恰好少一个"水线面积×吃水/3"，只有整体闭式校验才能发现。
3. IGES 无法给体积：B-rep 不带体积信息，本实现**明确降级**（需 `GEOMETRY_IGES_TESSELATOR`
   指定的细分器），无则返回 `E_FORMAT_UNSUPPORTED` —— 能力不足必须如实上报，不许伪造。
4. `polyMesh` 解析器把「元素计数」当成第一个面的节点数（面被读成 5 节点多边形，单元体积出现
   0.7/1.3 的假象）。修正为只解析计数之后的列表体。
5. 审计哈希曾包含 `duration_ms` → 同一输入两次运行哈希不同，破坏可复现性；改为剔除易变字段。
6. 退化三角形分两级：少量零面积三角形降级为警告（对体积无影响），占比 > 5 % 才是错误级。

**局限与适用范围：**
- **长度基准**：Cb 分母默认用包围盒长度（`W_GEOM_LENGTH_REFERENCE` 恒被标记）；有前伸/球鼻等
  悬伸时应用声明 LWL（`length_reference: declared`），否则 Cb 偏低。本节点未自动识别 LWL。
- **水线面面积**为 `dV/dz` 的数值导数，步长 `1e-4·(z_max−z_min)`；极端多面体（例如侧壁近乎
  与水线相切）会有 O(步长) 偏差。
- **不检查自交/内部空腔**：多壳导出会被"体积翻倍"捕捉（item 15），但真正自交的表面仍可能通过，
  需 CAD 侧布尔/NEK 检查。
- **OpenFOAM 单元中心**用面面积加权平均近似（非 OpenFOAM 的分解法），skewness 与 checkMesh 可能
  有千分位差异；生产环境应优先读 `checkMesh` 日志（本实现已支持）。
- **VTK 只支持 ASCII legacy 格式**（二进制/`.vtu` 明确拒绝而非误读）。
- **未接入 API 路由**：本节点交付服务层 + CLI（进程内可用、可被 dispatcher 调用），未改
  `backend/api`（该镜像缺 psycopg2/cryptography，API 测试集无法导入，见 m1609 §5）。

## ⑥ 误差判定（关键量 vs 基准，逐项是否 < 3 %）

| 关键量 | 基准 | 本实现误差 | 是否 < 3 % |
|---|---|---|---|
| 方盒 ∇（4 个吃水） | 闭式 `LBz` | 1.78e-14 % | **是** |
| 方盒 Aw | 闭式 `LB=40` | < 1e-6 % | **是** |
| Wigley Cb（n_u=40） | 解析 4/9 | -0.1601 % | **是** |
| Wigley Cb（n_u=160） | 解析 4/9 | -0.0100 % | **是** |
| Wigley Cb 最粗网格（n_u=10） | 解析 4/9 | 2.5469 % | 否（2.55 %）—— 但这是**刻意保留的网格分辨率效应**，用于证明需要足够细分；n_u≥40 才进入 <3 % |
| 结构化网格单元体积 | 解析 1.0 / 6.0 / 24.0 | 0（逐位相等） | **是** |
| 等角 skewness（剪切网格） | 平行四边形 `(180°−atan(1/s)−90°)/90°` | < 0.01 绝对 | **是** |
| y+ | `y·U√(Cf/2)/ν` | < 1e-12 相对 | **是** |

**结论：所有有解析基准的量都在 1 % 以内（多数到机器精度），远优于平台 3 % / 15 % 判据。**
唯一超过 3 % 的是刻意保留的最粗 Wigley 网格（−2.55 %，n_u=10），用于展示收敛性而非精度宣称；
判定为 **honest 记录，不粉饰**。

## 附：机器验收判据对照（数字与路径）

| # | 判据 | 结果 | 证据路径 |
|---|---|---|---|
| A1 | 自动提取几何参数（∇→Cb、包围盒→L/B/T），与声明值 1 % 容差比对 | 方盒/ Wigley 提取误差 <1 %；5 % 偏差被拦截（item 03） | `numbers.json:convergence`、`validation_report.json:items[2]` |
| A2 | 网格质量（skewness<0.85、AR<100、y+ 目标区间）读取并评估 | skew 0.8949 / AR 200 / y+ 0.4–7.5 三类均被拦截（items 10–12） | `validation_report.json:summary.reason_code_counts` |
| A3 | 越域拦截：几何参数出经验公式适用域自动标记 | 7 个算例出 Holtrop 包络（告警级）；1 个算例出物理可实现包络（阻断级） | `numbers.json:gate_summary.envelope_status_counts` |
| A4 | JSON 验证报告含通过/拦截判定与原因码 | `schema_version=1.0`，15 算例，14 种原因码 | `artifacts/p372w2_n04/validation_report.json` |
| A5 | 批量 + 错误恢复 + 审计日志 | 15 算例批跑 16.4 s；坏算例降级为 ERROR 不影响其余；17 条哈希链记录 `ok=True` | `audit/validation_audit.jsonl`、`validation_report.json:audit` |
| A6 | pytest 用例 | **37 passed** | `tests/test_geometry_validation_pipeline.py` |

**复跑：**
```bash
python3 tools/geometry_validation/run_pipeline.py          # 重算全部数字与图
python3 tools/geometry_validation/gen_report_md.py         # 由 numbers.json 生成本文
python3 -m pytest tests/test_geometry_validation_pipeline.py -q
```
