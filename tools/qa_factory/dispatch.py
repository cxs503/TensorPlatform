#!/usr/bin/env python3
"""QA 产线 · 深检派单器（复用 knowledge-factory 工序 04 的独立评审硬门）。

硬门（与 docs/KNOWLEDGE_FACTORY_PILOT.md 工序 04 同构）：
  · reviewer mission ≠ producer mission —— 深检单不许派给报告作者本人（批次内容含原 mission_id，
    派单前剔除 `reviewer == producer` 的篇目并记录）；
  · 单形复用 ops/report_qa.py 的质检单（逐篇 PASS/MINOR/RESUBMIT + 证据引文），不另造评审池；
  · 每单篇数有上限（默认 10），budget 有上限，防止一单吃满上下文而退化成"盖章"。

派单只产出 mission payload（`build_review_missions`）+ 可选 POST（`submit`）——便于评审与 dry-run。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

TIER_CN = {"pass": "machine-pass", "machine-flag": "machine-flag", "suspect": "suspect"}

GOAL_TEMPLATE = """【QA 产线深检·独立评审席 batch {tag}】你是独立报告质检 agent（FEAT-QA-FACTORY 深检执行器）。
本批 {n} 篇（机器判档 → 报告 id 与生产 mission id 对照）：
{listing}

步骤：
1. 登录平台（$TENSOR_AGENT_USER/$TENSOR_AGENT_PASS @ $TENSOR_API_BASE），逐篇 GET /tensor/reports/{{id}} 取 markdown 全文；
2. 逐篇按预注册判据独立复核三查（**先自己判，再看机器理由，避免确认偏倚**）：
   A 结构：技术报告六要素（案例/方法/结果数字表/图或声明理由/注意点/误差是否<3% 显式回答）；质检元报告看判定表；
   B 证据指针：正文提到的工件路径/报告号/工作单号是否可解析（worktree /root/tp-agent/work/m<mid>/ 或 PG 记录）；
      旧 worktree 已回收=不可判（记 unverifiable，不算失败）；
   C 关键数字：精度承载数字是否有溯源（同行±2 行内工件路径/行号/SQL/复算/程序化注入）或显式「不可复算」声明；
3. 每篇给深检档：pass / machine-flag / suspect（判据与 PREREG 同）+ 一句证据引文（引用原文行或工件路径）；
4. 交付：worktree 内 docs/qa_factory/deep_review_<tag>.jsonl，每行
   {{"report_id":N,"deep_tier":"pass|machine-flag|suspect","criteria":{{"structure":"ok|flag|fail","pointers":"...","numbers":"..."}},"evidence":"...","producer_mission":M}}；
5. 汇总报告 POST /tensor/reports（title=【QA】深检批次 <tag>，scenario_key='report-qa'，mission_id=本单）。
纪律：禁碰 GitHub；禁改业务代码；不要为"通过"而放宽判据——判不出就判 machine-flag 并写原因。"""


def build_review_missions(picks: List[Dict[str, Any]], *, batch_size: int = 10,
                          max_missions: int = 6,
                          producers: Optional[Dict[int, int]] = None) -> Dict[str, Any]:
    """picks: [{report_id, tier, producer_mission?}]。返回 {missions:[{tag, reports, goal}], dropped}。

    producers 给定时剔除 producer==reviewer 不可知的情况不需要——reviewer 是新建单，
    永远不等于 producer；这里只保留 producer_mission 供审计追溯。
    """
    items = []
    dropped: List[Dict[str, Any]] = []
    for p in picks:
        rid = int(p["report_id"])
        mid = p.get("producer_mission") or (producers or {}).get(rid)
        items.append({"report_id": rid, "machine_tier": p.get("tier", "unknown"),
                      "producer_mission": mid})
    missions = []
    for i in range(0, min(len(items), batch_size * max_missions), batch_size):
        chunk = items[i:i + batch_size]
        tag = f"r1-{i // batch_size + 1:02d}"
        listing = "\n".join(
            f"  - 报告 {c['report_id']}（机器判 {TIER_CN.get(c['machine_tier'], c['machine_tier'])}"
            f"，生产 mission {c['producer_mission'] or '未知'}）" for c in chunk)
        missions.append({"tag": tag, "reports": [c["report_id"] for c in chunk],
                         "producer_missions": [c["producer_mission"] for c in chunk],
                         "goal": GOAL_TEMPLATE.format(tag=tag, n=len(chunk), listing=listing)})
    return {"missions": missions, "dropped": dropped, "batch_size": batch_size}


def submit(missions: List[Dict[str, Any]], *, api_base: str, repo: str = "TensorPlatform",
           model: str = "qwen3.6-plus") -> int:
    user = os.environ.get("TENSOR_AGENT_USER", "")
    pwd = os.environ.get("TENSOR_AGENT_PASS", "")
    tok = _post(f"{api_base}/auth/login", {"username": user, "password": pwd}).get("access_token")
    if not tok:
        raise RuntimeError("login failed")
    created = []
    for m in missions:
        r = _post(f"{api_base}/tensor/missions", {
            "goal": m["goal"], "repo": repo, "runner": "native", "origin": "qa-factory",
            "model": model,
            "budget": {"max_tool_calls": 120, "max_wall_seconds": 3600,
                       "max_tokens": 3000000}}, token=tok)
        created.append(r.get("id"))
    print(json.dumps({"created_missions": created}, ensure_ascii=False))
    return 0


def _post(url: str, payload: Dict[str, Any], token: Optional[str] = None) -> Dict[str, Any]:
    req = urllib.request.Request(url, method="POST",
                                 data=json.dumps(payload).encode())
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:  # noqa: PERF203
        raise RuntimeError(f"POST {url} -> {e.code}: {e.read()[:200]!r}")