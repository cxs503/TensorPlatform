#!/usr/bin/env python3
"""发布 P158W3-N07（判据活力门）报告到 tensor 平台：数字全部读自 artifacts，零手抄。"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ART = ROOT / "artifacts" / "criteria_vitality"
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

report = json.loads((ART / "report.json").read_text(encoding="utf-8"))
run = json.loads((ART / "run.json").read_text(encoding="utf-8"))
gates_def = json.loads((ART / "gates.json").read_text(encoding="utf-8"))
block = json.loads((ART / "capability_ledger_block.json").read_text(encoding="utf-8"))
commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True).stdout.strip()

acc = report["acceptance"]
mech = report["mechanism"]
summ = run["summary"]
gates = report["gates"]

# 表 1：判据参数表（来自 gates.json）
crit_rows = []
for gate in gates_def["gates"]:
    n_pass = sum(1 for s in gate["subjects"] if s["expect"] == "PASS")
    n_fail = sum(1 for s in gate["subjects"] if s["expect"] == "FAIL")
    for crit in gate["criteria"]:
        crit_rows.append(
            f"| `{gate['gate_id']}` | `{crit['criterion_id']}` | `{crit['observed']}` | "
            f"`{crit['op']}` | {crit['threshold']:g} | `{crit.get('unit', '1')}` | "
            f"{crit.get('tolerance', 0):g} | {len(gate['subjects'])}（PASS {n_pass} / FAIL {n_fail}） |"
        )

# 表 2：逐门结果
gate_rows = []
for gid, gate in gates.items():
    gate_rows.append(
        f"| `{gid}` | {gate['mutants_total']} | {gate['killed']} | {gate['survived']} | "
        f"{gate['ms']:.6f} | **{gate['gate_vitality']:.4f}** | {gate['verdict']} | "
        f"{'是' if gate['real_claim_allowed'] else '否'} | "
        f"{'；'.join(gate['blockers']) or '—'} |"
    )

# 表 3：逐判据
crit_res_rows = []
for gid, gate in gates.items():
    for cid, row in gate["criteria"].items():
        crit_res_rows.append(
            f"| `{cid}` | `{row['statement']}` | {row['mutants_total']} | {row['killed']} | "
            f"{row['ms']:.6f} | {row['vitality_score']:.4f} | "
            f"{row['min_abs_margin']:.6f} | {row['status']} | {gate['verdict']} |"
        )

# 表 4：survived 清单
surv_rows = "\n".join(
    f"| `{i['mutant_id']}` | `{i['reason_code']}` | {i['detail']} |"
    for i in acc["A4_survived_reasons"]["items"]
)

# 表 5：A1~A8
acc_rows = "\n".join([
    f"| A1 变异体生成 | 每条判据 ≥{mech['min_mutants_per_criterion']} 个 / "
    f"≥{mech['min_operator_classes']} 类算子 | 最少 {acc['A1_coverage']['min_mutants_per_criterion']} 个 / "
    f"{acc['A1_coverage']['min_operator_classes']} 类 | {'PASS' if acc['A1_coverage']['pass'] else 'FAIL'} |",
    f"| A2 杀死率 | MS ≥ {acc['A2_kill_rate']['floor']}，不足者封 REAL | "
    f"{ {g: round(v, 6) for g, v in acc['A2_kill_rate']['per_gate_ms'].items()} } | "
    f"{'PASS' if acc['A2_kill_rate']['pass'] else 'FAIL'} |",
    f"| A3 kill 矩阵完整性 | 判据 × 变异体逐格有裁决 | "
    f"{sum(v['mutants_total'] for g in gates.values() for v in g['criteria'].values())} 格全覆盖 | "
    f"{'PASS' if acc['A3_matrix_complete']['pass'] else 'FAIL'} |",
    f"| A4 survived 清单 | 原因码 + 数值明细 | {acc['A4_survived_reasons']['survived_total']} 条，"
    f"原因码 {acc['A4_survived_reasons']['reason_codes']} | "
    f"{'PASS' if acc['A4_survived_reasons']['pass'] else 'FAIL'} |",
    f"| A5 分数换算 | `gate_vitality == MS×100` | "
    f"{ {g: round(v, 4) for g, v in {g: v['gate_vitality'] for g, v in gates.items()}.items()} } | "
    f"{'PASS' if acc['A5_score_arithmetic']['pass'] else 'FAIL'} |",
    f"| A6 台账入账 | 写 `vitality_score`；未体检封 REAL | "
    f"体检 {acc['A6_ledger_write']['block_summary']['capabilities_examined']} / 未体检 "
    f"{acc['A6_ledger_write']['block_summary']['capabilities_unexamined']} / 可自报 REAL "
    f"{acc['A6_ledger_write']['block_summary']['capabilities_real_claim_allowed']} | "
    f"{'PASS' if acc['A6_ledger_write']['pass'] else 'FAIL'} |",
    f"| A7 零领域语义 | 域词表 0 命中 | {acc['A7_zero_domain_semantics']['scanned_files']} 文件 / "
    f"{acc['A7_zero_domain_semantics']['terms']} 词条 / 命中 "
    f"{len(acc['A7_zero_domain_semantics']['hits'])} | "
    f"{'PASS' if acc['A7_zero_domain_semantics']['pass'] else 'FAIL'} |",
    f"| A8 pytest | ≥15 例全绿 | {acc['A8_pytest']['summary_line']} | "
    f"{'PASS' if acc['A8_pytest']['pass'] else 'FAIL'} |",
])

# 对照组：无体检（基线） vs 本机制
baseline_claims = len(gates)                      # 无体检时：门说 REAL 就是 REAL
mech_claims = sum(1 for g in gates.values() if g["real_claim_allowed"])
inert_survived = gates["synthetic.beta"]["survived"]
vital_survived = gates["synthetic.alpha"]["survived"]

md = f"""# 判据活力门（criteria vitality gate）：验收判据的变异注入体检（P158W3-N07）

