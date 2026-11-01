# 能力证据台账对账（平台审计）

- 生成时间：2026-11-01T07:57:32Z
- 能力注册表来源：audit_capabilities tool (platform :8010), snapshot 2026-11-01T07:54Z
- 台账：`artifacts/credit_ledger.jsonl`（1 条）

## 摘要

| 指标 | 值 |
|---|---|
| 能力总数 | 21 |
| NUMERIC 能力 | 12 |
| NUMERIC 且标 REAL | 9 |
| NUMERIC 已获独立裁决 | 1 |
| **数值证据覆盖率** | **8.3%** |
| 债务项 | 20（P0=8, P1=0, P2=9, P3=3） |

## 逐能力状态

| 能力 | 分级 | 层级 | 状态 | 台账条数 | pass | fail |
|---|---|---|---|---|---|---|
| `ai.predict` | NOT_SUPPORTED | NUMERIC | SUB-BAR_GRADE | 0 | 0 | 0 |
| `calc.resistance` | REAL | NUMERIC | SUBSTANTIATED | 1 | 1 | 0 |
| `calc.stability` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |
| `dev.gate_5415` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `dev.git` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `dev.logs` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `dev.nginx_audit` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `dev.probe` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `dev.restart` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `dev.services` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `geometry.generate` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |
| `geometry.hydro_curves` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |
| `geometry.list_models` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |
| `geometry.verify_official` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |
| `gov.stats` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `knowledge.docs` | REAL | OPERATIONAL | OPERATIONAL-PENDING | 0 | 0 | 0 |
| `methods.computation` | NOT_SUPPORTED | NUMERIC | SUB-BAR_GRADE | 0 | 0 | 0 |
| `misc.stubs` | STUB | NUMERIC | SUB-BAR_GRADE | 0 | 0 | 0 |
| `propeller.open_water` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |
| `shiphull.generate` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |
| `stdmodels.lookup` | REAL | NUMERIC | UNSUBSTANTIATED | 0 | 0 | 0 |

## 债务清单（按优先级）

| 优先级 | 规则 | 对象 | 说明 | 关闭路径 |
|---|---|---|---|---|
| P0 | R1_UNSUBSTANTIATED_REAL | `calc.stability` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: 矩形浮箱解析 GZ(GM/KB/BM/面积) 复核，需修复 D1–D7 后复判 |
| P0 | R1_UNSUBSTANTIATED_REAL | `geometry.generate` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: 生成船型 Cb 闭合与体积积分独立复算 |
| P0 | R1_UNSUBSTANTIATED_REAL | `geometry.hydro_curves` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: 排水量闭合 Cb/L/B/T 与 BM=I/∇ 定义级恒等式复核 |
| P0 | R1_UNSUBSTANTIATED_REAL | `geometry.list_models` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: 目录清单与磁盘工件一致性核对 |
| P0 | R1_UNSUBSTANTIATED_REAL | `geometry.verify_official` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: 官方主尺度表复算 + 排水量闭合门 |
| P0 | R1_UNSUBSTANTIATED_REAL | `propeller.open_water` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: Wageningen B-series 多项式 K_T/K_Q/eta 逐 J 点复核 |
| P0 | R1_UNSUBSTANTIATED_REAL | `shiphull.generate` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: 生成船型闭合/自交检查 |
| P0 | R1_UNSUBSTANTIATED_REAL | `stdmodels.lookup` | grade=REAL 但台账无 verdict=pass 裁决（拿到 0 条有效裁决） | verifier: 标准船型主尺度与公开表逐字段比对 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `dev.gate_5415` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `dev.git` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `dev.logs` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `dev.nginx_audit` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `dev.probe` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `dev.restart` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `dev.services` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `gov.stats` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P2 | R0_OPERATIONAL_UNADJUDICATED | `knowledge.docs` | infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用 | infra 席: 探针日志 + 近 24h 成功率入账 |
| P3 | R6_SUB_BAR_GRADE | `ai.predict` | grade=NOT_SUPPORTED 低于证据栏；合规但需在能力清单显式标注 | 先补齐能力（当前 NOT_SUPPORTED） |
| P3 | R6_SUB_BAR_GRADE | `methods.computation` | grade=NOT_SUPPORTED 低于证据栏；合规但需在能力清单显式标注 | 先补齐能力（当前 NOT_SUPPORTED） |
| P3 | R6_SUB_BAR_GRADE | `misc.stubs` | grade=STUB 低于证据栏；合规但需在能力清单显式标注 | 补齐能力后重判 |
