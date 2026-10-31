#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m845 返工报告 779：审计数据层（唯一数字来源）。

把 live DB、播种 SQL、提案源文件、播种任务 result 全部重算成
artifacts/audit_report_m845.json —— 报告 markdown / 图件 / POST payload
都只读这个 JSON，保证「数字零手抄、三处一致」。

只读输入：
  PostgreSQL tensorplatform（campaign_plans / campaign_milestones / campaign_nodes）
  /srv/tensor/agent/work/m832/seed_plan4.sql
  /srv/tensor/agent/work/m832/result.json
  /srv/tensor/agent/work/m775/proposals/p2/w1/prop-775.json
  /srv/tensor/agent/proposals/p2/w1/prop-776.json
  /srv/tensor/agent/work/m777/docs/proposals/prop-777.json
"""
import hashlib
import json
import os
import re
import subprocess
from collections import defaultdict

WORK = "/srv/tensor/agent/work/m845"
ART = os.path.join(WORK, "artifacts")
SQL_PATH = "/srv/tensor/agent/work/m832/seed_plan4.sql"
RES_PATH = "/srv/tensor/agent/work/m832/result.json"
PROP_PATHS = {
    775: "/srv/tensor/agent/work/m775/proposals/p2/w1/prop-775.json",
    776: "/srv/tensor/agent/proposals/p2/w1/prop-776.json",
    777: "/srv/tensor/agent/work/m777/docs/proposals/prop-777.json",
}
PLAN_ID, PLAN3_ID = 4, 3

# 派发器侧仓库白名单（campaign_dispatcher.py:31-32，只读引用）
REPO_WHITELIST = {"TensorPhysics", "TensorDEM", "TensorLBM", "TensorPlatform", "ship-platform"}
REPO_ALIAS = {"TensorFEM": "TensorPhysics"}


def psql_json(sql):
    out = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "tensorplatform", "-At", "-c", sql],
        capture_output=True, text=True, check=True).stdout.strip()
    return json.loads(out) if out else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def load_db():
    plan = psql_json(
        "select row_to_json(t) from (select id,name,status,plan_version,planned_start,planned_end,"
        "metadata,created_at,updated_at from campaign_plans where id=%d) t;" % PLAN_ID)
    ms = psql_json(
        "select json_agg(t) from (select id,name,status,progress_pct,planned_start,planned_end,updated_at "
        "from campaign_milestones where plan_id=%d order by id) t;" % PLAN_ID)
    nodes = psql_json(
        "select json_agg(t) from (select id, milestone_id, metadata->>'ext_id' ext_id, dependencies, "
        "metadata->>'repo' repo, metadata->>'is_maturity' is_maturity, metadata->>'proposal_src' proposal_src, "
        "name, length(coalesce(metadata->>'goal','')) goal_len, "
        "jsonb_array_length(coalesce(metadata->'acceptance_criteria','[]'::jsonb)) ac_len, "
        "coalesce(metadata->>'goal','') ~ '成熟化' goal_has_maturity_word, "
        "coalesce(metadata->>'goal','') goal_text "
        "from campaign_nodes where plan_id=%d order by id) t;" % PLAN_ID)
    p3 = psql_json(
        "select json_agg(t) from (select id, metadata->>'ext_id' ext, name from campaign_nodes "
        "where plan_id=%d order by id) t;" % PLAN3_ID)
    dup = psql_json(
        "select row_to_json(t) from (select count(*) total, count(distinct metadata->>'ext_id') distinct_ext "
        "from campaign_nodes) t;")
    return plan, ms, nodes, p3, dup


def parse_sql(path):
    """解析播种 SQL：每节点 {ext_id, milestone_id, dependencies} + plan status 字面量。"""
    txt = open(path, encoding="utf-8").read()
    stmts = txt.split("INSERT INTO campaign_nodes")[1:]
    out = []
    for st in stmts:
        ext = re.search(r'"ext_id":"(W4-N\d\d)"', st)
        ms = re.search(r"VALUES\s*\(\s*%d,\s*(\d+),\s*NULL," % PLAN_ID, st)
        dep = re.search(r"'task',\s*\n\s*(NULL|ARRAY\[[0-9, ]*\]|\{[0-9, ]*\}),", st)
        if not (ext and ms and dep):
            raise SystemExit("SQL 解析失败: %r %r %r" % (ext, ms, dep))
        raw = dep.group(1)
        deps = [] if raw == "NULL" else [
            int(x) for x in raw.replace("ARRAY", "").strip("{}[]").split(",") if x.strip()]
        out.append({"ext_id": ext.group(1), "milestone_id": int(ms.group(1)), "dependencies": deps})
    plan_status = re.search(r"\n\s*'(draft|active)',", txt)
    return out, (plan_status.group(1) if plan_status else None)


def load_proposals():
    props = {}
    for mid, path in PROP_PATHS.items():
        d = json.load(open(path, encoding="utf-8"))
        lst = d["proposals"] if isinstance(d, dict) else d
        props[mid] = [{
            "native_id": p.get("id"),
            "pos": i,
            "title": p.get("title") or p.get("name") or "",
            "repo": p.get("repo"),
            "domain": str(p.get("physics_domain") or "")[:60],
        } for i, p in enumerate(lst, 1)]
    return props


def deps_map(nodes):
    return {n["id"]: list(n["dependencies"] or []) for n in nodes}


def topo(dm):
    indeg = {k: len(v) for k, v in dm.items()}
    ready = [k for k, v in indeg.items() if v == 0]
    order = []
    while ready:
        k = ready.pop()
        order.append(k)
        for j, ds in dm.items():
            if k in ds:
                indeg[j] -= 1
                if indeg[j] == 0:
                    ready.append(j)
    return len(order) == len(dm), order


def longest_chain(dm):
    """最长链（节点数最多，并列取字典序最小）。返回 node id 列表。"""
    best = []

    def walk(node, path):
        nonlocal best
        if len(path) > len(best) or (len(path) == len(best) and path < best):
            best = list(path)
        for d in dm.get(node, []):
            walk(d, [d] + path)

    for n in sorted(dm):
        walk(n, [n])
    return best


def proposal_accounting(props, agg, nodes, p3):
    seeded_refs = defaultdict(list)
    for grp in agg["merged_proposals"]:
        for pid in grp["merged_from"]:
            seeded_refs[pid].append(grp["into"])
    for grp in agg["kept_separate"]:
        seeded_refs[grp["proposal"]].append(grp["into"])
    slug777 = {"prop-777-FORM/SORM": 4, "prop-777-channel": 6, "prop-777-icing": 5}

    seeded_native, seeded_slug, unseeded = [], [], []
    for mid in sorted(props):
        for p in props[mid]:
            if mid == 777:
                hit = [s for s, pos in slug777.items() if pos == p["pos"]]
                if hit:
                    seeded_slug.append({"key": hit[0], "pos": p["pos"], "title": p["title"],
                                        "repo": p["repo"], "nodes": sorted(seeded_refs[hit[0]])})
                else:
                    unseeded.append({"key": "prop-777-p%d" % p["pos"], "pos": p["pos"],
                                     "title": p["title"], "repo": p["repo"]})
            else:
                key = p["native_id"] or "prop-%d-p%d" % (mid, p["pos"])
                if key in seeded_refs:
                    seeded_native.append({"key": key, "title": p["title"], "repo": p["repo"],
                                          "nodes": sorted(seeded_refs[key])})
                else:
                    unseeded.append({"key": key, "title": p["title"], "repo": p["repo"]})

    node_ext = [n["ext_id"] for n in nodes]
    covered = sorted({e for v in seeded_refs.values() for e in v})
    orphans = [e for e in node_ext if e not in covered]

    p3_by_ext = {n["ext"]: n["name"] for n in p3}
    domain_hint = {
        "prop-776-02": ("W3-N06", "plan 3 已完成同域（空化+水下噪声）"),
        "prop-776-05": ("W3-N05", "plan 3 已完成同域（碰撞损伤+进水生存性）"),
        "prop-777-p1": ("W3-N01", "plan 3 已完成同域（冰激振动/结构声辐射）"),
        "prop-777-p2": ("W3-N06", "plan 3 已完成同域（水下辐射噪声）"),
        "prop-777-p3": ("W3-N11", "部分由 W4-N01+W4-N07 承接，其余未播种"),
    }
    for u in unseeded:
        u["plan3_domain"], u["domain_note"] = domain_hint.get(u["key"], ("", ""))
        u["plan3_node_name"] = p3_by_ext.get(u["plan3_domain"], "")[:46]

    mission_only = all(re.fullmatch(r'\["[\d,"\s]+\]', (n["proposal_src"] or "")) for n in nodes)
    return {
        "total": sum(len(v) for v in props.values()),
        "per_mission": {str(m): len(v) for m, v in sorted(props.items())},
        "native_ids_present": {str(m): sum(1 for p in props[m] if p["native_id"]) for m in sorted(props)},
        "seeded_native_ids": seeded_native,
        "seeded_arbiter_slugs": seeded_slug,
        "unseeded": unseeded,
        "identifiable_refs": len(seeded_native) + len(seeded_slug),
        "native_id_refs": len(seeded_native),
        "nodes_without_proposal_level_source": orphans,
        "node_proposal_src_mission_only": mission_only,
        "merge_groups": agg["merged_proposals"],
        "kept_separate": agg["kept_separate"],
    }


def _bigrams(text):
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def mapping_audit(nodes, props, agg):
    """审计 result.json 的提案→节点映射是否被节点 goal 文本承接。

    客观事实：goal 中出现的 prop-7xx-NN 引用；启发式筛子：提案标题与节点文本
    (name+goal) 的 CJK 2-gram 召回率，<0.2 视为「主题不搭」的告警信号（非证明）。
    """
    flat = {}
    for mid, lst in props.items():
        for p in lst:
            key = p["native_id"] or "prop-%d-p%d" % (mid, p["pos"])
            flat[key] = p
    slugmap = {"prop-777-FORM/SORM": "prop-777-p4", "prop-777-channel": "prop-777-p6",
               "prop-777-icing": "prop-777-p5"}
    out = {}
    for node in nodes:
        text = (node["name"] or "") + (node.get("goal_text") or "")
        bg = _bigrams(text)
        mentions = sorted(set(re.findall(r"prop-7\d{2}-[0-9A-Za-z/]+", node.get("goal_text") or "")))
        out[node["ext_id"]] = {"mentions": mentions, "groups": []}
    for tag, groups, field in (("merged", agg["merged_proposals"], "merged_from"),
                               ("separate", agg["kept_separate"], "proposal")):
        for grp in groups:
            src_list = grp[field] if field == "merged_from" else [grp["proposal"]]
            tgt = grp["into"]
            node = [n for n in nodes if n["ext_id"] == tgt][0]
            bg = _bigrams((node["name"] or "") + (node.get("goal_text") or ""))
            for pid in src_list:
                real = flat.get(slugmap.get(pid, pid))
                title = real["title"] if real else ""
                repo = real["repo"] if real else ""
                pbg = _bigrams(title)
                recall = (len(pbg & bg) / len(pbg)) if pbg else 0.0
                out[tgt]["groups"].append({
                    "kind": tag, "proposal": pid, "repo": repo, "title": title[:52],
                    "title_bigram_recall_in_node_text": round(recall, 3),
                    "mentioned_in_goal": any(pid == m or (real and real["native_id"] == m)
                                             for m in out[tgt]["mentions"]),
                })
    return out


def build_graph(nodes, ms, dup, sql_nodes, sql_plan_status):
    dm = deps_map(nodes)
    acyclic, order = topo(dm)
    ids = [n["id"] for n in nodes]
    idset = set(ids)
    ext = [n["ext_id"] for n in nodes]
    bad_dep = {n["ext_id"]: [d for d in dm[n["id"]] if d not in idset] for n in nodes}
    bad_dep = {k: v for k, v in bad_dep.items() if v}
    ms_cov = defaultdict(list)
    for n in nodes:
        ms_cov[n["milestone_id"]].append(n["ext_id"])
    mat = [n["ext_id"] for n in nodes if n["is_maturity"] == "true"]
    mat_word = [n["ext_id"] for n in nodes if n["goal_has_maturity_word"]]
    ext_by_id = {n["id"]: n["ext_id"] for n in nodes}
    edges = sorted({(ext_by_id[d], n["ext_id"]) for n in nodes for d in dm[n["id"]]})
    repo_counts = defaultdict(int)
    for n in nodes:
        repo_counts[n["repo"]] += 1
    bad_repo = [n["ext_id"] for n in nodes
                if REPO_ALIAS.get(n["repo"], n["repo"]) not in REPO_WHITELIST]
    chain = longest_chain(dm)

    sql_by_ext = {s["ext_id"]: s for s in sql_nodes}
    consistency = []
    for n in nodes:
        s = sql_by_ext.get(n["ext_id"], {})
        consistency.append({
            "ext_id": n["ext_id"], "db_id": n["id"],
            "db_milestone": n["milestone_id"], "sql_milestone": s.get("milestone_id"),
            "db_deps": list(n["dependencies"] or []), "sql_deps": s.get("dependencies"),
            "match": (s.get("milestone_id") == n["milestone_id"]
                      and list(n["dependencies"] or []) == s.get("dependencies")),
        })

    gates = [
        {"gate": "节点数 ∈ [5,12]", "requirement": "5-12", "actual": len(nodes),
         "pass": 5 <= len(nodes) <= 12},
        {"gate": "ext_id 全库唯一", "requirement": "distinct == total（全库）",
         "actual": "%d/%d" % (dup["distinct_ext"], dup["total"]),
         "pass": dup["distinct_ext"] == dup["total"] and len(set(ext)) == len(ext)},
        {"gate": "repo 在白名单", "requirement": "|whitelist|=%d（TensorFEM 走别名）" % len(REPO_WHITELIST),
         "actual": ", ".join("%s(%d)" % (k, v) for k, v in sorted(repo_counts.items())),
         "pass": not bad_repo},
        {"gate": "DAG 无环", "requirement": "toposort 覆盖全部节点",
         "actual": "acyclic=%s，%d/%d 入拓扑序" % (acyclic, len(order), len(nodes)), "pass": acyclic},
        {"gate": "dependencies 仅引用本 plan", "requirement": "⊆ {%d..%d}" % (min(ids), max(ids)),
         "actual": "越界 %d 条" % len(bad_dep), "pass": not bad_dep},
        {"gate": "每里程碑 ≥1 节点", "requirement": "min ≥ 1 且里程碑全有节点",
         "actual": ", ".join("M%d:%d" % (k, len(v)) for k, v in sorted(ms_cov.items())),
         "pass": all(len(v) >= 1 for v in ms_cov.values()) and len(ms_cov) == len(ms)},
        {"gate": "成熟化节点 ≥2（goal 含「成熟化」）", "requirement": "≥2",
         "actual": "%d（%s）" % (len(mat), ", ".join(mat)),
         "pass": len(mat) >= 2 and sorted(mat) == sorted(mat_word)},
        {"gate": "goal 长度 ≥80 字（附加）", "requirement": "≥80",
         "actual": "%d–%d" % (min(n["goal_len"] for n in nodes), max(n["goal_len"] for n in nodes)),
         "pass": min(n["goal_len"] for n in nodes) >= 80},
        {"gate": "acceptance_criteria 非空（附加）", "requirement": "≥1 条",
         "actual": "%d–%d 条" % (min(n["ac_len"] for n in nodes), max(n["ac_len"] for n in nodes)),
         "pass": min(n["ac_len"] for n in nodes) >= 1},
        {"gate": "SQL 声明 ↔ live DB 逐节点一致（审计门）",
         "requirement": "8/8 milestone+deps 相符",
         "actual": "%d/%d" % (sum(1 for c in consistency if c["match"]), len(consistency)),
         "pass": all(c["match"] for c in consistency)},
    ]
    return {
        "gates": gates,
        "all_pass": all(g["pass"] for g in gates),
        "edges": edges,
        "edge_count": len(edges),
        "longest_chain": [ext_by_id[i] for i in chain],
        "longest_chain_len_nodes": len(chain),
        "longest_chain_len_steps": len(chain) - 1,
        "deps_by_ext": {n["ext_id"]: [ext_by_id[d] for d in dm[n["id"]]] for n in nodes},
        "ms_coverage": {"M%d" % k: sorted(v) for k, v in sorted(ms_cov.items())},
        "repo_counts": dict(sorted(repo_counts.items())),
        "maturity_nodes": mat,
        "bad_repo": bad_repo,
        "bad_dep": bad_dep,
        "sql_consistency": consistency,
        "sql_plan_status": sql_plan_status,
    }


def main():
    os.makedirs(ART, exist_ok=True)
    plan, ms, nodes, p3, dup = load_db()
    sql_nodes, sql_plan_status = parse_sql(SQL_PATH)
    props = load_proposals()
    res = json.load(open(RES_PATH, encoding="utf-8"))
    agg = res["seed_report"]["aggregation_summary"]
    graph = build_graph(nodes, ms, dup, sql_nodes, sql_plan_status)
    acct = proposal_accounting(props, agg, nodes, p3)

    sources = [
        {"role": "live DB", "path": "postgresql tensorplatform: campaign_plans/milestones/nodes (plan 4)",
         "sha256": hashlib.sha256(json.dumps([plan, ms, nodes], ensure_ascii=False,
                                            sort_keys=True).encode()).hexdigest(),
         "use": "plan 4 结构与全部离散事实的唯一真值"},
        {"role": "播种 SQL", "path": SQL_PATH, "sha256": sha256(SQL_PATH),
         "use": "被审计对象：SQL 声明值 vs DB 实测（⑦.4）"},
        {"role": "播种 result", "path": RES_PATH, "sha256": sha256(RES_PATH),
         "use": "aggregation_summary：提案→节点映射（D4 账目）"},
        {"role": "提案源 M775", "path": PROP_PATHS[775], "sha256": sha256(PROP_PATHS[775]),
         "use": "5 条提案的原生 ID 与标题"},
        {"role": "提案源 M776", "path": PROP_PATHS[776], "sha256": sha256(PROP_PATHS[776]),
         "use": "5 条提案的原生 ID 与标题"},
        {"role": "提案源 M777", "path": PROP_PATHS[777], "sha256": sha256(PROP_PATHS[777]),
         "use": "6 条提案（无 id 字段，仅 title/order）"},
    ]

    audit = {
        "mission_id": 845,
        "original_report_id": 779,
        "source_mission_id": 832,
        "plan": plan,
        "milestones": ms,
        "nodes": nodes,
        "plan3_nodes": p3,
        "graph": graph,
        "proposal_accounting": acct,
        "mapping_audit": mapping_audit(nodes, props, agg),
        "sources": sources,
        "repo_whitelist": sorted(REPO_WHITELIST),
        "repo_alias": REPO_ALIAS,
    }
    out = os.path.join(ART, "audit_report_m845.json")
    json.dump(audit, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", out)
    print("nodes=%d ms=%d edges=%d longest_chain=%s (%d nodes/%d steps)" % (
        len(nodes), len(ms), graph["edge_count"], "→".join(graph["longest_chain"]),
        graph["longest_chain_len_nodes"], graph["longest_chain_len_steps"]))
    print("gates all_pass=%s" % graph["all_pass"])
    print("proposal accounting: total=%d identifiable=%d unseeded=%d orphans=%s" % (
        acct["total"], acct["identifiable_refs"], len(acct["unseeded"]),
        acct["nodes_without_proposal_level_source"]))
    print("sql<->db match=%d/%d; sql plan status=%r vs db=%r" % (
        sum(1 for c in graph["sql_consistency"] if c["match"]), len(graph["sql_consistency"]),
        graph["sql_plan_status"], plan["status"]))
    if not graph["all_pass"]:
        raise SystemExit("机器门复核未全过")
    if acct["total"] - acct["identifiable_refs"] != len(acct["unseeded"]):
        raise SystemExit("提案账目不闭合")


if __name__ == "__main__":
    main()