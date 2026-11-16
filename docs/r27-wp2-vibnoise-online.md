# r27-WP2 振动噪声域上线与证据（MISSION 22584）

> 仓库 TensorPlatform ｜ 分支 `agent/m22584` ｜ 产物 `artifacts/vibnoise/`、`artifacts/wiring_census/`
> 数字全部由 `tests/test_r27_vibnoise_anchors.py::build_anchors/build_registry` 程序化注入（禁手抄）。

## 1. 案例（物理背景 / 参数）

船舶总体性能 9 学科里，振动噪声域此前是「代码在、测试在、**线上不可达**、**台账无条目**」的
典型缺陷：`backend/api/domain/ship_vibration.py`（28.7 kB 解析层 + 6 条端点）早已存在，
m1849 也在 `backend/main.py` 里 `include_router`，但接线包在 `try/except → logging.warning`
里 —— **fail-open**：域静默缺失时 app 照常启动、日志无人看、线上 404 无人知。

本轮把状态从「静默」推到「可判」，并给该域补上三类此前没有的机器证据：

| 项 | 本轮前 | 本轮后 |
|---|---|---|
| 解析锚 | 梁/板各 1 档 | 5 族 26 点（梁 3 边界×3 阶、板 a/b=1/1.5/2、板 TL 3 点、辐射效率 8 点、传递率 3 点） |
| 辐射效率 | **函数不存在** | 障板 Rayleigh 积分 + 半球数值积分实现，对 3 条独立闭式交叉（最大 1.363%） |
| 线上可达 | 无断言（依赖外部 :8001 进程） | 进程内 `TestClient` 断言 6 条端点 200，非 200 即红 |
| 缺域检测 | `logging.warning` 静默跳过 | `VIBRATION_DOMAIN_STATUS`（fail-closed）+ ERROR 日志 + 两条负对照测试 |
| 注册台账 | 21 项里 0 条 | `registry_candidates.json` 7 条（5 REAL / 2 WITHHELD） |

工况参数（全 SI）：梁 E=2.1e11 Pa、I=8e-6 m⁴、ρ=7850 kg/m³、A=0.0115 m²、L=2.4 m（自由-自由取 L=6.0 m）；
板 E=2.1e11 Pa、ν=0.3、ρ=7850 kg/m³、h=8 mm；辐射效率板 0.6×0.4 m；空气 ρ0=1.225 kg/m³、c0=343 m/s。

## 2. 方法

1. **独立第二路径（禁复用被测实现）**：参考式全部写在测试文件内（E-B 梁 `λ²/(2πL²)√(EI/ρA)`、
   Blevins 表 8-1 根、Kirchhoff SSSS `(π/2)√(D/ρh)(1/a²+1/b²)`、质量定律 `10log10(1+x²)`、
   单极子极限 `(32/π⁵)k²S`、无限板超音速 `1/√(1−(k_mn/k)²)`、SDOF 传递率），
   不 import 被测模块的任何中间量；表值另用**本测试内**独立 bisection 复核到 <1e-4。
2. **新能力数值实现**：`radiation_efficiency` 用障板 Rayleigh 积分远场 + 半球立体角梯形积分
   （1D 方向积分有闭式，故只对 Ω 数值积分），并把「网格减半」的相对偏差回填为
   `quadrature_selfcheck_pct`（本轮各点 ≤0.41%）。修掉一个真实缺陷：φ 网格原先用
   `endpoint=False`，梯形法漏掉回绕面板 → O(1/n_φ)≈1% 系统偏小（自检从 1.14% 降到 0.002%）。
3. **fail-closed**：`ship_vibration.domain_status()` 把「router 是否真挂进 app + 6 条端点是否齐备」
   变成 `pass/missing_endpoints` 状态；`backend/main.py` 记入 `VIBRATION_DOMAIN_STATUS` 并在
   非 pass 时打 **ERROR**，同时 `app.state.vibration_domain_status` 供运维/门禁读取；
   `assert_domain_gate()` 供测试直接判红。**路由前缀与既有 URL 一字未改**。
4. **线上可达断言**：`TestClient` 打 6 条端点（本轮 `backend.main` 因本机缺 `cryptography`
   不可导入 → 以同一 `include_router` 调用复刻 app，`reachability_mode` 如实回填；外部 :8001
   实况面降级为**带原因的可跳过门**，本轮未连到外部服务）。
