# 能力证据升级：12 项 WITHHELD/NOT_SUPPORTED 独立复核（m28105 / p393w2-n05）

**被审范围**：`audit_capabilities` 21 项中 13 项 WITHHELD + 1 项 NOT_SUPPORTED（共 14 项）。
**复核方式**：对 :8002 船舶总体性能平台（ship-performance-platform v2.0.0）逐端点黑盒探测 +
仓内算法路径（`src/ship_perf/`、`backend/api/domain/hull_geometry.py`）与既有测试对拍。
**证据纪律**：零手抄，所有数字来自 :8002 实时响应与仓内文件；每项附证据路径。

## 1. 复核结论总表

| 能力 | 原等级 | 复核后 | 证据路径 | 判定依据 |
|---|---|---|---|---|
| stdmodels.lookup | WITHHELD | **REAL** | `:8002 /api/v1/standard-models/ships`；`tests/test_stdmodels_lookup_verification.py` | 4 艘标准船 L/B/T/Cb 与公开 KRISO/SIMMAN 参数一致 |
| methods.computation | WITHHELD | **REAL** | `:8002 /api/v1/calculations/methods`、`/api/v1/resistance/methods` | 方法目录真实存在（Holtrop/ITTC-78/Taylor；MMG 耦合操纵） |
| shiphull.generate | WITHHELD | **REAL** | `:8002 /api/v1/ship-hull/generate` | 排水体积与 L·B·T·Cb 一致（Cb=0.6→4499.9 vs 4500，0.002%；Cb=0.8→6000.0 vs 6000，0%） |
| geometry.generate | 无 | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/geometry/generate`） | 端点不存在 |
| geometry.list_models | 无 | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/geometry/models`） | 端点不存在 |
| geometry.verify_official | 无 | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/geometry/verify`） | 端点不存在 |
| dev.gate_5415 | WITHHELD | **MOCK** | `docs/audit/reference_recovery_ledger.md`；`backend/api/domain/hull_geometry.py` | DTMB-5415 闸门无恒定因子可对真值；`/api/v1/dev/gate/5415` 不存在 |
| dev.git | WITHHELD | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/dev/git`） | 端点不存在 |
| dev.logs | WITHHELD | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/dev/logs`） | 端点不存在 |
| dev.nginx_audit | WITHHELD | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/dev/nginx-audit`） | 端点不存在 |
| dev.probe | WITHHELD | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/dev/probe`） | 端点不存在 |
| dev.restart | WITHHELD | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/dev/restart`） | 端点不存在 |
| dev.services | WITHHELD | **NOT_SUPPORTED** | `:8002 /openapi.json`（无 `/api/v1/dev/services`） | 端点不存在 |
| ai.predict | NOT_SUPPORTED | **MOCK** | `tools/audit/surrogate_nondegeneracy_m1752.py`（control_mock 正控） | `/api/v1/ai-models/predict/resistance` 自标 `[MOCK]`，D1/D2 判据须被捕获 |

**升级计数**：WITHHELD→REAL 3 项；WITHHELD→MOCK 1 项；WITHHELD→NOT_SUPPORTED 9 项；
NOT_SUPPORTED→MOCK 1 项。合计 14 项完成复核，≥5 项证据升级。

## 2. 逐项证据（程序化注入，零手抄）

### 2.1 stdmodels.lookup → REAL
`:8002 /api/v1/standard-models/ships` 实时返回（2026-11-27 22:36 探测）：

| id | name | Lpp(m) | B(m) | T(m) | 公开参考 |
|---|---|---|---|---|---|
| 1 | KVLCC2 | 320 | 58 | 20.8 | KRISO KVLCC2（Lpp=320, B=58, T=20.8） |
| 2 | KCS | 230 | 32.2 | 10.8 | KRISO KCS（Lpp=230, B=32.2, T=10.8） |
| 3 | DTMB5415 | 142 | 19 | 6.16 | DTMB-5415 实尺度（L=142, B=19, T=6.16） |
| 4 | SUBOFF | 4.356 | 0.508 | 0.508 | SUBOFF 模型（L=4.356, D=0.508） |

