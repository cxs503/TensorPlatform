# 阻力 / 稳性链路验证报告（验证欠账清偿）

生成日期：2026-11-01　工件：`docs/validation/results_2026-11-01.json`（本文件所有数字由该 JSON 程序化生成，零手抄）

## 1. 背景与缺陷审计

`/calculations/resistance` 此前声称 REAL（Holtrop-Mennen），实测存在以下可证缺陷：

| 编号 | 缺陷 | 证据 | 结论 |
|---|---|---|---|
| D1 | 湿表面积 `S=pi*B*L*(0.5+0.25Cb)` 是回转体公式误用于排水船 | KVLCC2 得 S=40958 m2，超过同 L/B/T 箱形船湿表面积上界 34285 m2（+19.5%，物理不可能） | 几何级错误 |
| D2 | 残余阻力 `Cr=0.002(1+2Cb)Fn^2.5` 无文献出处 | 该值 3.1e-5，与实验所需 1.0e-3 量级差 30 倍以上 | 未引证经验拟合 |
| D3 | 无形状因子 (1+k)、无相关补贴 C_A、无波浪阻力 | 见第 5 节误差表 | 系统性低估 |
| D4 | 基准门 `benchmark_validate` 走 HTTP 到 8002 端口读 `ct`，该服务不存在且真实端点从不返回 `ct` | `calc_ct` 恒为 0.0，全案例 100%/999% 误差 | 门禁失效（伪结论） |
| D5 | 参考数据集自相矛盾 | DTMB-5415 行 table Fn 与 V/sqrt(gL) 差 5.6 倍（5.72m 模型 V=10.3 m/s → Fn=1.37）；SUBOFF 行 Rt 与 Ct*q*S 差 86-89% | 数据不可直接采信 |
| D6 | 稳性墙侧公式无适用域判断 | GM=1, BM=3, 60 度时 GZ=7.29 m（大于常见船宽） | 外推无警告 |
| D7 | 该链路零测试覆盖 | `grep calc_resistance backend/tests` 无命中 | 验证欠账 |

## 2. 修复内容

- 新增 `backend/services/resistance_model.py`：唯一权威模型（ITTC-1957 摩擦线 + 形状因子 (1+k) + ITTC-1978 相关补贴 + Holtrop-Mennen 1982 湿表面积）；波浪阻力显式标 `NOT_SUPPORTED`，缺失时输出**粘性下界**。
- 新增 `backend/services/resistance_benchmark.py`：门禁改为**进程内**调用同一模型，并先审计参考数据行（Fn 一致性 / Rt-Ct 一致性 / 湿表面积上界 / 船型 Fn 合理性），不一致行剔除并列入 `excluded_case_ids`。
- `/calculations/resistance` 与 `/calculations/benchmark_validate` 共用同一代码路径（交叉一致性测试锁定），响应新增 `ct / wetted_surface_m2 / form_factor / correlation_allowance / evidence / notes`；`method=legacy` 保留旧式便于回归对比。
- 新增 `backend/services/stability_hydrostatics.py`：墙侧 GZ 闭式解 + 独立数值静力学 + 甲板边入水限制角；`/calculations/stability` 新增可选 `beam_m/freeboard_m` 与 `within_wall_sided_validity` 判定。
- 新增 44 项回归测试 `backend/tests/test_resistance_validation.py`（`PYTHONPATH=. pytest --noconftest tests/test_resistance_validation.py` 全绿）。
- 可复现脚本 `scripts/validation/resistance_stability_validation.py` → 图 + 结果 JSON。

## 3. 摩擦线交叉校核（ITTC-1957 vs Schoenherr 1932）

| Re | C_F ITTC-57 | C_F Schoenherr | 相对差 |
|---|---|---|---|
| 1e+06 | 0.004687 | 0.004409 | +6.31% |
| 3e+06 | 0.003704 | 0.003566 | +3.85% |
| 1e+07 | 0.003000 | 0.002934 | +2.24% |
| 3e+07 | 0.002479 | 0.002450 | +1.20% |
| 1e+08 | 0.002083 | 0.002072 | +0.55% |
| 3e+08 | 0.001775 | 0.001772 | +0.17% |
| 1e+09 | 0.001531 | 0.001531 | -0.02% |

两条独立经验线在 Re>=1e8 收敛到 0.17% / 0.02%，证明摩擦线实现正确（Re=1e6 处 6.31% 属两线固有差异）。图：`validation/figures/fig_friction_lines.png`

