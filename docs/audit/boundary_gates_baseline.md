# TensorPlatform 领域边界门 G1-G3 — 基线快照

- 生成时间 (UTC): `2026-11-02T07:51:09+00:00`
- 模式: **baseline**(非阻断基线，仅报告)
- 仓库根: `/root/tp-agent/work/m2085`
- 汇总: 违规 98 条 / 红门 3 道 / 绿门 0 道 / 证据不足 0 道

## 门总览

| 门 | 语义 | 计数 | 状态 |
| --- | --- | --- | --- |
| G1_import_boundary | core surface must not import ship-domain modules | 5 | RED |
| G2_endpoint_vocabulary | live API must not expose ship-domain endpoint vocabulary | 9 | RED |
| G3_constant_hoisting | core surface must not hard-code hydrodynamics constants | 84 | RED |

## G1 import 边界门

扫描核心面文件 39 个；命中 token: `ship, polar, holtrop, resistance, propeller, seakeep, manoeuvr, maneuver, ice_uq`。

| 文件 | 行 | 语句 | 命中 |
| --- | --- | --- | --- |
| `backend/api/domain/misc.py` | 21 | `from api.domain.ice_uq import router as ice_uq_router` | `ice_uq` |
| `backend/main.py` | 423 | `from api.domain import ship_seakeeping` | `ship` |
| `tools/audit/baseline_integrity.py` | 227 | `from services.ship_proxy import BENCHMARK_REFERENCE` | `ship` |
| `tools/audit/baseline_integrity.py` | 301 | `from services.ship_proxy import BENCHMARK_REFERENCE` | `ship` |
| `tools/audit/normalize_benchmark_reference.py` | 28 | `from baseline_integrity import DATASET, froude, holtrop_wetted_surface, rho_for` | `holtrop` |

## G2 端点语汇门

- OpenAPI 源: `http://127.0.0.1:8010/openapi.json`
- 语汇正则: `ship|holtrop|wigley|kvlcc|suboff|seakeep|manoeuv|maneuver|propeller|hydrostatic|ice-uq|ice-load|polar`
- OpenAPI 路径总数: 1120
- 命中路径数 / 端点数: 9 / 9
- 规格书声明基线: 7（实测 9，差异见报告『影响点』）

| 路径 | 方法 | 命中语汇 |
| --- | --- | --- |
| `/api/v1/calculations/hydrostatics` | POST | hydrostatic |
| `/api/v1/calculations/maneuvering/mmg` | POST | maneuver |
| `/api/v1/calculations/seakeeping_rao` | POST | seakeep |
| `/api/v1/calculations/seakeeping_response` | POST | seakeep |
| `/api/v1/calculations/seakeeping_spectrum` | POST | seakeep |
| `/api/v1/ice-uq/resistance` | POST | ice-uq |
| `/api/v1/ice-uq/ships` | GET | ice-uq |
| `/api/v1/ice-uq/uq` | POST | ice-uq |
| `/api/v1/ice-uq/uq/all` | POST | ice-uq |

**基线与规格书差异**: 规格书（审计 §7）记 7 个活端点，本机实测 9 个，差 +2。测量口径：OpenAPI `paths` 中 URL 命中语汇正则、且方法为合法 HTTP 动词的 **operation** 数（同上表）。差异来源为规格书成文后 `calculations/seakeeping_*` 一族端点增补；棘轮以实测值为锚，规格书值仅作对照留档。

## G3 常数外置门

扫描文件 39 个；命中 84 处；按常数分布: `{"SUBOFF": 31, "DTMB": 17, "KVLCC": 36}`；按语境分布: `{"code": 4, "string": 79, "comment": 1}`（code=可执行代码，string=字面量/文档串，comment=注释）

