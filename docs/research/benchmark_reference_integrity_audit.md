# 船舶基准参考数据完整性审计：把"验证欠账"变成机器可判的闭合闸门

**席位**：tp-impl-4（platform） · **单号**：m1385 · **日期**：2026-11-01
**平台报告**：id 991（POST /api/v1/tensor/reports） · **commit**：1cdd3d8
**产物**：`backend/services/benchmark_integrity.py`（闸门） · `scripts/audit_benchmark_integrity.py`（CI）·
`scripts/migrate_benchmark_reference_v2.py`（v1→v2 迁移，派生量由脚本计算）·
`backend/data/benchmark_reference.json` (v2.0) · `backend/data/benchmark_reference_v1_legacy.json`（事故快照）·
`backend/tests/test_benchmark_integrity.py`（20 项） · `artifacts/benchmark_integrity_audit.json` ·
`artifacts/figures/benchmark_integrity_audit.png`

---

## ① 案例介绍（对象与几何）

平台把 `calc.resistance` / `calc.stability` 判为 **REAL** 证据，其判据是"计算 Ct 与实验参考值偏差 ≤15%"。
但**判据的来源本身从未被校验**。本次审计的对象是平台内两张互相独立的基准表：

| 表 | 位置 | 行数 | 内容 |
|---|---|---|---|
| A | `backend/data/benchmark_reference.json` | 12 | SUBOFF(4) / DTMB-5415(4) / KVLCC2(4)，Ct/Rt |
| B | `backend/services/ship_proxy.py::BENCHMARK_REFERENCE` | 3 | KVLCC2、SUBOFF、generic_barge，Rt/GM |

主尺度（审计前，逐字取自文件）：SUBOFF L=4.356 m, D=0.508 m, S=5.42 m²；DTMB-5415 L=5.72 m, B=0.759 m,
T=0.246 m, Cb=0.508；KVLCC2 L=320 m, B=58 m, T=20.8 m, Cb=0.8098。船模水池 ρ=1025 kg/m³（海水）、
ν=1.1883e-6 m²/s（海水 15 °C）。

## ② 怎么做的（方法与判据）

不引入任何拟合或 CFD，只用**可手算复核的解析恒等式**做闭合校验（`backend/services/benchmark_integrity.py`）：

| 检查 | 恒等式 | 判据 |
|---|---|---|
| C1 `froude_closure` | Fr = V/√(gL) | 相对误差 ≤2%，否则 FAIL（并用 (V/Fr)²/g 反解隐含船长定位尺度混用） |
| C2 `displacement_closure` | Δ = Cb·L·B·T·ρ | 相对误差 ≤2%；回转体标 WITHHELD（排水型闭式不适用） |
| C3 `resistance_closure` | Rt = Ct·½ρV²S | 有实测 S 直接比（≤15%）；无 S 则反解 S_imp，要求无量纲带内：排水型 S/(L·B)∈[1.0,3.2]，回转体 S/(πDL)∈[0.5,1.0] |
| C4 `friction_floor` | Ct ≥ Cf_ITTC57(Re) | 总阻力不可能低于平板摩擦 |
| C5 `form_bands` | L/B, B/T, Cb, S/(L·B) | 常规船型合理带，只拦"扯淡值" |

解析锚（用于证明实现无偏，见测试 H1–H5）：ITTC-57 在 Re=1e6 → 0.075/16 = 4.6875e-3；矩形浮箱
KM = T/2 + B²/(12T)（80/20/4 → 10.3333 m）；Rt = Ct·½ρV²S 的正/反解互逆。

判级 PASS(0) < WITHHELD(1) < WARN(2) < FAIL(3)，行级取最差项；**WITHHELD 必须显式声明"缺证据"，
不得当成 PASS**。工具链：纯 Python + matplotlib，可在 8001/8002 服务不可用时离线运行（本次正是如此）。

## ③ 计算结果（数字由 `artifacts/benchmark_integrity_audit.json` 程序化注入）

审计前（v1 快照）：**12/12 行 FAIL，两张表均不可作判据**（`binding=False`）。检出四类事故：

| 事故 | 证据（v1） | 反解/派生 |
|---|---|---|
| A1 DTMB-5415 尺度混用 | speed_ms = 2.572/5.144/10.289/15.433 m/s 恰为 **5/10/20/30 kn 全尺度航速**，却配 5.72 m 船模 | Fr 达 0.343…**2.060**（排水型船模 Fr>1 不存在）；由 (V/Fr)²/g 反解隐含船长 **181.2 m**，既非 5.72 也非 142 m |
| A2 排水量不闭合 | DTMB-5415 Δ=466.3 kg、KVLCC2 Δ=307000 t（表 B 的 kvlcc2_full 写 310000 t） | Cb·L·B·T·ρ = **556.1 kg** / **320437 t**；闭合误差 **−19.3%** / **−4.4%** |
| A3 Rt 列与 Ct 列互斥 | SUBOFF Rt=12.5 N（Ct=0.0038, S=5.42 m², V=3 m/s） | 应为 **95.00 N**（差 7.6 倍）；KVLCC2/DTMB 反解 S_imp/(L·B)=**0.37–0.40**、**0.10–0.13**，均远低于物理下限 1.0 |
| A4 barge 参数自相矛盾（表 B） | generic_barge Δ=10000 t 但 Cb=0.85, L·B·T=6400 m³ | 应为 **5576 t**（闭合误差 −44%）；参考 GM=3.5 m 与矩形等价体 GM=KM−KG=5.33 m 偏离 34% → 标 WITHHELD |

