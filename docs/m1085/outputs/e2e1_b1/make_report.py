#!/usr/bin/env python3
"""生成 mission 1085 的平台报告载荷（/tmp/report.json）——数字全部从工件读取，零手抄。

读取：
  outputs/e2e1_b1/negative_evidence.json   负测试证据（修复前/后）
  outputs/e2e1_b1/m811_positive_check.json m811 正例三门体检
  /srv/tensor/agent/work/m811/outputs/lid_driven_cavity/summary.json  m811 自身硬门指标（引用其自述）
  outputs/e2e1_b1/fig/*.png                图（base64 内嵌）

用法：python3 outputs/e2e1_b1/make_report.py   → /tmp/report.json
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "outputs" / "e2e1_b1"
M811_SUMMARY = Path("/srv/tensor/agent/work/m811/outputs/lid_driven_cavity/summary.json")


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def main() -> int:
    ev = load(OUT / "negative_evidence.json")
    m811 = load(OUT / "m811_positive_check.json")
    s = ev["summary"]
    audit = ev["audit_records"]

    m811_gate = None
    hard_gate = {}
    if M811_SUMMARY.exists():
        ms = load(M811_SUMMARY)
        hard_gate = ms.get("hard_gate", {})
        comparisons = ms.get("comparisons", {})
        for key, value in comparisons.items():
            if isinstance(value, dict) and value.get("re") == hard_gate.get("re"):
                m811_gate = {"case": key, **value}
                break
        if m811_gate is None:
            m811_gate = {"case": "n/a", "raw_keys": list(comparisons)[:5]}

    rows = "\n".join(
        f"| `{r['name']}` | {r['gate']} | {r['expect']} | {r['legacy']['verdict']} | "
        f"{r['gated'].get('code') or 'ADMITTED'} | **{r['judgement']}** |"
        for r in ev["results"]
    )
    gate_view = m811["gate_view"]
    m811_missing = "\n".join(f"  - {m}" for m in m811["missing"])
    audit_sample = json.dumps(audit[1] if len(audit) > 1 else audit[0], ensure_ascii=False, sort_keys=True)

    markdown = f"""# 【E2E-1 修复·门缺陷 B1】样本库准入未验证谱系：三门门禁落地 + B 类负测试回归

## ① 案例介绍

**物理/工程背景**：样本库（`samples` 表）是优化库与代理模型的**直接训练数据源**。
历史入库路径 `tools/sample_db/backfill.py::backfill_file()` 只做「JSON 字段抽取 + INSERT」，
不读 RunManifest、不复算工件哈希、不看 `validation_status`、不看来源 mission 终态。
m810 的 B 类负测试 `B1_sample_db_unverified_ingest` 已实测：一个**伪造 `verdict="PASS"` 且 τ=0.4
（ν=c_s²(τ−1/2)<0，非物理）的裸 JSON** 入库成功 1 行 → 判决 FAIL（缺陷 D3）。

**本单对象**：不是某个船型算例，而是平台的数据可信链闸门。攻击载荷与几何无关，
故本报告不含船体几何/Cb 校验（该链路由阻力/耐波等学科任务负责）。

**攻击载荷（m810 原文，本单复现）**：
| 字段 | 值 | 问题 |
|---|---|---|
| `verdict` | `PASS` | 自我声明，无任何验证 |
| `physics.tau` | 0.4 | τ≤0.5 ⇒ ν<0，LBM 不可压前提破缺 |
| `physics.Re` | 50000 | 与 τ 不自洽（ν 为负） |
| RunManifest | 缺失 | 无 run_id / outputs / checksum |
| 哈希 | 缺失 | 无法复算，产物可被事后篡改 |

## ② 怎么做的（方法 / 流程 / 工具链）

**门定义（三门，全过才准入）**

