# 船体几何与阻力基准链验证（mission m1609）

**任务类型**：验证欠账（对已合并功能做基准复核），只读扫描后自选，轻量计算（load15≈125，
未起重算力进程）。
**结论一句话**：阻力基准链此前**结构性失效**——基准端点比较的 `ct` 字段根本不存在，
所以每个算例都是 100% 误差，历史报告里的全部 "HONEST-FAIL" 是契约 bug 的产物而非物理结论；
修复后真实精度为：可认证子集 2 例（SUBOFF Fr 0.459/0.612）误差 **5.33% / 5.78%**，
商船船型（KVLCC2）系统偏低 **49–56%**，3 个参考点存在量纲/尺度不自洽需重建。

## 1. 发现的缺陷（每条都可机器判定）

| # | 缺陷 | 证据 | 严重度 |
|---|------|------|--------|
| D1 | 基准端点读 `calc_data["ct"]`，而阻力响应模型**没有 ct 字段** → `error_pct` 恒为 100%（或 999） | `ResistanceResponse` 字段集不含 ct；修复前 12/12 例全部"失败" | 致命（假 FAIL） |
| D2 | 基准 POST `{"speed":…,"displacement":…}`，请求模型声明 `speed_ms`/`displacement_t`，多余键被忽略 → **所有算例按默认 1.0 m/s 计算** | pydantic v2 默认 `extra=ignore` | 致命（静默错值） |
| D3 | 湿表面积用占位式 `S = π·B·L·(0.5+0.25Cb)` | KVLCC2 **+50.6%**、DTMB-5415 **+75.5%**、SUBOFF **−19.6%** | 高（力值错，系数不受影响） |
| D4 | SUBOFF 预设 `cb=0.508`、`beam=draft=0.508`、`displacement=0.508` | 由 (L,D,S) 的 Cauchy–Schwarz 下界 **Cb_cyl ≥ (S/πDL)² = 0.6078**，0.508 可证不可能 | 高 |
| D5 | DTMB-5415 预设 `displacement=466.3`（单位混用） | 反推 Cb=0.437 vs 公布 0.506（−13.7%）；公布 ∇=0.5492 m³ | 中 |
| D6 | 参考表 `experimental_rt_n` 与 `experimental_ct` 不自洽 | SUBOFF-01 12.5 N vs ½ρSV²Ct=92.5 N（**7.4×**）；KVLCC2-01 550 kN vs 1 971 kN（**3.6×**） | 高 |
| D7 | DTMB-5415 算例模型尺度长度配实船航速 | 5.72 m 船长 + 20/30 kn → Fr = 1.37 / 2.06，超出拖曳水池排水型区 | 高（参考点不可用） |
| D8 | 仓库 `api/domain/platform.py` 与标准库 `platform` 同名 | 把 `api/domain` 加入 `sys.path` 会遮蔽标准库，numpy/fastapi 立刻 ImportError | 陷阱（已写入 docs） |

## 2. 改了什么

- **新增 `backend/api/domain/hull_geometry.py`**：三型船公布主尺度登记簿（逐条引文）、
  Holtrop & Mennen (1982) 湿表面积、ITTC-57 摩擦线、回转体 Cauchy–Schwarz 体积下界、
  `validate_hull_geometry()` 几何闸门、`reference_provenance()` 参考点自洽性检查。
- **新增 `backend/api/domain/resistance_core.py`**：把阻力物理从 Web 层剥离为**零依赖**纯函数
  `solve_resistance()`，端点成为薄封装。这样审计与测试不依赖 psycopg2 / cryptography
  （本镜像缺这两个包，整个 API 测试集无法导入 —— 见 §5 局限）。
- **`backend/api/calculations.py`**：响应增加 `ct/cf/cr/wetted_surface_m2/method/geometry_validation`；
  接受 `speed` 别名；几何先过闸门（失败 422）；湿表面积优先用公布值，Holtrop 式作交叉校验；
  有效性包络随结果返回，不再静默外推。
- **`backend/api/domain/ship_benchmark.py`**：预设从登记簿投影（`cb_source/s_source` 可追溯）；
  payload 用 `speed_ms`；比较真实 `ct`；缺 `ct` 时显式记 `contract_violations`；
  HTTP 不通时回退**同源**纯函数并标注 `method_backend=local_fallback`。