5. **台账候选**：`registry_candidates.json` 含 beam / plate / panel_tl / radiation_efficiency /
   isolation（REAL）+ 场入射 TL、船用阻尼比（WITHHELD）；等级由实测误差推出，缺输入不填充。

## 3. 计算结果

### 3.1 五族锚（汇总）

| 族 | 物理量 | 锚点数 | 预声明阈值 | 实测最大相对误差 | 是否 <3% |
|---|---|---|---|---|---|
| Euler-Bernoulli 梁 f1..f3 | Euler-Bernoulli 梁前三阶弯曲固有频率 [Hz] | 9 | 1% | 0.0004% | 是（<1%，更严） |
| SSSS 板基频 f11 | Kirchhoff 薄板四边简支基频 f11 [Hz] | 3 | 3% | 0.0000% | 是（<3%） |
| 障板辐射效率 σ | 无限障板内简支矩形板第一阶模态辐射效率 σ [-] | 8 | 3% | 1.3631% | 是（<3%） |
| 隔振传递率 T | 基础激励单自由度隔振传递率 T [-] | 3 | 3% | 0.0000% | 是（<3%） |
| 单层板正入射 TL | 单层板正入射隔声量 TL [dB]（质量定律） | 3 | 3% | 0.0003% | 是（<3%） |

总计：**26 点，26 点在预声明阈值内**，最大相对误差
**1.3631%**（`radiation.supersonic.f=1000`，辐射效率超音速段 f=1000 Hz，
f/f_cm≈1.94 靠吻合一侧，无限板闭式本身在该处误差最大）。
族内最大误差：梁 0.0004% ／ 板 0.0000% ／
板 TL 0.0003% ／ 辐射效率 1.3631% ／
传递率 0.0000%。


逐点明细（由 `artifacts/vibnoise/anchors.json` 程序化渲染，零手抄）：

| 点 id | 参考式 | 参考值 | 模块值 | 相对误差 | 判定 |
|---|---|---|---|---|---|
| `beam.pinned-pinned.mode1` | lambda_n = n*pi（简支闭式） | 37.2022 | 37.2022 | 0.0000% | PASS |
| `beam.pinned-pinned.mode2` | lambda_n = n*pi（简支闭式） | 148.809 | 148.809 | 0.0000% | PASS |
| `beam.pinned-pinned.mode3` | lambda_n = n*pi（简支闭式） | 334.82 | 334.82 | 0.0000% | PASS |
| `beam.clamped-free.mode1` | Blevins 表 8-1 根（clamped-free） | 13.2531 | 13.2532 | 0.0004% | PASS |
| `beam.clamped-free.mode2` | Blevins 表 8-1 根（clamped-free） | 83.0561 | 83.0561 | 0.0000% | PASS |
| `beam.clamped-free.mode3` | Blevins 表 8-1 根（clamped-free） | 232.56 | 232.56 | 0.0001% | PASS |
| `beam.free-free.mode1` | Blevins 表 8-1 根（free-free） | 13.4933 | 13.4933 | 0.0000% | PASS |
| `beam.free-free.mode2` | Blevins 表 8-1 根（free-free） | 37.1948 | 37.1948 | 0.0001% | PASS |
| `beam.free-free.mode3` | Blevins 表 8-1 根（free-free） | 72.9167 | 72.9167 | 0.0000% | PASS |
| `plate.ssss.a_over_b=1` | (pi/2)sqrt(D/(rho h))(1/a^2+1/b^2) | 39.3372 | 39.3372 | 0.0000% | PASS |
| `plate.ssss.a_over_b=1.5` | (pi/2)sqrt(D/(rho h))(1/a^2+1/b^2) | 28.4102 | 28.4102 | 0.0000% | PASS |
| `plate.ssss.a_over_b=2` | (pi/2)sqrt(D/(rho h))(1/a^2+1/b^2) | 24.5857 | 24.5857 | 0.0000% | PASS |
| `radiation.low_ka.f=20` | (32/pi^5) k^2 S（单极子极限） | 0.00336855 | 0.00336482 | 0.1107% | PASS |
| `radiation.low_ka.f=35` | (32/pi^5) k^2 S（单极子极限） | 0.0103162 | 0.0102814 | 0.3374% | PASS |
| `radiation.low_ka.f=50` | (32/pi^5) k^2 S（单极子极限） | 0.0210535 | 0.0209089 | 0.6865% | PASS |
| `radiation.supersonic.f=1000` | 1/sqrt(1-(k_mn/k)^2)（无限板超音速分支） | 1.16684 | 1.18275 | 1.3631% | PASS |
| `radiation.supersonic.f=2000` | 1/sqrt(1-(k_mn/k)^2)（无限板超音速分支） | 1.03494 | 1.03556 | 0.0602% | PASS |
| `radiation.supersonic.f=4000` | 1/sqrt(1-(k_mn/k)^2)（无限板超音速分支） | 1.0084 | 1.00824 | 0.0156% | PASS |
| `radiation.asymptote.sigma_to_1.f=10000` | σ→1（高频渐近，ka>>1） | 1 | 1.00099 | 0.0991% | PASS |
| `radiation.asymptote.sigma_to_1.f=20000` | σ→1（高频渐近，ka>>1） | 1 | 0.998995 | 0.1005% | PASS |
| `transmissibility.r=0.5` | SDOF 传递率闭式 | 1.33204 | 1.33204 | 0.0000% | PASS |
| `transmissibility.r=1` | SDOF 传递率闭式 | 10.0499 | 10.0499 | 0.0000% | PASS |
| `transmissibility.r=3` | SDOF 传递率闭式 | 0.130412 | 0.130412 | 0.0000% | PASS |
| `panel_tl.normal.f=2.83961` | x=omega m''/(2 rho0 c0)=1 => TL=10log10(2)=3.0103 dB（解析恒等式） | 3.0103 | 3.0103 | 0.0000% | PASS |
| `panel_tl.normal.f=500` | 高频渐近 20log10(omega m''/(2 rho0 c0))（质量定律） | 44.9142 | 44.9144 | 0.0003% | PASS |
| `panel_tl.normal.f=1000` | 高频渐近 20log10(omega m''/(2 rho0 c0))（质量定律） | 50.9348 | 50.9348 | 0.0001% | PASS |

