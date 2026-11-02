# 阻力计算全调用链审计（阶段一 / A · 任务 m2086）

**仓库**：TensorPlatform（本 worktree，分支 `agent/m2086`，基线 = 部署树 `fix/runtime-local-patches`，`origin/main` = `d28d8209`）
**性质**：只读审计。**本阶段未修改任何生产代码**（`git status` 仅含 `docs/audits/`、`artifacts/audit1/`）。
**机器可读产物**：
- `artifacts/audit1/audit1_callgraph.json` —— AST 生成的调用图（路由 / import 边 / HTTP hop / 反向可达性 / 角色标注 + 标注失鲜检查）
- `artifacts/audit1/audit1_resistance_crosscheck.json` —— 同输入 × 8 内核实测
- `artifacts/audit1/audit1_findings.json` —— 20 条结论的结构化版（含三态判定）
- `artifacts/audit1/audit1_fig_kernels.png` —— 图：同输入 R_T 对比 + 15% 门禁视图

复现命令（全部零手抄，数字从工件读）：

```bash
python3 artifacts/audit1/audit1_build_callgraph.py         # -> audit1_callgraph.json
python3 artifacts/audit1/audit1_resistance_crosscheck.py   # -> audit1_resistance_crosscheck.json
python3 artifacts/audit1/audit1_make_figure.py             # -> audit1_fig_kernels.png
```

三态标注约定：**【已证实】**＝附 `文件:行号` 或命令输出；**【推测】**＝与证据一致但未直接复现；**【未验证】**＝本环境无法核验（写明原因）。

---

## 0. 结论摘要（TL;DR）

| # | 结论 | 三态 |
|---|---|---|
| 1 | 平台不是「一套阻力公式」，而是 **8 个可调用实现**；同一输入 R_T 极差 **357%**（SUBOFF）／**50.6–72.7%**（KVLCC2）／**75.5–101.7%**（DTMB5415） | 【已证实】 |
| 2 | 其中 6 个（端点 A、ship_resistance B、services/resistance_core C、domain core D2、resistance_model E）**物理逐位相同**（C_T=0.003461889 @SUBOFF），分歧只在 **湿表面积来源** 与 **形式因子是否参与** | 【已证实】 |
| 3 | 端点把 `speed` 字段**静默吞掉**并按默认 1.0 m/s 计算（`extra=ignore`，无 `AliasChoices`）；而 `services/ship_proxy.py` 的文档示例正是这种载荷 | 【已证实】 |
| 4 | 湿表面积静默兜底是最主要误差源：KVLCC2 用经验式得 40958 m²（公布 27194 m²，**+50.6%**），R_T 同步 +50.6%，响应里只有一句 `wetted_surface_source` | 【已证实】 |
| 5 | `form_factor` 只存在于端点，基准/batch/优化链**无法表达**；端点取 1.15 时 SUBOFF R_T +12.4%，基准链无法复现该数 | 【已证实】 |
| 6 | 验收已红：12 个基准算例中 **8 个超 15% 门禁**（KVLCC2×4、DTMB5415×4；SUBOFF 通过） | 【已证实】 |
| 7 | **PR #549 实际是「已合并进 origin/main」而非 closed**（`d28d8209` 就是 main 头）；它确实统一了端点→`resistance_breakdown`，新增测试 7/7 真实执行并通过 | 【已证实】 |
| 8 | 「contract tests 红」**不是 PR #549 引入**：该门禁在 PR 之前的树上就以 `AttributeError` 收集失败，且引用了 6+ 个早已不存在的符号 | 【已证实】 |
| 9 | CI 红的确切用例名：**无 GitHub Actions 日志可访问**（见 §6.4），只能给出本地可复现的等价失败集 | 【未验证】 |
| 10 | 部署树与 main **双向分叉**：部署树没有 PR #549；而 main 上 `backend/api/holtrop1984.py` 被 merge 解冲**误删**（`2f3caffb`），该文件只在部署树存在 | 【已证实】 |
| 11 | 几何验证内核 `api/domain/resistance_core.solve_resistance` **生产零调用者**（仅测试）——平台自己定义的 Cb/L/B 门禁在端点路径上根本不跑 | 【已证实】 |
| 12 | `api/holtrop1984.py` 头注释声称「Used by api.calculations」是**假的**（后端无人 import，仅通过 `git show` 动态加载） | 【已证实】 |

---

## 1. 真实调用图

### 1.1 端点入口

```
POST /api/v1/calculations/resistance
  └─ backend/api/calculations.py:321  @router.post("/resistance", response_model=ResistanceResponse)
       → :323 async def calc_resistance(req: ResistanceRequest)
            ├─ 几何门禁（内联）            :340-344  0.3<Cb<1.0、L/B/T>0 → HTTPException 400
            ├─ 湿表面积选择               :346-353  wetted_surface_m2 给定→用它；否则 _empirical_wetted_surface(L,B,Cb) (:309)
            ├─ Re = V·L/ν                 :355
            ├─ Cf = 0.075/(log10Re−2)²    :358      （ITTC-1957，内联字面量）
            ├─ Rf = ½ρS·Cf·V²             :361
            ├─ k = form_factor−1；Rform=k·Rf  :364-365   ← 只有这里有 (1+k) 项
            ├─ Fn = V/√(gL)               :369
            ├─ Cr = 0.002(1+2Cb)Fn^2.5    :373      （Fn>0.05）
            ├─ Rt = Rf+Rform+Rr           :376
            └─ Ct = ct_from_rt(...)       :393      ← 全端点唯一的外部物理依赖：services.ship_resistance.ct_from_rt (:40)
```

