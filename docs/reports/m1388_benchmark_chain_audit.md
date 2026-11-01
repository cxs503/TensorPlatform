# 对标链路（benchmark_validate）验证欠账清偿报告：破损传输 + 参考数据集一致性审计

**Mission**: m1388（tp-impl-7 自主工时） · **Branch**: `agent/m1388` · **Date**: 2026-11-01
**Markdown 报告正文（平台报告同步发布）** · 工件：`artifacts/m1388/`
**判定**：链路缺陷 **FIXED**（有回归测试）；参考数据集 **HONEST-FAIL / WITHHELD**（12/12 不可用）

---

## 1. 案例介绍

### 1.1 物理背景
平台「对标算例」KPI（guide_tracker：`对标算例 ≥10`、`静水阻力 ≤5%`）依赖
`POST /api/v1/calculations/benchmark_validate`：把平台阻力模型算出的总阻力系数
`Ct` 与实验参考 `Ct_ref` 比较，误差 ≤15% 判 pass。该链路是「平台算得准不准」的
唯一自动判据，因此它本身必须先被验证。

### 1.2 对象与参数
| 项 | 内容 |
|---|---|
| 阻力模型 | `S = π·B·L·(0.5+0.25Cb)`；ITTC-1957 `Cf=0.075/(log10Re−2)²`；`Cr=0.002(1+2Cb)Fn^2.5`（SUBOFF 类轴对称体拟合） |
| 参考数据 | `backend/data/benchmark_reference.json`，12 条（SUBOFF / DTMB-5415 / KVLCC2 各 4 条） |
| 几何预设 | SUBOFF L=4.356 m；DTMB-5415 L=5.720 m；KVLCC2 L=320 m |
| 水物性 | ρ=998 kg/m³，ν=1.004×10⁻⁶ m²/s（模型尺度）；全尺度 Re 仅作量级判断 |

---

## 2. 怎么做的

### 2.1 缺陷复现（改代码之前）
1. 直接调用 `_call_resistance_api("KVLCC2", 7.717, …)` → 返回 `None`（远端 8002 不可达）。
2. 对 KVLCC2 全部 4 条用例跑 `benchmark_validate`：`calc_ct=0.0`、`error_pct=999.0`、
   `pass=false`，并触发错误结论 `algorithm_upgrade_recommended=true`
   （“Holtrop 不适合 KVLCC2，建议改用 RANS”）。
3. 读代码确认根因不是“服务没起”，而是**schema 不匹配**：请求体发 `speed`（端点收 `speed_ms`）、
   响应读 `ct`/`method`（端点从未返回这两个字段，只返回 `total_resistance_n` 等）。
   即：即使 8002 在线，`calc_ct` 也恒为 0。**该链路在合并时即为 MOCK 行为，且被 mock 测试掩盖。**

### 2.2 修复（最小改动，单一物理源）
| 文件 | 改动 |
|---|---|
| `backend/services/ship_resistance.py`（新） | 抽出纯函数物理核（Cf/Re/Fn/Cr/Rt/Ct、Schoenherr 交叉线、Ct←Rt 恒等式），端点与对标链路共用 |
| `backend/api/calculations.py` | `/calculations/resistance` 改为调用物理核；响应新增 `method` / `wetted_surface_m2` / `total_resistance_coefficient`（数值不变，回归测试钉住） |
| `backend/api/domain/ship_benchmark.py` | 对标链路默认**进程内**计算（`transport=in_process`），HTTP 仅在 `SHIP_BENCHMARK_REMOTE=1` 时启用并修好 schema；新增数据集门禁：只有 `CONSISTENT` 行可计入 pass；新增 `GET /calculations/benchmark_cases_audit` |
| `backend/services/benchmark_dataset_audit.py`（新） | 三条免外部数据的一致性判据（I1 Ct–Rt 恒等式、I2 Froude 恒等式、I3 摩擦下限） |
| `backend/tests/feat/test_benchmark_chain_m1388.py`（新） | 20 条无 mock 测试 |
| `scripts/audit_benchmark_chain_m1388.py`（新） | 生成工件 JSON + 三张图（程序化，零手抄） |

### 2.3 数据集门禁判据（机器可算）
- **I1** `Ct = Rt/(0.5ρSV²)`，同一条用例内部，容差 1%。
- **I2** `Fr = V/√(gL)`，L 取该行 `geometry.length_m`（缺则用几何预设），容差 2%。
- **I3** `Ct ≥ Cf_ITTC57(Re)`：总阻力系数低于平板摩擦线物理上不可能（形状因子 ≥1）。
- 状态：`CONSISTENT`（三查全过）/ `PARTIAL`（I1 无法跑，缺湿面积）/ `INCONSISTENT`（任一失败）；
  **非 `CONSISTENT` 一律 `withheld`，不参与 pass 率、不产生精度结论**。