### 3.2 线上可达（进程内 TestClient 实测）

模式：`router 复刻（backend.main 导入失败：ModuleNotFoundError）`；6/6 条端点 HTTP 200。


| 端点 | HTTP |
|---|---|
| `/api/v1/calculations/vibration/beam_natural` | 200 |
| `/api/v1/calculations/vibration/plate_natural` | 200 |
| `/api/v1/calculations/vibration/excitation` | 200 |
| `/api/v1/calculations/vibration/isolation` | 200 |
| `/api/v1/calculations/noise/panel_tl` | 200 |
| `/api/v1/calculations/noise/levels` | 200 |

### 3.3 注册表候选（不改 `config/capabilities.json`）


| capability_id | evidence | 实测最大误差 | 缺输入 |
|---|---|---|---|
| `vibration.beam` | REAL | 0.0004% | — |
| `vibration.plate` | REAL | 0.0000% | — |
| `noise.panel_tl` | REAL | 0.0003% | — |
| `noise.radiation_efficiency` | REAL | 1.3631% | — |
| `vibration.isolation_transmissibility` | REAL | 0.0000% | — |
| `noise.panel_tl.field_incidence` | WITHHELD | — | 实测/标准倍频程场入射 TL（ISO 10140/ISO 717）、板件边界条件与加筋布置 |
| `vibration.isolation_transmissibility.ship_damping` | WITHHELD | — | 隔振器实测阻尼比 ζ / 损失因子、隔振器额定动刚度与额定载荷 |

## 4. 图

`artifacts/vibnoise/fig_r27_vibnoise_anchors.png`（两联图）：
左图 = 辐射效率 σ(f) 的数值结果（点线）与三条闭式交叉：无限板超音速 `1/√(1−(k_mn/k)²)`（虚线）、
单极子极限 `(32/π⁵)k²S`（点线）、σ=1 参考线（细实线）、模态吻合 f_cm=515.3 Hz（红竖线）——
可直接看出「低频 ka 依赖段」「f>f_cm 超音速段贴着无限板闭式」「高频 σ→1」三段结构；
右图 = 26 个锚点的逐点相对误差（symlog 纵轴）与预声明阈值短横线，红/绿区分超限与否。

## 5. 注意点（坑与局限）

