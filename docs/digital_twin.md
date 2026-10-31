# 冰区航行多物理场数字孪生（W3-N10）

> 生产级数字孪生框架：传感器输入 → 状态估计 → 多物理场代理预测 → 1 h / 6 h / 24 h 性能包络。
> 代码位置：`backend/services/digital_twin/`、`backend/api/digital_twin.py`；
> 验收工件：`artifacts/digital_twin/acceptance.json`、`artifacts/digital_twin/physics_validation.json`、`artifacts/digital_twin/figures/`。

---

## 1. 架构

```
传感器帧 (GM / 轴功率 / 转速 / 应变 / 冰况 / 海况)
        │  schema 校验 + 时标新鲜度检查
        ▼
ExtendedKalmanFilter      x = [GM_eff, resistance_bias, damage_scale]
        │  观测量 = 代理模型预测 × 偏置（含创新门控、Joseph 协方差）
        ▼
OnlinePredictor           R_tot = R_calm + R_ice + R_aw → P_D → 燃油 / 损伤 / GM
        │  dt = 300 s（生产） / 60 s（参考真值）
        ▼
DigitalTwinOrchestrator   审计事件流 · 延迟分位 · 降级标志 · 版本三元组
        │
        ▼
REST /api/v1/digital-twin/v1/{predict,state/estimate,forecast,health,surrogates,version}
```

| 模块 | 职责 |
|---|---|
| `models/geometry.py` | 参数化船型，`Cb` 闭式闭合（Beta 函数），位移/水线面/惯性矩解析积分 + 数值复核 |
| `models/resistance.py` | Holtrop–Mennen 静水阻力 + Lindqvist 层冰阻力 + 有效功率 |
| `models/seakeeping.py` | 垂荡/纵摇单自由度模态 RAO（Smith 修正）+ 经验波浪增阻 |
| `models/manoeuvring.py` | MMG 三自由度回转/ Z 形试验时域仿真 |
| `models/stability.py` | 数值 KN/GZ 复原力臂（二维水线积分 + 二分求解下沉量） |
| `models/fatigue.py` | DNV-RP-C203 F 曲线（双斜率）谱疲劳 + 雨流计数参考 |
| `pce.py` | Legendre 张量积多项式混沌（最小二乘），预测 = 一次基函数求值 + 点积 |
| `surrogate_registry.py` | 5 个代理模型统一 `predict()` 契约、延迟分位、故障回退、契约指纹 |
| `state_estimator.py` | 3 状态 EKF + 收敛研究 |
| `online_predictor.py` | 性能包络外推 + 与参考仿真的 MARE 校验 |
| `dt_orchestrator.py` | 生产闭环：摄取、预测、健康、审计、降级 |

## 2. 代理模型统一契约

```python
surrogate.predict({"V": 6.0, "ice_concentration": 0.5, "ice_thickness": 0.5})
# -> {"R_calm_kN": 344.4, "R_ice_kN": 838.0, "R_total_kN": 1182.4, "P_D_kW": 10580.5}
```

| 代理 | 输入 | 输出 | 参考模型 | 基函数 | R²(holdout) |
|---|---|---|---|---|---|
| resistance | V, ice_conc, ice_h | R_calm/R_ice/R_total/P_D | Holtrop 1982 + Lindqvist 1989 | 20 | 0.9995 |
| seakeeping | Hs, Tp, heading, V | heave_rms, pitch_rms, R_aw | 2DOF modal + Gerritsma | 35 | 0.98 |
| manoeuvring | V, rudder | D/L, advance/L, speed loss, r_ss | MMG 3DOF | 6 | 0.99 |
| stability | heel, KG | GZ, KN, GM | 数值稳性 | 6 | 1.00 |
| fatigue | Hs, Tp, ice_conc, ice_h | damage/day, life, σ_rms | 谱疲劳 Miner | 35 | 0.99 |

* 设计试验：拉丁超立方式均匀抽样；每 4 个样本至少保留 1 个基函数（`_adaptive_degree` 稀疏度自适应降阶，防止 4 维 35 项基函数配 30 样本的过拟合）。
* 延迟：单次 `predict` 实测 **P95 = 0.16–0.67 ms**（预算 100 ms，200 次连续调用统计）。
* 容错：`safe_predict()` 捕获代理异常后回退到参考物理解析式，返回 `degraded=True` 与错误串，调用方（EKF / 预测器 / API）不中断。

## 3. API（版本化 `/api/v1/digital-twin/v1`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/version` | 版本三元组（api / dt / schema） |
| GET | `/health` | 编排器 + 代理健康、延迟分位、计数器 |
| GET | `/surrogates` | 契约（输入输出 schema、版本、拟合质量、契约指纹） |
| POST | `/predict` | `{surrogate, inputs, vessel}` → `{outputs, latency_ms, degraded}` |
| POST | `/state/estimate` | 传感器帧 → EKF 后验状态 |
| POST | `/forecast` | `{horizon_h ≤ 48, speed_ms, heading_deg, environment, dt_s}` → 包络 |

错误码：400 模式违规 · 404 未知代理 · 422 参数越界（Pydantic）· 503 孪生体不可用。

## 4. 验收判据（`artifacts/digital_twin/acceptance.json`）

| 判据 | 目标 | 实测 | 结论 |
|---|---|---|---|
| (a) 5 代理注册且接口一致 | 5 | 5，契约指纹 `960d65d5d0d6cdde` | ✅ |
| (b) 单次 predict P95 | < 100 ms | 0.67 ms（最差） | ✅ |
| (c) EKF 收敛（RMSE < 5 % 真值，≤50 步） | < 5 % | GM 1.83 %（3 % 噪声）/ 2.57 %（5 % 噪声） | ✅ |
| (d) 1 h 预测 MARE | < 10 % | 1.80 %（中等冰况）/ 0.81 %（重冰况） | ✅ |
| (e) pytest 通过数 | ≥ 15 | 35 | ✅ |

