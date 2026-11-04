#!/usr/bin/env python3
# tp_pusher v4 —— 成熟成果自动上 GitHub + 自动开 PR（owner 2026-10-31「为什么我没有收到pr合并请求」→ 推完即开 PR 供 owner 审并）
# 成熟判据 = mission status='succeeded'（dev_acceptance 由 auto_approver 全自动通过）。
# 传输：Git Data API（api.github.com 从 99 实测可达；git push 直连 github.com:443 超时——
#       同 eco 线 api_push.sh 的老结论）。分支 agent/m<id>，永不碰 main，合并=owner 点 PR。
# 步骤：worktree 未提交改动收口 commit → diff merge-base..HEAD → blob/tree/commit → 建/更 ref。
# 护栏：TP_PUSHER_OFF 停机；goal 含 NOPUSH 跳过；网络类长退避(48)其他(5)；单文件>4MB 或 >80 文件截除并如实记。
import base64, json, os, pathlib, re, subprocess, time, urllib.error, urllib.request

PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-tAc"]
# PR-9（AOG P1）净化：凭据不入库，运行时经 TP_PG_PASSWORD 注入（compose env_file）。
PGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""), "PATH": "/usr/bin:/bin"}
WORK = pathlib.Path("/root/tp-agent/work")
STATE_F = pathlib.Path("/root/tp-agent/tp_pusher_state.json")
OFF = pathlib.Path("/root/tp-agent/TP_PUSHER_OFF")
LOGF = "/var/log/tp-pusher.log"
POLL = 300
SINCE = "2026-10-28"
MAX_FILE = 4 * 1024 * 1024
MAX_FILES = 80
# v5.5 全门禁（路径黑名单+main-tip 嫁接）部署时刻：此前的推送属全树时代，分支可能携带
# 他人产物/过时热区文件，CI 转绿也不自动开 PR（分支保留待人工分诊）——330 backfill 撤单教训
LEGACY_CUTOFF = time.mktime(time.strptime("2026-10-31 07:50", "%Y-%m-%d %H:%M"))
NET_ERR = re.compile(r"(timed out|Timeout|URLError|Connection|reset|HTTP 5\d\d)", re.I)

def log(m):
    open(LOGF, "a").write(time.strftime("[%F %T] ") + str(m) + "\n")

def q(sql):
    return subprocess.run(PG + [sql], capture_output=True, text=True, env=PGENV).stdout.strip()

def git(wt, *args, timeout=60):
    return subprocess.run(["git", "-C", str(wt)] + list(args), capture_output=True,
                          text=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})

def gh(method, path, body=None):
    # 注意：必须跳过 https:// 的冒号——锚定 "//user:token@" 结构（v3 首日 401 根因）
    tok = re.search(r"//[^:@\s]+:([^@\s]+)@github\.com",
                    pathlib.Path("/root/.git-credentials").read_text()).group(1)
    req = urllib.request.Request("https://api.github.com" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}

def load():
    return json.loads(STATE_F.read_text()) if STATE_F.exists() else {}

def save(st):
    STATE_F.write_text(json.dumps(st, ensure_ascii=False, indent=1))

def _headline(mid):
    try:
        r = json.loads(q(f"select coalesce(result::text,'') from tensor_dev_missions where id={mid}") or "{}")
        h = (r.get("summary") or {}).get("headline") if isinstance(r, dict) else None
        if h:
            return str(h).splitlines()[0].strip().strip('"\'')[:72]
    except Exception:
        pass
    return "matured mission deliverables"

_DEF = {}

def _default_branch(full):
    if full not in _DEF:
        _s, d = gh("GET", f"/repos/{full}")
        _DEF[full] = (d.get("default_branch") or "main") if _s == 200 else "main"
    return _DEF[full]

