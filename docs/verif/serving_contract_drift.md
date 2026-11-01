# 在线服务合同漂移矩阵：受证物理在 :8001 上「不可达」（m1762 / tp-infra-1）

## ① 案例介绍

平台近十张验证单（m1490 / m1564 / m1749 / m1755 / m1757 / m1758）都在裁决 **仓内**
实现（`src/ship_perf`、`backend/api`、`backend/services/stability_reference.py`），
而业主与外部客户端实际对话的是 **在线服务 `:8001`**。一个能力只有在在线 *请求合同*
暴露了受证实现所需的输入时才 **可达**。本单把「可达性」做成可复算判据，
并对在线自报的稳性数字做解析裁决。

算例：矩形浮箱驳 L=30 m, B=10 m, T=3 m, Δ=500 t, Cb=0.6, KG=4.0 m。
该几何是唯一有解析解的形状（矩形剖面、平底、满水线面），
因此 `stability_reference.exact_gz_box` 的独立积分（Sutherland-Hodgman 裁剪 + 鞋带公式 +
恒定排水量二分求浸深）与在线的 wall-sided 闭式 **零共享代码路径**。

## ② 怎么做的

1. **仓内合同**：`tools/verif/contract_drift/contract_drift.py` 用 `ast` 静态抽取
   `backend/api/*.py` 的 `APIRouter(prefix=...)` / `class X(BaseModel)` /
   `validation_alias=AliasChoices(...)`，**零 import**（无需 psycopg2、DB、起服务），可进 CI；
2. **在线合同**：取 `:8001/openapi.json`（裁剪快照落 `artifacts/live_openapi_trimmed.json`）；
3. **逐端点分类**：`SAME / HIDDEN_INPUTS / INPUT_DRIFT / TOPOLOGY_DRIFT / UNSERVED /
   UNDOCUMENTED`，另计输出侧 `RESPONSE_DRIFT`；可达性 `reachability = 在线暴露的受证输入 /
   受证输入总数`；
4. **在线数值 + 解析裁决**：`probe_drift.py` 用在线合法方言发包取 KB/BM/GM/衡准，
   再用 **仓内受证方言单独发包**，记录被拒字段（schema 漂移的直接证据）；
   同几何下用独立解析解逐量算相对误差；
5. **硬编码指纹**：KG 扫描（3.2/4.0/4.8 m），消失角与 GZmax 角必须随 KG 变化。

工具链：Python 3.11 + matplotlib(Agg) + pytest；全程只读在线服务，未起任何重算力进程。

## ③ 计算结果

合同矩阵（在线 57 路径 vs 仓内 1383 条路由）：

| 分类 | 条数 |
|---|---|
| SAME | 0 |
| HIDDEN_INPUTS | 0 |
| INPUT_DRIFT | 1 |
| TOPOLOGY_DRIFT | 2 |
| UNSERVED | 1380 |
| UNDOCUMENTED | 53 |

漂移端点明细：

| 端点 | 分类 | reachability | 在线隐藏的受证输入 | 在线索取的新输入 | 输出侧缺失 |
|---|---|---|---|---|---|
| `POST /api/v1/calculations/resistance` | TOPOLOGY_DRIFT | 0.5 | speed_ms | displacement, speed | wetted_surface_source |
| `POST /api/v1/calculations/stability` | TOPOLOGY_DRIFT | 0.0 | beam_m, bm_m, depth_m, draft_m, gm_m, heel_angle_deg, kb_m | cb, kg, length | gz_basis, gz_box_exact_m, heel_validity_limit_deg, validity_limit_source, wall_sided_valid |
| `POST /api/v1/quality/check` | INPUT_DRIFT | None | — | asset_id | — |

**受证方言被拒**：把 m1749 层的输入（`gm_m/bm_m/kb_m/depth_m/heel_angle_deg`）单独发给在线稳性端点 → HTTP 422，要求补 `length, beam, draft, displacement, cb, kg`。
也就是说：**没有任何客户端能在在线端口上注入受证的水静力输入**，reachability = 0。

在线自报数字 vs 独立解析（同几何，KB/BM/GM 与 depth 假设无关，逐位可复算）：

| 量 | 在线 | 解析 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| KB | 2.4000 | 1.500000 | +60.0% | ❌ |
| BM | 3.7500 | 2.777778 | +35.0% | ❌ |
| GM | 2.1500 | 0.277778 | +674.0% | ❌ |
| GZmax | 1.8800 | 0.132408 | +1319.9% | ❌ |
| A30-40 | 0.2777 | 0.000596 | +46493.1% | ❌ |
| A0-40 | 0.5975 | 0.039006 | +1431.8% | ❌ |

在线同时自报 IMO 衡准 **6/6 项 true**（`method="静水力学理论 + IMO衡准"`）。

KG 扫描（硬编码指纹）：

