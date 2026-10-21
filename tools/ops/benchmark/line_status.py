#!/usr/bin/env python3
"""B 闭环线状态一览（P3c：心跳/交接一条命令看清全局）。

四表合一：backlog × state × 平台 mission × GitHub open PR。
  - 每条目：done / failed(原因) / 在飞(mission 状态) / 待派（含等待门）
  - 汇总：完成率、一次通过率（gates/suite 门不烧条目）、在飞道占用
  - open PR：cxs503/TensorLBM 待 owner 合并清单

用法（须 source operator.env）：
  /srv/tensor/shared/venvs/tensor_eco/bin/python line_status.py [--json]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

WORK = Path("/srv/tensor/shared/tensor_eco/agent_work/benchmark_closure")
BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1")
GH_API = "https://api.github.com"
REPO_SLUG = "cxs503/TensorLBM"
MARK = "BENCH-CLOSE:"
INFLIGHT = ("queued", "claimed", "running", "awaiting_acceptance")


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def api(method: str, path: str, body=None, tok: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read() or b"null")


def open_prs() -> list[dict]:
    tok = os.environ.get("GH_PUSH_TOKEN", "")
    if not tok and Path("/tmp/gh_token").exists():
        tok = Path("/tmp/gh_token").read_text().strip()
    if not tok:
        return []
    req = urllib.request.Request(
        f"{GH_API}/repos/{REPO_SLUG}/pulls?state=open&per_page=30",
        headers={"Authorization": "Bearer " + tok,
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"[]")
    except Exception:  # noqa: BLE001  状态视图对网络错宽容
        return []


def main() -> int:
    as_json = "--json" in sys.argv
    backlog = load_json(WORK / "backlog.json", {"items": []})
    state = load_json(WORK / "state.json", {"items": {}})
    items = backlog.get("items", [])
    st_items = state.get("items", {})

    user, pw = os.environ.get("OPERATOR_USER"), os.environ.get("OPERATOR_PASS")
    missions: dict[int, dict] = {}
    if user and pw:
        try:
            code, body = api("POST", "/auth/login",
                             {"username": user, "password": pw})
            if code == 200:
                code, body = api("GET", "/tensor/missions?limit=100",
                                 tok=body["access_token"])
                missions = {m["id"]: m for m in body.get("items", [])
                            if str(m.get("goal") or "").startswith(MARK)}
        except Exception:  # noqa: BLE001
            missions = {}

    rows, n_done, n_fail, n_gate_fail, n_inflight, n_wait = [], 0, 0, 0, 0, 0
    for it in items:
        rec = st_items.get(it["key"], {})
        mid = rec.get("mission")
        ms = missions.get(mid, {}).get("status", "")
        if rec.get("done"):
            status, note = "done", f"PR={rec.get('pr', '')} 报告={rec.get('report')}"
            n_done += 1
        elif rec.get("failed"):
            status = "failed"
            note = str(rec.get("error", ""))
            n_fail += 1
            if "gates" in note or "suite" in note:
                n_gate_fail += 1
        elif mid and ms in INFLIGHT:
            status, note = "在飞", f"m{mid} {ms}"
            n_inflight += 1
        elif mid:
            status, note = "在飞", f"m{mid} {ms or '未知(平台未返)'}"
            n_inflight += 1
        elif it.get("wait_for") and not Path(it["wait_for"]).exists():
            status, note = "等门", f"wait_for={it['wait_for']}"
            n_wait += 1
        elif it.get("wait_for_pr"):
            status, note = "等门", f"wait_for_pr={it['wait_for_pr']}"
            n_wait += 1
        else:
            status, note = "待派", it["model"]
        rows.append({"key": it["key"], "case": it["case"], "lane":
                     "gpu" if it.get("gpu") else "cpu",
                     "isolated": bool(it.get("isolated")),
                     "status": status, "note": note})

    total = len(rows)
    judged = n_done + n_gate_fail
    first_pass = (round(100 * n_done / judged) if judged else None)
    prs = open_prs()
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "total": total,
           "done": n_done, "failed": n_fail, "gate_failed": n_gate_fail,
           "inflight": n_inflight, "waiting": n_wait,
           "pending": total - n_done - n_fail - n_inflight - n_wait,
           "first_pass_pct": first_pass,
           "open_prs": [{"num": p.get("number"), "title": p.get("title"),
                         "branch": (p.get("head") or {}).get("ref")}
                        for p in prs],
           "rows": rows}
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    fp = f"{first_pass}%" if first_pass is not None else "n/a"
    print(f"B 闭环线 @ {out['ts']}  总数={total} 完成={n_done} 失败={n_fail}"
          f"（门禁类={n_gate_fail}）在飞={n_inflight} 等门={n_wait} 待派={out['pending']}")
    print(f"一次通过率（gates/suite 不烧条目口径）={fp}")
    print(f"open PR（{len(prs)}，待 owner）："
          + ("；".join(f"#{p.get('number')} {str(p.get('title'))[:40]}"
                       for p in prs) or "无"))
    for r in rows:
        tag = r["lane"] + ("/隔离" if r["isolated"] else "")
        print(f"  [{r['status']:>4}] {r['key']:<28} {tag:<6} {r['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