## 4. 湿表面积：旧式 vs 修复 vs 物理上界

| 船型 | 旧式 S (m2) | Holtrop S (m2) | 箱形上界 (m2) | 旧式相对上界 |
|---|---|---|---|---|
| KVLCC2 | 40958.43 | 26710.98 | 34284.80 | +19.47% |
| SUBOFF(axisym.) | 4.36 | 4.16 | 7.15 | -39.08% |

图：`validation/figures/fig_wetted_surface.png`（Cb 扫描下旧式在 Cb>=0.62 越过物理上界）

## 5. 基准门定量结果（修复后，同一模型）

模型 = 粘性下界（无波浪阻力项），故 `error_pct` 是真实偏差的**上界**。

| 案例 | Fn | C_T 平台 | C_T 实验 | 误差 | 判定 | 备注 |
|---|---|---|---|---|---|---|

| KVLCC2-01 | 0.129 | 0.00175 | 0.00280 | 37.5% | HONEST-FAIL | - |
| KVLCC2-02 | 0.138 | 0.00172 | 0.00300 | 42.8% | HONEST-FAIL | - |
| KVLCC2-03 | 0.142 | 0.00170 | 0.00310 | 45.1% | HONEST-FAIL | - |
| KVLCC2-04 | 0.147 | 0.00169 | 0.00320 | 47.2% | HONEST-FAIL | - |
| SUBOFF-01 | 0.459 | 0.00307 | 0.00380 | 19.2% | HONEST-FAIL | RT_CT_MISMATCH(Ct_table=0.00380, Rt/(qS)=0.00051, 86%) |
| SUBOFF-02 | 0.612 | 0.00293 | 0.00400 | 26.9% | HONEST-FAIL | RT_CT_MISMATCH(Ct_table=0.00400, Rt/(qS)=0.00049, 88%) |
| SUBOFF-03 | 0.765 | 0.00282 | 0.00420 | 32.9% | HONEST-FAIL | RT_CT_MISMATCH(Ct_table=0.00420, Rt/(qS)=0.00047, 89%) |
| SUBOFF-04 | 0.918 | 0.00274 | 0.00440 | 37.8% | HONEST-FAIL | RT_CT_MISMATCH(Ct_table=0.00440, Rt/(qS)=0.00046, 89%) |
| DTMB5415-01 | 0.343 | 0.00303 | 0.00450 | 32.5% | EXCLUDED_INCONSISTENT | FROUDE_MISMATCH(table=0.061,V/sqrt(gL)=0.343) |
| DTMB5415-02 | 0.687 | 0.00271 | 0.00380 | 28.7% | EXCLUDED_INCONSISTENT | FROUDE_MISMATCH(table=0.122,V/sqrt(gL)=0.687); UNPHYSICA |
| DTMB5415-03 | 1.373 | 0.00244 | 0.00350 | 30.4% | EXCLUDED_INCONSISTENT | FROUDE_MISMATCH(table=0.244,V/sqrt(gL)=1.374); UNPHYSICA |
| DTMB5415-04 | 2.060 | 0.00229 | 0.00380 | 39.7% | EXCLUDED_INCONSISTENT | FROUDE_MISMATCH(table=0.366,V/sqrt(gL)=2.060); UNPHYSICA |

汇总：

- **KVLCC2**：判读 4 例、剔除 0 例；平均误差 43.15%、最大 47.24%；通过 0/4
- **SUBOFF**：判读 4 例、剔除 0 例；平均误差 29.17%、最大 37.76%；通过 0/4
- **DTMB-5415**：判读 0 例、剔除 4 例；平均误差 None%、最大 None%；通过 0/0

图：`validation/figures/fig_gate_ct_comparison.png`

## 6. 稳性：闭式解 vs 独立数值静力学

箱形剖面 B=20.0 m, T=8.0 m, 型深=12.0 m, KG=6.0 m → BM=4.1667 m, GM=2.1667 m, 甲板边入水角 21.801 度

