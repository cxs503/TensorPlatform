# 操纵性（Manoeuvring）域验证综合报告：MMG 模型自洽 + IMO 判据 + 平台端点证据链

**任务**: m1763 (自找活·tp-qa-1 verifier 席)  
**日期**: 2026-11-01  
**仓**: TensorPlatform  
**分支**: agent/m1763  
**作者**: TensorPlatform Research Agent (qwen3.6-plus)

---

## ① 案例介绍（物理背景/几何/参数表）

操纵性是 IMO 强制衡准域（MSC.137(76)），涵盖回转性（战术直径 D_T/L ≤ 5.0、进距 A_D/L ≤ 4.5）
与航向稳定性（Z 形超越角）。平台操纵性模块由三层组成：

| 层 | 模块 | 证据等级 | 说明 |
|---|---|---|---|
| 内核 | `backend/methods/manoeuvring/` | REAL | MMG 3-DOF 模型 + Nomoto 闭式解 + IMO 判据 |
| 服务 | `backend/api/domain/manoeuvring.py` | REAL | FastAPI 端点，调用内核 |
| 平台 | `:8001/api/v1/maneuvering/*` | **MOCK** (m1456 已证) | 旧端点返回与输入无关的常量 |

两艘基准船参数：

| 船 | L [m] | B [m] | T [m] | Cb | 排水量 [t] | 设计航速 [kn] |
|---|---|---|---|---|---|---|
| KCS | 230.0 | 32.2 | 10.8 | 0.651 | 52030 | 24.0 |
| KVLCC2 | 320.0 | 58.0 | 20.8 | 0.810 | 312622 | 15.5 |

## ② 怎么做的（方法/流程/关键设置/工具链）

本单为**综合审计**，不新增代码，而是对已有工作做证据链整合：

1. **内核自洽**（m1566 已完成）：MMG 模型对 Nomoto 一阶响应闭式解误差 < 0.12%，
   稳态回转直径与运动学恒等式 r = V/R 一致。
2. **平台端点证伪**（m1456 已完成）：88 组探针证明旧端点对舵角/排水量/航速变化**完全无响应**，
   判定为 MOCK。
3. **新端点验证**（本单）：检查 `backend/api/domain/manoeuvring.py` 是否正确连接 MMG 内核，
   并验证 IMO 判据的传递性。
4. **回归测试**：运行 `tests/test_maneuvering_reference.py` 和 `tests/test_maneuver_judge.py`。

## ③ 计算结果

### 3.1 内核自洽（m1566 结果复述）

| 判据 | 类型 | 最大误差 | 判定 |
|---|---|---|---|
| Nomoto 闭式解 vs RK4 数值积分 | 解析一致性 | 0.12% | ✅ PASS |
| 稳态回转 r_ss = V/R | 运动学恒等式 | < 1e-6 | ✅ PASS |
| 圆拟合直径 vs u_ss/r_ss | 几何一致性 | < 0.5% | ✅ PASS |
| IMO 判据传递性 | 规范符合性 | — | ✅ PASS |

### 3.2 平台端点状态

| 端点 | 状态 | 证据 |
|---|---|---|
| `:8001/api/v1/maneuvering/turning` | **MOCK** | m1456: 88 探针全返回常量 |
| `:8001/api/v1/maneuvering/zigzag` | **MOCK** | 同上 |
| `:8001/api/v1/maneuvering/k_t` | **MOCK** | 同上 |
| `backend/api/domain/manoeuvring.py` | **REAL** | 代码审查：正确调用 MMG 内核 |

### 3.3 回归测试

```
tests/test_maneuvering_reference.py: 12 passed
tests/test_maneuver_judge.py: 25 passed
```

## ④ 结果图

本单为综合审计，不生成新图。相关图件见：
- `artifacts/m1456_maneuver/fig_maneuver_audit.png` (m1456)
- `artifacts/m1566/fig_mmg_verification.png` (m1566)

## ⑤ 注意点（踩过的坑/局限/适用范围）

1. **绝对精度 WITHHELD**：MMG 模型使用 Newton 横流模型，无势流升力项（Y'_v, N'_v 缺失），
   因此回转能力是模型依赖的。不能宣称"<3% 绝对精度"。
2. **平台端点未更新**：旧端点 (`:8001/api/v1/maneuvering/*`) 仍为 MOCK，新端点
   (`backend/api/domain/manoeuvring.py`) 尚未部署到在线平台。
3. **舵面积/排水量敏感性**：横流模型对舵面积和排水量的响应方向正确但量级不确定，
   需 CFD/船模试验标定水动力导数后才能用于设计决策。

## ⑥ 误差判定

| 关键量 | 基准 | 判定标准 | 实际误差 | 是否 <3% |
|---|---|---|---|---|
| Nomoto 闭式解 vs 数值积分 | 解析解 | < 1% | 0.12% | ✅ YES |
| 稳态回转运动学恒等式 | r = V/R | < 1% | < 1e-6% | ✅ YES |
| IMO 判据传递性 | MSC.137(76) | 逻辑一致 | 一致 | ✅ YES |
| 平台端点响应性 | 参数变化应引起输出变化 | 非零灵敏度 | 0 (MOCK) | ❌ NO (已知缺陷) |

**结论**：内核层 REAL（自洽验证通过），服务层 REAL（代码审查通过），平台层 MOCK（待修复）。
绝对精度 WITHHELD（需标定水动力导数）。

---

## 给下次自找活的建议线索

1. **平台端点更新**：将 `backend/api/domain/manoeuvring.py` 部署到 `:8001`，替换旧 MOCK 端点。
   需验证路由注册和鉴权。
2. **水动力导数标定**：用 KVLCC2 SIMMAN 2008 数据标定 MMG 模型的 Y'_v, N'_v 等导数，
   将绝对精度从 WITHHELD 提升到 REAL（目标 <10% 对船模试验）。
3. **浅水/受限水域操纵**：当前模型仅适用于深水，浅水效应（bank effect, squat）未建模。
4. **与阻力/推进耦合**：自航点求解需阻力曲线和敞水曲线的耦合，当前为独立模块。
