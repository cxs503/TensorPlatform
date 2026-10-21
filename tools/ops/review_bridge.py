#!/usr/bin/env python3
"""评审意见 → 回应 mission 桥（B 改进：双向评审环的入向腿）。

cron 5,25,45（flock 串行）每轮：
  1. OFF 开关 → 跳过；
  2. 同步在飞回应 mission 终态：succeeded → 读 worktree/review_reply.md 代回贴
     到 PR；failed / 回贴件缺失 → needs_human（人工位，不重派）；
  3. 扫 eco 五库 open PR（head 为 agent/* 或 fix/*），拉行内评审评论
     （pulls/{n}/comments）+ 会话评论（issues/{n}/comments）；
  4. 跳过本桥自己回贴的评论（正文含 SKIP_MARK）；按评论 id 幂等去重；
  5. 每轮至多派 1 个回应 mission（REVIEW-RESPONSE:<repo>#<pr>-c<id>），
     base_ref=PR head SHA，model=qwen3.7-plus。

环境（cron 已 source）：agent.env（LLM_BASE_URL/LLM_API_KEY）+ operator.env
（OPERATOR_USER/PASS）。GitHub 令牌：GH_PUSH_TOKEN 或 /tmp/gh_token。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ECO = Path("/srv/tensor/shared/tensor_eco")
WORK = ECO / "agent_work" / "review_bridge"
HELPER = ECO / "TensorPlatform" / "tools" / "agent" / "dispatch_helper.py"
PY = "/srv/tensor/shared/venvs/tensor_eco/bin/python"
BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1")
GH_API = "https://api.github.com"
OWNER = "cxs503"
REPOS = ["TensorLBM", "TensorPlatform", "TensorPhysics", "TensorDEM", "TensorFVM"]

MARK = "REVIEW-RESPONSE:"
TERMINAL = ("succeeded", "failed", "stopped")
INFLIGHT = ("queued", "claimed", "running", "awaiting_acceptance")
HEAD_PREFIX = ("agent/", "fix/")
SKIP_MARK = "<!-- bridge-reply -->"      # 本桥回贴标记：扫描时跳过，防自激环
MAX_PER_ROUND = 1
LOG = WORK / "review_bridge.log"


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {msg}\n")


def api(method: str, path: str, body: dict | None = None, tok: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{method} {path} 传输错误: {type(exc).__name__}") from exc


def gh(method: str, path: str, tok: str, body: dict | None = None):
    req = urllib.request.Request(
        GH_API + path, data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + tok,
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, {}


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_state(state: dict) -> None:
    tmp = WORK / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, WORK / "state.json")


def gh_token() -> str:
    tok = os.environ.get("GH_PUSH_TOKEN", "")
    if not tok:
        p = Path("/tmp/gh_token")
        if p.exists():
            tok = p.read_text().strip()
    return tok


def relay_ok() -> bool:
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    if not base:
        return False
    req = urllib.request.Request(base + "/models")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


GOAL_TMPL = """REVIEW-RESPONSE:{repo}#{num}-c{cid} — 评审意见回应：{repo}#{num}

评审人意见原文（直读 GitHub API，零手抄）：
---
{body}
---

任务（这是对已有 PR {repo}#{num} 的评审回应，PR 分支基线=你 worktree 的起点）：
1. 读懂该 PR 的意图与 diff（git log/diff 你的基线），核实这条意见：
   属实 → 最小改动修复；部分属实 → 修该修的部分并说明；不属实 → 给出证据反驳。
2. 修复须带回归测试（新增或扩展对应 test_*；复现意见指出的缺陷再证修复）。
3. commit 到本 mission 分支（中文提交信息，聚焦该意见）；run_tests 全绿后
   request_acceptance（summary 给结论+测试证据，evidence_files 填测试与判据工件）。
4. 写 <worktree>/review_reply.md：回贴文本（中文）——结构：结论（接受/部分接受/
   反驳）→ 改动内容 → 测试证据（数字/用例数）→ commit SHA。派发线代你回贴到 PR。