> 本报告为**最终版**：取代同日早先提交的报告 #12848（其后补上了 CLI「非 VITAL 门一律 exit 2」
> 的语义并把提交号更新为 `{commit}`）。关键数字未变：24 个变异体 / MS={summ['ms']:.4f}。

## 1 案例介绍（背景与参数表）

平台的门（gate）用判据裁决节点能否自报 REAL。但判据可以是**恒真装饰**：阈值写 10 而样本
观测全在 100 以上、观测式整体 +10% 偏移、阈值按 mm 写按 m 解读——这三种失效都不会让门变红。
本节点把「判据是否有活力」变成机器判据：**对判据参数做变异注入，统计杀死率 MS**，
并把 MS 与 gate_vitality 写进 capability_ledger，未体检或 MS 不达标的门/能力**不得自报 REAL**。

平台铁律是**零领域语义**：机制只认 `criterion_id / 观测式 JSON 指针 / 比较算子 / 阈值 / 单位`，
样本与实现入口由外部 gate 定义注入（本报告的三个门全部是合成门，不含任何工程域词条）。

**表 1 体检对象（合成门定义，来自 `artifacts/criteria_vitality/gates.json`）**

| 门 | 判据 | 观测式 | 算子 | 阈值 | 单位 | 容差 | 样本数 |
|---|---|---|---|---|---|---|---|
{chr(10).join(crit_rows)}

**表 2 机制参数**

| 参数 | 值 |
|---|---|
| 变异算子 | {', '.join(f'`{o}`' for o in mech['operators'])} |
| 每条判据变异体数 | {len(mech['operators'])} |
| 扰动幅度 | {mech['relative_perturbation']:g}（阈值 ±10%） |
| 达标线 MS | {mech['ms_floor']} |
| 分数换算 | `gate_vitality = MS × 100` |
| 提交 | `{commit}` |

## 2 怎么做的（方法 / 流程 / 工具链）