【已证实】端点主体是**内联公式**（`backend/api/calculations.py:309-393`），唯一 import 的物理函数是 `ct_from_rt`（`calculations.py:40`）。这与模块内其它注释「uses a simplified Holtrop-Mennen formulation」不一致——它不含任何兴波/球鼻/方尾/相关补贴项。

### 1.2 同名 API 家族（容易混淆，但不是同一个端点）

| 路由 | 文件:行 | 说明 |
|---|---|---|
| `POST /calculations/resistance` | `backend/api/calculations.py:321` | 本审计主角（静水裸船体） |
| `POST .../resistance`（冰区） | `backend/api/domain/ice_uq.py:90` | `calc_ice_resistance`，另一套冰阻力 |
| `POST .../resistance/level-ice` | `backend/api/domain/polar.py:252` | 层冰阻力（含 `polar/resistance.py` 第三个 Holtrop 形状因子实现） |
| `POST .../resistance/level-ice/grid` | `backend/api/domain/polar.py:284` | 同上（网格化） |
| `POST .../resistance/channel` | `backend/api/domain/polar.py:300` | 渠道阻力 |

（来源：`artifacts/audit1/audit1_callgraph.json → routes`，AST 扫描全仓 `router.post/get` 装饰器。）

### 1.3 各消费链实际调用哪套内核

| 消费链 | 入口 | 实际内核 | 三态 |
|---|---|---|---|
| 基准验证（进程内） | `api/domain/ship_benchmark.py:247 benchmark_validate` → `:210 _call_resistance_api` → `:220 resistance_breakdown(...)` | **B** `services/ship_resistance.resistance_breakdown` | 【已证实】`ship_benchmark.py:57` import |
| 基准验证（远程兜底） | `ship_benchmark.py:149 _remote_resistance` → `POST {base}/api/v1/calculations/resistance`（`:179`） | **A** 端点 | 【已证实】 |
| 基准审计 | `services/benchmark_audit.py:33` → `resistance_core(...)` | **C** `services/resistance_core.resistance_core` | 【已证实】 |
| 数据集完整性 | `services/ship_benchmark_integrity.py:32 import resistance_model as rm` | **E** `services/resistance_model.compute_resistance` | 【已证实】 |
| 批量扫描 batch | `api/domain/ship_batch.py:81 client.post(RESISTANCE_ENDPOINT)`（`:28`） | **A** 端点（HTTP） | 【已证实】 |
| 代理调用 | `services/ship_proxy.py:222 _request("POST","calculations/resistance")` | **A** 端点（HTTP） | 【已证实】 |
| 优化器（canonical） | `optimizer/interfaces/resistance_eval.py:124 canonical_resistance` → `:135 holtrop1984_resistance(...)` | **G** `api/holtrop1984.py` | 【已证实】 |
| 优化器（legacy 路径） | `optimizer/interfaces/resistance_eval.py:176 _legacy_holtrop_resistance` | 第 9 套手写公式（含 `Cr_coeff*1e-3`、`0.002·exp(-2/Fn²)`） | 【已证实】`resistance_eval.py:198-232` |
| 优化器（冰区/静水目标） | `optimizer/ice_class/objectives.py:43/88` | 第 10 套：自带 Holtrop-Mennen 实现 | 【已证实】 |
| 参考语料/复算工具 | `tests/test_reference_corpus.py:26`、`tools/validate/*`、`tools/repro/kernels.py:36` | **F** `services/resistance_holtrop` | 【已证实】 |
| 几何门禁内核 | `api/domain/resistance_core.py:79 solve_resistance` | 独立内核 **D**，**仅测试调用** | 【已证实】`callgraph.kernels[D].orphan=true` |

**绕开公共 API 的内部直调**（任务问项）：

- 【已证实】`api/domain/ship_benchmark.py:220` 直接 import 并调用 `resistance_breakdown`，绕过端点（这是 m1388 有意的收编，避免 HTTP 契约漂移）。
- 【已证实】`services/benchmark_audit.py:33` 走的是**另一条**「single source of truth」`services/resistance_core`——两条基准链内核不同。
- 【已证实】`optimizer/interfaces/canonical_bridge.py:32,56,77` 通过 `importlib.util.spec_from_file_location` / `git show <ref>:backend/api/holtrop1984.py` **按文件路径动态加载**内核，静态 import 图上属「孤儿」，实为运行时加载。
- 【已证实】`optimizer/interfaces/resistance_eval.py:45-46` 硬编码 `RHO_WATER=1025 / NU_WATER=1.19e-6`，**不向被包装的内核传参**，导致桥接结果与内核结果不一致（Ct 0.011506 vs 0.011409，+0.85%）。

### 1.4 反向可达性（谁 import 了每个内核）