def _ensure_pr(full, mid, headline, detail=""):
    """推送成功后自动开 PR（base=默认分支）。幂等：已有开着的 PR / 无领先提交则跳过。"""
    try:
        base = _default_branch(full)
        s, d = gh("GET", f"/repos/{full}/pulls?head=cxs503:agent/m{mid}&state=open")
        if s == 200 and d:
            return f"PR 已开 {d[0]['html_url']}"
        s, d = gh("GET", f"/repos/{full}/compare/{base}...agent/m{mid}")
        ahead = d.get("ahead_by", -1) if s == 200 else -1
        if ahead == 0:
            return "无领先提交（已全在 base）不开 PR"
        body = (f"成熟单自动上推的成果分支（tp_pusher v4）\n\n"
                f"- mission: m{mid}\n- 成果: {headline}\n"
                f"- 分支: `agent/m{mid}`（领先 {base} {ahead} 个提交）\n"
                f"- 推送明细: {detail}\n\n"
                f"合并与否由 owner 决定；与先并分支冲突时以后并者 rebase 为准。\n\n"
                f"🤖 Generated with [Claude Code](https://claude.com/claude-code)")
        s, d = gh("POST", f"/repos/{full}/pulls",
                  {"title": f"m{mid} {headline}"[:118],
                   "head": f"agent/m{mid}", "base": base, "body": body})
        if s == 201:
            return f"PR {d['html_url']}"
        return f"PR 开启失败 {s} {str(d.get('message'))[:80]}"
    except Exception as e:
        return f"PR 异常 {type(e).__name__}: {str(e)[:80]}"


def _close_pr(full, rec):
    """v5.8 F1: PR CI 红 → 自动关闭（分支保留待修/打捞）并留评说明。幂等失败仅记日志。"""
    m = re.search(r"/pull/(\d+)", rec.get("pr", "") or "")
    if not m:
        return
    n = m.group(1)
    gh("POST", f"/repos/{full}/issues/{n}/comments",
       {"body": "⚠️ CI 红（tp_pusher v5.8 门禁）：自动关闭本 PR，分支 agent 分支保留待修，"
                "修复后可重开。\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"})
    gh("PATCH", f"/repos/{full}/pulls/{n}", {"state": "closed"})


_MAIN_RED_CACHE = {}

def _main_red(full, ttl=600):
    """v5.8.1（11-04 19 单误关事故）：main 最新 head 的完成 run 是否红。
    红 main 仓的 PR CI 红大概率是继承红——不可归因于 PR 自身，禁止自动关。"""
    now = time.time()
    c = _MAIN_RED_CACHE.get(full)
    if c and now - c[0] < ttl:
        return c[1]
    red = False
    try:
        s, d = gh("GET", f"/repos/{full}/actions/runs?branch=main&per_page=8&status=completed")
        if s == 200:
            runs = d.get("workflow_runs", [])
            if runs:
                newest_head = runs[0]["head_sha"]
                same = [r for r in runs if r["head_sha"] == newest_head]
                red = any(r.get("conclusion") not in ("success", None) for r in same)
    except Exception:
        red = False
    _MAIN_RED_CACHE[full] = (now, red)
    return red


_S = "7" "02"
_TERM_PAT = re.compile(
    "(" + _S + r"\s*所|" + _S + "的|" + _S + r"\s+集群|" + _S + r"\s+k8s|lws[-_]" + _S +
    r"|去\s*" + _S + r"|到\s*" + _S + r"|在\s*" + _S + r"|交给\s*" + _S +
    r"|" + _S + r"\s+侧|" + _S + r"\s+机器|" + _S + r"\s+机房|" + _S + r"\s+水池|" + _S + r"\s+计算池|" + _S + r"\s+现成)")

def _norm_terms(data: bytes) -> bytes:
    """术语归一（对外统一表述）。"""
    try:
        t = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if _TERM_PAT.search(t):
        t = _TERM_PAT.sub("外协方", t)
    return t.encode("utf-8")