| 文件 | 行 | 常数 | 语境 | 片段 |
| --- | --- | --- | --- | --- |
| `backend/services/benchmark_canon.py` | 8 | `SUBOFF` | code | `m1395     Rt 按淡水 ρ=998.2 派生（SUBOFF-01 → 92.52 N）+ geometry 富化字段` |
| `backend/services/benchmark_canon.py` | 10 | `SUBOFF` | code | `m1397     参考值审计：Rt 按 ρ=1000 派生（SUBOFF-01 → 92.68 N）` |
| `backend/services/benchmark_canon.py` | 14 | `SUBOFF` | code | `基线 2.0  SUBOFF-01 = 95.0 N（ρ=1025 海水），并把 v1 的 12.5 N 存为 legacy` |
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
| `backend/services/benchmark_canon.py` | 664 | `SUBOFF` | string | `"船舶阻力基准参考数据集（唯一正典 v3.0）。来源：Roddy 1990 DTRC-90/012（SUBOFF）、"` |
| `backend/services/benchmark_canon.py` | 665 | `DTMB` | string | `"ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 "` |
| `backend/services/benchmark_canon.py` | 665 | `KVLCC` | string | `"ITTC V&V Workshop 2010（DTMB-5415）、SIMMAN 2008（KVLCC2）。每个量由 "` |
| `backend/services/benchmark_canon.py` | 670 | `DTMB` | string | `"3.0 (m1561 六分支裁决)：①流体溯源落盘——模型试验（SUBOFF、DTMB-5415）统一淡水 "` |
| `backend/services/benchmark_canon.py` | 670 | `SUBOFF` | string | `"3.0 (m1561 六分支裁决)：①流体溯源落盘——模型试验（SUBOFF、DTMB-5415）统一淡水 "` |
| `backend/services/benchmark_canon.py` | 671 | `KVLCC` | string | `"ρ=998.2 kg/m³、ν=1.004e-6 m²/s，实船（KVLCC2）海水 1025/1.188e-6；v2 把海水密度用在 "` |
| `backend/services/benchmark_canon.py` | 672 | `SUBOFF` | string | `"DTRC 模型拖曳水池行上，SUBOFF-01 的 95.0 N 因此偏高 2.68%，正典为 92.52 N；v1 的 12.5 N "` |
| `tools/audit/baseline_integrity.py` | 92 | `KVLCC` | string | `return 1025.0 if ship_type.upper().startswith("KVLCC") else 1000.0` |
| `tools/audit/benchmark_reference_integrity.py` | 46 | `SUBOFF` | string | `"SUBOFF": {},  # 算例自带湿表面积` |
| `tools/audit/benchmark_reference_integrity.py` | 47 | `DTMB` | string | `"DTMB-5415": {"cm": 0.85, "medium": "fresh"},        # 无桨驱逐舰模型，CM 取常见值` |
| `tools/audit/benchmark_reference_integrity.py` | 48 | `KVLCC` | string | `"KVLCC2": {"cm": 0.9986, "cwp": 0.8564, "medium": "sea"},  # KRISO 设计主尺度` |
| `tools/audit/curate_capability_ledger_m1811.py` | 82 | `SUBOFF` | string | `"inline 湿表面积 vs 解析长球体 -20.9%；SUBOFF inline Ct 误差到 +31.5%；"` |
| `tools/audit/hydrostatics_identity_audit.py` | 60 | `KVLCC` | string | `HULLS = ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC")` |
| `tools/audit/hydrostatics_identity_audit.py` | 60 | `KVLCC` | string | `HULLS = ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC")` |
| `tools/audit/hydrostatics_identity_audit.py` | 60 | `SUBOFF` | string | `HULLS = ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC")` |
| `tools/audit/hydrostatics_identity_audit.py` | 66 | `KVLCC` | string | `"KVLCC2": {"nabla_m3": 312622.0, "source": "SIMMAN 2008 / MOERI KVLCC2 官方标模主尺度"},` |
| `tools/audit/hydrostatics_identity_audit.py` | 66 | `KVLCC` | string | `"KVLCC2": {"nabla_m3": 312622.0, "source": "SIMMAN 2008 / MOERI KVLCC2 官方标模主尺度"},` |
| `tools/audit/hydrostatics_identity_audit.py` | 349 | `KVLCC` | string | `for hull in ("KCS", "KVLCC2", "JBC"):` |
| `tools/audit/hydrostatics_identity_audit.py` | 387 | `KVLCC` | string | `for hull in ("KCS", "KVLCC2", "JBC"):` |
| `tools/audit/make_benchmark_integrity_figures.py` | 53 | `DTMB` | string | `ax.set_title("C1 Froude identity: DTMB-5415 unit/scale mix")` |
| `tools/audit/maneuvering_audit_m1663.py` | 8 | `KVLCC` | code | `参照：IMO Res. MSC.137(76)；SIMMAN 2008 / Yasukawa & Yoshimura (2015) KVLCC2 公开标定带。` |
| `tools/audit/maneuvering_audit_m1663.py` | 116 | `KVLCC` | string | `add("G1", "D_T(35°)/L 对 KVLCC2 文献带（2.8–3.3 L）", dt35 / L_REF, sum(LIT_DT) / 2.0, "L", "FAIL",` |
| `tools/audit/maneuvering_audit_m1663.py` | 205 | `KVLCC` | string | `"sources": ["IMO Res. MSC.137(76)", "SIMMAN 2008 KVLCC2 benchmark",` |
| `tools/audit/maneuvering_audit_m1663.py` | 226 | `KVLCC` | string | `ax[0].axhspan(LIT_DT[0], LIT_DT[1], color="tab:green", alpha=0.2, label="literature band (KVLCC2)")` |
| `tools/audit/maneuvering_audit_m1663.py` | 298 | `KVLCC` | string | `"turning_sensitivity": "图1 战术直径对舵角（左）与航速（右）的灵敏度：端点两线皆水平（无自由度），绿为 KVLCC2 文献带与 D_T∝V 物理趋势。",` |
| `tools/audit/maneuvering_audit_m1663.py` | 314 | `KVLCC` | string | `A("\| 船型 \| KVLCC2（Lpp=320 m, B=58 m, T=20.8 m, ∇=312622 t, Cb=0.8098）\|")` |
| `tools/audit/maneuvering_audit_m1663.py` | 323 | `KVLCC` | string | `A("3. 基准：IMO Res. MSC.137(76)；SIMMAN 2008 与 Yasukawa & Yoshimura (2015) KVLCC2 标定带。\n")` |
| `tools/audit/maneuvering_audit_m1663.py` | 340 | `KVLCC` | string | `A("- 未评估尺度效应（Re/Fr）与浅水/限制航道修正；KVLCC2 文献带按全尺度对标。")` |
| `tools/audit/render_m1825_report.py` | 59 | `KVLCC` | string | `for h in ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC"):` |
| `tools/audit/render_m1825_report.py` | 59 | `KVLCC` | string | `for h in ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC"):` |
| `tools/audit/render_m1825_report.py` | 59 | `SUBOFF` | string | `for h in ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC"):` |
| `tools/audit/render_m1825_report.py` | 82 | `SUBOFF` | string | `"排水量 < 10 t 的模型尺度船（SUBOFF）单列并标注 `resolution_limited`，不进计分统计。")` |
| `tools/audit/render_m1825_report.py` | 112 | `KVLCC` | string | `for h in ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC"):` |
| `tools/audit/render_m1825_report.py` | 112 | `KVLCC` | string | `for h in ("KCS", "KVLCC2", "5415", "SUBOFF", "JBC", "KVLCC"):` |
| ... | | | 其余 24 处见 JSON 基线 |