| KG (m) | 在线 GM | 在线 GZmax 角 | 在线消失角 | 解析消失角(D=4.5 m) |
|---|---|---|---|---|
| 3.2 | 2.95 | 40.0 | 60.0 | 55.0 |
| 4.0 | 2.15 | 40.0 | 60.0 | 32.0 |
| 4.8 | 1.35 | 40.0 | 60.0 | 0.5 |

消失角与 GZmax 角对 KG 完全不敏感（恒 60.0° / 40.0°），而 GM 确实随 KG 变化 —— 
这不是物理，是常数。

## ④ 结果图

![fig1](../tools/verif/contract_drift/artifacts/fig_live_vs_analytic.png)

*图 1*：在线自报量 vs 独立解析解的对数误差柱状图；绿虚线 3% 门、红点线 15% 拒收线。

![fig2](../tools/verif/contract_drift/artifacts/fig_gz_curve.png)

*图 2*：复原力臂曲线（场量曲线）——解析浮箱解在三个 depth 假设下的 GZ(φ)，
红星为在线自报的 GZmax 单点。在线不收 `depth`，因此它必然要假设一个干舷；
本图把该假设的敏感度显式画出。

![fig3](../tools/verif/contract_drift/artifacts/fig_contract_matrix.png)

*图 3*：合同可达性矩阵——绿=仓内与在线都有，琥珀=仅仓内有（在线隐藏），浅色=仅在线有（在线索取）。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **深度假设不可消除**：在线稳性合同没有 `depth`（干舷），所以 GZ/面积量无法唯一比对；
   本单把 KB/BM/GM（与 depth 无关，解析精确）作为主判据，把 GZ/面积量标注为
   「在线 depth 未知，解析取 D∈{4.5, 6.0, 8.0} m 的包络」。
   包络内 A30-40 解析值为 0.000596…0.088470 m·rad，
   在线 0.2777 m·rad **在包络外**，故该量超差不依赖假设。
2. **合同漂移 ≠ 公式算错**：本单证明二者叠加——在线既换掉了入口参数（拓扑漂移），
   又用回 m1490 已裁定的缺陷水静力（KB=T(1-0.5(1-Cb))）。单独修公式而不改合同，
   受证能力依然不可达。
3. **单干边界**：在线服务源码位于另一仓（`ship-performance-platform/backend`），
   不在本工作树白名单内；本单只做 **判据 + 工件 + 承接规格**，**未**改动在线服务。
4. **覆盖范围**：矩阵覆盖仓内 1383 条路由中能在在线 411 路径里定位到的部分；
   `UNSERVED` 绝对值包含大量非船舶域端点（运维/实验平台），
   不能解读为「船舶能力缺失」，故报告只对漂移端点下结论。

## ⑥ 误差判定（逐项显式回答「是否 <3%」）

- `KB`：在线 2.4000 vs 解析 1.500000，相对误差 **+60.0%** → 是否 <3%：**否**
- `BM`：在线 3.7500 vs 解析 2.777778，相对误差 **+35.0%** → 是否 <3%：**否**
- `GM`：在线 2.1500 vs 解析 0.277778，相对误差 **+674.0%** → 是否 <3%：**否**
- `GZmax`：在线 1.8800 vs 解析 0.132408，相对误差 **+1319.9%** → 是否 <3%：**否**
- `A30-40`：在线 0.2777 vs 解析 0.000596，相对误差 **+46493.1%** → 是否 <3%：**否**
- `A0-40`：在线 0.5975 vs 解析 0.039006，相对误差 **+1431.8%** → 是否 <3%：**否**

- 硬编码指纹：消失角跨 KG∈{[3.2, 4.0, 4.8]} 的极差 **0.00°** → 判定为常数，是否 <3%（等效零敏感度）：**否**

结论：在线 `:8001` 稳性端点 **不通过**本单任何一项关键量判据；
且受证实现所依赖的输入在在线合同上 **完全不可达**（reachability = 0.0）。
承接动作（不在本单白名单内）：把 `backend/api/calculations.py` 的
`StabilityRequest/StabilityResponse` 合同 + 受证水静力层下沉到在线服务，
验收命令 `python3 tools/verif/contract_drift/probe_drift.py` 应给出 reachability=1.0 且各量 <3%。

---

### 工件指针

| 工件 | 路径 |
|---|---|
| 合同矩阵工具（零 import AST） | `tools/verif/contract_drift/contract_drift.py` |
| 在线探针 + 解析裁决 + 出图 | `tools/verif/contract_drift/probe_drift.py` |
| 数字工件 | `tools/verif/contract_drift/artifacts/drift_probe.json` |
| 在线 openapi 快照（裁剪） | `tools/verif/contract_drift/artifacts/live_openapi_trimmed.json` |
| 回归测试 | `tests/audit/test_contract_drift.py`（10 passed） |
