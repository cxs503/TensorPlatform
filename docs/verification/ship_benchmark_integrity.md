# 基准参考数据集完整性裁定与阻力精度复核（m1542）

> 分支 `agent/m1542` · 工件：`artifacts/ship_benchmark_integrity.json`、
> `artifacts/ship_benchmark_accuracy.json`、`artifacts/benchmark_*.png`
> 代码：`backend/services/resistance_model.py`、`backend/services/ship_benchmark_integrity.py`、
> 改动 `backend/api/calculations.py`、`backend/api/domain/ship_benchmark.py`
> 测试：`tests/test_ship_benchmark_integrity.py`（17 passed）

## 1 案例介绍（物理背景/几何/参数）

平台精度闸门 `POST /api/v1/calculations/benchmark_validate` 是平台对外声明的
「已合并能力」（能力位 `dev.gate_5415 = REAL`）的判据来源，其参考证据是
`backend/data/benchmark_reference.json`：三型标模共 12 个算例。

| 船型 | 几何 | 参考线 | 算例数 |
|---|---|---|---|
| DARPA SUBOFF（轴对称裸艇体） | L=4.356 m, D=0.508 m, S=5.42 m² | Roddy 1990 / ITTC | 4（V=3–6 m/s） |
| DTMB-5415（驱逐舰标模） | L=5.72 m, B=0.759 m, T=0.246 m | ITTC V&V Workshop | 4 |
| KVLCC2（VLCC 标模） | L=320 m, B=58 m, T=20.8 m, Cb=0.8098 | SIMMAN 2008 | 4（V=7.2–8.2 m/s） |

比对流体统一取海水 ρ=1025.0 kg/m³、
ν=1.1892e-06 m²/s（ITTC 惯用值）。

## 2 怎么做的（方法/流程/关键设置）

**A. 先修契约断点。** 原实现在 `_call_resistance_api` 里向 port 8002 发 HTTP，
payload 用 `speed`、读回 `ct`；而端点 `/api/v1/calculations/resistance` 的契约为
`speed_ms`、且**不返回 `ct`**。Pydantic 忽略多余字段、缺失字段取默认 → 每个算例
都以「速度缺省 1 m/s、Ct 缺省 0.0」参与比较，误差恒为 100%。修复：把阻力模型抽为
纯函数 `services/resistance_model.compute_resistance`，端点与基准验证**进程内共用同一实现**，
响应新增 `ct / wetted_surface_m2 / wetted_surface_source / friction_coefficient / flow_regime`（纯增量字段）。

**B. 几何单一来源 + 先验证几何。** 原 `SHIP_GEOMETRY` 是与数据集重复的硬编码表，且单位错乱
（DTMB-5415 排水量数据集写 `displacement_kg: 466.3`，硬编码表当成 466.3 t，差 1000 倍）。
现由数据集派生（`build_ship_geometry()`），并对每型校核 L/B/T>0、0<Cb<1；
比对时把数据集**公布的湿面积**（SUBOFF S=5.42 m²）显式喂给模型，避免用回转体近似式
（πBL(0.5+0.25Cb)=4.418 m²，偏低 18.5%）把几何偏差记到阻力模型账上。

**C. 参考值本身先机器裁定。** 新模块 `services/ship_benchmark_integrity.py` 用**只依赖数据自身携带量**的
恒等式与物理下界逐条裁定（不引入任何新文献数字，也不改动任何已公布数字）：

| 判据 | 内容 | 容差 | 严重度 |
|---|---|---|---|
| `fn_identity` | Fn = V/√(gL) | 2% | critical |
| `cb_identity` | Cb = ∇/(L·B·T)，∇=Δ/ρ | 2% | warn |
| `ct_rt_identity` | Ct = Rt/(½ρSV²) | 2% | warn |
| `ct_friction_floor` | Ct ≥ 0.9·Cf_ITTC57(Re) | — | critical |
| `rt_friction_floor` | Rt ≥ 0.9·½ρS·Cf·V² | — | critical |
| `provenance` | 出处 ≥20 字符且含年份 | — | critical |

按通道（`ct` 用于系数比较、`rt` 用于阻力比较）分别裁定，仅准入算例进入精度统计，
被剔除者连同阻断判据一并返回（`summary.excluded_by_integrity`），不静默丢样本。

**D. 摩擦系数加流态分支。** Re<5e5 改用 Blasius 解析解 Cf=1.328/√Re；ITTC-57 的标定域是湍流平板，
在 Re=1e5 处它给出 1.98 倍于 Blasius 的值（超出适用范围）。

工具链：Python 3.11 + pydantic + pytest 8；工件与图由 `tools/gen_benchmark_integrity_figures.py`
程序化导出（matplotlib Agg），报告数字直接读工件 JSON。