1. **辐射效率的适用域**：f < f_cm 时无限板模型给 σ=0，而**有限**板仍有边/角辐射（数值非零），
   两者不是"矛盾"而是不同物理；代码里用 `regime` 如实标注，不用闭式覆盖数值。
   高频渐近 σ→1 的预声明适用域是 ka≥20：ka≈10（f=2000 Hz 时 σ=1.0356）偏差 3.6%，
   属**渐近趋近率**限制，已如实记录，未调阈值掩盖。
2. **`backend.main` 在本审计环境不可导入**（缺 `cryptography`；完整 app 还依赖 DB），
   故 C3 用「同一 `include_router` 调用复刻 + 进程内 TestClient」；这一替代已在本节与
   `reachability.json` 的 `reachability_mode` 中标注，实际"是否真连到外部服务"= **否**。
3. **两条 legacy 用例被重定向而非"调绿"**：
   `test_live_endpoint_still_returns_200` 原打 `/api/v1/vibration/predict`——本仓 `backend/`
   内**无该路由定义**（仅见于 docs/ 历史审计），判据实际测的是"运维是否起 8001 进程"；
   `test_live_endpoint_still_input_invariant` 原断言 `all_fields_constant is True`（把 MOCK 指纹
   钉成期望），其自身 message 已写明"若变成输入相关实现请同步更新"。两条都改成对**本仓产品面**
   的断言（200 / 输入相关），强度高于原用例；逐条理由进了各自 docstring。
4. **W6 仓内 baseline 曾被移出树**（`git ls-files artifacts/wiring_census` 为空，
   历史版本在提交 35a45a35c）：本单从该提交**原样恢复** `baseline.json`，并用
   `tools/audit/wiring_census.py` 重生成 `census.json/md`（39 模块 / 55 端点 / 9 个未接线域仍登记在案，
   不是清账）。残余风险：若平台后续再滤除 `artifacts/`，该用例会重新红。
5. **未做**：船体梁两节点频率（`tools/ref_vibration_8001.py` 的 f2v）本轮未并入注册候选；
   结构噪声（SEA/统计能量）与舱室声压级预报仍属该域缺口，需实测吸声/隔声谱支撑。
6. 域契约未变：本轮**不新增 HTTP 路由**（注册表冻结期），辐射效率作为 `panel_tl` 响应的
   附加字段（给 `a,b` 时出现）暴露，故既可在线上验证也不破坏既有 URL。

## 6. 误差判定（逐项回答「是否 <3%」）

| 关键量 | 基准 | 实测最大相对误差 | 是否 <3% |
|---|---|---|---|
| 梁 f1..f3（简支/悬臂/自由-自由） | 闭式 λ_n=nπ、Blevins 表 8-1 根 | 0.0004% | **是**（<1%，满足更严阈值） |
| 板基频 f11（a/b=1/1.5/2） | SSSS Navier 闭式 | 0.0000% | **是** |
| 板正入射 TL（x=1、500 Hz、1000 Hz） | 解析恒等式 3.0103 dB / 高频渐近 20log10(x) | 0.0003% | **是** |
| 辐射效率低频段（ka≤0.25） | 单极子极限 (32/π⁵)k²S | 0.6865% | **是** |
| 辐射效率超音速段（f/f_cm=1.9/3.9/7.8） | 无限板 1/√(1−(k_mn/k)²) | 1.3631% | **是** |
| 辐射效率高频渐近（ka=50/101） | σ→1 | 0.1005% | **是** |
| 隔振传递率（r=0.5/1/3） | SDOF 闭式 | 0.0000% | **是** |
| 场入射 TL | 需实测 ISO 10140/717 谱 | 无基准 | **无基准 → WITHHELD（不判 PASS）** |
| 船用隔振设计判据 | 需实测 ζ | 无基准（默认 ζ=0.05 是假设） | **无基准 → WITHHELD** |

## 7. 结论（能力等级）

- `vibration.beam` / `vibration.plate` / `noise.panel_tl` / `noise.radiation_efficiency` /
  `vibration.isolation_transmissibility`：**REAL**（均有 3 条以上独立闭式锚，误差 ≤1.363%）；
- `noise.panel_tl.field_incidence`、`vibration.isolation_transmissibility.ship_damping`：
  **WITHHELD**（缺实测谱/实测阻尼比，不填充、不升级）；
- 域可达性：6/6 端点进程内 200，`VIBRATION_DOMAIN_STATUS.pass=True`（复刻 app）；
  缺域/缺端点两条负对照测试均红（fail-closed 生效）。

生成时间：2026-11-15T23:51:42+00:00