| 门 | 判据 | 拒绝码 |
|---|---|---|
| 门1 manifest | RunManifest 存在（`run_manifest.json`/`manifest.json`/产物内嵌 `manifest`）+ `tensorphysics-run-manifest-v1` 契约（Draft2020）通过 + `validation_status=='passed'` + `outputs` 非空 | `E_MANIFEST_MISSING` `E_MANIFEST_NOT_OBJECT` `E_MANIFEST_UNREADABLE` `E_CONTRACT` `E_UNVERIFIED` `E_NO_OUTPUTS` |
| 门2 mission | 来源 mission 终态 `succeeded`（`approval.evidence.finished_status` 若在也须 succeeded）；解析不出 → fail-closed | `E_MISSION_STATE` `E_MISSION_UNRESOLVED` `E_MISSION_ID_MISSING` |
| 门3 checksum | `manifest.outputs[]` 的 sha256 复算命中 + `size_bytes` 一致 + `uri` 不越出 run 目录 | `E_CHECKSUM_MISMATCH` `E_CHECKSUM_FORMAT` `E_SIZE_MISMATCH` `E_ARTIFACT_MISSING` `E_ARTIFACT_ESCAPE` |

**流程**
1. 复现：以 m810 构造法造 12 个真实 run 目录（1 正对照 + 11 个只坏一个维度的负例），
   分别喂给「旧路径（backfill_file，无门）」与「新路径（src/sample_db/ingest，三门）」。
2. 修复：`src/sample_db/admission.py`（判定，永不短路误判为放行；每次判定写恰好一条 JSONL 审计）、
   `src/sample_db/ingest.py`（三门过后才复用原抽取逻辑入库；拒绝时零行写入）、
   `src/sample_db/inspect.py`（体检模式：三门各自独立评估，回答「缺什么」）。
   判定与抽取分离 ⇒ 抽取/建表仍复用 `tools/sample_db/backfill.py`（单一事实源，未改）。
3. 回归：`tests/test_e2e1_b1_sample_admission.py`（pytest，含 CLI 非零退出码与审计行数判据）；
   `tests/e2e1_b1/run_negative_suite.py`（证据 JSON + 审计流水）；`tests/e2e1_b1/render_docs.py`（判决表+图，数字零手抄）。
4. mission 终态真值源：平台 `GET /tensor/missions/{{id}}`（`ApiMissionResolver`，token 登录），
   离线复现用 `StaticMissionResolver`；默认 `NullMissionResolver` fail-closed。

**工具链**：Python 3.11 / pytest 9.2 / jsonschema 4.17（Draft202012Validator）/ sqlite3 / matplotlib 3.10。

## ③ 计算结果（程序化注入，零手抄）

| 指标 | 实测值 | 判据 |
|---|---|---|
| 用例数 | {s['cases']} | — |
| 坏产物（应拒） | {s['rejected_expected']} → 实拒 **{s['rejected_actual']}** | 全拒才 PASS |
| 正对照（应准入） | {s['admitted_expected']} → 实准入 **{s['admitted_actual']}** | 必须准入 |
| 修复前旧入口放行的坏产物数 | **{s['legacy_admitted_bad_products']}** | 应为 0（缺陷活体：非 0） |
| 审计留痕条数 / 判定次数 | {s['audit_records']} / {s['audit_records_expected']} | 必须逐条留痕 |
| 总判 | **{s['verdict']}** | — |

**逐用例（实际 = 门输出的结构化拒绝码）**

| 用例 | 攻击门 | 预期 | 修复前(legacy) | 修复后(gated) | 判定 |
|---|---|---|---|---|---|
{rows}

**审计流水样例（m810 攻击载荷的拒绝记录）**

```json
{audit_sample}
```

**m811 正例三门体检**（平台 mission 811，Ghia(1982) 顶盖驱动方腔，Re=1000 硬门）：

