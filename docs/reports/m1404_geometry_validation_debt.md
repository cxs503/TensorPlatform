# m1404 · 验证欠账清零：基准几何 Cb/L/B/T 一致性门 + 阻力 API 契约缺陷

**结论先行**：既有基准对标链路里存在两类缺陷，均已被审计脚本机器复现并修复。
(1) 三条基准几何记录里两条与自身声明的 Cb 不自洽（DTMB-5415 −16.15%、KVLCC2 −4.19%），
SUBOFF 预设是 `beam=draft=displacement=cb=0.508` 的占位符；
(2) 更严重的是契约缺陷——对标 harness 以 `speed` 键调用 `speed_ms` 字段、并额外发送
API 根本不接收的 `displacement`，pydantic 静默丢弃 → **所有基准工况实际都在默认 1.0 m/s 下计算**。
平台精度规则「几何必须先验证 Cb/L/B」此前没有任何机器门，本单把它变成可执行门 + 22 条测试。

## ① 案例介绍（几何 / 参数表）

三条对标基准（数据文件 `backend/data/benchmark_reference.json`，v1.0 → v1.1）：

| 记录 | L (m) | B (m) | T (m) | 声明 Cb | 位移（原值） | 位移（修正） | 来源 |
|---|---|---|---|---|---|---|---|
| DTMB-5415（模型） | 5.72 | 0.759 | 0.246 | 0.508 | 466.3 kg | 556.1 kg（∇=0.54247 m³） | ITTC 2010 V&V workshop |
| KVLCC2（实船） | 320.0 | 58.0 | 20.8 | 0.8098 | 307 000 t | 320 474 t（∇=312 657.6 m³） | SIMMAN 2008 / MOERI |
| SUBOFF（轴对称体） | 4.356 | D=0.508 | — | — | 未公布 | 不改，改走表面证据 | DTRC-90/012 (Roddy 1990)，S=5.42 m² |

判据（门规则）：`Cb_stated == V_displacement /(L·B·T)`，|偏差| ≤2% = PASS，≤5% = WARN，>5% = FAIL；
轴对称体用 `Cv = V/(πD²L/4)`，体积未公布时退化为 `S/(πDL)` 合理性检查。
退化输入（beam=draft=displacement=cb 全等）直接判 INVALID——不给百分比，避免用数字给占位符背书。

## ② 怎么做的（方法 / 工具链）

1. 新增零依赖门模块 `backend/api/domain/geometry_consistency.py`：单位换算（m³/t/kg/L，ρ=1025 海水 / 1000 淡水）、
   商船体 Cb 恒等式、轴对称体 Cv 与湿表面积双分支、退化检测、`audit_dataset` 批量审计与汇总。
2. 新增只读审计脚本 `tools/ship_perf/audit_benchmark_geometry.py`：AST 解析 `ResistanceRequest` 字段与
   harness payload 键，算出「发送了但 API 不接收」的键；输出 `artifacts/m1404/geometry_audit.{json,csv}`。
   **本单正是靠这个 AST 交叉检查抓到 speed/displacement 静默丢弃的**。
3. 修复：`ship_benchmark.py` payload 改 `speed_ms`、删除不存在的 `displacement`；`SHIP_GEOMETRY` 三预设
   改为单位显式 + 溯源字段 + `geometry_status`；`benchmark_validate` 响应 summary 增 `geometry_check`
   （诊断非致命，历史 honest-FAIL 证据仍可比）。
4. 数据集 v1.1：位移按自身 Cb 恒等式反解修正，并把推导写入每条的 `derivation` 字段（数字可复算）。
5. 复现脚本 `tools/ship_perf/plot_geometry_audit.py` 生成 before/after 数字与两张图。

## ③ 计算结果（程序化注入，零手抄）

来源：`artifacts/m1404/geometry_before_after.json`、`artifacts/m1404/geometry_audit.json`。

| 记录 | 修正前 Cb 偏差 | 修正后 Cb 偏差 | 判决（前→后） |
|---|---|---|---|
| DTMB-5415 数据集 | **−16.15%** | −0.002% | FAIL → PASS |
| KVLCC2 数据集 | **−4.19%** | +0.011% | WARN → PASS |
| DTMB-5415 预设 | **+85 846.62%**（把 kg 当 m³） | −0.014% | FAIL → PASS |
| KVLCC2 预设 | −1.80%（吨当 m³ 的巧合） | +0.011% | PASS → PASS |
| SUBOFF 预设 | 占位符（无意义） | INVALID（声明 APPROXIMATE） | 保持 INVALID（honest） |