- **`backend/data/benchmark_reference.json` v2.0**：公布几何 + 引文；力值列按
  `rt = ½ρ_ref·S_pub·ct·V²`（ρ=998）重算，v1.0 手写值保留为 `reported_rt_n_legacy`；
  **实验 Ct 一律不改**，不可信者标 `provenance_flag`。
- **新增 `tools/verify_ship_benchmark.py`**：可重复审计器（`--regenerate/--figures`），
  产物 `artifacts/m1609/`。
- **新增 `backend/tests/feat/test_ship_benchmark_verification.py`**：28 项回归。

## 3. 验证结果

几何闸门三型全部通过：

| 船型 | Cb 声明 vs 由体积反推 | Holtrop S vs 公布 S | 占位式 S 误差 |
|------|----------------------|---------------------|---------------|
| KVLCC2 | 0.8098 vs 0.8098 (**0.00%**) | 27 066.8 vs 27 194 m² (**−0.47%**) | +50.6% |
| DTMB-5415 | 0.506 vs 0.5094 (0.67%) | 4.678 vs 4.874 m² (−4.02%) | +75.5% |
| SUBOFF | 0.7796 (表面丰满度) ≥ 下界 0.6078 ✓ | 回转体，公式不适用 | −19.6% |

**注意**：Ct = Cf + Cr，湿表面积在系数中相消，因此 D3 只污染力值（N），不污染基准比较的系数
——基准失效的真正原因是 D1/D2。这一条是本单最重要的辨析。

修复后精度（`solve_resistance` 直接对参考 Ct）：

| 算例 | Fr | Ct 计算 | Ct 参考 | 误差 | 模型包络内 |
|------|-----|---------|---------|------|-----------|
| SUBOFF-01 | 0.459 | 0.003598 | 0.003800 | **5.33%** | 是 |
| SUBOFF-02 | 0.612 | 0.004231 | 0.004000 | **5.78%** | 是 |
| SUBOFF-03 | 0.765 | 0.005253 | 0.004200 | 25.06% | 否（Fr>0.62） |
| SUBOFF-04 | 0.918 | 0.006689 | 0.004400 | 52.01% | 否 |
| DTMB5415-01 | 0.343 | 0.003088 | 0.004500 | 31.37% | 否（非回转体） |
| DTMB5415-02..04 | 0.687–2.060 | — | — | 7.4–601% | 否 + 参考点不自洽 |
| KVLCC2-01..04 | 0.129–0.147 | ≈0.00141 | 0.0028–0.0032 | **49.5–56.1%** | 否 |

可认证子集（模型包络内 ∧ 参考点自洽）：n=2，mean|err| 5.55%，max 5.78%，2/2 ≤15%。
**是否 <3%？否**（5.33%/5.78%）——达标线 15% 满足，3% 未达，如实记录。

## 4. 局限与后续（honest）

1. 残差模型 `Cr = 0.002(1+2Cb)Fn^2.5` 是潜体拟合，**既无形状因子 (1+k) 也无兴波项**，
   所以商船型系统偏低 ~50%。修复路径：Holtrop–Mennen 兴波阻力量级（c1…c7、m1、m2、λ、iE）
   或 RANS——建议另派单，本单不改算法（无法自证）。
2. 参考表 3 个 DTMB 点（Fr 0.687/1.37/2.06）需回原始拖曳水池数据重建；本单只标注不改数
   （honest-FAIL 不得覆盖实验值）。
3. SUBOFF 仅有 (L,D,S)，无型值表，Cb 用表面丰满度 S/(πDL)=0.7796 作代理并在闸门中以
   Cauchy–Schwarz 下界约束；Holtrop 湿表面积式对回转体不适用（KVLCC2 校验 −0.47%，
   DTMB −4.02%，说明公式本身实现正确）。
4. 本镜像缺 psycopg2 / cryptography，仓库既有 `backend/tests/conftest.py` 会导入 `main.app`，
   因此整包测试集在本环境无法导入；本单新增测试用 `--noconftest` + 按路径加载纯模块
   （`pytest backend/tests/feat/test_ship_benchmark_verification.py --noconftest`）。
   原有 `test_ship_benchmark.py` 未在此环境执行过，属未验证面。

## 5. 复现

```bash
python3 tools/verify_ship_benchmark.py --regenerate --figures   # 审计 + 修数 + 出图
python3 -m pytest backend/tests/feat/test_ship_benchmark_verification.py -q --noconftest
```

产物：`artifacts/m1609/ship_benchmark_audit.json`、`ct_vs_fr_comparison.png`、
`error_per_case.png`、`wetted_surface_verification.png`。