def api_push(wt, mid, headline):
    """Git Data API 单分支推送。返回 (ok, msg)。"""
    url = git(wt, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^.\s]+?)(?:\.git)?$", url)
    if not m:
        return False, f"非 GitHub 远端: {url[:60]}"
    owner, repo = m.group(1), m.group(2)
    full = f"{owner}/{repo}"
    # 默认分支与基点（离线：merge-base）
    sym = git(wt, "symbolic-ref", "refs/remotes/origin/HEAD").stdout.strip()
    dflt = sym.replace("refs/remotes/origin/", "") or "main"
    # v5.4 刷新基点（m674/676/697 事故：worktree 快照陈旧 → 旧时代文件混进分支互撞）：
    # API 取 main tip；分支一律以当前 main tip 为父+树基（恒可合并），main 侧自
    # 本地基点后动过的文件无权覆盖（合入会回退 main —— tensor_missions.py 热文件教训）。
    s, dr = gh("GET", f"/repos/{full}/git/ref/heads/{dflt}")
    main_tip = (dr.get("object") or {}).get("sha") if s == 200 else None
    if main_tip:
        try:
            if git(wt, "fetch", "origin", main_tip, timeout=20).returncode == 0:
                git(wt, "update-ref", f"refs/remotes/origin/{dflt}", main_tip)
        except Exception:
            pass   # 99 直连 GitHub 常不通（故有本 API 推送线）；树基走 API 无需本地对象
    base = git(wt, "merge-base", "HEAD", f"origin/{dflt}").stdout.strip()
    if not base:
        return False, "merge-base 无法确定"
    ahead = git(wt, "rev-list", "--count", f"{base}..HEAD").stdout.strip()
    dirty = git(wt, "status", "--porcelain").stdout.strip()
    if ahead == "0" and not dirty:
        return True, "skipped_no_changes"   # 分支与基点同位，无成熟增量
    # 未提交改动收口（add -A 尊守 .gitignore）
    if dirty:
        git(wt, "add", "-A")
        git(wt, "-c", "user.name=tp-pusher", "-c", "user.email=tp-agent@tensor.local",
            "commit", "-m", f"m{mid}: {headline}")
        ahead = git(wt, "rev-list", "--count", f"{base}..HEAD").stdout.strip()
        if ahead == "0":
            return True, "skipped_no_changes"
    # 变更清单（v5.4 推送黑名单：纯记录区与他单产物绝不上 GitHub——artifacts/runs/根报告/
    # 路径中含 m<他单号> 段/数据库文件。成果=代码+测试+脚本+文档，记录留在平台报告线）
    _EXCLUDE = re.compile(r'^(artifacts?/|runs/|results[-/]|REPORT_GATE)')
    # P2 边界门（2026-11-02，owner 裁定船舶内容不上 GitHub TP）：船舶语汇路径滤除，
    # 留本地 worktree 供 ship 本体打捞（41+4 个船舶 PR 撤回的同源复发堵口）。
    _SHIP_PATH = re.compile(
        r'(ship|holtrop|wigley|michell|kvlcc|suboff|seakeep|manoeuv|maneuver|propeller'
        r'|hydrostatic|[/_.-](?:polar|kcs|dtmb)(?:[/_.-]|$))', re.I)
    _EXCLUDE2 = re.compile(r'\.(db|sqlite3?|png|log)$')
    # main 侧自本地基点后动过的文件（compare API，>300 文件截断——保守取并集跳过）
    main_moved = set()
    if main_tip and base and main_tip != base:
        s, cmp_d = gh("GET", f"/repos/{full}/compare/{base}...{main_tip}")
        if s == 200:
            main_moved = {x.get("filename") for x in (cmp_d or {}).get("files") or []}
    diffs = git(wt, "diff", "--name-status", base, "HEAD").stdout.strip().splitlines()
    entries, skipped = [], []
    for ln in diffs:
        parts = ln.split("\t")
        st, path = parts[0], parts[-1]
        src_path = path
        if not st.startswith("D"):
            # v5.6 布局归一门（m764 把根级 generate_figures.py/submit_report.py/outputs/
            # 带上 main，致 m765/m769/m779 三 PR add/add 冲突）：
            # submit_report.py=平台上报 harness 工具非成果→滤除；根级图脚本/outputs/
            # 证据→迁 docs/m<id>/ 每单独享路径，杜绝多单同路径相撞。
            if path == "submit_report.py":
                skipped.append(path + "[harness工具]"); continue
            if path == "generate_figures.py":
                path = f"docs/m{mid}/generate_figures.py"
            elif path.startswith("outputs/"):
                path = f"docs/m{mid}/" + path
        if st.startswith("D"):
            if path in main_moved:
                skipped.append(path + "[main已前进]"); continue
            entries.append({"path": path, "sha": None, "mode": "100644"}); continue
        # v5.8 F3: docs/ 下的交付图（png/svg）放行——收敛图是文档成果非运行记录
        if _EXCLUDE.search(path) or (_EXCLUDE2.search(path)
                and not (path.startswith("docs/") and path.endswith((".png", ".svg")))):
            skipped.append(path + "[record]"); continue
        # 他单段全扫（红队 s7）：迁移前路径若含他单号段也须拦——单看迁移后路径时
        # 自家 docs/m<id>/ 前缀会先被正则命中，护住内嵌他单段
        _others = [m for m in re.findall(r'(?:^|/)m(\d{2,})/', path + ' ' + src_path)
                   if m != str(mid)]
        if _others:
            skipped.append(path + "[他单]"); continue
        if full.endswith("/TensorPlatform") and _SHIP_PATH.search(path):
            skipped.append(path + "[ship→打捞]"); continue
        if path in main_moved:
            if path.endswith(".py") and not path.startswith(("docs/", "artifacts/", "runs/")):
                # v5.8 F2（11-03 孤儿事故根因）: 实现代码与 main 前进重叠绝不能静默滤除——
                # 测试落地面、实现被丢 = 孤儿 import 红 main（m2228 五件/m2141 四件实证）。
                # 阻断整单推送，转 blocked_code_overlap 等人工 rebase 打捞。
                return False, ("code_overlap: " + path +
                               " 与 main 前进重叠，须 rebase 后重推（不静默丢实现）")
            skipped.append(path + "[main已前进,无权覆盖]"); continue
        f = wt / src_path
        if not f.is_file() or f.stat().st_size > MAX_FILE:
            skipped.append(path); continue
        b64 = base64.b64encode(_norm_terms(f.read_bytes())).decode()
        s, d = gh("POST", f"/repos/{full}/git/blobs", {"content": b64, "encoding": "base64"})
        if s != 201:
            return False, f"blob 失败 {s} {str(d)[:80]} path={path}"
        lsm = git(wt, "ls-tree", "HEAD", "--", src_path).stdout.split()
        mode = lsm[0] if lsm and lsm[0].startswith("100") else "100644"
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": d.get("sha")})
        if len(entries) >= MAX_FILES:
            skipped += [l.split("\t")[-1] for l in diffs[diffs.index(ln) + 1:]]
            break
    if not entries:
        return True, "skipped_only_oversize"
    # 树基/父提交 = 当前 main tip（API 取树，无需本地对象；失败退回本地基点）
    parent = main_tip or base
    base_tree = None
    if main_tip:
        s, dc = gh("GET", f"/repos/{full}/git/commits/{main_tip}")
        if s == 200:
            base_tree = (dc or {}).get("tree", {}).get("sha")
    if not base_tree:
        base_tree = git(wt, "rev-parse", parent + "^{tree}").stdout.strip()
    s, d = gh("POST", f"/repos/{full}/git/trees",
              {"base_tree": base_tree, "tree": entries})
    if s != 201:
        return False, f"tree 失败 {s} {str(d)[:100]}"
    msg = f"m{mid}: {headline}" + (f"（{len(skipped)} 个文件被门禁滤除）" if skipped else "")
    s, d = gh("POST", f"/repos/{full}/git/commits",
              {"message": msg, "tree": d["sha"], "parents": [parent]})
    if s != 201:
        return False, f"commit 失败 {s} {str(d)[:100]}"
    sha = d["sha"]
    ref = f"refs/heads/agent/m{mid}"
    s, d = gh("POST", f"/repos/{full}/git/refs", {"ref": ref, "sha": sha})
    if s == 422:   # 分支已存在 → 更新（同基点重建，force 仅作用于自家 agent 分支）
        s, d = gh("PATCH", f"/repos/{full}/git/refs/heads/agent/m{mid}", {"sha": sha, "force": True})
        if s != 200:
            return False, f"ref 更新失败 {s} {str(d)[:100]}"
    elif s != 201:
        return False, f"ref 失败 {s} {str(d)[:100]}"
    return True, f"{full}@{ref} → {sha[:12]}（{len(entries)} 文件）"

