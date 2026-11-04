#!/usr/bin/env python3
# 影子合并试运行 (owner 2026-10-31 批准; L3 无人合并晋级证据生产线)
# 只读 GitHub: 对 open agent PR 计算门禁裁决(CI绿+实质增量+可合并+非纯记录),
# 记本地台账 shadow_merge.json; PR 离开 open 后回填实际结局, 统计一致率。
# 绝不写 GitHub (无评论/无合并/无状态变更)。
import json, re, time, urllib.request, urllib.error, pathlib
STATE = pathlib.Path("/root/tp-agent/shadow_merge.json")
LOG = "/var/log/tp-shadow-merge.log"
POLL = 1800
REPOS = ["TensorLBM", "TensorPlatform", "TensorPhysics", "TensorBEM", "TensorSPH",
         "TensorDG", "TensorCAD", "TensorFVM", "TensorFEM", "TensorDEM"]
RECORD_DIR = re.compile(r"^(benchmarks/|agent_work/|runs/|artifacts/|docs/benchmarks/|results/|reports/|\.benchmarks/)")
RECORD_FILE = re.compile(r"(result\.json|evidence.*\.json|diagnosis.*\.(json|md)|DIAGNOSIS.*\.md|_diagnostic\.json)$|\.png$|\.csv$")
CODE_EXT = (".py", ".ts", ".js", ".vue", ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".java", ".sh", ".triton")

def log(m): open(LOG, "a").write(time.strftime("[%F %T] ") + m + "\n")

def tok():
    for line in open("/root/.git-credentials"):
        m = re.search(r"//[^:]+:([^@]+)@", line)
        if m: return m.group(1)
    return None
TOK = tok()

def gh(method, path, body=None, tries=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method,
        headers={"Authorization": "token " + TOK, "Accept": "application/vnd.github+json"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError:
            raise
        except Exception:
            time.sleep(3 + i * 3)
    return None, {}

def ci_gate(full, sha):
    s, d = gh("GET", f"/repos/{full}/actions/runs?head_sha={sha}&per_page=5")
    for run in d.get("workflow_runs", []):
        if run.get("head_sha") == sha:
            if run.get("status") != "completed": return False, "ci:pending"
            return run.get("conclusion") == "success", "ci:" + str(run.get("conclusion"))
    return False, "ci:none"

def substance(full, head_ref):
    s, d = gh("GET", f"/repos/{full}/compare/main...{head_ref}")
    files = d.get("files", [])
    code_main = 0; doc_add = 0; all_record = bool(files)
    for f in files:
        path = f.get("filename", "")
        if RECORD_DIR.match(path) or RECORD_FILE.search(path):
            continue
        all_record = False
        add, dele = f.get("additions", 0), f.get("deletions", 0)
        if path.endswith(CODE_EXT) and add + dele >= 5: code_main += 1
        elif path.endswith(".md"): doc_add += add
    if all_record: return False, "纯记录类"
    if code_main >= 1: return True, f"code_files={code_main}"
    if doc_add >= 120: return True, f"doc+{doc_add}"
    return False, "无实质增量"

def judge(full, p):
    reasons = []
    if p.get("draft"): return "跳过", ["draft"]
    if p.get("mergeable") is False: reasons.append("冲突")
    sha = p["head"]["sha"]
    ok, why = ci_gate(full, sha)
    if not ok: reasons.append(why)
    ok2, why2 = substance(full, p["head"]["ref"])
    if ok2 is False: reasons.append(why2)
    return ("可合" if not reasons else "不可合"), (reasons or ["全门通过"])

def main():
    log("shadow merge 线启动 (只读)")
    st = {"entries": {}, "stats": {}} if not STATE.exists() else json.loads(STATE.read_text())
    while True:
        cur_open = set()
        ts = time.strftime("%F %T")
        for repo in REPOS:
            full = f"cxs503/{repo}"
            try:
                s, prs = gh("GET", f"/repos/{full}/pulls?state=open&per_page=100")
            except Exception as e:
                log(f"{repo} list ERR {e}"); continue
            for p in prs:
                ref = p.get("head", {}).get("ref", "")
                if not ref.startswith("agent/"): continue
                key = f"{repo}#{p['number']}"
                cur_open.add(key)
                sha = p["head"]["sha"]
                e = st["entries"].get(key)
                if e and e.get("last_sha") == sha and e.get("verdict"):
                    e["last_seen"] = ts; continue
                try:
                    v, why = judge(full, p)
                except Exception as ex:
                    log(f"{key} judge ERR {ex}"); continue
                st["entries"][key] = {"repo": repo, "pr": p["number"], "last_sha": sha,
                    "verdict": v, "reasons": why,
                    "first_seen": (e or {}).get("first_seen", ts), "last_seen": ts,
                    "outcome": (e or {}).get("outcome")}
                time.sleep(0.4)
            time.sleep(0.4)
        for key, e in list(st["entries"].items()):
            if e.get("outcome") or key in cur_open: continue
            try:
                s, d = gh("GET", f"/repos/cxs503/{e['repo']}/pulls/{e['pr']}")
                e["outcome"] = "merged" if d.get("merged_at") else "closed"
                e["outcome_ts"] = ts
            except Exception as ex:
                log(f"{key} outcome ERR {ex}")
            time.sleep(0.4)
        judged = [e for e in st["entries"].values() if e.get("outcome")]
        agree = sum(1 for e in judged if (e["verdict"] == "可合") == (e["outcome"] == "merged"))
        st["stats"] = {"ts": ts, "n_outcome": len(judged), "agree": agree,
                       "rate": round(100.0 * agree / max(len(judged), 1), 1),
                       "open_judged": sum(1 for k, e in st["entries"].items() if k in cur_open)}
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
        log(f"round done: open_judged={st['stats']['open_judged']} outcomes={len(judged)} agree={st['stats']['rate']}%")
        time.sleep(POLL)

main()