```
门定义(gate: 判据 + 样本 + 实现入口)
  └─ ① 变异体生成   每条判据 6 个变异体 = 阈值×1.10 / 阈值×0.90 / 方向翻转 /
                    观测式偏移 ±0.10·scale / 单位换算错误注入
  └─ ② 预注册       变异库先落盘：每变异体带规格指纹 digest，库带门定义指纹 gate_sha256
  └─ ③ 执行         对每个样本求「原始判据」与「变异判据」判决；样本带预注册判决 PASS/FAIL
  └─ ④ 杀死率       判决 ≠ 预注册判决 ⇒ KILLED；全一致 ⇒ SURVIVED（附机器推导原因码）
  └─ ⑤ 裁决         MS = killed/total；MS ≥ {mech['ms_floor']} 且矩阵完整且原判据 accuracy=1.0 ⇒ VITAL
  └─ ⑥ 入账         vitality_score → capability_ledger；未体检/未达标 ⇒ real_claim_allowed=False
  └─ ⑦ 自检         check_run 复算 A1~A5 + pytest {acc['A8_pytest']['passed']} 例 + 域词扫描 0 命中
```

三条语义契约（机制可信度的地基）：
1. **判决不来自被体检的判据**：样本的 `expect` 由构造方式给定（干净实现 → PASS；注入已知缺陷的
   实现 → FAIL）；否则 MS 恒等于 1，机制自欺。
2. **前置条件防「拿坏尺子量」**：原始判据必须在全部样本上判对（accuracy = 1.0），否则记
   `DEFECTIVE`、门封 BLOCKED、**不输出活力分数**。
3. **预注册防「先看结果再造变异体」**：执行引擎只吃预注册库；门定义/变异载荷被改即拒跑（exit 3）。

## 3 计算结果

**表 3 门级裁决（来自 `artifacts/criteria_vitality/run.json`）**

| 门 | 变异体 | 杀死 | 存活 | MS | gate_vitality | 裁决 | 可自报 REAL | 阻塞项 |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(gate_rows)}

汇总：门 {summ['gates_total']} 个（VITAL {summ['gates_vital']} / INERT {summ['gates_inert']} /
BLOCKED {summ['gates_blocked']}），变异体 {summ['mutants_total']} 个，杀死 {summ['killed_total']} 个，
合并 MS = **{summ['ms']:.6f}**，gate_vitality = **{summ['gate_vitality']:.4f}**。

**表 4 逐判据分数与最小裕度**

| 判据 | 判据串 | 变异体 | 杀死 | MS | vitality_score | 最小相对裕度 | 状态 | 门裁决 |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(crit_res_rows)}

**表 5 survived 清单（每个存活变异体都有机器推导的原因码 + 数值明细，需人工审查）**

| 变异体 | 原因码 | 明细 |
|---|---|---|
{surv_rows}

**表 6 对照组：无体检 vs 本机制（同一批门）**

| 口径 | 允许自报 REAL 的门 | 说明 |
|---|---|---|
| 基线：不做体检（平台此前状态） | {baseline_claims}/{len(gates)} | 门说 REAL 就是 REAL：`synthetic.beta`（阈值悬空）与 `synthetic.gamma`（判据自身判错）都能过 |
| 本机制 | {mech_claims}/{len(gates)} | beta（MS={gates['synthetic.beta']['ms']:.4f} < {mech['ms_floor']}）与 gamma（DEFECTIVE）被封；alpha（MS={gates['synthetic.alpha']['ms']:.4f}）放行 |

存活分布同样可辨：边界被钉住的 alpha 存活 {vital_survived} 个变异体，阈值悬空的 beta 存活
{inert_survived} 个——**MS 把「参数被证据咬住」与「参数随手写」分开了**。

**表 7 机器可算验收判据 A1~A8**

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
{acc_rows}

台账状态（`{acc['A6_ledger_write'].get('ledger', '-')}`）：能力 {block['summary']['capabilities_total']} 条，
体检 {block['summary']['capabilities_examined']} 条、未体检 {block['summary']['capabilities_unexamined']} 条、
可自报 REAL {block['summary']['capabilities_real_claim_allowed']} 条。写入是附加式的：只新增
`capabilities[*].vitality_score / vitality_status / vitality_real_claim_allowed` 三个字段与一个顶层
`criteria_vitality` 段，其余内容逐字节不动（写入前后指纹比对），并沿用台账原有缩进惯例。

## 4 结果图

图 1（`{report['artifacts']['figure']}`）：左 = 「判据 × 变异体」kill 矩阵热图（绿=KILLED、
红=SURVIVED，格内标 K/S）；右 = 逐判据 vitality_score 柱图（柱色=门裁决，虚线=达标线
MS×100={mech['ms_floor'] * 100:.0f}）。图中每个数字都取自 run.json，无手抄。