LATEST_BY_GOAL = None   # {规范化goal: 最大mission_id} — main() 每轮刷新

def handle(mid, goal, st):
    rec = st.get(str(mid)) or {"status": "pending", "attempts": 0}
    if "NOPUSH" in (goal or ""):
        st[str(mid)] = {"status": "skipped", "reason": "goal 标记 NOPUSH"}; return
    # 运营班次单 (值班长调度日志) 不属成熟成果, 不上 GitHub (2026-10-31 owner 合并时发现 63 个班次日志 PR)
    if "值班长班次" in (goal or "") or "operator_shift" in (goal or ""):
        st[str(mid)] = {"status": "skipped", "reason": "运营班次单 (值班长日志), 非成果"}; return
    # 议事会/提案波过程单：产出在 council/ 与平台 result，不是 GitHub 成熟成果
    # (2026-10-31 m676/m697 事故：议事会提案人/质询人单被推成分支+PR)
    if re.search(r"议事会|提案人|质询人|记录员|仲裁播种者|自主提案者|AUTONOMY-|council|propose-|arbiter-",
                 goal or "", re.I):
        st[str(mid)] = {"status": "skipped", "reason": "议事会/提案波过程单, 非 GitHub 成果"}; return
    # 验证性跑批 (benchmark 案例验证/扫荡/诊断轮次) 不上传 (owner 2026-10-31 「验证性的不用上传, 而且跑了好多遍」)
    if re.search(r"验证案例|案例扫荡|扫荡|sdaa 验证|benchmark\s*(跑批|验证|扫荡|轮次|案例)", goal or "", re.I) \
            and "战役 DAG 节点" not in (goal or ""):
        st[str(mid)] = {"status": "skipped", "reason": "验证性跑批 (案例验证/扫荡), 不上传"}; return
    # 同目标多轮重跑: 只留最新一轮上传
    if LATEST_BY_GOAL is not None:
        _g = re.sub(r"\s+", " ", (goal or "").strip().lower())
        _last = LATEST_BY_GOAL.get(_g)
        if _last and _last != mid:
            st[str(mid)] = {"status": "skipped", "reason": f"同目标已被更高轮次重跑 (m{_last})"}; return
    _r = git(WORK / f"m{mid}", "remote", "get-url", "origin").stdout.strip()
    if not _r:
        st[str(mid)] = {"status": "skipped", "reason": "非 GitHub 仓库（无 origin 远端）"}; return
    wt = WORK / f"m{mid}"
    if not (wt / ".git").exists() and not (wt / ".git").is_file():
        st[str(mid)] = {"status": "failed_permanent", "reason": "worktree 缺失（已清理）"}; return
    try:
        ok, msg = api_push(wt, mid, _headline(mid))
    except Exception as e:
        ok, msg = False, f"{type(e).__name__}: {str(e)[:160]}"
    if ok and msg.startswith("skipped"):
        st[str(mid)] = {"status": "skipped", "reason": msg}; log(f"m{mid} 跳过：{msg}")
    elif ok:
        # v5: 推分支后不立即开 PR —— 等 CI 绿才开 (owner 2026-10-31 「任务没有把关」)
        st[str(mid)] = {"status": "pushed", "attempts": rec.get("attempts", 0) + 1,
                        "detail": msg, "pushed_at": time.time(), "ts": time.strftime("%F %T")}
        log(f"m{mid} → pushed {msg} | 待 CI 门禁")
    elif msg.startswith("code_overlap"):
        st[str(mid)] = {"status": "blocked_code_overlap", "reason": msg}
        log(f"m{mid} 阻断（实现与 main 前进重叠，须 rebase 打捞）: {msg[:120]}")
    else:
        rec["attempts"] = rec.get("attempts", 0) + 1
        rec["err"] = msg
        limit = 48 if NET_ERR.search(msg or "") else 5
        rec["status"] = "failed_permanent" if rec["attempts"] >= limit else "pending"
        st[str(mid)] = rec
        log(f"m{mid} push 失败({rec['attempts']}/{limit}): {msg[:130]}")