MARE 定义：功率、燃油、平均航速、GM 裕度四项的 `mean(|预测-真值|/|真值|)`；疲劳损伤增量作为二阶量单列（见 §6 局限）。

## 5. 物理模型验证（`artifacts/digital_twin/physics_validation.json`）

| 项目 | 基准 | 误差 | 判定 |
|---|---|---|---|
| 几何闭合（Cb·L·B·T） | 解析积分 | 6.2e-6 | <3 % ✅ |
| 水线面惯性矩 I_T / I_L | 解析 | 2.6e-9 / 2.4e-5 | ✅ |
| ITTC-57 摩擦阻力 | 解析式 | 1e-12 | ✅ |
| GM 数值导数 vs KB+BM−KG | 双独立公式 | 0.54 % | <3 % ✅ |
| 方驳 GZ vs 舷侧直立解析式（≤8°） | 解析 | 0.10–0.11 % | <3 % ✅ |
| 长波极限 RAO→1（垂荡/纵摇） | 理论 | 1.0e-5 | ✅ |
| 谱疲劳 vs 雨流计数 | 时域 | 0.05 % | <3 % ✅ |
| 回转直径 @35° | 2.5–4.5 L 经验带 | 3.88 L | ✅ |
| 回转速降 | 公开试航 40–60 % | 67.6 % | ❌ 偏高（见 §6） |
| 冰阻 0.6 m/90 %/5 kn | 1A 级散货船 0.5–1.0 MN | 1.01 MN | 带内（单点标定，±40 %） |
| KCS 24 kn 总阻（Fn=0.26） | EFD/CFD 1.4–1.6 MN | 2.08 MN | ❌ 细长船型超算（见 §6） |

## 6. 局限与适用范围（必读）

1. **Holtrop–Mennen 在细长船型高傅汝德数偏保守**：KCS（Cb=0.651, Fn=0.26）总阻高估 +30…+48 %。本孪生体应用船为 1A 级 180 m 散货船（Fn ≤ 0.20），处于方法标定范围内；对细长船型应改用 CFD/模型试验回归修正。
2. **纵摇/垂荡为单自由度模态模型 + Smith 压力衰减**，仅 Froude–Krylov 激励、无绕射项：长波极限与共振幅值受控，但共振频带 RAO 绝对精度约 ±15–30 %，未做模型试验标定。
3. **波浪增阻为经验式**（`c_aw = 0.055`，标定至 Hs=4 m 顶浪时占静水阻力 5–15 % 的公开全尺度带），非一阶原理推导。
4. **冰阻力为 Lindqvist 三成分模型 + 下沉项 0.55 单点工程修正**：未与本工作区的冰模试验数据对比，绝对量 ±40 %；趋势（冰厚、浓度、航速单调性）可靠。
5. **疲劳应力为工程暴露律** `σ_amp = σ_design·(Hs/11)²`，双锚点（UR S11 设计弯矩 @Hs=11 m、实测 rms 10–20 MPa @Hs≈4 m）；Hs 2–8 m 外推 ±30 %。因此 1 h 疲劳损伤增量的相对误差达 30–130 %（绝对量 < 1e-4 损伤单位），不计入 MARE 主判据。
6. **操纵模型缺粘性升力**（理想化船体为等吃水横剖面），漂角偏大导致回转速降高估 ~10–25 %；回转直径与纵距在经验带内。
7. **稳性模型**：横剖面为全吃水矩形、水线面系数等于 Cb，BM 对丰满船型偏低 10–20 %；艉/艏甲板边缘浸没未建模，GZ 曲线适用至 25–30°。孤立验证（方驳解析式 0.1 %）与内部一致性（GM 0.54 %）成立，绝对 GM 仍应以外界静水力软件为准。
8. **数字孪生为单船单装载工况**；船型参数变化需重建注册表（约 30 s）。

## 7. 使用示例

```python
from services.digital_twin import DigitalTwinOrchestrator, VoyagePlan, EnvironmentForecast

twin = DigitalTwinOrchestrator(L=180, B=32, T=11.5, Cb=0.80, KG=10.0).warmup()

twin.ingest({"gm_meas_m": 1.47, "speed_ms": 6.2, "shaft_power_kW": 9200.0,
             "strain_rms_MPa": 11.0, "ice_concentration": 0.45, "ice_thickness": 0.4,
             "Hs": 3.4, "Tp": 8.0, "timestamp_s": 1000.0}, now_s=1000.0)

env = EnvironmentForecast(ice_concentration=[0.4, 0.5, 0.5], ice_thickness=0.4,
                          Hs=[3.0, 3.4, 3.2], Tp=8.0)
out = twin.forecast(horizon_h=1.0, plan=VoyagePlan(speed_ms=7.0), env=env)
print(out["summary"]["fuel_kg"], out["summary"]["min_GM_margin_m"])
```

## 8. 测试

```bash
python3 -m pytest backend/tests/test_digital_twin_physics.py \
                 backend/tests/test_digital_twin_surrogates.py \
                 backend/tests/test_digital_twin_estimator_predictor.py -q --noconftest
# 35 passed
```

> 注：仓库级 `backend/tests/conftest.py` 会 `import main`，在本执行环境缺少
> `cryptography` / `psycopg2` 等运行期依赖（与孪生模块无关），因此用 `--noconftest`
> 并在测试模块内设置 `DATABASE_URL=sqlite:///:memory:` 运行。

## 9. 复现基准

```bash
python3 scripts/run_digital_twin_benchmark.py   # 约 3 min，写出 acceptance.json + 5 张图
```