## 3 计算结果

### 3.1 参考数据集裁定

准入集合：`ct` 通道 8/12 条 → ["SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04", "KVLCC2-01", "KVLCC2-02", "KVLCC2-03", "KVLCC2-04"]；`rt` 通道 4 条（仅 KVLCC2）。

critical 失败（整条/整通道不可用）：
- `DTMB5415-01:fn_identity`
- `DTMB5415-02:fn_identity`
- `DTMB5415-03:fn_identity`
- `DTMB5415-04:fn_identity`
- `SUBOFF-01:rt_friction_floor`
- `SUBOFF-02:rt_friction_floor`
- `SUBOFF-03:rt_friction_floor`
- `SUBOFF-04:rt_friction_floor`

warn（记警告仍可用）：
- `DTMB5415-01:cb_identity`
- `DTMB5415-02:cb_identity`
- `DTMB5415-03:cb_identity`
- `DTMB5415-04:cb_identity`
- `KVLCC2-01:cb_identity`
- `KVLCC2-02:cb_identity`
- `KVLCC2-03:cb_identity`
- `KVLCC2-04:cb_identity`
- `SUBOFF-01:ct_rt_identity`
- `SUBOFF-02:ct_rt_identity`
- `SUBOFF-03:ct_rt_identity`
- `SUBOFF-04:ct_rt_identity`

三条关键发现（均由机器判据给出，非人工目测）：

1. **DTMB-5415 全块尺度混用**：几何为模型尺度（L=5.72 m），航速却是实船尺度（2.57–15.4 m/s）。
   按自身几何算 Fn 与申报 Fn 差 5–14 倍（见下表），该块无法确定 Ct 对应的试验工况 → 整体剔除。
2. **SUBOFF 总阻力列违反物理下界**：Rt=12.5 N @3 m/s 反算 Ct=5.0e-4，低于 ITTC-57 摩擦系数
   Cf=2.87e-3——无推力全湿艇体的总阻力不可能低于摩擦阻力，故 `rt` 通道禁用；
   但 Ct 列（0.0038）本身不违反下界，`ct` 通道仍可用。
3. **KVLCC2 排水量与 Cb 不自洽 4.2%**（Δ=307000 t 对应 Cb=0.776 而非 0.8098），
   记为 warn：模型直接吃 Cb、不吃排水量，故不阻断 Ct 比较，但该字段不可再被其他节点引用。

### 3.2 Fn 自洽性

| 算例 | 申报 Fn | V/√(gL) | 偏差 (%) |
|---|---|---|---|
| SUBOFF-01 | 0.4590 | 0.4589 | 0.0 |
| SUBOFF-02 | 0.6120 | 0.6119 | 0.0 |
| SUBOFF-03 | 0.7650 | 0.7649 | 0.0 |
| SUBOFF-04 | 0.9180 | 0.9179 | 0.0 |
| DTMB5415-01 | 0.0610 | 0.3434 | 82.2 |
| DTMB5415-02 | 0.1220 | 0.6867 | 82.2 |
| DTMB5415-03 | 0.2440 | 1.3735 | 82.2 |
| DTMB5415-04 | 0.3660 | 2.0602 | 82.2 |
| KVLCC2-01 | 0.1290 | 0.1285 | 0.4 |
| KVLCC2-02 | 0.1380 | 0.1377 | 0.2 |
| KVLCC2-03 | 0.1430 | 0.1423 | 0.5 |
| KVLCC2-04 | 0.1470 | 0.1469 | 0.1 |

### 3.3 Ct 精度比对（仅准入算例）

SUBOFF（几何修正后，S=5.42 m² 显式输入）：

| 算例 | V (m/s) | Ct_ref | Ct_calc | 误差 (%) | ≤15% | <3% |
|---|---|---|---|---|---|---|
| SUBOFF-01 | 3.000 | 0.0038 | 0.003546 | 6.68 | PASS | 否 |
| SUBOFF-02 | 4.000 | 0.0040 | 0.004031 | 0.78 | PASS | 是 |
| SUBOFF-03 | 5.000 | 0.0042 | 0.004840 | 15.25 | FAIL | 否 |
| SUBOFF-04 | 6.000 | 0.0044 | 0.005992 | 36.19 | FAIL | 否 |

KVLCC2：

| 算例 | V (m/s) | Ct_ref | Ct_calc | 误差 (%) | ≤15% | <3% |
|---|---|---|---|---|---|---|
| KVLCC2-01 | 7.202 | 0.0028 | 0.001443 | 48.45 | FAIL | 否 |
| KVLCC2-02 | 7.717 | 0.0030 | 0.001438 | 52.08 | FAIL | 否 |
| KVLCC2-03 | 7.974 | 0.0031 | 0.001435 | 53.70 | FAIL | 否 |
| KVLCC2-04 | 8.231 | 0.0032 | 0.001433 | 55.21 | FAIL | 否 |