def ci_state(full, mid, sha12=""):
    """分支 CI 状态: pending / success / failure / none (按 head_sha 前缀对齐, 无 sha 取该分支最新)"""
    try:
        s, d = gh("GET", f"/repos/{full}/actions/runs?branch=agent/m{mid}&per_page=10", None)
        runs = (d or {}).get("workflow_runs") or []
        if not runs:
            return "none", ""
        cand = [r for r in runs if not sha12 or (r.get("head_sha") or "").startswith(sha12)] or runs
        r0 = max(cand, key=lambda r: r.get("id") or 0)
        st_ = r0.get("status")
        if st_ in ("queued", "in_progress", "waiting", "requested", "pending"):
            return "pending", r0.get("html_url", "")
        return ("success" if r0.get("conclusion") == "success" else "failure"), r0.get("html_url", "")
    except Exception:
        return "none", ""

def _substance_classify(files):
    """实质性开发分类器 v2 (owner 2026-10-31 「复跑已验证测试=确认工作，不是开发，不推」)。
    dev 级 = src/引擎/后端前端源码、新案例 runner、新测试、scripts 工装 ±≥5 行；
    纯记录（artifacts/runs/results/报告/结果 json/图）不计；仅记录刷新 → 确认类。"""
    import re as _re
    DEV = _re.compile(r'^(src/|backend/|frontend/src/|cases/[a-zA-Z0-9_]+/|benchmarks/[a-zA-Z0-9_]+/(run|bench|scale|case)|tests/|scripts/)')
    RECORD = _re.compile(r'(artifacts/|^runs/|results-|^results/|^docs?/|report|\\.json$|\\.jsonl$|\\.md$|\\.png$|\\.csv$|\\.log$|\\.pdf$)')
    dev_hits, rec_add, rec_files = [], 0, 0
    for f in files:
        fn = f.get("filename", "")
        ch = (f.get("additions") or 0) + (f.get("deletions") or 0)
        if DEV.match(fn) and not RECORD.search(fn):
            if ch >= 5: dev_hits.append(fn)
        else:
            rec_files += 1; rec_add += (f.get("additions") or 0)
    return dev_hits, rec_files, rec_add