红线：不 git push（派发线统一推）；不改无关代码；不重开 PR；merge 留 owner。
预算纪律：核实优先于动手；预算过半未定型先落 review_reply.md 草稿。
"""


def goal_text(repo: str, num: int, cid: int, body: str) -> str:
    return GOAL_TMPL.format(repo=repo, num=num, cid=cid,
                            body=body[:4000])


def select_candidates(prs: list[dict], inline: list[dict],
                      convo: list[dict], processed: dict) -> list[dict]:
    """纯函数：从 PR 列表与两路评论里选未处理的候选。

    规则：head 前缀 agent/ 或 fix/；跳过本桥自贴（SKIP_MARK）；按评论 id 幂等。
    返回按评论 id 升序（老的先回应）。
    """
    cands = []
    for pr in prs:
        ref = str((pr.get("head") or {}).get("ref") or "")
        if not ref.startswith(HEAD_PREFIX):
            continue
        repo = ((pr.get("head") or {}).get("repo") or {}).get("name") \
            or str(pr.get("base", {}).get("repo", {}).get("name", ""))
        num = pr.get("number")
        if not (repo and num):
            continue
        for pfx, comments in (("c", inline), ("m", convo)):
            for c in comments:
                key = f"{repo}#{num}-{pfx}{c.get('id')}"
                if key in processed:
                    continue
                if SKIP_MARK in str(c.get("body") or ""):
                    continue
                if not str(c.get("body") or "").strip():
                    continue
                cands.append({"key": key, "repo": repo, "num": num,
                              "cid": c.get("id"),
                              "sha": (pr.get("head") or {}).get("sha"),
                              "body": str(c.get("body") or "")})
    return sorted(cands, key=lambda c: str(c["cid"]))


def post_reply(repo: str, num: int, text: str, tok: str) -> str | None:
    code, out = gh("POST", f"/repos/{OWNER}/{repo}/issues/{num}/comments",
                   tok, {"body": f"{SKIP_MARK}\n{text}"})
    if code not in (200, 201):
        log(f"ERROR {repo}#{num}: 回贴 HTTP {code} {str(out)[:160]}")
        return None
    return out.get("html_url")


def sync_terminal(mine: dict[int, dict], state: dict, tok: str) -> None:
    recs = state.setdefault("items", {})
    for key, rec in list(recs.items()):
        mid = rec.get("mission")
        if not mid or rec.get("done") or rec.get("needs_human"):
            continue
        m = mine.get(mid)
        if not m or m.get("status") not in TERMINAL:
            continue
        if m["status"] != "succeeded":
            rec["needs_human"] = True
            rec["status"] = m["status"]
            log(f"MANUAL {key}: m{mid} {m['status']} → 人工位")
            continue
        wt = ECO / "agent_work" / f"m{mid}" / rec["repo"]
        md = wt / "review_reply.md"
        if not md.exists():
            rec["needs_human"] = True
            rec["status"] = "reply_missing"
            log(f"MANUAL {key}: m{mid} succeeded 但 review_reply.md 缺失 → 人工位")
            continue
        url = post_reply(rec["repo"], rec["num"], md.read_text(encoding="utf-8"), tok)
        if url is None:
            continue            # 回贴瞬时失败：不置 done，下轮重试（幂等风险低）
        rec.update(done=True, reply=url, status="succeeded")
        log(f"DONE {key}: m{mid} succeeded → 回贴 {url}")
    save_state(state)


def dispatch(cand: dict) -> int | None:
    goal_file = WORK / "goals" / f"{cand['key'].replace('#', '_')}.txt"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text(
        goal_text(cand["repo"], cand["num"], cand["cid"], cand["body"]),
        encoding="utf-8")
    env = dict(os.environ,
               REVIEW_USER=os.environ.get("OPERATOR_USER", ""),
               REVIEW_PASS=os.environ.get("OPERATOR_PASS", ""))
    r = subprocess.run(
        [PY, str(HELPER), "--repo", cand["repo"],
         "--base-ref", cand["sha"], "--model", "qwen3.7-plus",
         "--runner", "native", "--budget", "120,10800,4000000",
         "--origin", "agent", "--goal-file", str(goal_file),
         "--memory-key", "review-response"],
        capture_output=True, text=True, timeout=180, env=env)
    if r.returncode != 0:
        log(f"ERROR {cand['key']}: dispatch_helper 失败 {r.stderr.strip()[:240]}")
        return None
    try:
        return json.loads(r.stdout).get("id")
    except json.JSONDecodeError:
        log(f"ERROR {cand['key']}: helper 输出不可解析 {r.stdout[:160]!r}")
        return None


def main() -> int:
    if (WORK / "OFF").exists():
        log("SKIP: OFF 开关存在")
        return 0
    user, pw = os.environ.get("OPERATOR_USER"), os.environ.get("OPERATOR_PASS")
    if not (user and pw):
        log("ERROR: OPERATOR_USER/PASS 未设置（cron 须 source operator.env）")
        return 1
    tok = gh_token()
    if not tok:
        log("ERROR: 无 GitHub 令牌（GH_PUSH_TOKEN//tmp/gh_token）")
        return 1
    code, body = api("POST", "/auth/login", {"username": user, "password": pw})
    if code != 200:
        log(f"ERROR: 登录失败 HTTP {code}")
        return 1
    ptok = body["access_token"]
    code, body = api("GET", "/tensor/missions?limit=100", tok=ptok)
    if code != 200 or not isinstance(body.get("items"), list):
        log(f"ERROR: mission 列表不可得 HTTP {code}（fail closed 不动作）")
        return 2
    # 前缀精确匹配（与 benchmark 线一致的教训：值班 goal 记忆里可能含 MARK 字样）
    mine = {m["id"]: m for m in body["items"]
            if str(m.get("goal") or "").startswith(MARK)}

    state = load_json(WORK / "state.json", {"items": {}})
    # 幂等兜底：state 丢失时按 mission goal 标记补录（防重复派发同一评论）
    for m in mine.values():
        g = str(m.get("goal") or "")
        stub = g.split(" — ", 1)[0][len(MARK):]
        try:
            repo, rest = stub.split("#", 1)
            num = int(rest.split("-")[0])
        except ValueError:
            continue
        if repo in REPOS and stub not in state["items"]:
            state["items"][stub] = {"mission": m["id"], "repo": repo, "num": num,
                                    "recovered": True}
            log(f"RECOVER {stub} → m{m['id']}（state 丢失补录）")
    sync_terminal(mine, state, tok)

    inflight = [m for m in mine.values() if m.get("status") in INFLIGHT]
    if len(inflight) >= MAX_PER_ROUND:
        log(f"SKIP: 已有 {len(inflight)} 个回应在飞")
        return 0

    for repo in REPOS:
        code, prs = gh("GET", f"/repos/{OWNER}/{repo}/pulls"
                       f"?state=open&per_page=50", tok)
        if code != 200 or not isinstance(prs, list):
            log(f"WARN {repo}: pulls 不可得 HTTP {code}（跳过该库）")
            continue
        for pr in prs:
            num = pr.get("number")
            code, inline = gh("GET", f"/repos/{OWNER}/{repo}/pulls/{num}/comments",
                              tok)
            code2, convo = gh("GET", f"/repos/{OWNER}/{repo}/issues/{num}/comments",
                              tok)
            if code != 200 or code2 != 200:
                log(f"WARN {repo}#{num}: comments 不可得 HTTP {code}/{code2}")
                continue
            cands = select_candidates([pr], inline if isinstance(inline, list) else [],
                                      convo if isinstance(convo, list) else [],
                                      state.get("items", {}))
            for cand in cands:
                if not cand.get("sha"):
                    log(f"WARN {cand['key']}: PR head sha 缺失 → 跳过")
                    continue
                if not relay_ok():
                    log("SKIP: 中继不通，本轮不派发（恢复后自动续）")
                    return 0
                mid = dispatch(cand)
                if mid:
                    state["items"][cand["key"]] = {
                        "mission": mid, "repo": cand["repo"], "num": cand["num"],
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
                    save_state(state)
                    log(f"DISPATCHED {cand['key']} → m{mid}")
                return 0          # 每轮至多 1 个
    log("IDLE: 无待回应评审意见")
    return 0


if __name__ == "__main__":
    sys.exit(main())
