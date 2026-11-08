# TensorPlatform 领域边界门 G1-G3 — 基线快照

- 生成时间 (UTC): `2026-11-08T06:45:33+00:00`
- 模式: **baseline**(非阻断基线，仅报告)
- 仓库根: `/root/tp-agent/work/m6643`
- 汇总: 违规 151 条 / 红门 2 道 / 绿门 0 道 / 证据不足 1 道

## 门总览

| 门 | 语义 | 计数 | 状态 |
| --- | --- | --- | --- |
| G1_import_boundary | core surface must not import ship-domain modules | 15 | RED |
| G2_endpoint_vocabulary | live API must not expose ship-domain endpoint vocabulary | WITHHELD | WITHHELD |
| G3_constant_hoisting | core surface must not hard-code hydrodynamics constants | 136 | RED |

## G1 import 边界门

扫描核心面文件 82 个；命中 token: `ship, polar, holtrop, resistance, propeller, seakeep, manoeuvr, maneuver, ice_uq`。

| 文件 | 行 | 语句 | 命中 |
| --- | --- | --- | --- |
| `backend/api/domain/misc.py` | 21 | `from api.domain.ice_uq import router as ice_uq_router` | `ice_uq` |
| `backend/main.py` | 76 | `from api.domain.physics_providers import register_ship_resistance_providers` | `ship` |
| `backend/main.py` | 429 | `from api.domain import ship_seakeeping` | `ship` |
| `backend/main.py` | 439 | `from api.domain import ship_vibration as _ship_vibration` | `ship` |
| `tools/audit/baseline_integrity.py` | 227 | `from services.ship_proxy import BENCHMARK_REFERENCE` | `ship` |
| `tools/audit/baseline_integrity.py` | 301 | `from services.ship_proxy import BENCHMARK_REFERENCE` | `ship` |
| `tools/audit/m1687_ice_three_way.py` | 34 | `from services.polar.resistance import ice_components` | `polar` |
| `tools/audit/m1687_ice_three_way.py` | 35 | `from services.polar.ships import ShipGeometry` | `polar` |
| `tools/audit/m1687_ice_three_way.py` | 36 | `from services.digital_twin.models import resistance as DT` | `resistance` |
| `tools/audit/m1687_ice_three_way.py` | 37 | `from physics.ship_env_maneuvering import data as SEM` | `ship` |
| `tools/audit/m1687_ice_three_way.py` | 38 | `from physics.ship_env_maneuvering.loads import lindqvist_like_ice_resistance, riska_ice_resistance` | `ship` |
| `tools/audit/m1868_render.py` | 10 | `from tools.audit.resistance_anchor_m1868 import G, NU_SEA, RHO_SEA` | `resistance` |
| `tools/audit/normalize_benchmark_reference.py` | 28 | `from baseline_integrity import DATASET, froude, holtrop_wetted_surface, rho_for` | `holtrop` |
| `tools/audit/optimizer_objective_audit.py` | 109 | `from optimizer.interfaces.resistance_eval import evaluate_resistance, holtrop_resistance` | `resistance` |
| `tools/audit/resistance_anchor_m1868.py` | 49 | `from services.ship_proxy import BENCHMARK_REFERENCE` | `ship` |

## G2 端点语汇门

- OpenAPI 源: `http://127.0.0.1:8010/openapi.json`
- 状态: WITHHELD（无法取到 openapi.json：`openapi unavailable: timed out`）

## G3 常数外置门

扫描文件 82 个；命中 136 处；按常数分布: `{"SUBOFF": 45, "WATER_DENSITY": 6, "DTMB": 27, "KVLCC": 57, "KINEMATIC_VISCOSITY": 1}`；按语境分布: `{"string": 122, "code": 10, "comment": 4}`（code=可执行代码，string=字面量/文档串，comment=注释）

