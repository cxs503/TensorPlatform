"""loop —— 最小记忆回路四环闭环（写入 → 注入 → 命中 → 下游影响）。

前置器官（m15946）：``context_package``（出包/渲染/落账）与 ``memory_compress``
（KB 生命周期 use_count / last_used）。本模块只补**接线**，不新开通道：

================  ======================  ==========================================
环                入口                    落的表 / 文件
================  ======================  ==========================================
① 写入            ``write_lesson``        negative_knowledge_ledger（entry_id 可溯 mission_id）
                                          + knowledge_base（kb_key=lesson:m<id>，计数器归此处）
② 注入            ``inject`` /             knowledge_injections（含 NKL 行，kb_id=NULL）
                  ``on_mission_start``   + runs/context_injections/<date>.jsonl
                                          + handoff/<m>.md（供给 worker 首条消息）
③ 命中            ``cite`` /              knowledge_injections.citation_count/cited_at
                  ``on_mission_complete`` + knowledge_base.cited_count/last_cited_at
                                          + runs/context_injections/<date>.citations.jsonl
④ 下游影响        ``cite(used_in_step=)``  citation_ref = 引用它的单的交付物指针；
                                          ``citations.jsonl`` 记录「该教训用在哪一步」
================  ======================  ==========================================

四条硬约束
----------
1. **零领域语义**：教训正文由单自身的 goal/fail_stage/失败原因派生，本模块不认识任何域词；
   检索靠 ``loader.terms``（ASCII 词 + CJK 二元组）做通用相关性。
2. **fail-open**：DB 不可用/表缺列 → 返回 ``{"degraded": ...}`` + 仍写 JSONL，绝不拖死派单。
3. **幂等**：``kb_key = lesson:m<mission_id>`` / ``entry_id = negk:loop:<mission_id>`` 唯一，
   重复调用返回 ``skipped=IDEMPOTENT``（同一单不会写第二条教训，计数器不会被重放刷高）。
4. **禁止伪造时间线**：所有时刻要么是 DB ``now()``（写库时刻），要么是原表时间戳；
   ``timeline()`` 只读不补，缺环如实标 ``MISSING``。

判据（先声明，见 docs/MEMORY_LOOP_E2E.md）
------------------------------------------------
- 命中判定 ``cite``：显式 id/kb_key/entry_id 出现 **或** 与教训 keyword_text 共享词元 ≥
  ``min_terms``(3)。低于阈值即未命中（不许事后调闸）。
- 覆盖率口径：``注入单 = COUNT(DISTINCT knowledge_injections.mission_id)``（当日 injected_at）；
  ``总单 = 当日 created_at 的 tensor_dev_missions``；首日=接线当日。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .assembler import build_package
from .ledger import record, sink_db
from .loader import ROOT, clip, est_tokens, load_config, terms
from .render import render_block
from .taxonomy import class_spec, detect_class

DEFAULT_DSN = "postgresql://postgres@/tensorplatform?host=/var/run/postgresql"
REGISTERED_BY = "memory_loop/context_package"
MIN_TERMS_DEFAULT = 3          # 命中判定阈值（先声明）
LEDGER_JSONL = "runs/context_injections"


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: Any) -> str:
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect(dsn: str | None = None):
    """返回 ``(conn, error)``；失败不抛（fail-open）。"""
    try:
        import psycopg  # 延迟导入：无驱动只降级
        conn = psycopg.connect(dsn or os.environ.get("CONTEXT_LOOP_DSN") or DEFAULT_DSN,
                              connect_timeout=8)
        return conn, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"[:200]


def _ledger_dir(cfg: Mapping[str, Any] | None = None, override=None) -> Path:
    cfg = cfg or load_config()
    return Path(override) if override else ROOT / ((cfg.get("ledger") or {}).get("jsonl_dir")
                                                  or LEDGER_JSONL)


def _append(path: Path, row: Mapping[str, Any]) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return str(path)
    except Exception as exc:  # noqa: BLE001
        return f"ERR:{type(exc).__name__}: {exc}"[:160]


def _keyset(text: Any, limit: int = 24) -> list[str]:
    """确定性关键词集（通用词元，非域词表）：供 NKL.keywords 与相关性检索。"""
    out = sorted(terms(text))
    out.sort(key=lambda t: (-len(t), t))
    return out[:limit]


def mission_row(mission_id: Any, conn) -> dict:
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, goal, repo, status, result, fail_stage, created_at,
                                  finished_at, claimed_by, model, campaign_id
                           FROM tensor_dev_missions WHERE id = %s""", (int(mission_id),))
            cols = [d.name for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else {}
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        return {}


def _failure_signals(row: Mapping[str, Any]) -> dict:
    """从单结果里抽失败画像（不判读域语义，只搬字段）。"""
    res = row.get("result")
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:
            res = {}
    res = res if isinstance(res, Mapping) else {}
    checks = res.get("checks") if isinstance(res.get("checks"), Mapping) else {}
    ev = res.get("evidence") if isinstance(res.get("evidence"), Mapping) else {}
    kind = (row.get("fail_stage") or checks.get("fail_stage") or ev.get("fail_stage")
            or checks.get("gate") or row.get("status") or "")
    reason = (checks.get("reason") or res.get("headline")
              or (res.get("summary") or {}).get("headline") if isinstance(res.get("summary"), dict) else None
              or "")
    if not str(kind).strip():
        kind = "failed_no_signal"      # 结果里没有 fail_stage/checks 时也不能留空
    if not str(reason).strip():
        reason = "result 未提供 checks.reason（见来源单 result 原文）"
    return {"failure_kind": str(kind)[:64], "failure_reason": str(reason)[:400],
            "result": res, "model": row.get("model") or res.get("model") or ""}


# --------------------------------------------------------------------------- #
# 环①：写入（真实失败单 → 一条负面教训）
# --------------------------------------------------------------------------- #
def write_lesson(mission_id: Any, *, conn=None, cfg: Mapping[str, Any] | None = None,
                 ledger_dir=None, force: bool = False, dry_run: bool = False) -> dict:
    """单末蒸馏：把一张真实失败单压成**一条**负面教训，写 NKL + KB，来源单号可溯。"""
    cfg = cfg or load_config()
    own = conn is None
    conn, err = connect() if own else (conn, "")
    out: dict[str, Any] = {"mission_id": mission_id, "written": False}
    if conn is None:
        out["degraded"] = f"NO_DB:{err}"
        return out
    try:
        row = mission_row(mission_id, conn)
        if not row:
            out["degraded"] = "MISSION_NOT_FOUND"
            return out
        if row.get("status") not in ("failed", "stopped", "revoked") and not force:
            out["skipped"] = f"NOT_FAILURE:{row.get('status')}"
            return out
        sig = _failure_signals(row)
        cls, cls_ev = detect_class(row.get("goal"), cfg)
        kb_key = f"lesson:m{int(mission_id)}"
        entry_id = f"negk:loop:{int(mission_id)}"
        title = clip(f"m{int(mission_id)} {sig['failure_kind'] or '失败'}｜"
                     f"{clip(row.get('goal'), 60)}", 120)
        lesson = (f"同类单（{cls}·{row.get('repo')}）先避开 {sig['failure_kind']}："
                  f"{sig['failure_reason'] or '无失败原因字段（result 缺失）'}；"
                  f"来源 mission#{int(mission_id)}")
        keys = _keyset(" ".join(str(x) for x in (row.get("goal"), sig["failure_kind"],
                                                sig["failure_reason"]) if x))
        evidence = {"kind": "negative_lesson", "mission_id": int(mission_id),
                    "fail_stage": sig["failure_kind"], "task_class": cls,
                    "repro": f"tensor_dev_missions#{int(mission_id)}",
                    "entry": f"negative_knowledge_ledger#{entry_id}",
                    "finished_at": _iso(row.get("finished_at"))}
        out.update({"kb_key": kb_key, "entry_id": entry_id, "task_class": cls,
                    "class_evidence": cls_ev, "keywords": keys, "lesson": lesson,
                    "title": title})
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM knowledge_base WHERE kb_key = %s", (kb_key,))
            exists_kb = cur.fetchone()
            cur.execute("SELECT id FROM negative_knowledge_ledger WHERE entry_id = %s", (entry_id,))
            exists_nk = cur.fetchone()
        if exists_kb and exists_nk and not force:
            with conn.cursor() as cur2:
                cur2.execute("SELECT created_at FROM knowledge_base WHERE id = %s",
                             (exists_kb[0],))
                got = cur2.fetchone()
                out["kb_created_at"] = _iso(got[0]) if got else ""
                cur2.execute("SELECT registered_at FROM negative_knowledge_ledger "
                             "WHERE entry_id = %s", (entry_id,))
                got2 = cur2.fetchone()
                out["nkl_registered_at"] = _iso(got2[0]) if got2 else ""
            out.update({"skipped": "IDEMPOTENT", "kb_id": exists_kb[0],
                        "nkl_id": exists_nk[0], "kb_key": kb_key, "entry_id": entry_id,
                        "task_class": cls, "class_evidence": cls_ev})
            return out
        if dry_run:
            out["skipped"] = "DRY_RUN"
            return out
        with conn.cursor() as cur:
            if not exists_nk:
                cur.execute(
                    """INSERT INTO negative_knowledge_ledger
                       (entry_id, mission_id, title, hypothesis, falsification_evidence,
                        failure_reason, failure_kind, applicable_conditions, keywords,
                        domain_tags, differentiation_required, source_artifacts, confidence,
                        schema_version, registered_by, registered_at, updated_at, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now(), 'active')""",
                    (entry_id, int(mission_id), title, lesson,
                     json.dumps(evidence, ensure_ascii=False), sig["failure_reason"],
                     sig["failure_kind"], f"task_class={cls}; repo={row.get('repo')}",
                     " ".join(keys), json.dumps([row.get("repo")] if row.get("repo") else [],
                                                ensure_ascii=False),
                     "改写既有教训前必须先回读来源单 result",
                     f"tensor_dev_missions#{int(mission_id)}", 0.6,
                     "negk/v1", REGISTERED_BY))
                out["nkl_written"] = True
            cur.execute("SELECT id FROM knowledge_base WHERE kb_key = %s", (kb_key,))
            got = cur.fetchone()
            if got:
                out["kb_id"] = got[0]
            else:
                cur.execute("""INSERT INTO knowledge_base
                               (kb_key, ktype, domain, title, content, evidence, source,
                                status, created_at, injected_count, cited_count)
                               VALUES (%s,'lesson',%s,%s,%s,%s,%s,'active', now(), 0, 0)
                               RETURNING id""",
                            (kb_key, row.get("repo"), title, lesson,
                             json.dumps(evidence, ensure_ascii=False), REGISTERED_BY))
                out["kb_id"] = cur.fetchone()[0]
                out["kb_written"] = True
                cur.execute("SELECT created_at FROM knowledge_base WHERE id = %s",
                            (out["kb_id"],))
                out["kb_created_at"] = _iso(cur.fetchone()[0])
            cur.execute("SELECT registered_at FROM negative_knowledge_ledger WHERE entry_id = %s",
                        (entry_id,))
            out["nkl_registered_at"] = _iso(cur.fetchone()[0])
        conn.commit()
        out["written"] = True
        out["ledger"] = _append(_ledger_dir(cfg, ledger_dir) / f"{date.today()}.lessons.jsonl",
                                {"ts": _iso(_now()), "ring": "write", **out})
        return out
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        out["degraded"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# 环②：注入（同类单开工 → 干货包含该条，决策键齐；同时落 handoff 供 worker 取用）
# --------------------------------------------------------------------------- #
def fresh_lesson_ids(goal: Any, *, hours: float = 72.0, cfg: Mapping[str, Any] | None = None,
                     conn=None) -> list[str]:
    """本任务类**最近 hours 小时**入账的教训条目 id（新鲜教训，供 inject 保底带上）。

    断点实测（2026-11-12）：新教训 cited/injected 计数为 0，在 KB 排序里垫底，
    同类不同主题的单出包时会被预算挤掉 → 注入环空转。处方=本函数 + ``inject(pin=...)``：
    每包至少保底带 1 条本类新鲜教训（决策键 avoid），与相关性打分无关。
    """
    cfg = cfg or load_config()
    cls, _ = detect_class(goal, cfg)
    own = conn is None
    conn, err = connect() if own else (conn, "")
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT kb_key FROM knowledge_base
                           WHERE ktype = 'lesson' AND status = 'active'
                             AND created_at > now() - (%s || ' hours')::interval
                             AND evidence::text LIKE %s
                           ORDER BY created_at DESC LIMIT 3""",
                        (str(float(hours)), f'%"task_class": "{cls}"%'))
            ids = [r[0] for r in cur.fetchall()]
        out = []
        for k in ids:
            out.append("negk:" + str(k).split("lesson:m", 1)[-1].join(["loop:", ""]))
        return out
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _pin_items(ids: list[str]) -> list[dict]:
    """把 pinned 条目从原表取回成候选 dict（详情仍留在原表，用指针回取）。"""
    out: list[dict] = []
    if not ids:
        return out
    conn, _err = connect()
    if conn is None:
        return out
    try:
        with conn.cursor() as cur:
            for k in ids:
                k = str(k)
                if k.startswith("negk:"):
                    eid = k
                    cur.execute("""SELECT entry_id, mission_id, title, failure_kind,
                                          failure_reason, applicable_conditions, keywords
                                   FROM negative_knowledge_ledger WHERE entry_id = %s""", (eid,))
                    r = cur.fetchone()
                    if not r:
                        continue
                    out.append({"id": eid, "slot": "negative_knowledge",
                                "source": "negative_knowledge_ledger",
                                "text": (clip(r[2], 56) + "｜" + str(r[3] or "") + "｜"
                                         + clip(r[4], 60)),
                                "keyword_text": " ".join(str(x) for x in r[2:] if x),
                                "decision": "avoid", "pinned": True,
                                "pointer": f"negative_knowledge_ledger#{eid} (mission={r[1]})",
                                "meta": {"failure_kind": r[3], "mission_id": r[1]}})
                else:
                    kid = int(k.split(":")[-1])
                    cur.execute("""SELECT id, kb_key, ktype, domain, title, content
                                   FROM knowledge_base WHERE id = %s""", (kid,))
                    r = cur.fetchone()
                    if not r:
                        continue
                    out.append({"id": f"kb:{r[0]}", "slot": "ko_index",
                                "source": "knowledge_base",
                                "text": f"{clip(r[4], 60)}〔{r[2]}·{r[3]}〕{clip(r[5], 70)}",
                                "keyword_text": " ".join(str(x) for x in r[1:] if x),
                                "decision": "avoid", "pinned": True,
                                "pointer": f"knowledge_base#{r[0]}",
                                "meta": {"kb_key": r[1], "ktype": r[2]}})
        return out
    except Exception:  # noqa: BLE001
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def inject(mission_id: Any, goal: Any, repo: str = "", *, consumer: str = "",
           cfg: Mapping[str, Any] | None = None, reader=None, ledger_dir=None,
           handoff_dir=None, as_of: Any = None, pin: list[str] | None = None,
           pin_newest_hours: float = 0.0) -> tuple[str, dict]:
    """出包 + 落账 + 写 handoff（返回 ``(块, 摘要)``；异常一律降级为空块）。"""
    cfg = cfg or load_config()
    info: dict[str, Any] = {"mission_id": mission_id, "injected": False}
    try:
        pkg = build_package(goal, repo=repo, mission_id=mission_id, cfg=cfg, reader=reader,
                            as_of=as_of)
        # 新鲜教训保底位：默认把本类最近教训钉进包（可在调用方关断/改窗口）
        pinned: list[dict] = []
        if pin is None and pin_newest_hours and pin_newest_hours > 0:
            pin = fresh_lesson_ids(goal, hours=pin_newest_hours, cfg=cfg)
        have = {it.get("id") for sl in (pkg.get("slots") or []) for it in (sl.get("items") or [])}
        pinned = [it for it in _pin_items(list(pin or [])) if it.get("id") not in have]
        for it in pinned:
            slot = next((sl for sl in (pkg.get("slots") or [])
                         if sl.get("slot") == it["slot"]), None)
            if slot is None:
                continue
            slot.setdefault("items", []).insert(0, {"id": it["id"], "slot": it["slot"],
                                                    "text": it["text"], "decision": it["decision"],
                                                    "pointer": it["pointer"], "pinned": True,
                                                    "tokens": est_tokens(it["text"], cfg),
                                                    "relevance": 1.0})
        if pinned:
            b = pkg.setdefault("budget", {})
            b["used_tokens"] = int(b.get("used_tokens") or 0) + sum(
                int(it.get("tokens") or 0) for sl in (pkg.get("slots") or [])
                for it in (sl.get("items") or []) if it.get("pinned"))
            b["pinned"] = [it["id"] for it in pinned]
        block = render_block(pkg, cfg.get("decisions") or {})
        if not block:
            info["degraded"] = "EMPTY_PACKAGE"
            return "", info
        entry = record(pkg, rendered=block,
                       mode=os.environ.get((cfg.get("mode_env") or "CONTEXT_PACKAGE_MODE"), "report"),
                       ledger_dir=ledger_dir, consumer=consumer or "memory_loop",
                       manifest_text=str(goal or ""))
        if str(os.environ.get("CONTEXT_LOOP_DB", "1")) not in ("0", "false", "off"):
            entry["_db"] = sink_db(entry, str(goal or ""))   # 显式落总线表（不靠环境变量）
        items = entry.get("items") or []
        info.update({"package_id": pkg.get("package_id"), "task_class": pkg.get("task_class"),
                     "package_sha256": entry.get("package_sha256"), "items": items,
                     "item_ids": [it.get("id") for it in items],
                     "tokens": (pkg.get("budget") or {}).get("used_tokens"),
                     "ledger": entry.get("_ledger_path"), "injected": True,
                     "db_sink": entry.get("_db")})
        hd = Path(handoff_dir) if handoff_dir else _ledger_dir(cfg, ledger_dir) / "handoff"
        try:
            hd.mkdir(parents=True, exist_ok=True)
            (hd / f"m{mission_id}.md").write_text(block + "\n", encoding="utf-8")
            (hd / f"m{mission_id}.json").write_text(
                json.dumps({"package_id": pkg.get("package_id"),
                            "task_class": pkg.get("task_class"),
                            "package_sha256": entry.get("package_sha256"),
                            "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
            info["handoff"] = str(hd / f"m{mission_id}.md")
        except Exception as exc:  # noqa: BLE001
            info["handoff_error"] = f"{type(exc).__name__}: {exc}"[:120]
        info["ledger_jsonl"] = _append(_ledger_dir(cfg, ledger_dir) / f"{date.today()}.loop.jsonl",
                                       {"ts": _iso(_now()), "ring": "inject", **info})
        return block + "\n\n", info
    except Exception as exc:  # noqa: BLE001
        info["degraded"] = f"INJECT_ERROR:{type(exc).__name__}: {exc}"[:160]
        return "", info


def load_handoff(mission_id: Any, *, ledger_dir=None) -> str:
    """worker 侧取用：返回本单注入块（无则空串）。可用 ``CONTEXT_LOOP_HANDOFF`` 改目录。"""
    try:
        cfg = load_config()
        env_dir = os.environ.get("CONTEXT_LOOP_HANDOFF")
        hd = Path(env_dir) if env_dir else (_ledger_dir(cfg, ledger_dir) / "handoff")
        p = hd / f"m{mission_id}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# 环③：命中（交付文本引用 → 计数 +1）+ 环④：下游影响（用在哪一步）
# --------------------------------------------------------------------------- #
def cite(mission_id: Any, artifact_text: str, *, used_in_step: str = "", conn=None,
         cfg: Mapping[str, Any] | None = None, ledger_dir=None,
         min_terms: int = MIN_TERMS_DEFAULT, citation_ref: str = "",
         dry_run: bool = False) -> dict:
    """命中归因 + 计数器回写。``used_in_step`` 即环④「该教训被用在哪一步」。

    命中判定（先声明，不许事后调闸）：交付文本里出现条目 id/kb_key/entry_id
    **或** 与条目正文共享词元 ≥ ``min_terms``。
    """
    cfg = cfg or load_config()
    own = conn is None
    conn, err = connect() if own else (conn, "")
    out: dict[str, Any] = {"mission_id": mission_id, "hits": [], "misses": [],
                           "min_terms": min_terms, "used_in_step": used_in_step}
    text = str(artifact_text or "")
    if conn is None:
        out["degraded"] = f"NO_DB:{err}"
        return out
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT injection_id, kb_id, kb_key, source, injected_at, citation_count
                           FROM knowledge_injections
                           WHERE mission_id = %s ORDER BY injected_at""", (int(mission_id),))
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            probes: dict[str, str] = {}
            for r in rows:
                probe_text, mirror_kb = "", None
                if r.get("kb_id"):
                    cur.execute("SELECT title, content FROM knowledge_base WHERE id = %s",
                                (r["kb_id"],))
                    g = cur.fetchone()
                    probe_text = f"{g[0]} {g[1]}" if g else ""
                    mirror_kb = r["kb_id"]
                elif str(r.get("kb_key") or "").startswith("negk:"):
                    cur.execute("""SELECT mission_id, title, failure_reason, keywords
                                   FROM negative_knowledge_ledger WHERE entry_id = %s""",
                                (r["kb_key"],))
                    g = cur.fetchone()
                    if g:
                        probe_text = " ".join(str(x) for x in (g[1], g[2], g[3]) if x)
                        cur.execute("SELECT id FROM knowledge_base WHERE kb_key = %s",
                                    (f"lesson:m{g[0]}",))
                        m = cur.fetchone()
                        mirror_kb = m[0] if m else None
                probes[r["injection_id"]] = probe_text
                r["_mirror_kb"] = mirror_kb
        if not rows:
            out["skipped"] = "NO_INJECTION_FOR_MISSION"
            return out
        at = terms(text)
        ts = _iso(_now())
        with conn.cursor() as cur:
            for r in rows:
                probe_text = probes.get(r["injection_id"]) or ""
                direct = any(k and k in text for k in (r.get("kb_key"), r.get("injection_id")))
                shared = len(terms(probe_text) & at) if probe_text else 0
                hit = bool(direct or shared >= min_terms)
                rec = {"id": r.get("kb_key"), "injection_id": r.get("injection_id"),
                       "kb_id": r.get("kb_id"), "direct": direct, "shared_terms": shared,
                       "hit": hit, "injected_at": _iso(r.get("injected_at"))}
                (out["hits"] if hit else out["misses"]).append(rec)
                if hit and not dry_run:
                    cur.execute("""UPDATE knowledge_injections
                                   SET citation_count = COALESCE(citation_count,0)+1,
                                       cited_at = %s, citation_ref = %s
                                   WHERE injection_id = %s""",
                                (ts, citation_ref or f"mission#{int(mission_id)}",
                                 r["injection_id"]))
                    if r.get("_mirror_kb"):
                        cur.execute("""UPDATE knowledge_base
                                       SET cited_count = COALESCE(cited_count,0)+1,
                                           last_cited_at = %s WHERE id = %s""",
                                    (ts, r["_mirror_kb"]))
            if not dry_run:
                conn.commit()
        out["cited_at"] = ts
        out["hit_rate"] = round(len(out["hits"]) / max(1, len(out["hits"]) + len(out["misses"])), 4)
        out["ledger"] = _append(_ledger_dir(cfg, ledger_dir) / f"{date.today()}.citations.jsonl",
                                {"ts": ts, "ring": "cite", **out})
        return out
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        out["degraded"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def citation_line(cite_result: Mapping[str, Any]) -> str:
    """环④产物行：把「该教训用在哪一步」写成可贴进报告的一行（无命中返回空串）。"""
    hits = [h for h in (cite_result.get("hits") or []) if h.get("hit")]
    if not hits:
        return ""
    seen, ids = set(), []
    for h in hits:                       # 同一教训多注入行 → 引用行只列一次
        i = str(h.get("id"))
        if i not in seen:
            seen.add(i); ids.append(i)
    ids = ", ".join(ids)
    step = cite_result.get("used_in_step") or "未标注"
    return (f"记忆回路引用：本单注入的 {ids} 在【{step}】被引用"
            f"（cited_at={cite_result.get('cited_at', '')}，"
            f"min_terms={cite_result.get('min_terms')}，ref={cite_result.get('ledger', '')}）")


# --------------------------------------------------------------------------- #
# 钩子门面（后端/worker 调这两个；永不抛）
# --------------------------------------------------------------------------- #
def on_mission_start(mission_id: Any, goal: Any, repo: str = "", consumer: str = "",
                     enabled: str | None = None, **kw) -> dict:
    """开工钩子：出包 + 落账 + handoff。``CONTEXT_MEMORY_LOOP=0`` 全关。"""
    if str(enabled if enabled is not None else os.environ.get("CONTEXT_MEMORY_LOOP", "1")) in ("0", "false", "off"):
        return {"injected": False, "degraded": "LOOP_OFF"}
    kw.setdefault("pin_newest_hours",
                  float(os.environ.get("CONTEXT_LOOP_PIN_HOURS", "72") or 0))
    block, info = inject(mission_id, goal, repo, consumer=consumer, **kw)
    info["block_tokens"] = len(block)
    info["block"] = block          # runner 直接拼进首条消息；不进 JSONL 摘要（下面剥掉）
    return info


def on_mission_complete(mission_id: Any, *, status: str = "", goal: str = "", repo: str = "",
                        deliverable_text: str = "", used_in_step: str = "",
                        citation_ref: str = "", enabled: str | None = None,
                        conn=None) -> dict:
    """收尾钩子：失败单→环①写作；成功单→环③④归因（用交付文本机械判命中）。"""
    if str(enabled if enabled is not None else os.environ.get("CONTEXT_MEMORY_LOOP", "1")) in ("0", "false", "off"):
        return {"degraded": "LOOP_OFF"}
    out: dict[str, Any] = {"mission_id": mission_id, "status": status}
    try:
        if status in ("failed", "stopped", "revoked"):
            out["write"] = write_lesson(mission_id, conn=conn)
        else:
            out["cite"] = cite(mission_id, deliverable_text, used_in_step=used_in_step,
                               citation_ref=citation_ref, conn=conn)
            out["citation_line"] = citation_line(out["cite"])
    except Exception as exc:  # noqa: BLE001
        out["degraded"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


# --------------------------------------------------------------------------- #
# 证据：四环时间线 / 时延 / 覆盖率
# --------------------------------------------------------------------------- #
def timeline(mission_id: Any, *, lesson_key: str | None = None, conn=None,
             ledger_dir=None) -> dict:
    """四环 SQL 时间线（表+行+时刻原文），缺环标 MISSING，时延按真实时刻算。"""
    own = conn is None
    conn, err = connect() if own else (conn, "")
    out: dict[str, Any] = {"source_mission": mission_id, "rings": {}}
    if conn is None:
        out["degraded"] = f"NO_DB:{err}"
        return out
    kb_key = lesson_key or f"lesson:m{int(mission_id)}"
    entry_id = f"negk:loop:{int(mission_id)}"
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, status, fail_stage, created_at, finished_at
                           FROM tensor_dev_missions WHERE id = %s""", (int(mission_id),))
            r = cur.fetchone()
            out["rings"]["source"] = ({"table": "tensor_dev_missions", "row": int(mission_id),
                                       "status": r[1], "fail_stage": r[2],
                                       "created_at": _iso(r[3]), "finished_at": _iso(r[4])}
                                      if r else {"status": "MISSING"})
            cur.execute("""SELECT id, entry_id, title, failure_kind, registered_at, status
                           FROM negative_knowledge_ledger WHERE entry_id = %s""", (entry_id,))
            nk = cur.fetchone()
            cur.execute("""SELECT id, kb_key, ktype, created_at, injected_count, cited_count,
                                  last_hit_at, last_cited_at
                           FROM knowledge_base WHERE kb_key = %s""", (kb_key,))
            kb = cur.fetchone()
            out["rings"]["write"] = {
                "negative_knowledge_ledger": ({"row": nk[0], "entry_id": nk[1], "title": nk[2],
                                               "failure_kind": nk[3],
                                               "registered_at": _iso(nk[4]), "status": nk[5]}
                                              if nk else "MISSING"),
                "knowledge_base": ({"row": kb[0], "kb_key": kb[1], "ktype": kb[2],
                                    "created_at": _iso(kb[3]), "injected_count": kb[4],
                                    "cited_count": kb[5], "last_hit_at": kb[6],
                                    "last_cited_at": kb[7]} if kb else "MISSING"),
            }
            cur.execute("""SELECT injection_id, mission_id, kb_id, kb_key, source, injected_at,
                                  cited_at, citation_count, citation_ref
                           FROM knowledge_injections
                           WHERE injection_id LIKE %s OR kb_key = %s
                           ORDER BY injected_at""",
                        (f"%{kb_key}%", entry_id))
            cols = [d.name for d in cur.description]
            inj = [dict(zip(cols, row)) for row in cur.fetchall()]
            out["rings"]["inject"] = inj or "MISSING"
            if kb:
                cur.execute("""SELECT kb_key, consumer, injected_at FROM knowledge_injections
                               WHERE kb_id = %s ORDER BY injected_at LIMIT 5""", (kb[0],))
                out["rings"]["inject_by_kbid"] = [
                    {"kb_key": a, "consumer": b, "injected_at": _iso(c)} for a, b, c in cur.fetchall()]
        # ④ 下游影响：citations.jsonl 的 used_in_step
        lad = _ledger_dir()
        steps = []
        try:
            for p in sorted(lad.glob("*.citations.jsonl")):
                for line in p.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    ids = [h.get("id") for h in (row.get("hits") or [])]
                    if f"kb:{kb[0]}" in ids if kb else False:
                        steps.append({"file": p.name, "ts": row.get("ts"),
                                      "mission_id": row.get("mission_id"),
                                      "used_in_step": row.get("used_in_step"),
                                      "hits": ids})
        except Exception:  # noqa: BLE001
            pass
        out["rings"]["downstream"] = steps or "MISSING"
        out["latency"] = _latency(out["rings"])
        return out
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _latency(rings: Mapping[str, Any]) -> dict:
    def _p(ts):
        if not ts or not isinstance(ts, str):
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    wr = (rings.get("write") or {}).get("knowledge_base")
    born = _p(wr.get("created_at")) if isinstance(wr, dict) else None
    inj = rings.get("inject")
    first = _p(inj[0].get("injected_at")) if isinstance(inj, list) and inj else None
    cited = None
    if isinstance(inj, list):
        for r in inj:
            if r.get("cited_at"):
                cited = _p(r["cited_at"])
                break
    out: dict[str, Any] = {"lesson_created_at": _iso(born), "first_injected_at": _iso(first),
                           "first_cited_at": _iso(cited)}
    for k, a, b in (("write_to_inject_s", born, first), ("inject_to_cite_s", first, cited)):
        out[k] = round((b - a).total_seconds(), 1) if a and b else None
    return out


def coverage(day: str | None = None, *, conn=None) -> dict:
    """覆盖率口径：注入单 / 当日总单（首日=接线当日）。"""
    own = conn is None
    conn, err = connect() if own else (conn, "")
    d = day or date.today().isoformat()
    out: dict[str, Any] = {"day": d}
    if conn is None:
        out["degraded"] = f"NO_DB:{err}"
        return out
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT COUNT(DISTINCT mission_id) FROM knowledge_injections
                           WHERE (injected_at)::date = %s::date""", (d,))
            out["injected_missions"] = cur.fetchone()[0]
            cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE status='succeeded')
                           FROM tensor_dev_missions WHERE created_at::date = %s::date""", (d,))
            total, ok = cur.fetchone()
            out["total_missions"] = total
            out["succeeded_missions"] = ok
            cur.execute("""SELECT COUNT(*) FROM knowledge_injections
                           WHERE (injected_at)::date = %s::date""", (d,))
            out["injection_rows"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM knowledge_base WHERE ktype='lesson' AND created_at::date = %s::date", (d,))
            out["lessons_written"] = cur.fetchone()[0]
        out["coverage"] = round(out["injected_missions"] / max(1, out["total_missions"]), 4)
        return out
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# CLI（worker / 后端 / 运维共用；全部 JSON 输出）
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.context_package.loop",
                                 description="记忆回路四环 CLI（JSON 输出）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("write"); p.add_argument("--mission", type=int, required=True)
    p.add_argument("--force", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("start")
    p.add_argument("--mission", type=int, required=True); p.add_argument("--goal", default=None)
    p.add_argument("--repo", default=""); p.add_argument("--consumer", default="")
    p = sub.add_parser("complete")
    p.add_argument("--mission", type=int, required=True); p.add_argument("--status", default="succeeded")
    p.add_argument("--text", default=None, help="交付文本文件路径（缺省读 handoff 目录内 m<id>.done）")
    p.add_argument("--used-in-step", default="")
    p.add_argument("--citation-ref", default="")
    p = sub.add_parser("inject"); p.add_argument("--mission", type=int, required=True)
    p.add_argument("--goal", required=True); p.add_argument("--repo", default="")
    p = sub.add_parser("cite"); p.add_argument("--mission", type=int, required=True)
    p.add_argument("--text", required=True); p.add_argument("--used-in-step", default="")
    p = sub.add_parser("timeline"); p.add_argument("--mission", type=int, required=True)
    p = sub.add_parser("coverage"); p.add_argument("--day", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "write":
        res = write_lesson(a.mission, force=a.force, dry_run=a.dry_run)
    elif a.cmd == "inject":
        block, res = inject(a.mission, a.goal, a.repo, consumer="cli")
        res["block"] = block
    elif a.cmd == "start":
        goal = a.goal
        conn, _ = connect()
        if goal is None:
            row = mission_row(a.mission, conn)
            goal = row.get("goal") or ""
            a.repo = a.repo or (row.get("repo") or "")
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        res = on_mission_start(a.mission, goal, a.repo, consumer=a.consumer)
    elif a.cmd == "complete":
        text = a.text or ""
        if text and Path(text).exists():
            text = Path(text).read_text(encoding="utf-8", errors="ignore")
        res = on_mission_complete(a.mission, status=a.status, deliverable_text=text,
                                  used_in_step=a.used_in_step, citation_ref=a.citation_ref)
    elif a.cmd == "cite":
        text = Path(a.text).read_text(encoding="utf-8", errors="ignore") if Path(a.text).exists() else a.text
        res = cite(a.mission, text, used_in_step=a.used_in_step)
        res["citation_line"] = citation_line(res)
    elif a.cmd == "timeline":
        res = timeline(a.mission)
    else:
        res = coverage(a.day)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())