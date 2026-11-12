"""sources —— 干货包的五个信息槽取数器（来源：KO 索引 / 负面知识 / 同域 verdict /
容差判据 / 相关锚状态），全部"取候选"，不做选择（选择在 assembler）。

设计约束
--------
1. **零领域语义**：SQL 只按 ``repo``/``as_of`` 过滤，相关性由目标文本词元算出；
   类别到决策的映射写在配置 ``slots[*].decisions``。
2. **可审计**：每个候选带 ``pointer``（详情下沉指针），包体只放索引密度，
   正文/证据一律留在原表，用指针回取 → 上下文篇幅不随知识量增长。
3. **降级不崩**：DB 不可用（无驱动/连不上/无权限）→ 返回空集 + ``degraded`` 原因，
   上层照常出包（只是槽位空缺并留痕）。

候选 dict 契约::

    {"id": "kb:512", "slot": "ko_index", "source": "knowledge_base",
     "text": "<一行索引>（详情指针另存）", "keyword_text": "<给相关性打分的原文摘要>",
     "decision": "reuse|avoid|gate|tolerance|anchor|scope|''",
     "pointer": "<回取路径>", "meta": {...}}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .loader import ROOT, clip, est_tokens, load_config

DEFAULT_DSN = "postgresql://postgres@/tensorplatform?host=/var/run/postgresql"

# as_of 过滤按需拼进 SQL（参数化 NULL 判定会让 PG 无法推断类型）
AS_OF_COND = "\n          AND created_at <= %(as_of)s"
AS_OF_COND_NK = "\n          AND registered_at <= %(as_of)s"

def _human(raw: Any, max_chars: int = 64) -> str:
    """把可能是 JSON 串的字段压成一句人话（索引密度：一句话能被判读才有价值）。"""
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        obj = raw
    else:
        txt = str(raw).strip()
        if not txt:
            return ""
        if txt[:1] in "{[":
            try:
                obj = json.loads(txt)
            except Exception:
                return clip(txt, max_chars)
        else:
            return clip(txt, max_chars)
    if isinstance(obj, dict):
        for k in ("usage", "note", "summary", "reason", "scope", "title", "entry", "os"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return clip(v, max_chars)
        for v in obj.values():
            if isinstance(v, str) and v.strip():
                return clip(v, max_chars)
        return clip(json.dumps(obj, ensure_ascii=False), max_chars)
    if isinstance(obj, list):
        for v in obj:
            if isinstance(v, str) and v.strip():
                return clip(v, max_chars)
        return clip(json.dumps(obj, ensure_ascii=False), max_chars)
    return clip(str(obj), max_chars)



# --------------------------------------------------------------------------- #
# 读取器
# --------------------------------------------------------------------------- #
class PgReader:
    """最小只读 DB 读取器（psycopg3）；不可用即降级。"""

    def __init__(self, dsn: str | None = None, timeout: int = 8):
        self.dsn = dsn or DEFAULT_DSN
        self.timeout = timeout
        self._conn = None
        self.error = ""

    def _connect(self):
        if self._conn is not None:
            return self._conn
        import psycopg  # 延迟导入：无驱动时走降级分支
        self._conn = psycopg.connect(self.dsn, connect_timeout=self.timeout)
        return self._conn

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(sql, dict(params or {}))
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001 —— 取数失败不得拖死出包
            self.error = f"{type(exc).__name__}: {exc}"[:240]
            try:
                if self._conn is not None:
                    self._conn.rollback()   # 事务中止后不清场，后续查询会连锁失败
            except Exception:
                pass
            return []

    def close(self):
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None


def _decision_for(slot: str, default: str, cfg: Mapping[str, Any], hint: str | None = None) -> str:
    """槽位 → 决策键；知识条目按 ``ktype_decision`` 细分，未知 ktype 用槽位首选。"""
    spec = (cfg.get("slots") or {}).get(slot) or {}
    mapping = spec.get("ktype_decision") or {}
    if hint is not None and hint in mapping:
        return mapping[hint]
    ds = spec.get("decisions") or ([default] if default else [])
    return ds[0] if ds else ""


# --------------------------------------------------------------------------- #
# 槽 1：KO 索引（knowledge_base）
# --------------------------------------------------------------------------- #
def fetch_knowledge_base(ctx: Mapping[str, Any], reader, cfg: Mapping[str, Any]) -> tuple[list[dict], str]:
    limit = int((cfg.get("defaults") or {}).get("pool_limit", 400))
    sql = """
        SELECT id, kb_key, ktype, domain, title, content, evidence, status,
               injected_count, cited_count, created_at
        FROM knowledge_base
        WHERE status = 'active'{as_of_cond}
        ORDER BY cited_count DESC NULLS LAST, injected_count DESC NULLS LAST, id DESC
        LIMIT %(lim)s
    """
    rows = reader.query(sql.format(as_of_cond=AS_OF_COND),
                        {"as_of": ctx.get("as_of"), "lim": limit})
    out = []
    for r in rows:
        ev = r.get("evidence")
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except Exception:
                ev = {}
        ev = ev or {}
        gist = _human(ev, 70) or _human(r.get("content"), 70)
        entry_ptr = (ev.get("entry") if isinstance(ev, dict) else "") or (ev.get("repro") if isinstance(ev, dict) else "")
        if isinstance(entry_ptr, (list, tuple)):
            entry_ptr = entry_ptr[0] if entry_ptr else ""
        pointer = clip(entry_ptr, 60) or f"knowledge_base#{r.get('id')}"
        text = f"{clip(r.get('title'), 60)}〔{r.get('ktype')}·{r.get('domain')}〕{gist}"
        out.append({
            "id": f"kb:{r.get('id')}",
            "slot": "ko_index",
            "source": "knowledge_base",
            "text": text,
            "keyword_text": " ".join(str(x) for x in (r.get("title"), r.get("kb_key"),
                                                      r.get("ktype"), r.get("domain"), gist) if x),
            "decision": _decision_for("ko_index", "reuse", cfg, str(r.get("ktype") or "")),
            "pointer": pointer or f"knowledge_base#{r.get('id')}",
            "meta": {"kb_key": r.get("kb_key"), "ktype": r.get("ktype"), "domain": r.get("domain"),
                     "injected_count": r.get("injected_count"), "cited_count": r.get("cited_count")},
        })
    return out, reader.error


# --------------------------------------------------------------------------- #
# 槽 2：负面知识（negative_knowledge_ledger）
# --------------------------------------------------------------------------- #
def fetch_negative_knowledge(ctx: Mapping[str, Any], reader, cfg: Mapping[str, Any]) -> tuple[list[dict], str]:
    limit = int((cfg.get("defaults") or {}).get("pool_limit", 400))
    sql = """
        SELECT entry_id, mission_id, title, failure_reason, failure_kind,
               applicable_conditions, keywords, domain_tags, confidence, registered_at
        FROM negative_knowledge_ledger
        WHERE TRUE{as_of_cond}
        ORDER BY registered_at DESC NULLS LAST, id DESC
        LIMIT %(lim)s
    """
    rows = reader.query(sql.format(as_of_cond=AS_OF_COND_NK),
                        {"as_of": ctx.get("as_of"), "lim": limit})
    out = []
    for r in rows:
        kind = str(r.get("failure_kind") or "")
        reason = _human(r.get("failure_reason"), 60) or _human(r.get("hypothesis"), 60)
        cond = _human(r.get("applicable_conditions"), 40)
        eid = str(r.get("entry_id") or "")
        text = (f"{clip(r.get('title'), 56)}｜{kind}"
                f"｜{reason}" + (f"｜条件:{cond}" if cond else ""))
        tags = r.get("domain_tags") or []
        out.append({
            "id": eid if eid.startswith("negk:") else f"negk:{eid}",
            "slot": "negative_knowledge",
            "source": "negative_knowledge_ledger",
            "text": text,
            "keyword_text": " ".join(str(x) for x in (
                r.get("title"), kind, r.get("failure_reason"),
                r.get("applicable_conditions"), r.get("keywords"),
                " ".join(tags) if isinstance(tags, list) else tags) if x),
            "decision": _decision_for("negative_knowledge", "avoid", cfg),
            "pointer": f"negative_knowledge_ledger#{r.get('entry_id')} (mission={r.get('mission_id')})",
            "meta": {"failure_kind": kind, "domain_tags": tags,
                     "confidence": r.get("confidence"), "mission_id": r.get("mission_id")},
        })
    return out, reader.error


# --------------------------------------------------------------------------- #
# 槽 3：同域 verdict（tensor_maturity_verdicts）
# --------------------------------------------------------------------------- #
def fetch_verdicts(ctx: Mapping[str, Any], reader, cfg: Mapping[str, Any]) -> tuple[list[dict], str]:
    sql = """
        SELECT id, repo, scope, mission_id, level, gate_state, gate_level,
               gate_note, evidence, created_at
        FROM tensor_maturity_verdicts
        WHERE TRUE{as_of_cond}
        ORDER BY level DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT %(lim)s
    """
    rows = reader.query(sql.format(as_of_cond=AS_OF_COND),
                        {"as_of": ctx.get("as_of"),
                         "lim": int((cfg.get("defaults") or {}).get("pool_limit", 400))})
    out = []
    for r in rows:
        note = r.get("gate_note")
        if isinstance(note, str):
            try:
                note = json.loads(note)
            except Exception:
                note = {}
        note = note or {}
        entry_ptr = note.get("entry") if isinstance(note, dict) else ""
        if isinstance(entry_ptr, (list, tuple)):
            entry_ptr = entry_ptr[0] if entry_ptr else ""
        pointer = clip(entry_ptr, 60)
        text = (f"{r.get('repo')}·{clip(r.get('scope'), 36)} L{r.get('level')}"
                f" gate={r.get('gate_state') or '-'} {_human(note, 52)}")
        out.append({
            "id": f"verdict:{r.get('id')}",
            "slot": "domain_verdict",
            "source": "tensor_maturity_verdicts",
            "text": text,
            "keyword_text": " ".join(str(x) for x in (r.get("repo"), r.get("scope"),
                                                      note.get("usage") if isinstance(note, dict) else "") if x),
            "decision": _decision_for("domain_verdict", "gate", cfg),
            "pointer": pointer or f"tensor_maturity_verdicts#{r.get('id')} (mission={r.get('mission_id')})",
            "meta": {"repo": r.get("repo"), "level": r.get("level"), "gate_state": r.get("gate_state")},
        })
    return out, reader.error


# --------------------------------------------------------------------------- #
# 槽 4：容差判据（data/criteria/threshold_registry.json，纯文件、可 sha 校验）
# --------------------------------------------------------------------------- #
def fetch_thresholds(ctx: Mapping[str, Any], cfg: Mapping[str, Any],
                     path: Path | None = None) -> tuple[list[dict], str]:
    p = path or (ROOT / "data" / "criteria" / "threshold_registry.json")
    try:
        with open(p, encoding="utf-8") as fh:
            reg = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: threshold_registry 不可读"
    criteria: Iterable[Mapping[str, Any]] = reg.get("criteria") or []
    out = []
    for c in criteria:
        if not isinstance(c, Mapping):
            continue
        if c.get("decidable") is False:
            continue          # 不可裁判据不进干货包（避免把拍脑袋阈值当口径）
        thr = c.get("threshold")
        expr = (c.get("derivation") or {}).get("expression") if isinstance(c.get("derivation"), dict) else ""
        text = (f"{clip(c.get('criterion_id'), 40)}: {clip(c.get('quantity'), 48)}"
                f" {c.get('comparator') or '<='} {thr}"
                f"{(' [' + str(expr) + ']') if expr else ''}")
        out.append({
            "id": f"thr:{c.get('criterion_id')}",
            "slot": "tolerance_criteria",
            "source": "threshold_registry",
            "text": text,
            "keyword_text": " ".join(str(x) for x in (c.get("criterion_id"), c.get("quantity"),
                                                      c.get("source_note"), c.get("probe")) if x),
            "decision": _decision_for("tolerance_criteria", "tolerance", cfg),
            "pointer": f"{p.relative_to(ROOT)}#{c.get('criterion_id')} src={clip(c.get('source_ref'), 60)}",
            "meta": {"threshold": thr, "source_type": c.get("source_type"),
                     "review_date": c.get("review_date")},
        })
    return out, ""


# --------------------------------------------------------------------------- #
# 槽 5：锚状态（tensor_benchmark_cases）
# --------------------------------------------------------------------------- #
def fetch_benchmark_cases(ctx: Mapping[str, Any], reader, cfg: Mapping[str, Any]) -> tuple[list[dict], str]:
    sql = """
        SELECT id, anchor_slug, case_name, ref_source, metric, tolerance, repo, status, created_at
        FROM tensor_benchmark_cases
        WHERE TRUE{as_of_cond}
        ORDER BY created_at DESC NULLS LAST
        LIMIT %(lim)s
    """
    rows = reader.query(sql.format(as_of_cond=AS_OF_COND),
                        {"as_of": ctx.get("as_of"),
                         "lim": int((cfg.get("defaults") or {}).get("pool_limit", 400))})
    out = []
    for r in rows:
        text = (f"{r.get('anchor_slug')}｜{clip(r.get('case_name'), 40)}"
                f"｜{clip(r.get('metric'), 36)}｜{r.get('tolerance')}｜{r.get('status')}")
        out.append({
            "id": f"anchor:{r.get('anchor_slug')}",
            "slot": "anchor_status",
            "source": "tensor_benchmark_cases",
            "text": text,
            "keyword_text": " ".join(str(x) for x in (r.get("anchor_slug"), r.get("case_name"),
                                                      r.get("metric"), r.get("ref_source"),
                                                      r.get("repo")) if x),
            "decision": _decision_for("anchor_status", "anchor", cfg),
            "pointer": f"tensor_benchmark_cases#{r.get('id')} ref={clip(r.get('ref_source'), 60)}",
            "meta": {"repo": r.get("repo"), "status": r.get("status"),
                     "tolerance": r.get("tolerance")},
        })
    return out, reader.error


SOURCE_FUNCS = {
    "knowledge_base": fetch_knowledge_base,
    "negative_knowledge_ledger": fetch_negative_knowledge,
    "tensor_maturity_verdicts": fetch_verdicts,
    "threshold_registry": fetch_thresholds,
    "tensor_benchmark_cases": fetch_benchmark_cases,
}


def fetch_slot(slot: str, ctx: Mapping[str, Any], reader, cfg: Mapping[str, Any]) -> tuple[list[dict], str]:
    """按槽位取候选；未知来源/取数异常一律返回空集 + 原因（降级不崩）。"""
    spec = (cfg.get("slots") or {}).get(slot) or {}
    src = spec.get("source")
    fn = SOURCE_FUNCS.get(src)
    if fn is None:
        return [], f"UNKNOWN_SOURCE:{src}"
    try:
        if src == "threshold_registry":
            return fn(ctx, cfg)
        return fn(ctx, reader, cfg)
    except Exception as exc:  # noqa: BLE001
        return [], f"SOURCE_ERROR:{type(exc).__name__}"