契约审计（`geometry_audit.json → api_contract`）：
`request_fields = [ship_type, speed_ms, length, beam, draft, cb, water_density, kinematic_viscosity]`，
修正前 `silently_dropped_keys = ["speed", "displacement"]`，修正后 `[]`。
后果量化：`speed` 被丢弃 → 全部 12 个基准工况按 `speed_ms` 默认值 1.0 m/s 计算，
而真实工况为 2.572–15.433 m/s（DTMB/SUBOFF）与 7.202–8.231 m/s（KVLCC2），
即 Re 差 2.5–15 倍、Fr 差 2.5–15 倍，任何以该链路得出的对标误差百分比都不成立。

## ④ 结果图

- `fig_geometry_error_before_after.png`：Cb 恒等式偏差 before/after 对比（symlog 纵轴，虚线为 ±2% 门限）；
- `fig_stated_vs_implied_cb.png`：声明 Cb vs 隐含 Cb 散点，修正前点明显离开 y=x 对角线。

（图件由脚本读取门模块实时计算后绘制，无手抄数字。）

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **本次未改阻力物理**：门只认证几何输入，不认证 Holtrop/ITTC-57 的精度。修复 speed 键后，
   基准误差百分比会与历史报告（m1370、tp-gen-2 等）不同——这是修 bug 的必然重标定，需在后续
   对标单里以新契约重跑，不能与旧数字混用。
2. **SUBOFF 仍是 honest-FAIL**：轴对称体没有水线宽/方形系数，商船体参数化不适用；本次只做了
   表面证据检查（S/(πDL)=0.780 落在 [0.30,1.60]），Cv 因体积未在公开文献中直接给出而未认证。
3. **KVLCC2 320 474 t 系 SIMMAN 2008 常规值**；与数据集自声明 Cb=0.8098 交叉验证残差 0.011%，
   但本单未引入独立第三方复算，属于「内部自洽」级证据。
4. 预设 `displacement` 现统一为 m³ 体积（非吨位），单位字段 `displacement_unit` 显式声明；
   下游若按吨位解释会再出错，消费方须读单位字段。
5. 现行 `backend/tests/feat/test_ship_benchmark.py` 在本机环境无法 import（缺 `cryptography`/`psycopg2`，
   属既有环境限制，非本单引入）；本单测试用按路径直接加载模块的方式绕开 `api/__init__` 的 DB 依赖，
   并对 `benchmark_validate` 做了端到端 mock 覆盖。

## ⑥ 误差判定（逐项，是否 <3%）

| 关键量 | 基准 | 修正后偏差 | 是否 <3% |
|---|---|---|---|
| DTMB-5415 几何 Cb | 自身声明 Cb=0.508（恒等式自洽基准） | 0.002% | 是 |
| KVLCC2 几何 Cb | 自身声明 Cb=0.8098 | 0.011% | 是 |
| DTMB-5415 预设 Cb | 上述 0.508 | 0.014% | 是 |
| KVLCC2 预设 Cb | 上述 0.8098 | 0.011% | 是 |
| SUBOFF 湿表面积比 S/(πDL) | 合理性区间 [0.30,1.60]（非解析基准） | 0.780 在带内 | 无解析基准，声明为区间判据 |
| SUBOFF Cb/Cv | 公开文献未给出体积 | 未认证 | **否/未认证（honest）** |

> 说明：本单的「基准」是几何恒等式与记录自身声明值，属可机器裁决的自洽判据；
> 未对阻力数值做新的文献对标，因此不宣称任何 Ct/Cd 误差指标 <3%。原 15% 精度标准所约束的
> 对标任务需在 speed 契约修复后重新执行。

## 复现

```bash
python3 tools/ship_perf/audit_benchmark_geometry.py   # 门审计 -> artifacts/m1404/geometry_audit.{json,csv}
python3 tools/ship_perf/plot_geometry_audit.py        # before/after + 图件
python3 -m pytest tests/test_geometry_consistency.py -q   # 22 passed
```