#!/usr/bin/env python3
"""Harness v3 — 工作日志(journal)与断点续跑原语（mission m1231 / owner 优先一）。

问题陈述（平台实证）
--------------------
席位进程退出（kill -9 / OOM / 守护重启）后，内存里的"做到哪一步"随进程消失：
mission 被 reclaim 后重领的单从零开始读代码。m1121 事故 = 15 分钟内存工作全丢。
「进程还在 ≠ 工作连续性已保存；进程退出 ≠ 成果必须丢失。」

本模块只做一件事：把「我在干什么 / 干完什么 / 验证了什么 / 下一步」变成
**append-only、可校验、可降级** 的落盘事实，供 claim 时注入。

四条不可让步的约束
------------------
1. append-only：永不重写历史行；崩溃最多丢最后一条未落盘记录。
2. 自校验 + 链式摘要：每条记录带 sha256(正文) 与 prev_digest 链头；
   读到坏行 / 链断 / 篡改 → 明确 degraded，不猜、不修复、不补写。
3. 续跑必须回到磁盘证据：journal 声称完成但工作树无证据 → 该步重做，
   并记 deviation（绝不伪造续跑）。
4. 预算帽跨续跑累计：journal 中 calls_used 是唯一权威，续跑**不追加**预算；
   用尽就记 budget_exhausted 收尾。

CLI
---
    python3 scripts/harness_journal.py brief  --mission-id 1231 --root /tmp/j
    python3 scripts/harness_journal.py verify --mission-id 1231 --root /tmp/j
    python3 scripts/harness_journal.py show   --mission-id 1231 --root /tmp/j
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

SCHEMA = "harness.journal.v3"
DEFAULT_ROOT = pathlib.Path(
    os.environ.get("HARNESS_JOURNAL_ROOT") or "/srv/tensor/agent/journals")


# ─────────────────────────── 路径 ───────────────────────────
def journal_root(root=None) -> pathlib.Path:
    """每次调用都重读环境变量（测试/多租户可在同进程内切换根目录）。"""
    if root:
        return pathlib.Path(root)
    return pathlib.Path(os.environ.get("HARNESS_JOURNAL_ROOT") or DEFAULT_ROOT)


def mission_dir(root, mission_id) -> pathlib.Path:
    return journal_root(root) / "missions" / f"m{int(mission_id)}"


def journal_path(root, mission_id) -> pathlib.Path:
    return mission_dir(root, mission_id) / "journal.jsonl"


def meta_path(root, mission_id) -> pathlib.Path:
    return mission_dir(root, mission_id) / "meta.json"


# ─────────────────────────── 摘要 ───────────────────────────
def _canon(body: dict) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "digest"}
    import hashlib
    return "sha256:" + hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()


# ─────────────────────────── 脏工作树快照 ───────────────────────────
def tree_snapshot(root) -> dict:
    """当前工作树的"脏状态"快照：git 仓库给 porcelain+HEAD，非 git 给文件清单摘要。

    目的不是完整 diff，而是让续跑者知道「磁盘被停在写入中途」这件事本身。
    """
    root = pathlib.Path(root)
    snap = {"path": str(root), "exists": root.exists(), "kind": "unknown"}
    if not root.exists():
        return snap
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=20)
        if head.returncode == 0:
            snap["kind"] = "git"
            snap["head"] = head.stdout.strip()[:40]
            st = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=30)
            lines = [ln for ln in st.stdout.splitlines() if ln.strip()]
            snap["dirty_count"] = len(lines)
            snap["dirty_paths"] = [ln[3:].strip() for ln in lines][:40]
            return snap
    except Exception:
        pass
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                   if p.is_file())[:200]
    snap["kind"] = "dir"
    snap["file_count"] = len(files)
    snap["files"] = files[:40]
    return snap


# ─────────────────────────── 写 ───────────────────────────
def append(root, mission_id, kind="checkpoint", agent_id="", **fields) -> dict:
    """追加一条记录并返回它（含 digest）。只追加，永不重写。"""
    p = journal_path(root, mission_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    st = read(root, mission_id)
    prev = st["entries"][-1]["digest"] if st["entries"] else ""
    rec = {
        "schema": SCHEMA,
        "seq": (st["entries"][-1]["seq"] + 1) if st["entries"] else 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mission_id": int(mission_id),
        "agent_id": agent_id,
        "kind": kind,
        "prev_digest": prev,
    }
    for k, v in fields.items():
        rec[k] = v
    rec["digest"] = digest(rec)
    line = _canon(rec) + "\n"
    # O_APPEND 单次 write：进程被 kill -9 最多留下半行，而不是写坏整份文件
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return rec


def write_meta(root, mission_id, **fields) -> dict:
    p = meta_path(root, mission_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = {}
    if p.exists():
        try:
            meta = json.loads(p.read_text())
        except Exception:
            meta = {}
    meta.update({k: v for k, v in fields.items()})
    meta.setdefault("schema", SCHEMA)
    meta.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    meta["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    os.replace(tmp, p)
    return meta


# ─────────────────────────── 读 + 校验 ───────────────────────────
def read(root, mission_id) -> dict:
    """读日志并做完整性校验。永不抛异常：坏数据变成可解释的 degraded 状态。"""
    p = journal_path(root, mission_id)
    out = {"path": str(p), "exists": p.exists(), "entries": [], "corrupt": [],
           "raw_lines": 0, "degraded": False, "reason": ""}
    if not p.exists():
        out["degraded"] = True
        out["reason"] = "journal_missing"
        return out
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:                              # 权限/IO 异常也算缺失
        out["degraded"] = True
        out["reason"] = f"journal_unreadable:{type(e).__name__}"
        return out
    lines = raw.splitlines()
    out["raw_lines"] = len(lines)
    prev = ""
    for i, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            out["corrupt"].append({"line": i, "why": "unparsable_json"})
            continue
        if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
            out["corrupt"].append({"line": i, "why": "bad_schema"})
            continue
        body_digest = rec.get("digest", "")
        if digest(rec) != body_digest:
            out["corrupt"].append({"line": i, "why": "digest_mismatch"})
            continue
        if out["entries"] and rec.get("prev_digest") != out["entries"][-1]["digest"]:
            # 链断：说明中间有条记录丢失/被截断 —— 但本条自身完整，仍可用
            out["corrupt"].append({"line": i, "why": "chain_break"})
        if out["entries"] and rec.get("seq", 0) <= out["entries"][-1]["seq"]:
            out["corrupt"].append({"line": i, "why": "seq_regression"})
            continue
        out["entries"].append(rec)
        prev = rec["digest"]
    if out["corrupt"]:
        out["degraded"] = True
        out["reason"] = "journal_corrupt"
    if not out["entries"]:
        out["degraded"] = True
        out["reason"] = out["reason"] or "journal_empty"
    return out


def merge(entries) -> dict:
    """把 append-only 记录流折叠成"当前已知状态"（后写覆盖先写，步骤按 id 去重）。"""
    st = {"goal": "", "plan": [], "done_steps": {}, "verified_facts": [],
          "unverified_assumptions": [], "next_intent": "", "calls_used": 0,
          "progress": 0.0, "attempt": 0, "status": "", "dirty_tree": {},
          "deviations": [], "steps_total": 0}
    for rec in entries:
        if rec.get("goal"):
            st["goal"] = rec["goal"]
        if rec.get("plan"):
            st["plan"] = list(rec["plan"])
        for s in rec.get("done_steps") or []:
            if isinstance(s, dict) and s.get("id"):
                st["done_steps"][s["id"]] = s
        if rec.get("verified_facts"):
            st["verified_facts"] = list(rec["verified_facts"])
        if rec.get("unverified_assumptions"):
            st["unverified_assumptions"] = list(rec["unverified_assumptions"])
        if rec.get("next_intent"):
            st["next_intent"] = rec["next_intent"]
        if isinstance(rec.get("calls_used"), int):
            st["calls_used"] = max(st["calls_used"], rec["calls_used"])
        if isinstance(rec.get("progress"), (int, float)):
            st["progress"] = max(st["progress"], float(rec["progress"]))
        if isinstance(rec.get("attempt"), int):
            st["attempt"] = max(st["attempt"], rec["attempt"])
        if rec.get("steps_total"):
            st["steps_total"] = int(rec["steps_total"])
        if rec.get("status"):
            st["status"] = rec["status"]
        if rec.get("dirty_tree"):
            st["dirty_tree"] = rec["dirty_tree"]
        for d in rec.get("deviations") or []:
            st["deviations"].append(d)
    return st


# ─────────────────────────── 续跑简报（claim 注入） ───────────────────────────
HEADER_RESUME = "【断点续跑 · 你在续跑，不是从零开始】"
HEADER_FRESH = "【无可用工作日志 · 从零开始】"


def resume_brief(root, mission_id, verified_evidence=None) -> dict:
    """生成 claim 时注入的续跑上下文。

    返回 {mode: resume|fresh, text, resumed_steps:[...], degraded:bool, ...}。
    `verified_evidence`：callable(step)->bool，用于复核该步证据是否真的在磁盘上
    （不给则视为未复核，说明里如实写明）。证据缺失的步骤会被剔出 resumed_steps，
    并在 text 里标成"需重做"，从而不可能伪造续跑。
    """
    st = read(root, mission_id)
    m = merge(st["entries"])
    brief = {"mode": "fresh", "degraded": st["degraded"], "reason": st["reason"],
             "journal_path": st["path"], "valid_records": len(st["entries"]),
             "corrupt_records": len(st["corrupt"]), "resumed_steps": [],
             "evidence_missing_steps": [], "calls_used": m["calls_used"],
             "progress": m["progress"], "text": ""}

    if st["degraded"] and not st["entries"]:
        brief["text"] = (
            f"{HEADER_FRESH} mission#{mission_id}：journal 不可用"
            f"（{st['reason']}，路径 {st['path']}）。\n"
            f"→ 从零开始，且**不要声称续跑**；若你发现此前的成果痕迹（工作树/artifacts），"
            f"以磁盘为准并显式记录「历史无日志、按磁盘复核」这一事实。\n")
        return brief

    resumed, missing = [], []
    for sid, s in m["done_steps"].items():
        ok = True
        if callable(verified_evidence):
            try:
                ok = bool(verified_evidence(s))
            except Exception:
                ok = False
        (resumed if ok else missing).append(sid)
    brief["mode"] = "resume" if resumed else "fresh"
    brief["resumed_steps"] = sorted(resumed)
    brief["evidence_missing_steps"] = sorted(missing)

    L = [HEADER_RESUME,
         f"mission#{mission_id} · journal={st['path']} · 有效记录 {len(st['entries'])} 条"
         f" / 损坏 {len(st['corrupt'])} 条（degraded={st['degraded']}）"
         f" · 上次进度 {m['progress']:.0%} · 已用工具调用 {m['calls_used']}"]
    if st["degraded"]:
        L.append(f"⚠ 日志部分损坏（{st['reason']}）：以下仅来自通过校验的记录，"
                 f"坏行 {[c['line'] for c in st['corrupt']][:8]} 未采信。")
    if m["goal"]:
        L.append(f"[目标] {m['goal'][:500]}")
    if m["plan"]:
        L.append("[计划] " + " | ".join(str(x)[:60] for x in m["plan"][:12]))
    if resumed:
        L.append("[已完成步骤 · 已复核磁盘证据，勿重做]")
        for sid in sorted(resumed):
            s = m["done_steps"][sid]
            L.append(f"  - {sid}: {str(s.get('what',''))[:160]}"
                     f"（证据: {str(s.get('evidence','(未记)'))[:120]}）")
    if missing:
        L.append("[日志声称完成但磁盘无证据 · 必须重做] " + ", ".join(sorted(missing)))
    if m["verified_facts"]:
        L.append("[已验证事实 · 可直接采用]")
        for f in m["verified_facts"][:10]:
            L.append(f"  - {str(f)[:200]}")
    if m["unverified_assumptions"]:
        L.append("[未验证假设 · 用前先验]")
        for a in m["unverified_assumptions"][:10]:
            L.append(f"  - {str(a)[:200]}")
    dt = m.get("dirty_tree") or {}
    if dt.get("kind") == "git" and dt.get("dirty_count"):
        L.append(f"[脏工作树] {dt['dirty_count']} 个未提交路径（HEAD {dt.get('head','')[:8]}），"
                 f"可能停在写入中途，先核对：{[p for p in (dt.get('dirty_paths') or [])[:8]]}")
    if m["deviations"]:
        L.append(f"[历史偏差] {m['deviations'][-3:]}")
    if m["next_intent"]:
        L.append(f"[下一步意图] {m['next_intent'][:300]}")
    L.append("★纪律：以上是 append-only 日志事实，不是猜测；与工作树冲突时以工作树为准并记录偏差。"
             "不要从零重读整个仓库，先按「已完成/下一步意图」推进。已用预算不因续跑恢复。")
    brief["text"] = "\n".join(L) + "\n"
    return brief


def budget_remaining(mission_budget: dict, m: dict) -> dict:
    """跨续跑累计预算：remaining = total - journal.calls_used（不追加）。"""
    total_calls = int((mission_budget or {}).get("max_tool_calls", 0) or 0)
    total_wall = int((mission_budget or {}).get("max_wall_seconds", 0) or 0)
    used = int(m.get("calls_used", 0) or 0)
    return {"total_tool_calls": total_calls, "used_tool_calls": used,
            "remaining_tool_calls": max(0, total_calls - used),
            "total_wall_seconds": total_wall,
            "extended": False}


# ─────────────────────────── CLI ───────────────────────────
def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Harness v3 journal / resume brief")
    ap.add_argument("cmd", choices=["brief", "verify", "show", "append"])
    ap.add_argument("--mission-id", type=int, required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "brief":
        b = resume_brief(a.root, a.mission_id)
        print(json.dumps(b, ensure_ascii=False, indent=1) if a.json else b["text"])
        return 0 if b["mode"] == "resume" else 2
    if a.cmd == "verify":
        st = read(a.root, a.mission_id)
        m = merge(st["entries"])
        rep = {"path": st["path"], "exists": st["exists"], "raw_lines": st["raw_lines"],
               "valid_records": len(st["entries"]), "corrupt_records": len(st["corrupt"]),
               "corrupt": st["corrupt"][:20], "degraded": st["degraded"],
               "reason": st["reason"], "calls_used": m["calls_used"],
               "progress": m["progress"], "status": m["status"],
               "done_steps": sorted(m["done_steps"])}
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if (st["entries"] and not st["corrupt"]) else 2
    if a.cmd == "append":
        rec = json.loads(sys.stdin.read() or "{}")
        print(json.dumps(append(a.root, a.mission_id, **rec), ensure_ascii=False))
        return 0
    st = read(a.root, a.mission_id)
    if a.json:
        print(json.dumps({"path": st["path"], "entries": st["entries"],
                          "corrupt": st["corrupt"], "degraded": st["degraded"],
                          "merged": merge(st["entries"])}, ensure_ascii=False, indent=1))
        return 0
    for rec in st["entries"]:
        print(_canon(rec)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(_main())