| 内核文件 | 被 API 层 import | 全部消费者数 | 孤儿? |
|---|---|---|---|
| `backend/services/ship_resistance.py` | **是**（`calculations.py`、`ship_benchmark.py`） | 11 | 否 |
| `backend/services/resistance_core.py` | 否 | 2 | 否 |
| `backend/services/resistance_holtrop.py` | 否 | 4（tests/tools） | 否 |
| `backend/services/resistance_model.py` | 否 | 1 | 否 |
| `backend/services/holtrop_mennen.py` | 否 | 3（tests/tools） | 否 |
| `backend/api/domain/resistance_core.py` | 否 | **0（仅测试）** | **是** |
| `backend/api/holtrop1984.py` | 否 | **0（仅动态加载）** | **是** |

（来源：`artifacts/audit1/audit1_callgraph.json → kernels[*]`。）

---

## 2. 重复实现清单与职责标注

| # | 文件:行 | 职责 | 物理式 | 形式因子 | 湿表面积 | 三态 |
|---|---|---|---|---|---|---|
| **A** | `backend/api/calculations.py:323` | API 适配 + **内联物理** | `Rt=Rf+kRf+Rr`，`Cr=0.002(1+2Cb)Fn^2.5` | `form_factor` 默认 **1.0** | 传入值优先，否则 `πBL(0.5+0.25Cb)` | 【已证实】 |
| **B** | `backend/services/ship_resistance.py:108` | 通用纯函数内核（自称 single source of truth） | 同 A 但 **无 k 项**（`Rt=Rf+Rr`） | **无该参数** | 仅经验式 | 【已证实】 |
| **C** | `backend/services/resistance_core.py:48` | 通用纯函数内核（第二份「single source of truth」） | 同 B | **无** | 仅经验式 | 【已证实】 |
| **D** | `backend/api/domain/resistance_core.py:79` | 几何校验 + 物理 + 有效性判决 | `Ct=Cf+Cr`，**强制走几何门禁** | 无 | 公布几何表（SUBOFF 5.42 / KVLCC2 27194） | 【已证实】 |
| **E** | `backend/services/resistance_model.py:101` | 物理模型（含层流分支） | 同 B + `Re<5e5` 切 Blasius | 无 | 仅经验式 | 【已证实】 |
| **F** | `backend/services/resistance_holtrop.py:200` | 物理模型：Holtrop-Mennen 1982/84 全分解 | `Rt=(1+k)Rf+Rw+Rb+Rtr+Ra` | 有（`form_factor_1k1`） | Holtrop 回归 | 【已证实】 |
| **G** | `backend/api/holtrop1984.py:287` | 物理模型：Holtrop 1984（优化器正典） | `Rt=(1+k)Rf+Rw+Rb+Rtr+Ra` | `form_factor_1pk`（SUBOFF 1.147） | `wetted_surface`/Denny-Mumford | 【已证实】 |
| **H** | `optimizer/interfaces/resistance_eval.py:176` | 优化器 legacy 手写公式 | `Cr_coeff*1e-3` + `0.002exp(-2/Fn²)` | 手写 | Holtrop 回归 | 【已证实】 |
| **I** | `optimizer/ice_class/objectives.py:88` | 优化器静水阻力目标 | 自带 Holtrop-Mennen | 由 `i_E` 派生 | 自带 | 【已证实】 |
| **J** | `backend/services/holtrop_mennen.py` | 物理模型/回归语料（S、Cp/Cwp、iE、附体） | 提供系数与回归数据 | 有（`form_factor`） | 有 | 【已证实】 |
| **K** | `backend/services/hull_geometry.py:105`、`backend/api/domain/hull_geometry.py:121` | 几何校验内核（**两份同名 `holtrop_wetted_surface`**） | — | — | Holtrop-1982 回归 + Cb/L/B 可信带 | 【已证实】 |
| **L** | `backend/services/benchmark_audit.py:103` / `api/domain/ship_benchmark.py:247` / `services/ship_benchmark_integrity.py` | 验收逻辑（PASS 门限 15%） | 不产物理 | — | — | 【已证实】 |

**观察**：B 与 C 是**逐式重复**（`residual_coefficient`、`axisym_wetted_surface`、`ittc57_cf`、`ct_from_rt` 各一份），唯一差别是 B 多了 `schoenherr_cf` 交叉校验线。E 是 B 的超集（多一个层流分支）。K 存在 `services/` 与 `api/domain/` 两份同名几何内核。

---

## 3. 参数语义比对

### 3.1 单位 / 默认值