汇总：SUBOFF 通过率 50.0%（平均误差 14.72%，
最大 36.19%）；KVLCC2 通过率 0.0%
（平均误差 52.36%，最大 55.21%）。
DTMB-5415 评估 0 条（全部被完整性闸门剔除）。

## 4 结果图

![Fn 自洽性](benchmark_froude_identity.png)

**图 1** 参考数据集弗劳德数自洽性：蓝柱为申报 Fn，橙柱为按其自身 length_m 由 V/√(gL) 算出的 Fn。
5415 块两者相差 5–14 倍（对数纵轴），是「模型尺度几何 + 实船尺度航速」的直接证据；
SUBOFF 与 KVLCC2 两族重合于同一水平，说明其 Fn 列可信。

![Ct 比对](benchmark_ct_comparison.png)

**图 2** 左：公布 Ct 与平台模型 Ct 的对比曲线（SUBOFF 蓝、KVLCC2 绿）；右：逐算例相对误差与
15% 验收门（红虚线）。SUBOFF 低 Fr 两点进入 15% 门内（0.78% / 6.68%），Fr>0.7 两点超门
（15.25% / 36.19%），与「残差阻力经验拟合仅覆盖低-中 Fr」的适用域声明一致。

## 5 注意点（坑与局限）

1. **静默契约不匹配最危险**：旧实现 `speed`/`ct` 键名错配不会抛错，只会让全表误差恒为 100%，
   而能力位仍显示 REAL。凡跨服务/跨模块取数，必须有一条恒等式测试（本单：Ct=Rt/(½ρSV²)）兜底。
2. **不要为“修好闸门”去改参考值**：5415 块的尺度混用无法在本 worktree 内复原
   （需 ITTC 5415 workshop 原始表格），故剔除并显式报告，不猜数、不填数。
3. **湿面积是 SUBOFF 误差主源**：回转体近似式偏低 18.5%，几何未验证前把误差归给阻力模型会误判算法。
   接口现允许显式传入实测 S，并在响应里标明 `wetted_surface_source ∈ {estimate, input}`。
4. **流态分支在 Re=5e5 处不连续**（层流→湍流跳变约 2.9 倍），这是转捩的物理现实而非数值 bug；
   对 Re<5e5 的模型试验算例，必须显式声明所用分支。
5. **残差阻力 Cr 仍为经验拟合**（Cr=0.002(1+2Cb)Fn^2.5），既非 Holtrop-Mennen 全量法也无形状因子 1+k；
   本单只如实标注与验证，不做算法升级（升级需文献系数表，属另单）。
6. 端点响应新增字段为纯增量（旧字段与取整方式不变），不破坏既有调用方。

## 6 误差判定

关键量 = Ct 相对误差，判据 = |Ct_calc−Ct_ref|/Ct_ref ≤ 15%（平台验收门）。

- SUBOFF 四点：SUBOFF-01=6.68%, SUBOFF-02=0.78%, SUBOFF-03=15.25%, SUBOFF-04=36.19%
  → 2/4 通过 15% 门。
- KVLCC2 四点：KVLCC2-01=48.45%, KVLCC2-02=52.08%, KVLCC2-03=53.7%, KVLCC2-04=55.21%
  → 0/4 通过 15% 门。
- **是否 <3%？** 明确回答：**否（仅 SUBOFF-02 达标）**。
  目前模型是「ITTC-57 摩擦 + 两点经验残差拟合」，仅在 SUBOFF-02 一点达到 <3% 级；
  其余算例误差 3%–36%，不满足 <3% 精度要求，属**未达标、已如实记录**（honest-FAIL，不调参粉饰）。
- 地基判据（几何/参考值自洽）：Fn 自洽 SUBOFF、KVLCC2 通过（<2%），5415 不通过；
  Cb 自洽 KVLCC2 偏差 4.2% 超 2% 容差 → 记 warn。

## 7 局限与后续

- 未做：残差阻力模型升级（需 ITTC/Holtrop 系数表数字化）、5415 参考块重数字化（需原始 workshop 表格）。
- 后续线索：把 `services/ship_benchmark_integrity.py` 的恒等式套到其他参考资产
  （推进 open-water KT/KQ、稳性 GZ 曲线）上，作为「参考值入库前必过」的机器门；
  `tests/test_public_wall.py` 在本机为既有环境失败（sqlite 度缺失，与本单改动无关）。