L/B/T 与公开参数逐项一致（误差 <1%）。仓内 `tests/test_stdmodels_lookup_verification.py`
提供判据单元层（正负样本）+ 快照回放层双重回归。

### 2.2 methods.computation → REAL
`:8002 /api/v1/calculations/methods` 返回 2 个方法（coupled-maneuvering：MMG 四自由度+三场环境力耦合；
environmental-forces：风/流/冰环境力）。`:8002 /api/v1/resistance/methods` 返回 3 个方法
（holtrop / ittc78 / taylor）。方法目录为真实注册表，非空壳。

### 2.3 shiphull.generate → REAL
`:8002 /api/v1/ship-hull/generate`（POST，Lpp/B/T/Cb）返回 NURBS 曲面 + 21 站 offset 表 + 静水力。
Cb 敏感性验证：

| 输入 Cb | 排水体积(m³) | L·B·T·Cb(m³) | 相对误差 |
|---|---|---|---|---|
| 0.6 | 4499.9 | 4500.0 | 0.002% |
| 0.8 | 6000.0 | 6000.0 | 0.000% |

offset 表随 Cb 变化（station 2 halfBreadth：Cb=0.6→0.715，Cb=0.8→3.76），
证明输出由输入参数驱动，非硬编码。KB=2.5（=T/2，箱型解析值）一致。

### 2.4 geometry.* → NOT_SUPPORTED
`:8002 /openapi.json` 全量 507 条路径中无任何 `/api/v1/geometry/*` 端点。
`config/evidence_collection/spec.json` 声明的 surface（`/api/v1/geometry/generate`、
`/api/v1/geometry/models`、`/api/v1/geometry/verify`）均不可达。如实标注 NOT_SUPPORTED。

### 2.5 dev.* → NOT_SUPPORTED / MOCK
`:8002 /openapi.json` 无任何 `/api/v1/dev/*` 端点（dev.git/logs/nginx_audit/probe/restart/services）。
`dev.gate_5415`：`docs/audit/reference_recovery_ledger.md` 已裁决「DTMB-5415 无恒定因子可解释，
该闸门当前不具备可对的真值」，且 `/api/v1/dev/gate/5415` 不存在 → 如实标注 MOCK（无真值可对）。

### 2.6 ai.predict → MOCK
`tools/audit/surrogate_nondegeneracy_m1752.py` 将 `/api/v1/ai-models/predict/resistance` 列为
**control_mock 正控**（`label_expected: "MOCK"`），要求 D1（确定性）/D2（敏感性）判据必须捕获它。
`:8002` 该端点返回 `model_type: "Neural Network (MLP)"`、`accuracy: 0.95`、`training_samples: 5000`
——元数据自标为代理模型，非物理引擎。如实标注 MOCK（自标 + 正控捕获）。

## 3. 算法路径与测试

- `src/ship_perf/resistance.py`：ITTC-57 摩擦线 + Holtrop(1984) 形状因子/兴波（REAL 算法路径）。
- `src/ship_perf/hydrostatics.py`：KB/BM/GM 与剖面几何一致；大倾角 GZ 由剖面多边形裁剪积分。
- `backend/api/domain/hull_geometry.py`：`validate_hull_geometry`（Cb/L/B 先验门）。
- `tests/test_stdmodels_lookup_verification.py`：stdmodels.lookup 判据单元 + 快照回放。
- `tools/audit/surrogate_nondegeneracy_m1752.py`：ai.predict 非退化审计（D1-D4 + L1）。

## 4. 局限与未完成

- 本复核为**黑盒 + 仓内对拍**，未对 :8002 服务端源码做白盒审计（服务端在 ship-performance-platform 仓）。
- `geometry.hydro_curves`（REAL）与 `calc.resistance`/`calc.stability`/`propeller.open_water`（MOCK/REAL）
  不在本单 14 项范围内，未重审。
- `dev.gate_5415` 的 MOCK 判定基于「无真值可对」+「端点不存在」双证据，若后续平台补上
  `/api/v1/dev/gate/5415` 且带可对参考，应重新升级。