| 参数 | A 端点 | B ship_resistance | C services core | D domain core | E resistance_model | F resistance_holtrop | G holtrop1984 |
|---|---|---|---|---|---|---|---|
| 长度 | `length` m（默认 4.356，**L overall**） | `length` m | `length` m | `length`（公布表/覆盖） | `length` m | `length` m | `l` m（**Lpp/Lwl**） |
| 速度 | `speed_ms` m/s，默认 **1.0** | `speed` m/s | `speed_ms` m/s | `speed_ms` m/s | `speed_ms` m/s | `speed_ms` m/s | `speed_ms` m/s |
| 排水量 | **不接收** | 仅 `displacement_from_cb` 派生 | 不接收 | 由几何表 `vol` 派生 | 不接收 | `displacement_t` 可选（缺省 Cb·LBT） | `vol`（`__post_init__` 缺省 Cb·LBT） |
| Cb | 默认 **0.542**，`0<Cb≤1`，处理器再查 `0.3<Cb<1.0` | 查 `0.3<Cb<1.0` | 查 `0.3<Cb<1.0` | 查 `0.3<Cb<1.0` + 几何门禁 | 查 `(0,1]` 与 `[0.3,1.0]` 两段 | 查 `0.3<Cb<1.0` | 不查（假定调用方合法） |
| 湿表面积 | `wetted_surface_m2` 可选；缺省经验式 | **无参数**，只能经验式 | 可选 `wetted_surface` | 公布表/覆盖 | 可选 | Holtrop 回归（`S_published` 仅做核对） | `s_published` 仅核对，实际用回归 |
| ρ | 默认 **998** | **998.0** | **998.0** | **998.0** | **998.0** | **1025.0**（`RHO_SEA`） | **1025.0**（`RHO_SEA`） |
| ν | 默认 **1.004e-6** | **1.004e-6** | **1.004e-6** | **1.004e-6** | **1.004e-6** | **1.19e-6**（`NU_SEA`） | **1.1892e-6** |
| 形式因子 | `form_factor`，默认 **1.0**，`[1.0,1.5]` | 无 | 无 | 无 | 无 | 自带回归 | 自带回归 |
| 转捩处理 | 无（全湍流） | `Re≤0` 抛错；`log10Re≤2` 返回 **0.0** | 同 B（0.0 兜底） | `ittc57_cf` 同 `hull_geometry` | `Re<5e5` → Blasius | 无 | 无 |

（来源：各文件常量行 `ship_resistance.py:33-35`、`resistance_core.py:21-23`、`resistance_holtrop.py:43-49`、`holtrop1984.py:45-53`、`resistance_model.py:26-41`、`calculations.py:74-95`。）

**关键语义冲突【已证实】**：
1. **ρ/ν 默认值跨内核不一致**（998/1.004e-6 vs 1025/1.19e-6，淡水/海水）——同一输入在 F/G 与 A–E 上算的是不同流体；优化器桥接还额外硬编码 1025/1.19e-6（`resistance_eval.py:45-46`）而非透传。
2. **长度定义可能不同**（端点是 L overall；Holtrop 族要求 Lpp/Lwl）——对 KVLCC2 这类 `Lpp≠Loa` 的船会直接改变 Fn 与 Re。
3. **`draft` 在 A–E 全部被接收、校验、然后丢弃**（`test_calc_verification_v01.py:275-282` 记录了该特征；SUBOFF 湿面积式 `wetted_surface_axisym(beam,length,cb)` 不含 draft）。
4. **`form_factor` 语义只在端点存在**：默认 1.0 = 「裸平板摩擦」；而 `backend/methods/manoeuvring/model.py:72` 默认 1.15。两端默认值不同，跨模块搬运时无任何提示。

### 3.2 默认值掩盖缺输入的实例（逐个列出）

| # | 缺失输入 | 实际发生 | 证据 | 三态 |
|---|---|---|---|---|
| S1 | 调用方按 `ship_proxy` 文档发 `"speed": 12` | `speed_ms` 保持 **1.0 m/s**，无报错、无 warning；`extra` 默认忽略 | `calculations.py:74`（无 `AliasChoices`）；`ship_proxy.py:214-222`；实测 `ResistanceRequest(**{'speed':12,...}).speed_ms == 1.0`；门禁测试 `test_ship_benchmark_verification.py:149` 期望 `AliasChoices("speed_ms","speed")` **不存在**（该测试当前 FAILED） | 【已证实】 |
| S2 | 不传 `wetted_surface_m2` | 回落经验式：KVLCC2 40958.4 m² vs 公布 27194 m²（**+50.6%**），DTMB5415 8.55 vs 4.87（**+75%**），SUBOFF 4.418 vs 5.42（**−18.5%**） | 交叉核对 `audit1_resistance_crosscheck.json`；`calculations.py:309,346-353` | 【已证实】 |
| S3 | 不传 `form_factor` | 默认 1.0，即「无粘性形状阻力」；对 KVLCC2 合理量级应为 1.15–1.35（Holtrop 回归给 1.340） | `calculations.py:84-89`；G 内核 `form_factor_1pk`=1.3397 | 【已证实】 |
| S4 | 不传 `ship_type` | 默认 `"SUBOFF"` 并带上 SUBOFF 的 L/B/T/Cb 默认值——传一个真实船型名但漏几何，会静默按 4.356 m 的 SUBOFF 算 | `calculations.py:73-79` | 【已证实】 |
| S5 | 不传 `water_density`/`kinematic_viscosity` | 默认淡水 998/1.004e-6；KVLCC2 这类实船基准应用海水 1025/1.188e-6 → Re 差 15%、Rt 差约 2.7% | `calculations.py:81-82`；数据集 `KVLCC2-*` 记录 1025/1.188e-6 | 【已证实】 |
| S6 | 不传 `form_factor`（F/G 族） | 用回归值而非缺省，且 `lcb/cstern/abt/at` 缺省时静默置 0 并写进 `assumptions`（这一条是**良性**做法，可作范式） | `resistance_holtrop.py:186-215` | 【已证实】 |
| S7 | 优化器不传 ρ/ν | `resistance_eval.py:45-46` 硬编码海水值，导致与内核结果差 0.85%（Ct 0.011506 vs 0.011409） | crosscheck `H_optimizer_bridge` vs `G_holtrop1984` | 【已证实】 |
| S8 | SUBOFF 的 Cb | 端点默认 0.542；但由公布 (L,D,S) 反推的 Cauchy-Schwarz 下界是 **0.6078** → 平台自己的几何门禁判其不合格 | `hull_geometry.py:158-204`；`test_ship_benchmark_verification.py:104-121` | 【已证实】 |