| 横倾角 | GZ 数值 (m) | GZ 闭式 (m) | 绝对差 (m) | 相对差 | 适用域 |
|---|---|---|---|---|---|
| 0.5° | 0.018909 | 0.018909 | 1.5e-16 | 0.0000% | 是 |
| 2.0° | 0.075704 | 0.075704 | 1.1e-16 | 0.0000% | 是 |
| 5.0° | 0.190227 | 0.190227 | 1.1e-16 | 0.0000% | 是 |
| 10.0° | 0.387485 | 0.387485 | 5.0e-16 | 0.0000% | 是 |
| 15.0° | 0.599488 | 0.599488 | 6.7e-16 | 0.0000% | 是 |
| 20.0° | 0.835437 | 0.835437 | 4.4e-16 | 0.0000% | 是 |
| 21.0° | 0.886477 | 0.886477 | 4.4e-16 | 0.0000% | 是 |
| 22.0° | 0.938907 | 0.939043 | 1.4e-04 | 0.0144% | 否 |
| 25.0° | 1.074087 | 1.107121 | 3.3e-02 | 2.9838% | 否 |
| 30.0° | 1.228253 | 1.430556 | 2.0e-01 | 14.1416% | 否 |
| 45.0° | 1.402428 | 3.005204 | 1.6e+00 | 53.3333% | 否 |
| 60.0° | 1.141667 | 7.289047 | 6.1e+00 | 84.3372% | 否 |

结论：适用域内两独立方法一致到机器精度（1e-15 m 量级）；越界后闭式解发散（22° 0.01%、25° 2.98%、30° 14.14%、45° 53.33%、60° 84.34%），故新增甲板边限制角判定。图：`validation/figures/fig_gz_validity.png`

## 7. 误差判定（逐项回答是否 <3%）

| 关键量 | 基准 | 误差 | <3%? |
|---|---|---|---|
| C_F（ITTC-57 实现） | Schoenherr 1932，Re>=1e8 | 0.17% / 0.02% | 是 |
| C_F（同左） | Schoenherr，Re=1e6 | 6.31% | 否（两经验线固有差异，非实现误差） |
| 湿表面积 S（KVLCC2） | 箱形上界（严格物理界） | 上界内 22.2% | 结构性满足，非等值基准 |
| GZ（适用域内） | 独立数值静力学 | <=1e-15 m | 是 |
| GZ（越界 60°） | 独立数值静力学 | +84% | 否（已加适用域判定） |
| C_T（KVLCC2，Fn 0.13-0.15） | SIMMAN 2008 实验 | 平均 43.15% | 否（honest FAIL） |
| C_T（SUBOFF） | Roddy 1990 | 平均 29.17% | 否（honest FAIL） |

## 8. 注意点与局限
1. **波浪阻力量化缺口**：KVLCC2 需补 C_W≈1.0-1.5e-3 才能闭合 37-47% 的 C_T 缺口，旧式经验项仅 3.1e-5（小 30 倍以上）。平台当前没有波浪阻力模块，C_T 只能宣称粘性下界；据此把 `calc.resistance` 的证据等级由 REAL 下调为**分量混合**（摩擦/湿表面积 REAL、形状因子 ESTIMATE、波浪 NOT_SUPPORTED）。
2. **形状因子 ESTIMATE**：单桨运输船 1+k≈1.08-1.20 为类表默认值；测试给出 [1.05,1.25] 带内 C_T 展布 <20%，误差须按带宽读。
3. **SUBOFF 行参考数据不可全信**：Rt 列与 Ct*q*S 差 86-89%，本轮仅用 Ct 列并打警告；DTMB-5415 行 Fn 与速度矛盾（Fn 至 2.06），整行剔除、不计入通过率。
4. **Cm/Cwp 默认值**为经验回归（ESTIMATE），敏感性测试显示对 S 影响 <3%。
5. 全 app TestClient 路径在本沙箱不可用（cryptography 缺失、api/__init__ 触发 SQLAlchemy 弃用异常），端点测试改用按路径加载模块的直接调用；不影响模型验证覆盖。
6. 修复不是把数调到对：模型未对任何基准做拟合，残差如实保留并公开。

## 9. 后续（建议派单）
1. 实现 Holtrop-Mennen 1982 波浪阻力项（c1..c17 系数需回原文核对）或细长体 Michell 积分；验收判据：KVLCC2 Fn 0.13-0.15 的 C_T 误差 <=15%（当前 37-47%）。
2. 重建基准数据集：每行给出处页码 + 独立复算的 Rt/Ct/S 一致性检查（本模块 `audit_case` 可直接当门禁）。
3. 稳性：以按实际型线数值积分的 GZ 曲线替代墙侧公式（含大倾角与进水角）。