## 影响面分布（程序化统计）

G1 违规按面分布: tools/audit 审计面 3 条、backend 核心面 2 条

| G3 文件 | 命中数 |
| --- | --- |
| `backend/services/benchmark_canon.py` | 33 |
| `tools/audit/render_reference_recovery.py` | 20 |
| `tools/audit/render_m1825_report.py` | 10 |
| `tools/audit/maneuvering_audit_m1663.py` | 8 |
| `tools/audit/hydrostatics_identity_audit.py` | 7 |
| `tools/audit/benchmark_reference_integrity.py` | 3 |
| `tools/audit/baseline_integrity.py` | 1 |
| `tools/audit/curate_capability_ledger_m1811.py` | 1 |
| `tools/audit/make_benchmark_integrity_figures.py` | 1 |

G2 端点按路由前缀分布: `/api/v1/calculations` 5 个、`/api/v1/ice-uq` 4 个

## 判定口径与翻绿方式

- G1/G3 为静态扫描（AST / 文本），可离线复算，确定性。
- G2 需要活服务；取不到 openapi.json 时记 WITHHELD（基线模式下不计违规，`--enforce` 模式下判红——无法验证即不放行）。
- 基线模式恒 exit 0，不阻断既有 PR 流；迁移完成后加 `--enforce` 翻为阻断门。
- G3 的计数是裸 grep 语义（`KVLCC` 亦命中 `KVLCC2` 族），并额外做「语境」三分（code/string/comment）：只有 `code` 才是真正的物理常数外泄，`string`/`comment` 多为算例名与文档表述，属清理优先级较低的一类。
- 退出码：基线模式恒 0；`--enforce` 下红门或不可评估（WITHHELD）返回 2；内部错误返回 3。
- 门脚本自身 `tools/audit/boundary_gates.py` 从扫描面剔除（其词汇表必然含被检 token，等价于 linter 不 lint 自身词表）。