---

## 4. 同输入异果实测（核心证据）

三个探针点全部取自仓库自带数据集 `backend/data/benchmark_reference.json`（零手抄物理量）：`SUBOFF-01`、`KVLCC2-01`、`DTMB5415-01`。同一份 `{L,B,T,Cb,V,ρ,ν}` 依次喂给 11 个内核/配置。

### 4.1 SUBOFF-01（L=4.356 m, B=0.508, T=0.476, Cb=0.542, V=3.0 m/s, ρ=998.2, ν=1.004e-6）

| 内核 | S [m²] | (1+k) | C_T | R_T [N] | vs 参考 C_T=0.0038 | vs 参考 R_T=92.52 N |
|---|---|---|---|---|---|---|
| A 端点（内联） | 4.4179 | 1.0 | 0.003461889 | 68.70 | −8.9% | −25.75% |
| B ship_resistance | 4.4179 | 1.0 | 0.003461889 | 68.70 | −8.9% | −25.75% |
| C services core | 4.4179 | 1.0 | 0.003461889 | 68.70 | −8.9% | −25.75% |
| D domain core（公布 S） | **5.4200** | 1.0 | 0.003461889 | **84.28** | −8.9% | **−8.90%** |
| D2 domain core（同经验 S） | 4.4179 | 1.0 | 0.003461889 | 68.70 | −8.9% | −25.75% |
| E resistance_model | 4.4179 | 1.0 | 0.003461889 | 68.70 | −8.9% | −25.75% |
| F resistance_holtrop | 4.3766 | 回归 | **0.0159681** | **313.92** | **+320.2%** | **+239.3%** |
| G holtrop1984 | 4.3743 | 1.1471 | **0.0114086** | **224.17** | **+200.2%** | **+142.3%** |
| H 优化器桥接 | 4.3743 | — | 0.0115057 | 232.14 | +202.8% | +150.9% |
| J 端点 + (1+k)=1.15 | 4.4179 | 1.15 | 0.0038920 | 77.24 | +2.4% | −16.5% |

**极差 356.9%**（min B / max F）。

### 4.2 KVLCC2-01（L=320 m, B=58, T=20.8, Cb=0.8098, V=7.202 m/s, ρ=1025, ν=1.188e-6；公布 S=27194 m²，参考 C_T=0.0028）

| 内核 | S [m²] | C_T | R_T [N] | vs 参考 C_T |
|---|---|---|---|---|
| A/B/C/E | 40958.4 | 0.001443151 | 1571286 | **−48.46%** |
| D（公布 S） | 27194.0 | 0.001443151 | 1043242 | **−48.46%** |
| D2（经验 S） | 40958.4 | 0.001443151 | 1571286 | −48.46% |
| F | 26690.8 | 0.002088867 | 1482083 | −25.40% |
| G | 26678.4 | 0.002141215 | 1518518 | −23.53% |
| H | 26678.4 | 0.002141590 | 1518787 | −23.50% |
| J（端点 1+k=1.15） | 40958.4 | 0.001654970 | 1801910 | −40.89% |

**极差 72.7%**。注意：**所有内核都超 15% 门禁**——这不是实现分歧问题，是模型对肥大型船（Cb=0.81）不适用的问题（`services/ship_resistance.py:1-30` 自己的适用范围声明就写了 Cb<0.6）。

### 4.3 DTMB5415-01（模型尺度；参考 C_T=0.0045）

| 内核 | S [m²] | C_T | R_T [N] | vs 参考 C_T |
|---|---|---|---|---|
| A/B/C/E | 8.5518 | 0.003850550 | 3.431 | **−14.43%**（贴边） |
| D（公布 S=4.874） | 4.8740 | 0.003850550 | 1.955 | −14.43% |
| F | 4.4995 | 0.006781985 | 3.179 | **+50.71%** |
| G | 4.4976 | 0.005219099 | 2.446 | **+15.98%** |
| H | 4.4976 | 0.005370760 | 2.584 | +19.35% |
| J | 8.5518 | 0.004427580 | 3.945 | **−1.61%** |

**极差 101.7%**。J（端点 + (1+k)=1.15）在本点恰好最接近参考值——**这是巧合，不是调参依据**（同一组参数在 SUBOFF 上把误差从 −8.9% 推到 +2.4%，在 KVLCC2 上从 −48.5% 推到 −40.9%，没有一致收益）。

### 4.4 物理解读（哪些差异是「应当的」）

- 【已证实】A≡B≡C≡D2≡E 的 C_T 逐位相同（≤1e-9），说明 **SUBOFF 经验族内部已经统一**，端点在 HEAD 上并没有和基准链给出不同 C_T。
- 【已证实】D 的差异**唯一来源是湿表面积来源**（把 S 强制成经验值后 D2 与 B 完全一致）→ 几何校验内核的物理是同一份。
- 【已证实】F/G/H 的差异来自**模型族**（Holtrop 全分解，含兴波项）：Fn=0.459 已远超 Holtrop 标定域（Fn≤0.4），对 SUBOFF 这种回转体本就 NOT_SUPPORTED。但它们在仓库里以 REAL 状态被工具/优化器/参考语料直接调用，且 F 与 G 之间还互相差 40%。
- 【推测】F/G 在 SUBOFF 上的巨大高估主要来自兴波项 `Rw` 与 (1+k) 因子对细长体的外推，未逐项归因（未做分量级灵敏度实验）。