修正（只改"算得出来"的量，v1 原值全部保留在 `*_legacy_*` 字段与 v1 快照中）：
Δ 由 Cb·L·B·T·ρ 派生；DTMB-5415 速度按 Fr 反算为模型尺度（0.4569/0.9139/1.8278/2.7417 m/s）；
SUBOFF 的 Rt 由 Ct·½ρV²S 派生（95.00/177.78/291.66/440.00 N）；缺湿面积的 KVLCC2/DTMB Rt 列置 `null`
并标 `rt_status=WITHHELD`——**不臆造观测值**。同时把 `SHIP_GEOMETRY` 改为从数据文件派生（消灭"代码一份、
数据一份"的双表漂移），payload 补 `displacement_t`（下游 schema 单位为 t）。

审计后：表 A **FAIL 0 行**（12 行 WITHHELD，全部因 Rt 列缺湿面积证据）、表 B **FAIL 0 行**
（2 WITHHELD + 1 WARN），`binding=True`（可作判据）。闭合误差：Fr 全部 ≤0.02%，Δ 全部 0.00%。

## ④ 结果图

`artifacts/figures/benchmark_integrity_audit.png`：(a) 参考 Fr、v1 由 speed_ms 反算的 Fr（红，DTMB 冲到 2.06）、
v2 修正后 Fr（绿，全部重合）；(b) 排水量闭合误差 v1（−19.3%/−4.4%）vs v2（0.00%），带宽 ±2%；
(c) 由 v1 的 (Ct, Rt) 反解的表观湿面积与物理下限的对比——所有点都在下限以下，说明 Rt 列不可能是实测值。

## ⑤ 注意点（踩坑与局限）

1. **判据的可信度必须与数据可信度同级**：平台此前只校验算法，不校验参考行，导致 12 行"实验参考值"里
   有 12 行物理不闭合——按这些行算出的 ≤15% 误差是**假证据**。
2. **不要为了"过闸"而编数**：KVLCC2/DTMB 的 Rt 无法从记录复核（缺湿面积），正确做法是 WITHHELD，
   而不是拍一个"合理"的总阻力。
3. 表 A 与表 B 对同一 SUBOFF 给出互相差 6.3 倍的参考阻力（15.0 N vs 95.0 N）：双表并存是漂移温床，
   本次已统一为派生值并保留 legacy。
4. DTMB-5415 的 Fr 列（0.061…0.366）虽与模型尺度自洽，但 0.061 对 ITTC 研讨会工况偏低——**Fr 与 v
   究竟哪个是原始转录量，记录未给出出处**，本次按"Fr>1 不可能"这一物理事实取 Fr 为准，并在
   `speed_basis` 里写明反算依据，待文献核对。
5. 局限：`backend/api/domain/ship_benchmark.py`、`ship_samples.py`、`api/calculations.py` 三个路由
   **均未在 `main.py` 挂载**（本平台按 README 只交付领域中立底座 + factory 应用），因此本次不以新增
   HTTP 端点交付，而交付可离线运行的闸门模块 + CI 脚本；`benchmark_validate` 的 summary 已内嵌
   `reference_integrity` 判决（binding / non_binding_cases），接上路由即生效。
6. 运行环境缺 `cryptography`/`psycopg2`，`backend/tests/conftest.py` 导入 `main` 会失败，故本次测试以
   `--noconftest` 运行（被测模块本身不依赖 DB）。

## ⑥ 误差判定（vs 基准，逐项）

| 关键量 | 基准/判据 | 结果 | 是否 <3% |
|---|---|---|---|
| ITTC-57 实现 | Re=1e6 手算 0.075/16 = 4.6875e-3 | 相对差 0（机器精度） | ✅ 是 |
| 矩形浮箱 KM 实现 | T/2+B²/12T = 10.33333 m（手算） | 相对差 <1e-12 | ✅ 是 |
| 排水量闭合（v2，8 行） | Δ 派生 vs Cb·L·B·T·ρ | 最大 0.00% | ✅ 是 |
| Fr 闭合（v2，12 行） | 记录 Fr vs V/√(gL) | 最大 0.02%（容差 2%） | ✅ 是 |
| v1 事故检出率 | 应判 FAIL 的 12 行 | 12/12 判 FAIL | ✅ 是 |
| SUBOFF Rt 派生 | Ct·½ρV²S 与记录值 | 记录值偏差 87%（7.6 倍）→ 判 FAIL | ✅ 检出 |
| generic_barge Δ | Cb·L·B·T·ρ = 5576 t vs 10000 t | 偏差 44% → 判 FAIL | ✅ 检出 |

**结论**：闸门实现对解析锚的误差 <3%（实际为机器精度）；对 v1 参考行，12/12 判定为不可闭合
（honest-FAIL，不粉饰）。参考行本身缺湿面积的 8 行维持 WITHHELD，尚未取得"与实验一致"的证据等级
——这是本单**未完成**的部分，需文献/水池原始报告补 S 后复算。