---

## 3. 计算结果（全部由 `artifacts/m1388/*.json` 程序注入）

### 3.1 参考数据集审计（`artifacts/m1388/benchmark_audit.json`）

| case | ship | V (m/s) | Ct_ref | 状态 | 失败/未跑检查 |
|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | 3.0 | 0.0038 | INCONSISTENT | I1_ct_rt_identity |
| SUBOFF-02 | SUBOFF | 4.0 | 0.0040 | INCONSISTENT | I1_ct_rt_identity |
| SUBOFF-03 | SUBOFF | 5.0 | 0.0042 | INCONSISTENT | I1_ct_rt_identity |
| SUBOFF-04 | SUBOFF | 6.0 | 0.0044 | INCONSISTENT | I1_ct_rt_identity |
| DTMB5415-01 | DTMB-5415 | 2.572 | 0.0045 | INCONSISTENT | I2, I1 |
| DTMB5415-02 | DTMB-5415 | 5.144 | 0.0038 | INCONSISTENT | I2, I1 |
| DTMB5415-03 | DTMB-5415 | 10.289 | 0.0035 | INCONSISTENT | I2, I1 |
| DTMB5415-04 | DTMB-5415 | 15.433 | 0.0038 | INCONSISTENT | I2, I1 |
| KVLCC2-01…04 | KVLCC2 | 7.202–8.231 | 0.0028–0.0032 | PARTIAL | I1 未跑（缺湿面积） |

汇总：`total=12`，`CONSISTENT=0`、`INCONSISTENT=8`、`PARTIAL=4`，`withheld=12`，
`verifiable_accuracy_claim=false`。

两处**硬矛盾**（不是精度问题，是数据自相矛盾）：
1. **SUBOFF**：同一行的 `Ct` 与 `Rt` 相差 **7.40 / 8.24 / 9.02 / 9.53 倍**（见 `fig_suboff_identity.png`）。
   `S=5.42 m²`、`V=3 m/s`、`Rt=12.5 N` 反算 `Ct=5.1×10⁻⁴`，而该行 `Ct` 写 0.0038。
   `Rt` 列（12.5/21.0/31.5/45.0 N）呈近似线性，明显为占位值。
2. **DTMB-5415**：`V=2.572 m/s`、`L=5.72 m` 对应 `Fr=0.343`，该行却声明 `Fr=0.061`
   —— 声明值反推的有效船长约 **181 m**，与同一文件几何预设的模型尺度差 **32 倍**。

### 3.2 实链路对标结果（进程内物理，无 mock；`chain_results.json`）

| ship | cases | verified | calc Ct (min–max) | ref Ct (min–max) | 误差幅度 | pass |
|---|---|---|---|---|---|---|
| SUBOFF | 4 | 0 | 0.00346–0.00592 | 0.00380–0.00440 | 1.2%–34.6% | 0 |
| DTMB-5415 | 4 | 0 | 0.00309–0.02669 | 0.00350–0.00450 | 7.5%–602.3% | 0 |
| KVLCC2 | 4 | 0 | 0.00141–0.00142 | 0.00280–0.00320 | 49.5%–56.1% | 0 |

修复前后对比：`calc_ct` 0.0 → 物理值；`transport` `unavailable` → `in_process`；
`error_pct` 999 → 有限值；错误建议不再触发。

### 3.3 摩擦线独立交叉校核（`friction_crosscheck.json`）

| Re | Cf ITTC-1957 | Cf Schoenherr | 相对差 |
|---|---|---|---|
| 1×10⁶ | 4.687500e-03 | 4.409433e-03 | 6.31% |
| 1×10⁷ | 3.000000e-03 | 2.934279e-03 | 2.24% |
| 1×10⁸ | 2.083333e-03 | 2.072030e-03 | 0.55% |
| 1×10⁹ | 1.530612e-03 | 1.530937e-03 | 0.02% |

ITTC-1957 与 Schoenherr（1932）是两条独立得到的历史摩擦线，不是自证：在 10⁷–10⁹（对标船型相关区间）
一致到 ≤2.24%；10⁶ 处分歧 6.31%，属已知的低 Re 偏离（测试中作为**有界事实**断言，不强行通过）。

---

## 4. 结果图