---

## 5. PR #549 复核

### 5.1 事实校正：它被**合并**了，不是 closed

- 【已证实】`origin/main` 的头就是 `d28d8209234a0852d775ab14e3b5f52bd6c3a194`「Merge pull request #549 from cxs503/fix/unify-resistance-call-chain-2026-11-02」。
- 【已证实】PR 头提交 `6554616450f0b21e22b3d1a5b9507e5043381294` 存在，但本 worktree（部署树）**不含它**：`git merge-base --is-ancestor 65546164 HEAD` → false。
- 任务书说「65546164 是 PR 的全部 diff」并不准确：`65546164` 只是最后一个提交（`refactor: remove duplicate resistance model constants`，`backend/api/calculations.py ±17`）。PR 的完整 diff 是 `git diff 5bd044dc d28d8209`：

```
backend/api/calculations.py                        | 122 +++++-------
backend/services/ship_resistance.py                | 141 +++++++------
backend/tests/feat/test_resistance_kernel_unification.py |  92 +++++++++
docs/verification/resistance_call_chain_unification_2026-11-02.md | 76 ++++
tools/verif/test_ship_calcs_verification.py        |   2 +-
5 files changed, 299 insertions(+), 134 deletions(-)
```

（与任务书给的「calculations −122 / ship_resistance +141 / 新测试」量级一致；新测试实际路径在 `backend/tests/feat/` 而非 `backend/tests/`。）

### 5.2 是否真统一入口？——是，且做得比较干净

【已证实】PR 后 `calc_resistance` 的函数体变成：

```python
result = resistance_breakdown(length=..., beam=..., draft=..., cb=..., speed=...,
                              rho=..., nu=..., wetted_surface_m2=req.wetted_surface_m2,
                              form_factor=req.form_factor)
```

即**端点不再持有物理**，`_empirical_wetted_surface` 退化为 `wetted_surface_axisym` 的别名。内联的 `Cf/Cr/Rf/Rr` 字面量全部删除（`git diff 5bd044dc d28d8209 -- backend/api/calculations.py`）。

### 5.3 兼容性判断

| 兼容面 | 结论 | 证据 | 三态 |
|---|---|---|---|
| 函数签名 | **向后兼容**：`wetted_surface_m2`、`form_factor` 都是追加在末尾的关键字参数，默认 `None`/`1.0` | `ship_resistance.py:108-119`（PR 版） | 【已证实】 |
| 返回字典 | **向后兼容**：保留 `rf_n/rr_n/rt_n/ct/froude/reynolds`，并追加 `total_resistance_n/frictional_resistance_n/...`、`model_id`、`validity_status`、`validity_notes`、`surface_source` | 同上 | 【已证实】 |
| 物理默认值 | **不变**：`form_factor=1.0` 时 `Ct = cf+cr`，与 PR 前逐位相同 → 端点输出不变 | `test_resistance_kernel_unification.py::test_form_factor_and_force_decomposition_are_consistent` | 【已证实】 |
| 湿表面积 | **不变**：仍优先用调用方值 | 同上 | 【已证实】 |
| 响应语义 | **破坏性小改**：`wetted_surface_source` 从 `"input_validated"` 改为 `"caller_supplied_unverified"`（**诚实化**，但引用旧字符串的消费方会挂） | `git diff` 对应 hunk；旧字符串仍在 `tools/verif/test_ship_calcs_verification.py:83` 断言（PR 顺手改了该文件） | 【已证实】 |
| 错误语义 | **收紧**：`ittc57_cf` 新增 `log10(Re) ≤ 2` → `ValueError("Reynolds number too small for ITTC-1957")`；端点 `except ValueError → HTTP 400`。→ 低 Re（Re≤100）调用从「返回离谱 Cf」变成 400 | `d28d8209:backend/services/ship_resistance.py` ittc57_cf | 【已证实】 |
| 缺省值的**可观测性** | **改善**：新增 `validity_status="unvalidated_empirical_estimate"` 与 `validity_notes`（Cb≥0.6、Fn>0.4 显式告警） | 同上 | 【已证实】 |
| 未解决项 | `form_factor` 仍只从端点传入；基准链调用不带该参数 → **基准与端点在 (1+k)≠1 时仍不一致**；ρ/ν 默认值分歧、`speed` 别名问题、几何门禁未接入，全部未在 PR #549 中处理 | `ship_benchmark.py:220-232`（PR 版仍无 form_factor） | 【已证实】 |

### 5.4 新测试是否真执行？

【已证实】在 `git archive d28d8209` 的干净副本里，对**同一批 7 个阻力测试文件**跑两次：

```
$ python -m pytest tests/feat/test_resistance_kernel_unification.py -v --no-header -rN
  -> 7 passed
$ python -m pytest <上述 7 个文件 + 新增统一性测试> -v --no-header -rN
  d28d8209 : 21 failed, 123 passed
  HEAD(PR前): 21 failed, 116 passed      # 同样命令、同样文件集
```

