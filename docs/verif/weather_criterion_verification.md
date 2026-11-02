# 验证欠账：IMO 天气衡准（weather criterion）对 IS Code 2008 A.2.3 的逐条复核

- 复核对象：`POST /api/v1/dynamic-stability/weather-criterion`（summary「天气衡准检查」，description「IMO天气衡准」）
- 实现位置：`backend/api/dynamic_stability.py :: WeatherCriterionChecker.check`（serving copy `/srv/tensor/ship-workspace/...`）
- 独立参考实现：`tools/verif/weather/imo_weather.py`（零平台依赖，双数值路径）
- 探针 / 图 / 数字：`tools/verif/weather/probe_weather.py` → `artifacts/weather_probe.json`、`fig_gz_areas.png`、`fig_ratio.png`
- 回归测试：`tests/test_weather_criterion_reference.py`（3 算例 + 10 条判据/合同测试，全绿）
- 欠账来源：`docs/verification/GZ_CROSS_CURVE_AUDIT.md` §7 明确列「未覆盖：…风暴横摇衡准（weather criterion）」

## 1. 规范要求（IS Code 2008, MSC.267(85), Part A §2.3）

| 要素 | 规范表达 | 置信度 |
|---|---|---|
| 定常风压 | P = 504 Pa | CONF_HIGH |
| 定常风倾力臂 | lw1 = P·A·Z / (1000·g·Δ)  [m]，Δ[t]，g=9.80665 | CONF_HIGH |
| 阵风力臂 | lw2 = 1.5·lw1 | CONF_HIGH |
| 定常风平衡角 | θ0: GZ(θ0) = lw1 | CONF_HIGH |
| 横摇回摆角 | θ1 = 109·k·X1·X2·√(r·s)（表 2.3.4） | CONF_MED（代码表未转录，故用 θ1 扫描） |
| 面积 a | ∫ θ1→θC (lw2 − GZ) dθ，θC = GZ 与 lw2 的上风侧交点 | CONF_HIGH |
| 面积 b | ∫ θC→θ2 (GZ − lw2) dθ | CONF_HIGH |
| 终止角 | θ2 = min(下风侧交点, θf, 50°) | CONF_HIGH |
| 判据 | b ≥ a（等价 b/a ≥ 1） | CONF_HIGH |

设计纪律：承担 `<3%` 判决的量只用 CONF_HIGH 定义式；θ1 代码表属 CONF_MED，故以 θ1∈[0°,30°] 扫描给出判据族，
任一 θ1 下结论同向才算判据稳健——这足以复核「平台表达是否等于规范表达」，因为平台实现里既没有 θ1 也没有 lw2。

## 2. 算例（几何/参数全表）

| 算例 | L [m] | B [m] | T [m] | Δ [t] | 受风面积 A [m²] | 风压中心高 Z [m] | GZ_max [m] | θf [°] |
|---|---|---|---|---|---|---|---|---|
| case1_stiff_high_windage | 100 | 18 | 6.0 | 6000 | 1200 | 9 | 0.520 | 75.0 |
| case2_borderline_false_pass | 60 | 13 | 3.2 | 1800 | 700 | 10 | 0.340 | 60.0 |
| case3_gross_noncompliant | 60 | 13 | 3.2 | 1800 | 1500 | 10 | 0.340 | 60.0 |

三个算例覆盖判据的三个区：`case1` 稳性富余（θ1 处 GZ 已高于 lw2 → a=0）、`case2` 边界船（b 只剩薄层）、
`case3` 明显不满足（lw2 超过 GZ 峰值 → b=0）。

## 3. 结果：参考实现 vs 平台算式（θ1 = 15° 代表点）

| 算例 | lw1 [m] | lw2 [m] | θC [°] | θ2 [°] | a_ref | b_ref | b/a_ref | 合规_ref | b/a_pchip | a_plat | b_plat | b/a_plat | 合规_plat |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| case1_stiff_high_windage | 0.09251 | 0.13876 | 8.67 | 50.00 | 0.00000 | 0.18524 | ∞ (a=0) | True | ∞ (a=0) | 0.08070 | 0.40492 | 5.017 | True |
| case2_borderline_false_pass | 0.19986 | 0.29980 | 24.97 | 40.02 | 0.00804 | 0.00529 | 0.658 | False | 0.983 | 0.17436 | 0.21118 | 1.211 | True |
| case3_gross_noncompliant | 0.42828 | 0.64242 | — | — | 0.31890 | 0.00000 | 0.000 | False | 0.000 | 0.37362 | 0.21118 | 0.565 | False |

`*_plat` 列为逐行复刻平台 `WeatherCriterionChecker.check` 的算式（取证用），其与实测 API 返回值在 2% 内一致
（`tests/test_weather_criterion_reference.py::test_platform_false_pass_regression` 钉住）。

## 4. 平台表达与规范的逐条映射（缺陷清单）

