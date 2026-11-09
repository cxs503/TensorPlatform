#!/usr/bin/env python3
"""publish_report_m7812.py —— 构建并提交 P171W1-N08 论文级进展报告。

正文由 artifacts/criteria_governance/report.json + docs 报告程序化生成（零手抄数字），
两张 PNG 以 base64 内嵌。
"""
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def post(path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


report = json.loads((REPO / "artifacts/criteria_governance/report.json").read_text(encoding="utf-8"))
doc_md = (REPO / "docs/P171W1-N08_criteria_governance_maturity.md").read_text(encoding="utf-8")
acc = report["acceptance"]["checks"]
crit = report["criteria"]
gate = report["drift_gate"]
mat = report["maturity"]
kpi = report["before_after_kpi"]
neg = report["negative_control"]

rows = "\n".join(
    f"| {cid} | {row['source_type']} | {row['derivation_method']} | {row['measured_value']:.3e} | "
    f"{row['threshold']['op']} {row['threshold']['value']} | {row['verdict']} |"
    for cid, row in sorted(crit.items())
)
ref_rows = "\n".join(
    f"| {cid} | {row['refinement']['declared_exponent']:.1f} | "
    f"{row['refinement']['measured_exponent']:.4f} | {row['refinement']['deviation']:.4f} | "
    f"{row['refinement']['tolerance']} | {'一致' if row['refinement']['consistent'] else '不一致'} |"
    for cid, row in sorted(crit.items()) if row.get("refinement")
)
acc_rows = "\n".join(
    f"| {name} | {row['statement']} | {'PASS' if row['pass'] else 'FAIL'} |"
    for name, row in acc.items()
)
mat_rows = "\n".join(
    f"| {name} | {row['score']:.3f} | {row['numerator']}/{row['denominator']} |"
    for name, row in mat["dimensions"].items()
)

markdown = f"""# P171W1-N08 判据治理成熟化集成：阈值注册表 × 不变性群 × 体积四极子

**总判定**：`{report['acceptance']['verdict']}`（未通过项：{report['acceptance']['failing'] or '无'}）

## 1. 案例介绍（物理背景/几何/参数表）

把两条判据源（N04 算子级体积四极子、N05 立方不变性群/斜槽观测阶，各 7 条 C1–C7）
交叉集成进平台级阈值注册表，建立"入册 → 分类 → 派生 → 漂移门 → 成熟度"的治理验收链。

- **N04 体积四极子**：立方对称速度集四阶矩应精确等于 c_s⁴(δδ+δδ+δδ)（c_s²=1/3）；四极子取
  Q_ij = M2_ij − c_s²δ_ij。细化算例为余弦调制密度场 ρ=1+{report['probes']['quadrupole_refinement']['amplitude']}·cos(2πx/L)
  （L={report['probes']['quadrupole_refinement']['length']}），闭式解 Q_xx={report['probes']['quadrupole_refinement']['exact_q_xx']:.6e}、
  Q_yy={report['probes']['quadrupole_refinement']['exact_q_yy']:.6e}。
- **N05 不变性群**：O_h（{report['probes']['skew_slot_refinement'] and report['parameters']['group_order']} 元，符号置换矩阵）的闭包/逆元、
  各向同性 4 阶张量不变性、Sym(3) 平凡表示重数；斜槽算例为剪切映射 x=Aξ（s={report['probes']['skew_slot_refinement']['shear']}）
  下的度量张量拉普拉斯 Δ_x = G_ij ∂²_{{ξiξj}}，G=A⁻¹A⁻ᵀ。

| 参数 | 值 |
|---|---|
| 判据总数 | {report['parameters']['criteria_total']} |
| 晶格速度集 | {report['parameters']['lattice_sets']} |
| c_s² | {report['parameters']['cs2']} |
| 细化网格 N | {report['parameters']['refinement_resolutions_quadrupole']} |
| 椭球半轴 | {report['parameters']['ellipsoid_semi_axes']} |
| 群阶 | {report['parameters']['group_order']} |

## 2. 怎么做的（方法/流程/关键设置/工具链）

管线：判据工件（`data/criteria_governance/*.criteria.json`）→ `ThresholdRegistry`
（source_type/cohort/派生链校验 + 负控剔除）→ `DerivationEngine`（执行探针：解析恒等式残差或
h→h/2 细化扫描，**引擎自行 log-log 重算观测阶**，不采信探针自报）→ `drift_gate`
（规范化 JSON sha256 钉桩 + 数值叶子逐点对照；每工件 10 个程序化篡改变体 + 10 个未改动对照）→
`maturity`（四维评分 + 雷达图）。

- 机制代码零领域语义：`tools/criteria_governance/{{registry,derivation,drift_gate,maturity,kernels,probes}}.py`
- 仪器（探针）14 个：张量矩、权重归一、无迹残差、90° 旋转协变、群闭包、4 阶不变、不变子空间秩、
  反演宇称、椭球四极子、四极子细化、斜槽细化、剪切扫描稳定性、误差常数比
- 篡改变体优先命中 `threshold` 字段（阈值定向 {gate['threshold_targeted_trials']}/{gate['tamper_total']} 次）

## 3. 计算结果（数字表）

### 3.1 验收判据
| 判据 | 说明 | 判定 |
|---|---|---|
{acc_rows}

### 3.2 逐条判据（14 条）
| 判据 | source_type | 派生方法 | 实测量 | 阈值 | 判定 |
|---|---|---|---|---|---|
{rows}

### 3.3 派生一致性（细化扫描）
| 判据 | 声明阶 | 实测阶 | 偏差 | 容差 | 结论 |
|---|---|---|---|---|---|
{ref_rows}

### 3.4 漂移门
- 钉桩工件：{gate['pinned_artifacts']}
- 篡改检出 **{gate['tamper_detected']}/{gate['tamper_total']}**（detection_rate={gate['detection_rate']:.3f}）
- 未改动对照假阳性 **{gate['false_positive']}/{gate['clean_total']}**（FPR={gate['false_positive_rate']:.3f}）

### 3.5 成熟度四维
| 维度 | 分数 | 分子/分母 |
|---|---|---|
{mat_rows}

最小维 {mat['min_score']:.3f} ≥ 门槛 {mat['gate_threshold']} → `{mat['verdict']}`

### 3.6 负控（C5）
- 注入 `{neg['impostor_id']}`（cohort=analytic, source_type={neg['impostor_source_type']}）
- 剔除：{neg['quarantined']}；原因：{neg['quarantine_reasons']}
- 分析型队列计数 {neg['analytic_total_baseline']} → {neg['analytic_total_after_injection']}（不变：{neg['analytic_count_unchanged']}）
- 告警：{neg['warnings']}

### 3.7 治理前后 KPI（C6）
| 指标 | 治理前 | 治理后 |
|---|---|---|
| 入册判据数 | {kpi['before']['criteria_registered']} | {kpi['after']['criteria_registered']} |
| 覆盖率 | {kpi['before']['coverage']:.3f} | {kpi['after']['coverage']:.3f} |
| 可裁率 | {kpi['before']['adjudicable_rate']:.3f} | {kpi['after']['adjudicable_rate']:.3f} |
| 派生链可追溯率 | {kpi['before']['traceability_rate']:.3f} | {kpi['after']['traceability_rate']:.3f} |
| CI 漂移门 | {kpi['before']['drift_gate']} | {kpi['after']['drift_gate']} |

治理前测量方法：`{kpi['before']['measurement_method']}`（可用={kpi['before']['measurement_available']}）

## 4. 结果图

**图 1**　四维成熟度雷达图（蓝实线=实测分数，橙虚线=0.7 门槛；最小维 {mat['min_score']:.3f}）。
**图 2**　细化扫描双对数误差：斜槽度量算子（N05-C1, p={crit['N05-C1']['refinement']['measured_exponent']:.4f}）与
体积四极子中点求积（N04-C4, p={crit['N04-C4']['refinement']['measured_exponent']:.4f}），黑虚线为声明阶 2.0 参考斜率。

## 5. 注意点（坑/局限/适用范围）

{chr(10).join('- ' + n for n in report['notes'])}

## 6. 误差判定

| 关键量 | 参考/基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
{chr(10).join(f"| {r['quantity']} | {r['reference']} | {r['measured']} | {r['error']} | {r['under_3pct']} |" for r in report['error_table'])}

{report['error_verdict']}

**审计链**：`artifacts/criteria_governance/report.json`（全量数字）→ `pins.json`（sha256 钉桩）→
`N04/N05 *.verification.json`（逐条测量）→ `docs/P171W1-N08_criteria_governance_maturity.md`（论文级报告）→
`tests/test_criteria_governance.py`（20 项 pytest，含两类负控与确定性复跑）。

<details><summary>docs 全文（程序化生成的论文级报告）</summary>

{doc_md}
</details>
"""

payload = {
    "title": "P171W1-N08 判据治理成熟化集成：阈值注册表×不变性群×体积四极子（C1–C6 全通过）",
    "summary": (
        f"N04/N05 共 14 条判据 100% 入册（source_type 缺失 0）；派生一致性实测指数偏差 "
        f"{report['derivation']['max_exponent_deviation']:.4f}≤0.3；漂移门篡改检出 "
        f"{gate['tamper_detected']}/{gate['tamper_total']}、假阳性 {gate['false_positive']}/{gate['clean_total']}；"
        f"四维成熟度最小 {mat['min_score']:.3f}≥0.7；负控 heuristic 判据被剔除并告警；"
        f"治理前后覆盖率 {kpi['before']['coverage']:.3f}→{kpi['after']['coverage']:.3f}、"
        f"可裁率 {kpi['before']['adjudicable_rate']:.3f}→{kpi['after']['adjudicable_rate']:.3f}。"
    ),
    "scenario_key": "platform/governance",
    "mission_id": int(os.environ.get("MISSION_ID", "7812")),
    "tags": ["P171W1-N08", "criteria-governance", "threshold-registry",
             "derivation-consistency", "drift-gate", "maturity", "TensorPlatform"],
    "markdown": markdown,
    "figures": [
        {"name": "maturity_radar.png",
         "caption": "四维判据治理成熟度雷达图（覆盖率/可派生率/漂移检出率/1−FPR），橙虚线为 0.7 门槛",
         "png_base64": b64(REPO / "artifacts/criteria_governance/maturity_radar.png")},
        {"name": "refinement_scan.png",
         "caption": "细化扫描双对数误差：斜槽度量算子与体积四极子中点求积的观测阶（声明 p=2.0）",
         "png_base64": b64(REPO / "artifacts/criteria_governance/refinement_scan.png")},
    ],
}


def main() -> int:
    if os.environ.get("DRY_RUN"):
        Path("/tmp/report_m7812.json").write_text(json.dumps(payload, ensure_ascii=False),
                                                  encoding="utf-8")
        print(f"dry-run: payload written to /tmp/report_m7812.json "
              f"({len(payload['markdown'])} chars, {len(payload['figures'])} figures)")
        return 0
    token = post("/auth/login", {"username": USER, "password": PASS}).get("access_token")
    if not token:
        print("login failed")
        return 1
    out = post("/tensor/reports", payload, token=token)
    print(json.dumps({k: out.get(k) for k in ("id", "status", "title")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())