123 − 116 = 7 = 新增测试数，失败集合逐字相同 → 新测试**确实执行、确实通过、且没有掩盖任何既有失败**。

> 工具注意：本环境的 `pytest -q` 在部分调用下会丢失末尾计数行（终端报告被 conftest 的自定义表格输出覆盖），
> 复现时请用 `-v --no-header -rN` 再解析 `N failed, M passed`。本文所有计数均取自该形式。

其中两个测试用 `ast` 静态断言端点/基准链把物理委托给共享内核（`test_public_endpoint_delegates_physics_to_shared_kernel`、`test_benchmark_path_uses_the_same_shared_kernel`），这是**结构不变量**而非物理正确性——它们无法发现公式错，只能防止回退到内联。（这一点该写进 PR 的自述里，PR 文档已部分承认。）

### 5.5 CI 红的具体原因

【已证实】`backend/tests/test_resistance_benchmark_contract.py`（"contract tests" 家族的阻力门禁）在 **PR 之前的树** 上就在收集阶段崩：

```
tests/test_resistance_benchmark_contract.py:49: in <module>
    holtrop_wetted_surface = calc.holtrop_wetted_surface
E   AttributeError: module 'tp_calculations' has no attribute 'holtrop_wetted_surface'
```

按符号逐个核验（`hasattr` 扫描）：

| 门禁要求的符号 | 实际存在 |
|---|---|
| `calc.holtrop_wetted_surface` | ✗ |
| `calc.body_of_revolution_wetted_surface` | ✗ |
| `calc.ittc57_friction_coefficient` | ✗ |
| `sb.FAIL / sb.PASS / sb.WITHHELD` | ✗ |
| `sb.build_resistance_payload` | ✗ |
| `sb.extract_ct` | ✗ |
| `sb.geometry_from_case` | ✗ |
| `sb.benchmark_validate` | ✓ |
| `sb.BenchmarkValidateRequest` | ✓ |

即该门禁引用的 API 面在 m1530/m1388 的重构中已被删除，门禁自 m1531 落库后**从未更新**，属**长期红灯**。PR #549 与它无因果关系。

【未验证】GitHub Actions 上「contract tests」与「backend suite」两个 job 的失败用例名：本环境无外网、worktree 内无 run 日志/工件（`ls .github/workflows/` 只有 `ci.yml`、`resident-community.yml`，无 logs 目录）。只能给出等价替代证据：本地对同一批文件复现出**完全相同**的失败集（PR 前 21 failed / PR 后 21 failed），以及 `ci.yml:35`（contracts job 跑根 `tests/`）与 `ci.yml:102`（backend job 跑 `backend/tests/`）的 job 定义。**建议阶段二在 CI 里保留 pytest 报告工件，否则「红在哪」永远只能猜。**

### 5.6 几何验证内核（`api/domain/resistance_core.py`）剩余调用者

【已证实】全仓只有 `backend/tests/feat/test_ship_benchmark_verification.py:118,131,132,162` 调用 `solve_resistance`。**生产路径零调用者**：端点、基准、batch、优化器都不经过它。它持有的能力（Cb/L/B/S 的可信带、Cauchy-Schwarz 界、`validity_verdict`）目前**只对测试生效**。

---

## 6. 测试独立性

| 测试文件 | 判据来源 | 独立性 | 三态 |
|---|---|---|---|
| `backend/tests/test_calculations.py:24` | 文件内**复制一份端点公式**再断言端点与副本一致 | **自证（同义反复）** | 【已证实】 |
| `backend/tests/feat/test_resistance_verification.py:38` | 又复制第三份，且自带 `FORM_FACTORS={SUBOFF:1.08, DTMB:1.18, KVLCC2:1.35}` 与端点**不一致**；同文件 `:99` 又断言 `KVLCC2 1+k==1.0`，自相矛盾 | **自证 + 内部矛盾** | 【已证实】 |
| `backend/tests/test_resistance_benchmark_validation.py` | 数据集 `experimental_ct`（外部锚点）+ 15% 门禁 | **独立**（当前 8/12 FAIL） | 【已证实】 |
| `backend/tests/feat/test_benchmark_chain_m1388.py:75` | ITTC-57 vs **Schoenherr 1932** 两族互相校验 | **独立** | 【已证实】 |
| `tests/test_calc_verification_v01.py:85` | 自己实现 Schoenherr 隐式解 | **独立** | 【已证实】 |
| `backend/tests/test_inline_resistance_audit.py` | 与**解析几何**（圆柱 `piDL`、长椭球 `2πb²+2πab·asin(e)/e`）对湿面积 | **独立** | 【已证实】 |
| `tools/verif/verify_ship_calcs.py:64-93` | 同上解析式（工具层） | **独立** | 【已证实】 |
| `tools/validate/holtrop_ref_independent.py:4` | 文件头明示「与 `services/resistance_holtrop.py` 刻意不复用任何代码、不共享常量」 | **独立** | 【已证实】 |
| `backend/tests/feat/test_resistance_kernel_unification.py`（PR #549） | AST 结构断言 + 力分解恒等式 | **结构不变量**（非物理正确性） | 【已证实】 |
| `tests/test_calc_verification_v01.py:275-301` | 把**缺陷当作特征**断言（draft 不影响 Rt、Cr 无峰、Cb=0.1 抛错） | 独立但**反向**：实现变好它们就红 | 【已证实】 |