- `artifacts/m1388/fig_data_quality.png` —— 12 用例 × 3 判据一致性矩阵（绿 PASS / 红 FAIL / 黄 n/a）。
- `artifacts/m1388/fig_ct_vs_fr.png` —— 三个船型的 Ct–Fr 曲线：平台模型 Ct、ITTC-57 摩擦下限、参考数据点
  （参考点已审计为 WITHHELD，故只作可视对照，不作精度结论）。
- `artifacts/m1388/fig_suboff_identity.png` —— SUBOFF 同一行 `Ct` 列与由 `Rt` 列反算的 `Ct`，标注 7.4–9.5 倍偏差。

---

## 5. 注意点（坑与局限）

1. **“测试绿”不等于“链路通”**：原 374 行测试全部 `patch` 掉 `_call_resistance_api`，因此
   schema 错配与 8002 不可达同时存在也照样 100% 绿。修法：新增**无 mock** 测试，断言 `calc_ct>0`
   与 `transport=in_process`。
2. **测试断言可以固化缺陷**：原 `test_all_fail_triggers_upgrade_recommendation` 要求
   “全 fail 时必须建议换算法”，而当时的“全 fail”只是传输失败的产物。已改为断言
   “无可用参考行 → 不下算法判决”。
3. **门禁不能救数据**：`verified_cases=0` 不是保守，而是事实——12 条参考行无一可自洽。
   平台目前的 `静水阻力 ≤5%` KPI **不应**以这份数据宣称任何数值（本单把它明确降级为 WITHHELD）。
4. **模型适用范围**：`Cr=0.002(1+2Cb)Fn^2.5` 是 SUBOFF 类轴对称体拟合。KVLCC2 上 Ct 平台给
   0.00141 vs 参考 0.0028–0.0032（−50%），DTMB 高 Fr 处 +602%——**根本原因是没有兴波项/形状因子**，
   不是标定差。若要对标商船，需实装 Holtrop-Mennen（含 Cw、1+k、附体）。
5. **残余风险**：I1/I2 只能抓“自相矛盾”，抓不到“全体自洽但整体抄错”。真正的数据可信度仍需
   回到原始文献（Groves 1989 / Roddy 1990 / SIMMAN 2008 / ITTC 5415 报告）逐点复核，本单未做（无原文）。

---

## 6. 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 平台值 | 相对误差 | <3% ? |
|---|---|---|---|---|
| Cf @ Re=10⁸ | Schoenherr 2.072030e-03 | 2.083333e-03 | 0.55% | ✅ 是 |
| Cf @ Re=10⁹ | Schoenherr 1.530937e-03 | 1.530612e-03 | 0.02% | ✅ 是 |
| Cf @ Re=10⁷ | Schoenherr 2.934279e-03 | 3.000000e-03 | 2.24% | ✅ 是 |
| Cf @ Re=10⁶ | Schoenherr 4.409433e-03 | 4.687500e-03 | 6.31% | ❌ 否（已知低 Re 偏离，已文档化） |
| Ct 端到端（SUBOFF 3 m/s） | 数据集 0.0038 | 0.00346 | 8.9% | ❌ 否，**且基准 WITHHELD（I1 失败 7.4×）** |
| Ct 端到端（KVLCC2 7.717 m/s） | 数据集 0.0030 | 0.00141 | 53.0% | ❌ 否，**基准 PARTIAL（无湿面积）** |
| 链路数值可用性 | 修复前 `calc_ct` 恒 0 | 有限物理值 12/12 | — | ✅ 是（缺陷已消除） |

**结论**：摩擦子模型对独立解析线 <3%（Re≥10⁷）✓；端到端阻力精度**无法判定**，因为参考数据
本身不可用——按 honest-FAIL 如实记录，不粉饰。建议后续单：①按原始文献重建 12 条参考行
（含湿面积、湿表面积、每行尺度声明），②实装 Holtrop-Mennen 商船阻力方法，再谈 ≤5% KPI。

---

## 7. 验证与复现

```bash
cd backend && ENVIRONMENT=test DATABASE_URL=sqlite:///:memory: \
  python3 -m pytest --noconftest tests/feat/test_benchmark_chain_m1388.py tests/feat/test_ship_benchmark.py -q
# 41 passed（新增 20 条无 mock 测试 + 存量 21 条，其中 1 条按新语义改写）
cd backend && ENVIRONMENT=test DATABASE_URL=sqlite:///:memory: python3 ../scripts/audit_benchmark_chain_m1388.py
# 生成 artifacts/m1388/{benchmark_audit,chain_results,friction_crosscheck}.json + table.md + 3 PNG
```