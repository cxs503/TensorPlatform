#!/usr/bin/env python3
"""merge_governor v1.0 — serial merge queue + main ratchet + optional dirty patrol.

Owner authorization 2026-11-04 ("ABC都上吧"), recorded in
memory/merge-pipeline-governance-20261104.md:
  * Machine-merge authority is granted ONLY for PRs on agent/m<id> branches
    whose mid exists in tp_pusher_state.json (pusher's own records), passing
    all gates below. Manual/arbitration PRs (hold list, non-agent branches)
    stay owner-merged. ONE merge per repo per cycle.

v1.0 (after DG campaign-2 review):
  * per-repo "order": domain-specific merge order (same-file chains MUST be
    arbitrated in order; oldest-first is wrong for sibling chains).
  * per-repo "patrol": "overlay" (opt-in) | "off" (default). Mechanical
    overlay re-apply has a SILENT-LOSS mode on same-file siblings: the
    re-applied branch version overwrites the just-merged sibling's file,
    CI goes GREEN (fewer tests) and content is lost. Same-file chains go
    through agent arbitration rounds; overlay patrol is only safe for
    repos/PRs with independent filesets. TODO: pr3way-based patrol.

Cycle per active repo:
  1. RATCHET: latest completed workflow runs on main must ALL be 'success'
     (run-level conclusion, so advisory jobs like FVM lint don't block).
     Red main => freeze merges for that repo this cycle.
  2. QUEUE: open PRs on agent/m* (mid in pusher state, not on hold list),
     sorted by (order index, number); first with mergeable=True AND all
     check-run conclusions 'success' (minus repo ignore list) => MERGE,
     then stop for this repo this cycle.
  3. PATROL (opt-in per repo, "overlay"): dirty agent/m* PRs get a
     mechanical re-apply via pr_rebuild --plan + --apply. CI is the safety
     net only for INDEPENDENT filesets (see silent-loss note above).

Modes: dryrun (default) logs intended actions only; active executes.
Audit: /root/tp-agent/merge_governor.log + state json. Lockfile prevents
concurrent runs. Config is re-read every cycle (no restart needed for
config-only changes; code changes require daemon restart).

v1.3 (owner "patrol明确授权" 2026-11-04):
  * per-repo "patrol": "heal" — a dirty PR gets ONE platform healing
    mission (marker 【治愈·repo#num】; agent does the three-way
    resolution, pushes back to the same agent/m<id> branch). This
    supersedes the overlay TODO above: NO mechanical re-apply, the
    silent-loss mode stays closed. Missing eco_admin.env creds or a
    dispatch failure degrade to dirty_wait — the queue itself never
    depends on the platform API.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "/root/tp-agent"
CFG_PATH = BASE + "/merge_governor.json"
STATE_PATH = BASE + "/merge_governor_state.json"
LOG_PATH = BASE + "/merge_governor.log"
LOCK_PATH = "/tmp/merge_governor.lock"
TOOLS = BASE + "/tools"

RE_PUSH = re.compile(r"^agent/m(\d+)$")


def log(msg):
    line = "%s %s" % (datetime.now(timezone.utc).strftime("%m-%dT%H:%M:%SZ"), msg)
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def tok():
    with open("/root/.git-credentials") as f:
        m = re.search(r"//[^:@\s]+:([^@\s]+)@github\.com", f.read())
    return m.group(1)


TOK = tok()


def api(url, method="GET", body=None):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + TOK,
        "Accept": "application/vnd.github+json",
        "User-Agent": "merge-governor"}, method=method)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def record(state, **kw):
    kw["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.setdefault("events", []).append(kw)
    state["events"] = state["events"][-2000:]
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def main_green(repo):
    """All latest completed runs on branch main must be success (run-level)."""
    st, runs = api("https://api.github.com/repos/cxs503/%s/actions/runs"
                  "?branch=main&per_page=10" % repo)
    if st != 200:
        return None, "runs api %s" % st
    latest = {}
    for r in runs.get("workflow_runs", []):
        if r["status"] != "completed":
            continue
        wf = r["name"]
        if wf not in latest:  # newest first
            latest[wf] = r
    bad = [("%s:%s@%s" % (wf, r["conclusion"], r["head_sha"][:8]))
           for wf, r in latest.items() if r["conclusion"] != "success"]
    return (not bad), "; ".join(bad) or "all green"


def pr_checks_green(repo, sha, ignore):
    st, cr = api("https://api.github.com/repos/cxs503/%s/commits/%s/check-runs"
                 "?per_page=100" % (repo, sha))
    if st != 200 or not cr.get("check_runs"):
        return False, "no check runs yet"
    bad, pend = [], []
    for c in cr["check_runs"]:
        name = c["name"]
        if any(p in name for p in ignore):
            continue
        if c["status"] != "completed":
            pend.append(name)
        elif c["conclusion"] != "success":
            bad.append("%s=%s" % (name, c["conclusion"]))
    if bad:
        return False, "failing: " + ",".join(bad)
    if pend:
        return False, "pending: " + ",".join(pend)
    return True, "green"


ART_RE = re.compile(r"(outputs|figures|artifacts|__pycache__)/|\.png$|\.npz$|\.pt$|\.vti$|\.pdf$|\.pyc$")


def pr_artifact_bytes(repo, num, cap_kb):
    """Sum blob sizes of artifact-class files in a PR (owner artifact
    discipline 2026-11-04: recompute-able raw outputs stay out of git).
    Returns (bytes, [names]) — only fetched for candidates about to merge."""
    st, fl = api("https://api.github.com/repos/cxs503/%s/pulls/%d/files?per_page=100" % (repo, num))
    if st != 200:
        return -1, []  # unknown -> do not block
    total, names = 0, []
    for f in fl:
        path = f.get("filename", "")
        if not ART_RE.search(path):
            continue
        if f.get("status") == "removed":
            continue
        sz = f.get("additions", 0) * 60  # line-count proxy until blob fetched
        blob = f.get("raw_url")
        st2, cd = api("https://api.github.com/repos/cxs503/%s/contents/%s" % (repo, urllib.parse.quote(path)))
        if st2 == 200 and isinstance(cd, dict):
            sz = cd.get("size", sz)
        total += max(sz, 1)
        names.append(path.split("/")[-1][:30])
        if total > cap_kb * 1024:
            break
    return total, names[:5]


def load_pusher_mids():
    try:
        with open(BASE + "/tp_pusher_state.json") as f:
            st = json.load(f)
    except Exception as e:
        return {}, "state read err %s" % e
    mids = {}
    for k, v in st.items():
        if isinstance(v, dict) and ("pr" in v or "push" in str(v.get("status", ""))):
            mids[k] = v.get("pr")
    return mids, ""


# ---------------------------------------------------------------------------
# v1.3 heal patrol: dirty PR -> ONE platform healing mission
# ---------------------------------------------------------------------------

PAPI = "http://127.0.0.1:8010/api/v1"
HEAL_ENV = BASE + "/eco_admin.env"


def _papi_token():
    """Platform token for mission dispatch (creds in eco_admin.env, 600).

    Returns None on any failure — heal patrol degrades to dirty_wait and
    the merge queue keeps working (it never depends on this API)."""
    try:
        env = {}
        for line in open(HEAL_ENV):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
        body = json.dumps({"username": env["ECO_ADMIN_USER"],
                           "password": env["ECO_ADMIN_PASS"]}).encode()
        r = urllib.request.Request(PAPI + "/auth/login", data=body, method="POST")
        r.add_header("Content-Type", "application/json")
        return json.load(urllib.request.urlopen(r, timeout=30))["access_token"]
    except Exception as e:
        log("heal: platform auth unavailable (%r)" % e)
        return None


def dispatch_heal(repo, num, mid):
    """Open ONE healing mission for a dirty PR.

    Dedup: if an active mission already carries the marker for this PR,
    return None (heal already in flight). Returns mission id or None."""
    tokp = _papi_token()
    if not tokp:
        return None
    marker = "【治愈·%s#%d】" % (repo, num)
    try:
        r = urllib.request.Request(
            PAPI + "/tensor/missions?limit=200&offset=0",
            headers={"Authorization": "Bearer " + tokp})
        ms = json.load(urllib.request.urlopen(r, timeout=30))
        for m in (ms.get("items") or []):
            if (m.get("status") in ("queued", "running")
                    and marker in (m.get("goal") or "")):
                return None
        goal = marker + (
            "PR #%d（分支 agent/m%s）对 main 变脏（mergeable=false）。步骤：\n"
            "1) 建工作树，拉本仓 main 与 agent/m%s 分支；\n"
            "2) 三方合并 agent/m%s → main，逐文件解冲：语义并集保双方内容，"
            "任何一侧的新增/删除不得静默丢失（机械 overlay 有静默丢内容前科，禁止）；\n"
            "3) 本地跑核心测试，不得低于 main 基线；\n"
            "4) 推回同分支 agent/m%s（记录旧/新 SHA）；\n"
            "5) 报告论文级入库：冲突文件清单+逐文件解法+测试证据。\n"
            "merge_governor 下一周期自动复验合并。"
        ) % (num, mid, mid, mid, mid)
        r = urllib.request.Request(
            PAPI + "/tensor/missions", method="POST",
            data=json.dumps({"goal": goal, "repo": repo, "origin": "owner",
                             "runner": "native", "priority": 1}).encode(),
            headers={"Authorization": "Bearer " + tokp,
                     "Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(r, timeout=30))
        return d.get("id")
    except Exception as e:
        log("heal: dispatch failed %r" % e)
        return None


def cycle(cfg):
    state = {}
    if os.path.exists(STATE_PATH):
        try:
            state = json.load(open(STATE_PATH))
        except Exception:
            state = {}
    pusher_mids, err = load_pusher_mids()
    if err:
        log("ABORT " + err)
        return
    for repo, rcfg in cfg["repos"].items():
        if not rcfg.get("active"):
            continue
        try:
            one_repo(cfg, state, repo, pusher_mids)
        except Exception as e:
            log("%s ERROR %r" % (repo, e))


def one_repo(cfg, state, repo, pusher_mids):
    mode = cfg["mode"]
    rcfg = cfg["repos"][repo]
    ignore = cfg.get("ignore_checks", {}).get(repo, [])
    hold = set(cfg.get("hold_mids", []))
    patrol = rcfg.get("patrol", "off")
    order_map = {n: i for i, n in enumerate(rcfg.get("order", []))}

    green, why = main_green(repo)
    log("%s ratchet: green=%s (%s)" % (repo, green, why))
    # v1.2 healing PRs: coordinator-listed fix PRs are exempt from the
    # ratchet (they ARE the fix for a red main; DG#42 precedent) and jump
    # the order queue. Still gated on: pusher record + mergeable + checks
    # green + artifact cap. healing_only: merge nothing else until the
    # chain order is arbitrated (unordered same-file merging loses content).
    healing = set(rcfg.get("healing_prs", []))

    st, prs = api("https://api.github.com/repos/cxs503/%s/pulls?state=open&per_page=100" % repo)
    if st != 200:
        log("%s pulls api %s" % (repo, st))
        return
    cands = []
    for p in prs:
        m = RE_PUSH.match(p["head"]["ref"])
        if not m:
            continue
        mid = m.group(1)
        if mid not in pusher_mids:
            continue  # not a pusher record (manual rebuild branches stay owner-merged)
        if int(mid) in hold:
            continue
        cands.append((p["number"], int(mid), p))
    healing_cands = [t for t in cands if t[0] in healing]
    if healing_cands:
        log("%s healing candidates: %s" % (repo, [t[0] for t in healing_cands]))
    if rcfg.get("healing_only"):
        cands = healing_cands
    else:
        cands.sort(key=lambda t: (order_map.get(t[0], 900), t[0]))
        if healing_cands:
            cands = healing_cands + [t for t in cands if t[0] not in healing]
        if not green:
            record(state, repo=repo, action="ratchet_freeze", why=why)
            if not healing_cands:
                return
            cands = healing_cands  # frozen main: only the healing PR may merge

    strict = bool(order_map)  # ordered repo: first non-ready PR blocks the
    merged_one = False        # whole chain (same-file sibling ordering is
    for num, mid, p in cands:  # domain-arbitrated, skipping ahead inverts it)
        if merged_one:
            break
        # recompute mergeable
        d = {}
        for _ in range(4):
            st, d = api("https://api.github.com/repos/cxs503/%s/pulls/%d" % (repo, num))
            if d.get("mergeable") is not None:
                break
            time.sleep(2)
        mk = d.get("mergeable")
        if mk is False:
            # dirty: overlay re-apply is OPT-IN (silent-loss risk on
            # same-file sibling chains — those go through agent rounds)
            if mode == "active" and patrol == "overlay":
                log("%s #%d m%d dirty -> re-apply" % (repo, num, mid))
                r1 = subprocess.run([sys.executable, TOOLS + "/pr_rebuild.py",
                                     repo, str(num), str(mid), "--plan"],
                                    capture_output=True, text=True, timeout=900)
                r2 = subprocess.run([sys.executable, TOOLS + "/pr_rebuild.py",
                                     repo, str(num), str(mid), "--apply"],
                                    capture_output=True, text=True, timeout=900)
                ok = r1.returncode == 0 and r2.returncode == 0
                log("%s #%d m%d re-apply rc=%s%s" % (
                    repo, num, mid, ok, "" if ok else " NEEDS_HUMAN"))
                record(state, repo=repo, pr=num, mid=mid,
                       action="reapply" if ok else "needs_human",
                       tail=(r2.stderr or r2.stdout)[-300:])
            elif mode == "active" and patrol == "heal":
                # v1.3: dirty -> platform healing mission (three-way by an
                # agent, no mechanical overlay). Dedup inside dispatch_heal.
                h = dispatch_heal(repo, num, mid)
                if h:
                    log("%s #%d m%d dirty -> heal mission m%s"
                        % (repo, num, mid, h))
                    record(state, repo=repo, pr=num, mid=mid,
                           action="heal_dispatched", heal_mid=h)
                else:
                    log("%s #%d m%d dirty (heal: mission open or dispatch failed)"
                        % (repo, num, mid))
                    record(state, repo=repo, pr=num, mid=mid, action="dirty_wait")
            else:
                log("%s #%d m%d dirty (patrol=%s: agent arbitration round)"
                    % (repo, num, mid, patrol))
                record(state, repo=repo, pr=num, mid=mid, action="dirty_wait")
            if strict:
                break
            continue
        ok, why = pr_checks_green(repo, p["head"]["sha"], ignore)
        if not ok:
            log("%s #%d m%d checks: %s%s" % (repo, num, mid, why,
                " [strict: holding chain]" if strict else ""))
            if strict:
                break
            continue
        # v1.1 artifact gate (owner discipline: raw outputs stay out of git)
        cap_kb = cfg.get("max_artifact_kb", 200)
        abytes, anames = pr_artifact_bytes(repo, num, cap_kb)
        if abytes > cap_kb * 1024:
            log("%s #%d m%d artifact_hold %dKB>%dKB %s" % (
                repo, num, mid, abytes // 1024, cap_kb, ",".join(anames)))
            record(state, repo=repo, pr=num, mid=mid, action="artifact_hold",
                   kbytes=abytes // 1024, files=anames)
            if strict:
                break
            continue
        # ready: merge (B) — one per repo per cycle
        if mode == "active":
            st, res = api("https://api.github.com/repos/cxs503/%s/pulls/%d/merge"
                          % (repo, num), "PUT",
                          {"merge_method": cfg["merge_method"],
                           "commit_title": "Merge #%d (m%s) via merge-governor" % (num, mid)})
            log("%s #%d m%d MERGE api=%s %s" % (repo, num, mid, st, res.get("message", "")))
            record(state, repo=repo, pr=num, mid=mid,
                   action="merged" if st == 200 else "merge_failed",
                   http=st, msg=res.get("message", "")[:200])
            if st == 200:
                merged_one = True
        else:
            log("%s #%d m%d READY (dryrun: would merge)" % (repo, num, mid))
            record(state, repo=repo, pr=num, mid=mid, action="would_merge")
            merged_one = True  # dry-run: still one decision per cycle


def acquire_lock():
    if os.path.exists(LOCK_PATH):
        try:
            pid = int(open(LOCK_PATH).read().strip())
            os.kill(pid, 0)
            return False  # alive
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    once = "--once" in sys.argv
    if not acquire_lock():
        print("another governor holds the lock")
        sys.exit(0)
    if not os.path.exists(CFG_PATH):
        with open(CFG_PATH, "w") as f:
            json.dump({"mode": "dryrun", "poll_seconds": 300,
                       "merge_method": "merge", "repos": {}}, f, indent=1)
        print("wrote default config", CFG_PATH)
    while True:
        cfg = json.load(open(CFG_PATH))
        try:
            cycle(cfg)
        except Exception as e:
            log("CYCLE ERROR %r" % e)
        if once:
            break
        time.sleep(cfg.get("poll_seconds", 300))
    release_lock()