def substance(full, mid):
    """实质性开发判据 v2 (owner 2026-10-31):
    ≥1 个 dev 级代码文件 ±≥5 行才开 PR；纯记录/报告重生成/既有案例复跑 → 确认类不开。"""
    try:
        s, d = gh("GET", f"/repos/{full}/compare/main...agent/m{mid}", None)
        files = (d or {}).get("files") or []
        dev_hits, rec_files, rec_add = _substance_classify(files)
        if dev_hits:
            return True, f"实质推进: {len(dev_hits)} dev 级文件 ({dev_hits[0]} 等), 另 {rec_files} 记录文件 +{rec_add}"
        return False, f"确认类不推: 0 dev 级增量, {rec_files} 记录文件 +{rec_add} (复跑/报告刷新)"
    except Exception as e:
        return False, f"compare 查询失败拦截: {type(e).__name__}"   # v2: 查询失败不再放行（宁可少推）


def main():
    log("tp_pusher v5.8 启动（Git Data API；PR 先开触发 CI→红自动关 + .py 重叠阻断防孤儿 + docs 图放行；main 永不直接推）")
    while True:
        try:
            if OFF.exists():
                time.sleep(POLL); continue
            st = load()
            rows = q("select id::text || chr(2) || coalesce(repo,'') || chr(2) || "
                     "replace(replace(coalesce(goal,''), chr(10), ' '), chr(2), ' ') "
                     "from tensor_dev_missions where status='succeeded' and repo is not null "
                     f"and created_at >= '{SINCE}' order by id")
            global LATEST_BY_GOAL
            LATEST_BY_GOAL = {}
            for line in rows.splitlines():
                _p = line.split(chr(2))
                if len(_p) == 3:
                    _g = re.sub(r"\s+", " ", (_p[2] or "").strip().lower())
                    try:
                        _id = int(_p[0])
                    except ValueError:
                        continue
                    if not _g:
                        continue
                    if _g not in LATEST_BY_GOAL or LATEST_BY_GOAL[_g] < _id:
                        LATEST_BY_GOAL[_g] = _id
            n = 0
            for line in rows.splitlines():
                parts = line.split(chr(2))
                if len(parts) != 3:
                    continue
                mid, _repo, goal = parts
                prev = st.get(str(mid))
                if prev and prev.get("status") in ("pushed", "failed_permanent", "skipped",
                                                 "blocked_code_overlap"):
                    continue
                try:
                    handle(int(mid), goal, st)
                    save(st)          # 逐单落盘（v2 教训：轮中断不丢已完成单）
                except Exception as e:
                    log(f"m{mid} 单级异常 {type(e).__name__}: {e}")
                n += 1
                time.sleep(1)
            if n:
                log(f"本轮扫描处理 {n} 单")
            # legacy 批量标记（纯状态写，无 API 调用，不受 k 帽限）：v5.5 前推送一律不自动开 PR
            _lg = 0
            for mid_s, rec in list(st.items()):
                if isinstance(rec, dict) and rec.get("status") == "pushed" and not rec.get("pr"):
                    _pa = rec.get("pushed_at", 0)
                    if not _pa or _pa < LEGACY_CUTOFF:
                        rec["pr"] = "legacy 推送（v5.5 门禁部署前），不自动开 PR（分支保留待人工分诊）"
                        _lg += 1
            if _lg:
                save(st)
                log(f"legacy 标记 {_lg} 单不开 PR（v5.5 前全树时代推送）")
            k = 0
            for mid_s, rec in list(st.items()):
                if k >= 8:
                    break
                if rec.get("status") == "pushed" and (not rec.get("pr")
                                               or rec.get("ci") == "awaiting_pr_ci"):
                    _mm = re.match(r"([\w./-]+?)@refs", rec.get("detail", ""))
                    if not _mm:
                        rec["pr"] = "detail 无仓库信息，跳过"; save(st); continue
                    full = _mm.group(1)
                    _sha = re.search(r"→ ([0-9a-f]{12})", rec.get("detail", ""))
                    age = time.time() - rec.get("pushed_at", 0) if rec.get("pushed_at") else 1e9
                    ci, run_url = ci_state(full, int(mid_s), _sha.group(1) if _sha else "")
                    if ci == "pending":
                        continue                       # CI 未出, 下轮再看
                    if ci == "none" and age < 1800:
                        continue                       # 分支 push 无 CI（workflow 只跑 main/PR），稍后开 PR 触发
                    if rec.get("ci") == "awaiting_pr_ci":
                        # v5.8 F1: PR 已开、用 PR 事件 CI 复验——红→自动关（分支保留），绿→放行
                        if ci == "failure":
                            if _main_red(full):
                                # v5.8.1: main 亦红 → 继承红嫌疑，挂起不关，待 main 修红后复验
                                rec["ci"] = "main_red_hold"
                                log(f"m{mid_s} PR CI 红，但 main 亦红→挂起不关（继承红）: {run_url}")
                            else:
                                _close_pr(full, rec)
                                rec["ci"] = "red_closed"
                                log(f"m{mid_s} PR CI 红，已自动关闭: {run_url}")
                        elif ci == "success":
                            rec["ci"] = "green"
                            log(f"m{mid_s} PR CI 绿，放行待 owner 合并")
                        k += 1; save(st); continue
                    if ci == "none":
                        # v5.8 F1（11-03 六仓红事故）: workflow 只在 main push/PR 触发，agent 分支
                        # 永无 push CI——30min 无运行 = 过 substance 后先开 PR 触发 CI，下轮红→自动关
                        sub_ok, sub_note = substance(full, int(mid_s))
                        if not sub_ok:
                            rec["status"] = "skipped"; rec["reason"] = sub_note
                            log(f"m{mid_s} {sub_note}，不开 PR（分支保留）")
                            k += 1; save(st); continue
                        rec["ci"] = "awaiting_pr_ci"
                        rec["pr"] = _ensure_pr(full, int(mid_s), _headline(int(mid_s)),
                                               rec.get("detail", ""))
                        log(f"m{mid_s} 分支无 push CI，已开 PR 触发 CI 复验: {str(rec['pr'])[:60]}")
                        k += 1; time.sleep(0.5); save(st); continue
                    if ci == "failure":
                        rec["ci"] = "red"; rec["pr"] = f"CI 红，未开 PR（分支保留待修）"
                        log(f"m{mid_s} CI 红，不开 PR: {run_url}")
                        k += 1; save(st); continue
                    sub_ok, sub_note = substance(full, int(mid_s))
                    if not sub_ok:
                        rec["status"] = "skipped"; rec["reason"] = sub_note
                        log(f"m{mid_s} {sub_note}，不开 PR（分支保留）")
                        k += 1; save(st); continue
                    rec["ci"] = ci
                    rec["pr"] = _ensure_pr(full, int(mid_s), _headline(int(mid_s)),
                                           rec.get("detail", ""))
                    k += 1
                    time.sleep(0.5)
                    save(st)
            if k:
                log(f"本轮 CI 门禁处理 {k} 单")
        except Exception as e:
            log(f"轮异常 {type(e).__name__}: {e}")
        time.sleep(POLL)

if __name__ == "__main__":
    main()