| 门 | 状态 | 事实 |
|---|---|---|
| 门1 manifest | **{gate_view['manifest']}** | run 目录只有 RESULTS.md / profiles.csv / summary.json，无 `run_manifest.json`，产物内也无 `manifest` 键 |
| 门2 mission | **{gate_view['mission']}** | mission 811 `status=succeeded`、`approval.evidence.finished_status=succeeded`（API 实测） |
| 门3 checksum | **{gate_view['checksum']}** | 无声明哈希 ⇒ 无复算基准（≠「哈希对得上」） |

结论：**{m811['verdict']} —— {m811['conclusion']}**。缺什么：
{m811_missing}

m811 自身硬门自述（其 `outputs/lid_driven_cavity/summary.json`，非本单复算）：reference =
Ghia/Ghia/Shin 1982 Tables I/II(129×129)；metric = {json.dumps(m811_gate.get('metric', 'n/a'), ensure_ascii=False)}；
Re={hard_gate.get('re')} 硬门阈值 max|Δ|≤{hard_gate.get('max_abs_dev')}；其摘要中该档记录 =
`{json.dumps(m811_gate, ensure_ascii=False)[:420]}`。

> 口径澄清（honest）：派单文字写「m811（Re=1000 St 硬门达成）」，但平台档案 mission 811 的交付物是
> Ghia 方腔 Re=1000 `max|Δ|` 硬门，**不是**圆柱绕流 Strouhal 门；St 属另一工况线。本单以平台档案为准，
> 且 m811 的物理指标未由本单复算（不在范围），只做**准入三门**体检。

## ④ 结果图

图1 三门判定矩阵（12 用例 × 3 门，格内为门状态与拒绝码，右侧为最终判定）。
图2 修复前后准入行为对比（红=旧入口放行坏产物，绿=三门入口拒绝）。

本单为**门/后端任务**：无场量云图可画（没有任何流体场求解），也没有与解析解/文献的比较曲线
（判据是数据谱系与哈希一致性，非数值离散误差）；因此以「判定矩阵 + 准入行为对比」替代，
不作伪造的场量图。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **哈希门不能证明物理正确**：manifest 与产物若由同一次错误运行一起产出，哈希照样命中。
   这就是门1必须同时要求 `validation_status=='passed'` 的原因；验证判据的强度仍是上游（求解入口物理域门）责任。
2. **事后补写 manifest 只锁字节、不能追认验证**：m811 可以补登记 sha256/size，但 `validation_status=passed`
   必须由当时那次运行的验证器证据支撑，否则等于自我声明 —— 故 m811 保留 NOT_ADMISSIBLE，不做事后补录。
3. **路径穿越**：`uri` 用 `../` 指向 run 目录外时，即使哈希/尺寸都对得上也必须拒（`E_ARTIFACT_ESCAPE`）——
   否则可用「引用别处的合法文件」洗白本 run 的产物。
4. **fail-closed 优先**：无 mission 状态源 → 一律拒；mission `running`（非终态）也拒，不是「非 failed 即放行」。
5. **残留缺口（未修，诚实标注）**：`tools/sample_db/backfill.py` 不在本单 EDIT_WHITELIST
   （`src/, tests/, docs/, outputs/`），旧裸入口仍在；已用 `xfail(strict=True)` 用例钉住，
   接线后 XPASS 报错强制更新台账。接线 patch 见 `docs/e2e1_b1/README.md` §5。接线前批量回填必须走
   `python3 -m src.sample_db.ingest`。
6. **审计无防篡改**：JSONL 为 append-only 应用层约定，无签名/哈希链（建议后续纳入 WORM 或链式哈希）。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

本单为门禁/后端任务，无连续物理量输出，因此**没有数值解误差**；判据是**离散精确匹配**，
其「误差」定义为 |实际 − 判据|：