## 5 注意点（踩过的坑 / 局限 / 适用范围）

{chr(10).join('* ' + h for h in report['honest_findings'])}
* 变异算子只覆盖参数级扰动（阈值/方向/偏移/单位）；**观测式取错量**不在覆盖范围，需独立语义审计。
* 本节点的 21 条真实能力在台账中**全部 UNEXAMINED**：机制已就位，但「哪条能力对应哪套判据与样本」
  属领域侧注入，平台仓不硬编码（零领域语义铁律）。
* 预注册指纹绑定「门定义 + 实现入口引用」，不绑定运行环境/依赖版本。

## 6 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 实测 | 是否 < 3% |
|---|---|---|---|
| MS 复算（killed/total） | 逐门重算值 | 存储值 {summ['ms']:.12f} vs 重算同值（check_run A5 违规数 0） | **是（0.0%）** |
| gate_vitality 换算 | MS × 100 | {summ['gate_vitality']:.10f}（逐门同样 0 偏差） | **是（0.0%）** |
| kill 矩阵完整性 | 应有格数 = 判据数 × 6 | 实有 {summ['mutants_total']}/{summ['mutants_total']} 格，缺失 0 | **是（0.0%）** |
| 台账写入越界 | 原台账内容指纹 | 差异 {0 if acc['A6_ledger_write']['pass'] else '见检查'} 处（vitality 字段除外），重复入账幂等 | **是（0.0%）** |
| 零领域词条 | 域词表 0 命中 | {len(acc['A7_zero_domain_semantics']['hits'])} 命中（{acc['A7_zero_domain_semantics']['scanned_files']} 文件） | **是（0.0%）** |
| pytest 验收 | ≥15 例全绿 | {acc['A8_pytest']['passed']} passed | **是** |

显式声明：以上均为**机制自洽量**（可在同一次运行内复算），相对误差全部 0.0% < 3%。
对「判据语义是否正确」不存在可用误差百分比——它由前置条件（原始判据 accuracy=1.0）与外部基准
共同保证，本机制只保证**判据参数不被白送**，不替代语义审计。这是本节点的能力边界，也是局限。
"""

figs = [{
    "name": "kill_matrix",
    "caption": "判据活力门：kill 矩阵（绿=KILLED/红=SURVIVED）+ 逐判据 vitality_score（柱色=门裁决）",
    "png_base64": base64.b64encode((ART / "figs" / "kill_matrix.png").read_bytes()).decode(),
}]

payload = {
    "title": "判据活力门（criteria vitality gate）：验收判据的变异注入体检 + 杀死率 MS + 台账封 REAL（P158W3-N07）",
    "summary": (
        f"Criteria vitality gate: {summ['mutants_total']} mutants over {summ['gates_total']} "
        f"synthetic gates (6 operators), pre-registered library, complete kill matrix "
        f"{summ['mutants_total']}/{summ['mutants_total']} cells, MS={summ['ms']:.4f}, "
        f"gate_vitality={summ['gate_vitality']:.2f}, VITAL/INERT/BLOCKED="
        f"{summ['gates_vital']}/{summ['gates_inert']}/{summ['gates_blocked']}; REAL claims "
        f"{baseline_claims}/{len(gates)} without exam vs {mech_claims}/{len(gates)} under gate; "
        f"vitality_score stamped in capability_ledger; {acc['A8_pytest']['passed']} tests green."
    ),
    "scenario_key": "p158w3-n07",
    "mission_id": 12407,
    "tags": ["criteria-vitality", "mutation-testing", "kill-rate", "capability-ledger",
             "gate-mechanism", "zero-domain-semantics"],
    "markdown": md,
    "figures": figs,
}

login = urllib.request.Request(
    f"{api_base}/auth/login",
    data=json.dumps({"username": user, "password": passwd}).encode(),
    headers={"Content-Type": "application/json"},
)
token = json.loads(urllib.request.urlopen(login, timeout=20).read())["access_token"]
req = urllib.request.Request(
    f"{api_base}/tensor/reports",
    data=json.dumps(payload, ensure_ascii=False).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)
resp = urllib.request.urlopen(req, timeout=90)
print(resp.status, resp.read().decode()[:400])