**结论**：能发现物理错误的测试是少数（解析几何、Schoenherr 互校、独立 Holtrop 复算、数据集锚点）；主力"回归"测试是**自证**性质，实现错了它们照样绿。

---

## 7. 部署差异（部署树 vs `origin/main`）

【已证实】`git rev-list --count origin/main..HEAD` = **21**，`git diff origin/main HEAD --stat` = 347 files / +16096 / −117677（大量删除是 main 侧后续新增文档部署树没有，方向相反）。

与阻力链**直接相关**的 4 个文件差异：

| 文件 | 差异 | 方向 | 影响 |
|---|---|---|---|
| `backend/api/calculations.py` | 122 行 | main 有 PR #549（端点委托 B）；部署树仍是**内联公式** | 部署树 = 未统一状态；同一端点在两棵树上「物理所在位置」不同 |
| `backend/services/ship_resistance.py` | 141 行 | main 有 `form_factor`/`wetted_surface_m2`/`model_id`/`validity_*` | 部署树调 `resistance_breakdown(..., form_factor=...)` 会 `TypeError` |
| `backend/services/benchmark_audit.py` | 640 行 | 两侧各自重写（部署树 m1530 版） | 同一审计函数在两棵树给出不同判据/字段 |
| `backend/api/holtrop1984.py` | +467（部署树独有） | 该文件由 `474cb5ae`(m1610) 加入、被 main 侧 merge 解冲 `2f3caffb`(m1589) **删除** | **优化器正典内核在 main 上不存在**；`canonical_bridge.py:33 GIT_REFS=("agent/m1553","main")` 在 main 上取不到 → 优化器在 main 环境下退化为 legacy 手写公式 |

【已证实】21 个领先提交本身**都与阻力无关**（auth/auto-login/frontend/dispatch 门禁/runtime 快照）。阻力差异来自：① main 侧后续合入了 PR #549；② 部署树的 merge base 早于 m1530/m1553 之后的若干重构；③ main 侧的 `2f3caffb` merge 解冲删除了 `holtrop1984.py`。

【推测】`2f3caffb` 的删除是「解冲时取一侧全量」的副作用，而非有意下线——因为同一 PR/提交仍在部署树保留该文件，且优化器侧仍把它当正典。

---

## 8. 未验证 / 局限

| 项 | 原因 |
|---|---|
| GitHub Actions 失败用例名 | 无外网、无 run 工件；只能给本地等价复现（§5.5） |
| 冰区阻力链（`ice_uq.py`、`polar/resistance.py`、`ice_class/objectives.py`）的正确性 | 本单范围外，只做了调用图定位，未做数值对拍 |
| F/G 在 SUBOFF 上 +239%/+142% 的分量级归因（Rw / (1+k) / Ra 各占多少） | 未做分量灵敏度实验（阶段二候选） |
| 真实船型（Lpp≠Loa）长度定义错位造成的 Fn/Re 偏差量 | 需要船型 Lpp 数据；数据集未记录 Lpp |
| `services/hull_geometry.py` 与 `api/domain/hull_geometry.py` 两份几何内核是否数值一致 | 本单只标注存在性，未逐函数对拍 |

---

## 9. 阶段二建议（**本阶段不改生产代码**，仅排序建议）

1. **P0 — 让缺输入不再静默**：`ResistanceRequest` 加 `AliasChoices("speed_ms","speed")` + `extra="forbid"`（或显式 `model_validator` 拒绝 `speed`），并把 `ship_proxy` 文档示例改对。这是最低成本、最高收益的一条。
2. **P0 — 打通几何门禁**：端点改为 `solve_resistance`-路线（或先调用 `validate_hull_geometry`），让 Cb/L/B/S 的可信带在**生产路径**上生效；`wetted_surface_m2` 缺省时应给出 `validity_status` 而不是只给一个字符串 tag。
3. **P1 — 内核去重**：`services/resistance_core.py` 与 `services/ship_resistance.py` 保留一个（前者删除或改为 re-export）；`services/hull_geometry.py` 与 `api/domain/hull_geometry.py` 同理。
4. **P1 — 参数语义单一化**：把 ρ/ν 从「各内核默认值」改为**必填或显式 fluid 枚举**（fresh_water_20c / sea_water_15c），避免 998/1025 分歧；`form_factor` 语义（1.0 vs 1.15 缺省）写入契约。
5. **P1 — 修复腐烂门禁**：`test_resistance_benchmark_contract.py` 按当前 API 面重写（或删除并迁移到新的不变量测试）；把"contract tests" job 拆成「只跑标准库契约」与「阻力链契约」两个 job，避免整包收集错误掩埋真实信号。
6. **P2 — 部署/主干合流**：把 PR #549 合入部署树；恢复 main 上被 `2f3caffb` 删除的 `backend/api/holtrop1984.py`（或正式下线并在 `canonical_bridge.GIT_REFS` 里去掉 `main`）。
7. **P2 — 交叉验证常态化**：把 `artifacts/audit1/audit1_resistance_crosscheck.py` 提升为 CI 门禁（同输入跨内核 C_T 差 > 1e-6 即红），这样"多套公式并存"不会再靠人工发现。