| 文件 | 行 | 常数 | 语境 | 片段 |
| --- | --- | --- | --- | --- |
| `backend/services/benchmark_canon.py` | 1 | `SUBOFF` | string | `'船舶阻力基准参考数据的**正典引擎**（mission m1561：六分支裁决）。\n\n背景\n----\n同一个基准数据集被六张成熟分支各自独立重推导，产生了互相矛盾的值：\n\n    分支      主张\n    m1395     Rt 按淡水 ρ=998.2 派生（SUBOFF-01 → 92.52 N` |
| `backend/services/benchmark_canon.py` | 1 | `WATER_DENSITY` | string | `'船舶阻力基准参考数据的**正典引擎**（mission m1561：六分支裁决）。\n\n背景\n----\n同一个基准数据集被六张成熟分支各自独立重推导，产生了互相矛盾的值：\n\n    分支      主张\n    m1395     Rt 按淡水 ρ=998.2 派生（SUBOFF-01 → 92.52 N` |
| `backend/services/benchmark_canon.py` | 8 | `SUBOFF` | code | `m1395     Rt 按淡水 ρ=998.2 派生（SUBOFF-01 → 92.52 N）+ geometry 富化字段` |
| `backend/services/benchmark_canon.py` | 10 | `SUBOFF` | code | `m1397     参考值审计：Rt 按 ρ=1000 派生（SUBOFF-01 → 92.68 N）` |
| `backend/services/benchmark_canon.py` | 14 | `SUBOFF` | code | `基线 2.0  SUBOFF-01 = 95.0 N（ρ=1025 海水），并把 v1 的 12.5 N 存为 legacy` |
| `backend/services/benchmark_canon.py` | 22 | `WATER_DENSITY` | code | `平台物理核 ``api.domain.ship_resistance.DEFAULT_WATER_DENSITY = 998.0``` |
| `backend/services/benchmark_canon.py` | 155 | `SUBOFF` | string | `return "body_of_revolution" if case.get("ship_type") == "SUBOFF" else "displacement_hull"` |
| `backend/services/benchmark_canon.py` | 260 | `SUBOFF` | comment | `# --- R1' 密度选择本身（SUBOFF 争议的根）--------------------------------` |
| `backend/services/benchmark_canon.py` | 439 | `SUBOFF` | string | `"SUBOFF-01": {` |
| `backend/services/benchmark_canon.py` | 445 | `SUBOFF` | string | `"SUBOFF-02": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},` |
| `backend/services/benchmark_canon.py` | 445 | `SUBOFF` | string | `"SUBOFF-02": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},` |
| `backend/services/benchmark_canon.py` | 446 | `SUBOFF` | string | `"SUBOFF-03": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},` |
| `backend/services/benchmark_canon.py` | 446 | `SUBOFF` | string | `"SUBOFF-03": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},` |
| `backend/services/benchmark_canon.py` | 447 | `SUBOFF` | string | `"SUBOFF-04": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},` |
| `backend/services/benchmark_canon.py` | 447 | `SUBOFF` | string | `"SUBOFF-04": {"scale": "model", "fluid": "fresh_water_20c", "rt": "derive", "why": "同 SUBOFF-01。"},` |
| `backend/services/benchmark_canon.py` | 448 | `DTMB` | string | `"DTMB5415-01": {` |
| `backend/services/benchmark_canon.py` | 454 | `DTMB` | string | `"DTMB5415-02": {"scale": "model", "fluid": "fresh_water_20c", "rt": "withhold",` |
| `backend/services/benchmark_canon.py` | 455 | `DTMB` | string | `"speed_from": "froude", "why": "同 DTMB5415-01。"},` |
| `backend/services/benchmark_canon.py` | 456 | `DTMB` | string | `"DTMB5415-03": {"scale": "model", "fluid": "fresh_water_20c", "rt": "withhold",` |
| `backend/services/benchmark_canon.py` | 457 | `DTMB` | string | `"speed_from": "froude", "why": "同 DTMB5415-01。"},` |
| `backend/services/benchmark_canon.py` | 458 | `DTMB` | string | `"DTMB5415-04": {"scale": "model", "fluid": "fresh_water_20c", "rt": "withhold",` |
| `backend/services/benchmark_canon.py` | 459 | `DTMB` | string | `"speed_from": "froude", "why": "同 DTMB5415-01。"},` |
| `backend/services/benchmark_canon.py` | 460 | `KVLCC` | string | `"KVLCC2-01": {` |
| `backend/services/benchmark_canon.py` | 465 | `KVLCC` | string | `"KVLCC2-02": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},` |
| `backend/services/benchmark_canon.py` | 465 | `KVLCC` | string | `"KVLCC2-02": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},` |
| `backend/services/benchmark_canon.py` | 466 | `KVLCC` | string | `"KVLCC2-03": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},` |
| `backend/services/benchmark_canon.py` | 466 | `KVLCC` | string | `"KVLCC2-03": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},` |
| `backend/services/benchmark_canon.py` | 467 | `KVLCC` | string | `"KVLCC2-04": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},` |
| `backend/services/benchmark_canon.py` | 467 | `KVLCC` | string | `"KVLCC2-04": {"scale": "full", "fluid": "sea_water_15c", "rt": "withhold", "why": "同 KVLCC2-01。"},` |
| `backend/services/benchmark_canon.py` | 634 | `KINEMATIC_VISCOSITY` | string | `"reference_kinematic_viscosity_m2_s": nu,` |
| `backend/services/benchmark_canon.py` | 664 | `DTMB` | string | `'船舶阻力基准参考数据集（唯一正典 v3.0）。来源：Roddy 1990 DTRC-90/012（SUBOFF）、ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 backend/services/benchmark_canon.py 的解析恒等式复` |
| `backend/services/benchmark_canon.py` | 664 | `KVLCC` | string | `'船舶阻力基准参考数据集（唯一正典 v3.0）。来源：Roddy 1990 DTRC-90/012（SUBOFF）、ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 backend/services/benchmark_canon.py 的解析恒等式复` |
| `backend/services/benchmark_canon.py` | 664 | `SUBOFF` | string | `"船舶阻力基准参考数据集（唯一正典 v3.0）。来源：Roddy 1990 DTRC-90/012（SUBOFF）、"` |
| `backend/services/benchmark_canon.py` | 665 | `DTMB` | string | `"ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 "` |
| `backend/services/benchmark_canon.py` | 665 | `KVLCC` | string | `"ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 "` |
| `backend/services/benchmark_canon.py` | 670 | `DTMB` | string | `"3.0 (m1561 六分支裁决)：①流体溯源落盘——模型试验（SUBOFF、DTMB-5415）统一淡水 "` |
| `backend/services/benchmark_canon.py` | 670 | `KVLCC` | string | `'3.0 (m1561 六分支裁决)：①流体溯源落盘——模型试验（SUBOFF、DTMB-5415）统一淡水 ρ=998.2 kg/m³、ν=1.004e-6 m²/s，实船（KVLCC2）海水 1025/1.188e-6；v2 把海水密度用在 DTRC 模型拖曳水池行上，SUBOFF-01 的 95.0 N 因此偏高` |
| `backend/services/benchmark_canon.py` | 670 | `SUBOFF` | string | `"3.0 (m1561 六分支裁决)：①流体溯源落盘——模型试验（SUBOFF、DTMB-5415）统一淡水 "` |
| `backend/services/benchmark_canon.py` | 671 | `KVLCC` | string | `"ρ=998.2 kg/m³、ν=1.004e-6 m²/s，实船（KVLCC2）海水 1025/1.188e-6；v2 把海水密度用在 "` |
| `backend/services/benchmark_canon.py` | 672 | `SUBOFF` | string | `"DTRC 模型拖曳水池行上，SUBOFF-01 的 95.0 N 因此偏高 2.68%，正典为 92.52 N；v1 的 12.5 N "` |
| `backend/services/benchmark_canon.py` | 673 | `DTMB` | string | `"与 R1 差 7.6 倍作废，两者均留 legacy_values。②DTMB-5415 的 Fr/航速互不自洽（Fr 列隐含"` |
| `tools/audit/baseline_integrity.py` | 92 | `KVLCC` | string | `return 1025.0 if ship_type.upper().startswith("KVLCC") else 1000.0` |
| `tools/audit/benchmark_reference_integrity.py` | 46 | `SUBOFF` | string | `"SUBOFF": {},  # 算例自带湿表面积` |
| `tools/audit/benchmark_reference_integrity.py` | 47 | `DTMB` | string | `"DTMB-5415": {"cm": 0.85, "medium": "fresh"},        # 无桨驱逐舰模型，CM 取常见值` |
| `tools/audit/benchmark_reference_integrity.py` | 48 | `KVLCC` | string | `"KVLCC2": {"cm": 0.9986, "cwp": 0.8564, "medium": "sea"},  # KRISO 设计主尺度` |
| `tools/audit/capability_audit_scan.py` | 169 | `DTMB` | string | `"keywords": ("stdmodels", "KVLCC2", "KCS", "DTMB", "SUBOFF"),` |
| `tools/audit/capability_audit_scan.py` | 169 | `KVLCC` | string | `"keywords": ("stdmodels", "KVLCC2", "KCS", "DTMB", "SUBOFF"),` |
| `tools/audit/capability_audit_scan.py` | 169 | `SUBOFF` | string | `"keywords": ("stdmodels", "KVLCC2", "KCS", "DTMB", "SUBOFF"),` |
| `tools/audit/capability_audit_scan.py` | 187 | `DTMB` | string | `"keywords": ("5415", "gate_5415", "dtmb"),` |
| `tools/audit/capability_audit_scan.py` | 503 | `DTMB` | string | `keys = {"resistance": ("suboff", "dtmb", "kvlcc2"), "geometry": ("dtmb", "kvlcc2")}` |
| `tools/audit/capability_audit_scan.py` | 503 | `DTMB` | string | `keys = {"resistance": ("suboff", "dtmb", "kvlcc2"), "geometry": ("dtmb", "kvlcc2")}` |
| `tools/audit/capability_audit_scan.py` | 503 | `KVLCC` | string | `keys = {"resistance": ("suboff", "dtmb", "kvlcc2"), "geometry": ("dtmb", "kvlcc2")}` |
| `tools/audit/capability_audit_scan.py` | 503 | `KVLCC` | string | `keys = {"resistance": ("suboff", "dtmb", "kvlcc2"), "geometry": ("dtmb", "kvlcc2")}` |
| `tools/audit/capability_audit_scan.py` | 503 | `SUBOFF` | string | `keys = {"resistance": ("suboff", "dtmb", "kvlcc2"), "geometry": ("dtmb", "kvlcc2")}` |
| `tools/audit/capability_card_evidence.py` | 2 | `DTMB` | string | `'Capability-card evidence-chain checker (E1-E7) — mission 1819 / infra 席.\n\nProblem it closes (measured, see docs/audits/capability_card_evidence_chain.md):\na` |
| `tools/audit/capability_card_evidence.py` | 2 | `SUBOFF` | string | `'Capability-card evidence-chain checker (E1-E7) — mission 1819 / infra 席.\n\nProblem it closes (measured, see docs/audits/capability_card_evidence_chain.md):\na` |
| `tools/audit/capability_card_evidence.py` | 8 | `SUBOFF` | code | `* `report_id` was a free-text label ("suboff_sail", "e2e1", "wave10_multiphase",` |
| `tools/audit/capability_card_evidence.py` | 9 | `DTMB` | code | `"dtmb5415_geometry", "report_gate_v1") — not a single one is a platform report id;` |
| `tools/audit/capability_ledger_upgrade.py` | 2 | `SUBOFF` | string | `'能力台账升账 CLI（capability ledger upgrade CLI）\n\n功能\n----\n1. 新增/更新能力卡片条目，强制证据分级校验：\n   - 升 REAL 必须挂 ≥1 工件 sha256 引用 + 判词，否则拒收并降为 WITHHELD\n   - 升 PASS 必须满足 E2+E3+` |
| `tools/audit/capability_ledger_upgrade.py` | 38 | `SUBOFF` | code | `python3 tools/audit/capability_ledger_upgrade.py --audit --card docs/capability_registry/cards/CAP-002-tensorlbm-suboff-resistance.json` |
| ... | | | 其余 76 处见 JSON 基线 |

