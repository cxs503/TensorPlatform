# 能力证据台账对账表（自动生成，勿手改）

- 能力数：21（有证据 3 / 无证据 18）
- 分歧：OVERCLAIM 2 · REVERIFY 0 · UNVERIFIED 15 · OK 4
- 未注册域缺口：5；证据行：10；违规：0

| 能力 | 域 | 自述 | 对账 | 分歧 | 最新证据 | 依据 |
|---|---|---|---|---|---|---|
| `propeller.open_water` | 推进 | REAL | **MOCK** | OVERCLAIM | doc:docs/verification/PROPULSION_OPENWATER_M1584.md | 最新证据 doc:docs/verification/PROPULSION_OPENWATER_M1584.md stance=contradicts → MOCK |
| `calc.stability` | 稳性 | REAL | **REAL** | OK | doc:docs/reports/m1564_stability_adjudication.md | 最新证据 doc:docs/reports/m1564_stability_adjudication.md 支持 REAL |
| `calc.resistance` | 阻力 | REAL | **WITHHELD** | OVERCLAIM | doc:docs/verification/inline_resistance_audit_2026-11-01.md | 最新证据 doc:docs/verification/inline_resistance_audit_2026-11-01.md stance=contradicts → WITHHELD |
| `geometry.hydro_curves` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `geometry.generate` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `geometry.list_models` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `geometry.verify_official` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `shiphull.generate` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `stdmodels.lookup` | 数据 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `gov.stats` | 平台 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `knowledge.docs` | 平台 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.gate_5415` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.git` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.logs` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.nginx_audit` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.probe` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.restart` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.services` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `ai.predict` | AI | NOT_SUPPORTED | **WITHHELD** | OK | - | 无证据 |
| `methods.computation` | 方法 | NOT_SUPPORTED | **WITHHELD** | OK | - | 无证据 |
| `misc.stubs` | 杂项 | STUB | **WITHHELD** | OK | - | 无证据 |

## 注册表覆盖缺口

| 域 | 最新证据判定 | 缺口 | 动作 |
|---|---|---|---|
| 耐波/seakeeping | REAL（报告 1139） | 注册表 21 项里没有耐波能力条目，但平台已有耐波端点并有 2 份审计报告 → 注册表覆盖缺口（能力在飞而账上无条目）。 | 在注册表新增 seakeeping.rao（初始 WITHHELD，待外部 RAO 基准复核后升 REAL）。 |
| 绿色航行/green-ship | WITHHELD（报告 1134） | 注册表无绿色航行条目；报告 1134 交付了 EEDI/EEXI/CII 分层实现。 | 新增 green.eedi、green.cii（等级按报告 1134 的三层拆分入账，不得合并成单个 REAL）。 |
| 基准参照数据/benchmark-reference | WITHHELD（报告 1137） | 基准台账本身是能力判据的基石，注册表未列该能力；其自洽性已有独立审计。 | 新增 data.benchmark_integrity（canonical v3.0，WITHHELD 行逐条列出）。 |
| 结构/structure | MOCK（报告 doc:docs/reports/m1590_structure_verification_debt.md） | 注册表 21 项里没有结构能力条目，但平台有 4 个结构端点（板格屈曲/疲劳/板厚屈曲/极限强度），且 m1590 已做闭式基准复核 → 注册表覆盖缺口。 | 注册表新增 structure.buckling（可 REAL）与 structure.fatigue / structure.ultimate（记 MOCK），并按 m1590 的契约层建议让 actual_stress 缺输入时返回 WITHHELD 而不是 0.5 sigma_y。 |
| 振动噪声/vibration-noise | NOT_SUPPORTED（报告 doc:docs/audits/vibration_noise_domain_audit.md） | 注册表无振动噪声域条目；仓内 m1493 已实现梁/板固有频率、隔振传递率、板隔声、声级合成共 9 个解析端点并复核到 <3%，但**未挂进在线服务** → 在线仍 NOT_SUPPORTED。 | 把 backend/api/domain/ship_vibration.py 的 router 挂进主站并重启（属运维动作），挂载后本行寄存器新增 vibration.beam/plate、noise.panel_tl 并把在线等级升 REAL。 |