| # | 规范要素 | 平台做法 | 判定 |
|---|---|---|---|
| 1 | lw1 = P·A·Z/(1000·g·Δ) | 同式，但 g 硬编码 9.81 | 通过（偏差 0.034%） |
| 2 | lw2 = 1.5·lw1（阵风） | **缺失**，area_a 直接用 lw1 | 不通过（力臂低 33.3%，判据单向放宽） |
| 3 | 横摇回摆角 θ1 | **缺失**（隐式 θ1 = 0°） | 不通过（忽略上风侧横摇，a 区被低估） |
| 4 | θ2 = min(下风侧交点, θf, 50°) | **硬编码 50°** | 不通过（与 GZ 曲线解耦；case2 真值 θ2 = 40.0°） |
| 5 | b = ∫θC→θ2 (GZ − lw2) | 对**整条 GZ 曲线 0→末采样角**做梯形积分，含 GZ<lw2 与 θ>θ2 段 | 不通过（b 被系统性放大） |
| 6 | a = ∫θ1→θC (lw2 − GZ) | 用 lw1 × 50° 的矩形 | 不通过（力臂、区间、形状三重错配） |
| 7 | 判据 b ≥ a | 同形式 ratio ≥ 1 | 形式通过、输入错 → 结论错 |
| 8 | API 合同：`GZ_curve: array<object>` | 实现只认键 `heel`/`GZ`，其余键抛 KeyError → HTTP 500 | 不通过（合同缺陷，见 §6） |
| 9 | 悬置常数 `assess/ratio` 缺省 | `area_a==0 → ratio=10`、`downflooding=50` 常量 | 不通过（未声明启发式） |

## 5. 误差判定（关键量 vs 规范基准，逐项是否 <3%）

| 算例 | lw1 [m] | 平台 a 相对误差 | 平台 b 相对误差 | 平台 b/a 相对误差 | 合规判决 |
|---|---|---|---|---|---|
| case1_stiff_high_windage | 0.09251 | — | +118.6% | +nan% | 一致 |
| case2_borderline_false_pass | 0.19986 | +2069.5% | +3892.7% | +84.0% | 翻转 (false PASS) |
| case3_gross_noncompliant | 0.42828 | +17.2% | — | — | 一致 |

补充量化：

- **lw1 定义式**：机械自检 `0.028908955` m vs 手算 `0.028908955` m，相对误差 `0.00e+00` → **<3% 满足**；平台仅因 `g=9.81` 引入 `0.034%` 偏差，仍 **<3%**。
- **阵风力臂 lw2**：平台缺失 1.5 因子 → 力臂相对偏差 `-33.3%` → **不满足 <3%**（单项即超限 11 倍）。
- **b/a（case2 边界船）**：平台 `1.211` vs 规范 `0.658` → **相对误差 +84.0%，不满足 <3%**；且判决从「不满足」翻成「满足」（**false PASS**）。
- **合规判决一致性**：3 个算例中 1 个**判决翻转**（case2），另外 2 个虽结论相同但数值不可比（case1 规范 a=0 使 b/a 无界，平台给出有限值 5.02；case3 规范 b=0）。
- **θ1 稳健性（case1 扫描）**：0°: 17.639 | 5°: 98.357 | 10°: ∞ | 15°: ∞ | 20°: ∞ | 25°: ∞ | 30°: ∞ → 在 θ1∈[0°,30°] 全扫描下「b/a 巨大/自动满足」的结论不变，故 case1 的通过判决对代码表转录误差不敏感。
- **数值路径自校**：参考实现 linear 与 PCHIP 两条独立路径的 b/a 相对差，case1/case3 为 `1.383%`/`0.000%`；**case2 为 `48.8%`**——边界船 b 只剩薄层，b/a 对 GZ 插值模型敏感。为此本报告对 case2 **只主张判决（b<a），不主张数值**；工程建议：边界船 GZ 采样间隔应 ≤5°，且 b/a 应同时给出两路径区间。

## 6. 附带发现的合同缺陷（可独立修）

1. `WeatherCriterionInput.GZ_curve: array<object>` 对键名零约束，而实现硬取 `["heel"]`/`["GZ"]`：
   `{"angle":..,"gz":..}` 的**合法载荷返回 500**（`alt_keys=500`，见 `tests::test_platform_500_on_schema_legal_keys`）。应改为显式 schema（`GZPoint{heel: float, GZ: float}`）并在缺键时返回 422。
2. `displacement` 只用于力臂，未与 L/B/T/Cb 做一致性校验（沿用 GZ 审计同一建议）。
3. 响应 `method` 恒为「天气衡准检查」，无 `validation_status`，无法区分「规范实现」与「启发式」——建议改名 `IMO_ISCODE2008_A2.3` 并补 `standard_ref`。

## 7. 结论与证据等级

- 该端点对「IMO 天气衡准」的能力声明 → **WITHHELD**：算式可执行、数值有限，但**与规范不同构**（缺 1.5 阵风臂、缺 θ1、θ2 硬编码、a/b 积分域错配），且存在 **false PASS** 与 **500 合同缺陷**。
- 在 `case2` 这类边界船上，平台把「不满足」判成「满足」——**安全相关结论不可用于合规判定**，直至按本报告 §4 修正。
- 修复验收判据（建议作为门禁）：`tests/test_weather_criterion_reference.py` 全绿 + case2 `b/a` 与规范相对误差 <3% + 合法键名不 500。

## 8. 复现

```bash
python3 tools/verif/weather/probe_weather.py        # 生成 artifacts/weather_probe.json + 两张图
python3 tools/verif/weather/make_doc.py             # 由工件重生成本文件（数字零手抄）
python3 -m pytest tests/test_weather_criterion_reference.py -q
```

## 9. 局限

- θ1 未按 IS Code 表 2.3.4 转录（CONF_MED），以扫描替代；因此本报告判定「平台缺 lw2/θ1 结构」成立，但**不**给出与规范逐位对齐的单点 b/a。
- 未涉及破损稳性、自由液面修正、非线性横摇阻尼；GZ 输入取自平台/外部生成，本单不重算 GZ 曲线（另有 `GZ_CROSS_CURVE_AUDIT.md` 覆盖）。
- case2 数值对插值敏感（见 §5），修正后仍需用实船密采样 GZ 复核。
