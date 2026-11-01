"""HTTP surface of the reference-value corpus (``/api/v1/reference``).

Contract
--------
* ``GET /reference/{id}``                  metadata of one anchored entry
* ``GET /reference?quantity=&validity=``    filter (never extrapolates)
* ``GET /reference/{id}/evaluate?x=``       interpolated value, OUT_OF_RANGE -> 416
* ``GET /reference/{id}/dualpass``          two-pass digitisation report
* ``GET /reference/audit``                  schema / anchor / dual-pass / interpolation audit
* ``GET /reference/wave8/ledger``           Wave-8 acceptance ledger
* ``GET /reference/wave8/readjudication``   corpus-driven re-adjudication results

Failure mapping: unknown id -> 404, broken sha256 anchor -> 409 (the corpus
fails closed and refuses to serve), out-of-range abscissa -> 416 with
``code = "OUT_OF_RANGE"``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .adjudicate import build_ledger, readjudicate
from .corpus import ReferenceCorpus
from .errors import EntryNotFound, IntegrityError, OutOfRangeError

router = APIRouter(prefix="/reference", tags=["参考值语料库"])


@lru_cache(maxsize=4)
def get_corpus(root: Optional[str] = None) -> ReferenceCorpus:
    """Process-wide corpus (re-read on demand; integrity is checked per access)."""
    return ReferenceCorpus(root)


def _entry_payload(entry) -> dict:
    payload = entry.summary()
    payload["table"] = {
        "path": entry.raw["table"]["path"],
        "pass_b_path": entry.raw["table"]["pass_b_path"],
        "x_quantity": entry.raw["table"]["x_quantity"],
        "x_unit": entry.raw["table"]["x_unit"],
        "y_quantity": entry.raw["table"]["y_quantity"],
        "n_points": entry.raw["table"]["n_points"],
    }
    payload["dual_pass"] = entry.dual_pass()
    payload["interpolation_consistency"] = entry.consistency()
    payload["verification"] = entry.raw["verification"]
    payload["sha256_scope"] = entry.raw["sha256_scope"]
    return payload


# ---------------------------------------------------------------------------
# static routes first (must precede /{entry_id})
# ---------------------------------------------------------------------------


@router.get("/audit", summary="语料库完整审计（schema/锚点/双人复核/插值一致性）")
def corpus_audit() -> dict:
    return get_corpus().audit()


@router.get("/pending", summary="请求但未数字化的参考值族（WITHHELD 台账）")
def pending_families() -> dict:
    import json
    from pathlib import Path

    path = Path(get_corpus().root) / "pending.json"
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/wave8/ledger", summary="Wave-8 全部节点的验收项分类台账")
def wave8_ledger() -> dict:
    corpus = get_corpus()
    return build_ledger(corpus.root, corpus)


@router.get("/wave8/readjudication", summary="用本语料重判已交付节点的判据")
def wave8_readjudication() -> dict:
    corpus = get_corpus()
    return readjudicate(corpus, corpus.root)


@router.get("", summary="按 quantity / validity / domain / provenance 查询参考值")
def query_entries(
    quantity: Optional[str] = Query(default=None, description="物理量关键字（子串匹配）"),
    validity: Optional[float] = Query(default=None, description="横坐标取值；只返回 validity_range 覆盖该值的条目"),
    domain: Optional[str] = Query(default=None, description="resistance/seakeeping/structure/..."),
    provenance: Optional[str] = Query(default=None, description="MEASURED/ANALYTIC/FITTED"),
) -> dict:
    corpus = get_corpus()
    hits = corpus.query(quantity=quantity, validity=validity, domain=domain, provenance=provenance)
    return {
        "n": len(hits),
        "query": {"quantity": quantity, "validity": validity, "domain": domain, "provenance": provenance},
        "entries": [_entry_payload(e) for e in hits],
    }


# ---------------------------------------------------------------------------
# per-entry routes
# ---------------------------------------------------------------------------


@router.get("/{entry_id}", summary="单条参考值/参考表元数据（含 sha256 锚点校验）")
def get_entry(entry_id: str) -> dict:
    corpus = get_corpus()
    try:
        entry = corpus.get(entry_id)
        corpus.verify_integrity(entry_id)
    except EntryNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(exc)})
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INTEGRITY_REFUSED",
                "message": str(exc),
                "entry_id": exc.entry_id,
                "expected": exc.expected,
                "actual": exc.actual,
            },
        )
    return _entry_payload(entry)


@router.get("/{entry_id}/evaluate", summary="按锚定表插值求值（范围外 416 OUT_OF_RANGE）")
def evaluate_entry(
    entry_id: str,
    x: float = Query(..., description="横坐标（见条目 validity_range.x_quantity）"),
    scheme: str = Query(default="pchip", pattern="^(pchip|linear)$"),
) -> dict:
    corpus = get_corpus()
    try:
        return corpus.evaluate(entry_id, x, scheme=scheme)
    except EntryNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(exc)})
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail={"code": "INTEGRITY_REFUSED", "message": str(exc)})
    except OutOfRangeError as exc:
        raise HTTPException(
            status_code=416,
            detail={
                "code": "OUT_OF_RANGE",
                "message": str(exc),
                "entry_id": exc.entry_id,
                "x": exc.x,
                "x_min": exc.x_min,
                "x_max": exc.x_max,
                "note": exc.note,
            },
        )


@router.get("/{entry_id}/dualpass", summary="双人独立数字化复核报告")
def entry_dualpass(entry_id: str) -> dict:
    corpus = get_corpus()
    try:
        entry = corpus.get(entry_id)
    except EntryNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(exc)})
    rep = entry.dual_pass()
    rep["entry_id"] = entry_id
    rep["declared_in_entry"] = entry.raw["dual_pass"]
    return rep