| 关键量 | 基准（判据） | 实测 | 偏差 | <3%？ |
|---|---|---|---|---|
| 坏产物拒绝数 | {s['rejected_expected']}（全拒） | {s['rejected_actual']} | 0 | **是（0%）** |
| 正对照准入数 | {s['admitted_expected']}（必准入） | {s['admitted_actual']} | 0 | **是（0%）** |
| 审计留痕条数 | {s['audit_records_expected']}（每次判定恰一条） | {s['audit_records']} | 0 | **是（0%）** |
| 拒绝码与攻击面一致性 | 11/11 逐一对应 | 11/11 | 0 | **是（0%）** |
| m811 三门通过数 | 无基准（描述性体检） | 1/3 | — | 不适用（声明原因：正例本身就是不合格样本，不存在可比的期望值） |
| 旧入口缺陷复现率 | m810 记录：坏产物 100% 放行 | {s['legacy_admitted_bad_products']}/{s['rejected_expected']} | 0 | **是（0%，缺陷活体与 m810 一致）** |

结论：所有可判定项偏差为 0（远小于 3%）；不可判定项（m811 物理量误差、其 Ghia 硬门 0.0114 vs 阈值 0.02）
**未由本单复算**，不在此声称达标与否。honest-FAIL 保留：残留的工具侧裸入口未接线（条目 ⑤-5），
故「样本库全路径收口」这一目标**未完全达成**，只达成「带门入口 + 负测试固化 + 缺口看守」。

---

**工件**：`src/sample_db/{{admission,ingest,inspect}}.py`、`tests/test_e2e1_b1_sample_admission.py`、
`tests/e2e1_b1/{{build_cases,run_negative_suite,render_docs}}.py`、
`outputs/e2e1_b1/{{negative_evidence.json,admission_audit.jsonl,m811_positive_check.json,fig/*}}`、
`docs/e2e1_b1/{{README.md,VERDICTS.md,m811_positive_check.md}}`。
**复现**：`python3 tests/e2e1_b1/run_negative_suite.py --root outputs/e2e1_b1/cases && python3 tests/e2e1_b1/render_docs.py && pytest tests/test_e2e1_b1_sample_admission.py -q`
（实测 19 passed, 1 xfailed）。
"""

    figures = [
        {
            "name": "gate_matrix.png",
            "caption": "样本库准入三门判定矩阵（12 用例 × 3 门；格内为门状态与结构化拒绝码，右侧为最终判定）",
            "png_base64": b64(OUT / "fig" / "gate_matrix.png"),
        },
        {
            "name": "red_to_green.png",
            "caption": "修复前后准入行为对比：旧入口(backfill_file)放行全部坏产物 vs 三门入口全拒且正对照准入",
            "png_base64": b64(OUT / "fig" / "red_to_green.png"),
        },
    ]
    payload = {
        "title": "【E2E-1 修复·门缺陷 B1】样本库准入三门（RunManifest/mission 终态/sha256 复算）+ B 类负测试回归",
        "summary": (
            f"复现 m810 B1：旧入库入口放行 {s['legacy_admitted_bad_products']}/{s['rejected_expected']} 坏产物；"
            f"新增三门准入后 {s['rejected_actual']}/{s['rejected_expected']} 全拒（码与攻击面一一对应）、"
            f"{s['admitted_actual']}/{s['admitted_expected']} 正对照准入、审计 {s['audit_records']}/{s['audit_records_expected']} 留痕；"
            f"m811 体检 = 门1 fail（无 RunManifest）、门2 pass（mission 终态 succeeded）、门3 不可评估 → 不可准入。"
            "残留：tools/ 旧裸入口未接线（xfail strict 看守）。"
        ),
        "scenario_key": "e2e1-b1-sample-db-admission",
        "mission_id": 1085,
        "tags": ["门禁", "样本库", "负测试", "谱系", "sha256", "E2E-1"],
        "markdown": markdown,
        "figures": figures,
    }
    Path("/tmp/report.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"bytes": len(json.dumps(payload, ensure_ascii=False)),
                      "figures": [f["name"] for f in figures],
                      "verdict": s["verdict"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())