## 影响面分布（程序化统计）

G1 违规按面分布: tools/audit 审计面 11 条、backend 核心面 4 条

| G3 文件 | 命中数 |
| --- | --- |
| `backend/services/benchmark_canon.py` | 41 |
| `tools/audit/render_reference_recovery.py` | 21 |
| `tools/audit/render_m1825_report.py` | 12 |
| `tools/audit/capability_audit_scan.py` | 9 |
| `tools/audit/maneuvering_audit_m1663.py` | 9 |
| `tools/audit/hydrostatics_identity_audit.py` | 7 |
| `tools/audit/resistance_anchor_m1868.py` | 7 |
| `tools/audit/capability_card_evidence.py` | 4 |
| `tools/audit/report_claim_consistency.py` | 4 |
| `tools/audit/benchmark_reference_integrity.py` | 3 |
| `tools/audit/m1868_render.py` | 3 |
| `tools/audit/ship_api_evidence_census.py` | 3 |
| `tools/audit/capability_ledger_upgrade.py` | 2 |
| `tools/audit/curate_capability_ledger_m1811.py` | 2 |
| `tools/audit/falsification/spec.py` | 2 |
| `tools/audit/pin_capability_evidence.py` | 2 |
| `tools/audit/baseline_integrity.py` | 1 |
| `tools/audit/falsification/runner.py` | 1 |
| `tools/audit/falsification/selftest.py` | 1 |
| `tools/audit/make_benchmark_integrity_figures.py` | 1 |
| `tools/audit/maneuver_debt_m1874.py` | 1 |

## 判定口径与翻绿方式

- G1/G3 为静态扫描（AST / 文本），可离线复算，确定性。
- G2 需要活服务；取不到 openapi.json 时记 WITHHELD（基线模式下不计违规，`--enforce` 模式下判红——无法验证即不放行）。
- 基线模式恒 exit 0，不阻断既有 PR 流；迁移完成后加 `--enforce` 翻为阻断门。
- G3 的计数是裸 grep 语义（`KVLCC` 亦命中 `KVLCC2` 族），并额外做「语境」三分（code/string/comment）：只有 `code` 才是真正的物理常数外泄，`string`/`comment` 多为算例名与文档表述，属清理优先级较低的一类。
- 退出码：基线模式恒 0；`--enforce` 下红门或不可评估（WITHHELD）返回 2；内部错误返回 3。
- 门脚本自身 `tools/audit/boundary_gates.py` 从扫描面剔除（其词汇表必然含被检 token，等价于 linter 不 lint 自身词表）。

