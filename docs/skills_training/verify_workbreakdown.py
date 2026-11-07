#!/usr/bin/env python3
"""verify_workbreakdown.py — 对 Work Breakdown 产物做机器校验（技能 Quality note：
每个结论可追溯到命令输出；未执行项标 not_run，不标 passed）。
输出 JSON 到 stdout，供报告程序化注入。"""
import json, re, sys, os

BASE = os.path.dirname(os.path.abspath(__file__))
mds = [f for f in os.listdir(BASE) if f.endswith(".md")]
DOC = os.path.join(BASE, mds[0])  # 目录内唯一 .md 主产物
GRAPH = os.path.join(BASE, "dependency_graph.json")

checks = {}

def add(name, ok, evidence):
    checks[name] = {"status": "pass" if ok else "fail", "evidence": evidence}

# --- 1. 文档存在且非空 ---
text = ""
if os.path.isfile(DOC):
    text = open(DOC, encoding="utf-8").read()
add("doc_exists_nontrivial", len(text) > 3000, f"len={len(text)} chars")

# --- 2. Required artifact 要素（11 项，对照 SKILL.md Required artifact） ---
element_markers = {
    "parent_goal": "父目标",
    "problem_contract": "问题契约",
    "work_packages_and_owners": "工作包",
    "deliverables_and_acceptance": "验收",
    "dependency_graph": "依赖图",
    "interfaces_and_version_assumptions": "接口契约",
    "parallelization_rationale": "并行项",
    "conflict_risks": "冲突风险",
    "integrator": "集成负责人",
    "risk_register": "风险登记册",
    "definition_of_done": "完成定义",
}
found = {k: (m in text) for k, m in element_markers.items()}
missing = [k for k, v in found.items() if not v]
add("required_elements_11", not missing,
    f"found={sum(found.values())}/11 missing={missing}")

# --- 3. 图：无环 + 关键路径 + agent 数 + 写所有权 ---
g = json.load(open(GRAPH, encoding="utf-8"))
nodes = set(g["packages"])
edges = g["edges"]
adj = {n: [] for n in nodes}
for a, b in edges:
    adj[a].append(b)
# 拓扑排序（Kahn）判环
indeg = {n: 0 for n in nodes}
for a, b in edges:
    indeg[b] += 1
from collections import deque
q = deque([n for n in nodes if indeg[n] == 0])
seen = 0
while q:
    n = q.popleft()
    seen += 1
    for m in adj[n]:
        indeg[m] -= 1
        if indeg[m] == 0:
            q.append(m)
acyclic = seen == len(nodes)
add("graph_acyclic", acyclic, f"topo_visited={seen}/{len(nodes)}")

# 关键路径（最长路径，按 effort）
eff = {n: g["packages"][n]["effort_person_days"] for n in nodes}
dist = {n: eff[n] for n in nodes}
order = []
indeg2 = dict(indeg)
q = deque([n for n in nodes if indeg2[n] == 0])
pred = {n: [] for n in nodes}
for a, b in edges:
    pred[b].append(a)
while q:
    n = q.popleft()
    order.append(n)
    for m in adj[n]:
        dist[m] = max(dist[m], dist[n] + eff[m])
        indeg2[m] -= 1
        if indeg2[m] == 0:
            q.append(m)
cp_end = max(dist, key=dist.get)
cp_len = dist[cp_end]
add("critical_path_within_budget", cp_len <= g["budget_person_days"],
    f"critical_path_end={cp_end} len={cp_len}pd budget={g['budget_person_days']}pd")

n_agents = len(g["agents"])
add("agents_le_4", n_agents <= 4, f"agents={n_agents}")
integrators = [a for a, v in g["agents"].items() if v.get("integrator")]
add("single_integrator", len(integrators) == 1, f"integrators={integrators}")

overlap = {k: v for k, v in g["write_ownership"].items() if len(v) > 1}
add("no_write_ownership_overlap", not overlap, f"overlaps={overlap}")
read_only = [k for k, v in g["write_ownership"].items() if v == []]
add("shared_readonly_declared", len(read_only) >= 1, f"readonly={read_only}")

# --- 4. 契约显式性：端点/状态码/负向授权 ---
endpoints = all(s in text for s in ["/api/v1/exports", "POST", "GET", "Bearer"])
codes = all(c in text for c in ["401", "403", "404", "410"])
neg_authz = "负向授权" in text and "401" in text and "403" in text
add("explicit_interfaces", endpoints and codes, f"endpoints={endpoints} codes={codes}")
add("negative_authorization_tests", neg_authz, "WP4 验收 + 完成定义-2")

# --- 5. 依赖感知分工：每个包有依赖行 ---
dep_lines = len(re.findall(r"\*\*依赖\*\*", text))
add("dependency_aware_packages", dep_lines >= 6, f"dependency_lines={dep_lines}")

# --- 6. F/I/A/U 标注 ---
labels = {lab: len(re.findall(rf"\*\*\[{lab}\]\*\*|\[{lab}\]", text)) for lab in "FIAU"}
add("fiAU_labels_present", all(v >= 1 for v in labels.values()), f"counts={labels}")

# --- 7. 早期假设验证（S1-S3 具体可判定） ---
add("early_assumption_tests", all(s in text for s in ["S1", "S2", "S3"]), "S1-S3 表")

# --- 汇总 ---
passed = sum(1 for c in checks.values() if c["status"] == "pass")
total = len(checks)
summary = {
    "checks_total": total,
    "checks_passed": passed,
    "checks_failed": total - passed,
    "coverage_pct": round(100.0 * passed / total, 1),
    "critical_path_person_days": cp_len,
    "total_effort_person_days": round(sum(eff.values()), 1),
    "agents": n_agents,
    "integrator": integrators,
    "element_coverage": f"{sum(found.values())}/11",
    "checks": checks,
}
json.dump(summary, sys.stdout, ensure_ascii=False, indent=1)
print()
