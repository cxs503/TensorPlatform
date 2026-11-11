"""机器判据：依赖图与基准图的 Jaccard、级联结果与人工分析的误差、零领域语义扫描。

三项判据都返回**可复算的中间量**（交集/并集/逐节点误差/diff 明细），报告里的数字
从这些中间量直接引用，不允许只报一个结论标量。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from .model import Edge

__all__ = [
    "edge_set",
    "jaccard",
    "compare_edge_sets",
    "compare_cascade_with_manual",
    "mutation_sensitivity",
    "scan_forbidden_tokens",
]

EdgeKey = Tuple[str, str]


def edge_set(edges: Iterable[Any]) -> Set[EdgeKey]:
    """边集合归一：接受 ``Edge`` / ``{"src","dst"}`` / ``(src,dst)`` / ``"src->dst"``。"""
    out: Set[EdgeKey] = set()
    for e in edges:
        if isinstance(e, Edge):
            out.add((e.src, e.dst))
            continue
        if isinstance(e, Mapping):
            src, dst = e.get("src") or e.get("dep"), e.get("dst") or e.get("dependent")
            if src and dst:
                out.add((str(src), str(dst)))
            continue
        if isinstance(e, str) and "->" in e:
            src, dst = e.split("->", 1)
            out.add((src.strip(), dst.strip()))
            continue
        if isinstance(e, (tuple, list)) and len(e) == 2:
            out.add((str(e[0]), str(e[1])))
            continue
        raise ValueError(f"unsupported edge form: {e!r}")
    return out


def jaccard(derived: Iterable[Any], baseline: Iterable[Any]) -> float:
    a, b = edge_set(derived), edge_set(baseline)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def compare_edge_sets(derived: Iterable[Any], baseline: Iterable[Any]) -> Dict[str, Any]:
    a, b = edge_set(derived), edge_set(baseline)
    inter = a & b
    union = a | b
    return {
        "derived_count": len(a),
        "baseline_count": len(b),
        "intersection_count": len(inter),
        "union_count": len(union),
        "missing_edges": sorted([f"{s}->{d}" for s, d in (b - a)]),
        "extra_edges": sorted([f"{s}->{d}" for s, d in (a - b)]),
        "recall": round(len(inter) / len(b), 6) if b else 1.0,
        "precision": round(len(inter) / len(a), 6) if a else 1.0,
        "jaccard": round(len(inter) / len(union), 6) if union else 1.0,
    }


def compare_cascade_with_manual(
    result: Mapping[str, Any], manual: Mapping[str, Any]
) -> Dict[str, Any]:
    """岗位判据：引擎级联结果 vs 人工分析的逐节点误差（相对误差，%）。

    ``manual`` 结构::

        {"chain_id": "...", "capability_id": "...", "to_level": "MOCK",
         "expected": {"<node>": {"degradation": 0.45, "confidence_after": 0.55,
                                 "level_after": "MOCK"}}}
    """
    expected = manual.get("expected") or {}
    degradation = result.get("degradation") or {}
    eff_after = result.get("eff_after") or {}
    levels_after = result.get("levels_after") or {}
    rows: List[Dict[str, Any]] = []
    max_err = 0.0
    level_mismatches: List[str] = []
    for node_id in sorted(expected):
        exp = expected[node_id]
        exp_deg = float(exp["degradation"])
        exp_conf = float(exp["confidence_after"])
        got_deg = float(degradation.get(node_id, 0.0))
        got_conf = float(eff_after.get(node_id, 0.0))
        deg_err = 0.0 if exp_deg == 0 else abs(got_deg - exp_deg) / abs(exp_deg) * 100.0
        conf_err = 0.0 if exp_conf == 0 else abs(got_conf - exp_conf) / abs(exp_conf) * 100.0
        err = max(deg_err, conf_err)
        max_err = max(max_err, err)
        level_ok = levels_after.get(node_id) == exp.get("level_after")
        if not level_ok:
            level_mismatches.append(
                f"{node_id}: expected={exp.get('level_after')} got={levels_after.get(node_id)}"
            )
        rows.append(
            {
                "id": node_id,
                "degradation_expected": round(exp_deg, 6),
                "degradation_got": round(got_deg, 6),
                "confidence_after_expected": round(exp_conf, 6),
                "confidence_after_got": round(got_conf, 6),
                "rel_error_pct": round(err, 6),
                "level_expected": exp.get("level_after"),
                "level_got": levels_after.get(node_id),
                "level_match": level_ok,
                "affected_expected": bool(exp.get("affected", exp_deg > 1e-09)),
                "affected_got": node_id in {a["id"] for a in result.get("affected") or []},
            }
        )
    affected_mismatch = [
        r["id"]
        for r in rows
        if r["affected_expected"] != r["affected_got"]
    ]
    return {
        "chain_id": manual.get("chain_id"),
        "capability_id": manual.get("capability_id") or result.get("event", {}).get("capability_id"),
        "to_level": manual.get("to_level") or result.get("to_level"),
        "node_count": len(rows),
        "max_rel_error_pct": round(max_err, 6),
        "level_mismatches": level_mismatches,
        "affected_mismatches": affected_mismatch,
        "rows": rows,
    }


def scan_forbidden_tokens(paths: Sequence[Path], tokens: Sequence[str]) -> Dict[str, Any]:
    """零领域语义扫描：代码/配置路径里出现域词条即失败（大小写不敏感子串）。"""
    hits: List[Dict[str, Any]] = []
    scanned: List[str] = []
    for root in paths:
        if root.is_dir():
            files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".json", ".md", ".txt"})
        else:
            files = [root] if root.exists() else []
        for path in files:
            if "__pycache__" in path.parts:
                continue
            scanned.append(str(path))
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:  # pragma: no cover
                continue
            low = text.lower()
            for token in tokens:
                # ASCII 词条按词边界匹配：否则三字母短词会命中普通英文单词的内部
                # （service / relationship 之类），把扫描变成噪声源。CJK 词条没有词边界
                # 概念，直接子串匹配。
                low_tok = token.lower()
                if low_tok.isascii():
                    import re as _re

                    m = _re.search(rf"(?<![a-z0-9_]){_re.escape(low_tok)}(?![a-z0-9_])", low)
                    idx = m.start() if m else -1
                else:
                    idx = low.find(low_tok)
                if idx >= 0:
                    hits.append(
                        {
                            "path": str(path),
                            "token": token,
                            "line": text[:idx].count("\n") + 1,
                        }
                    )
    return {"scanned_files": len(scanned), "hit_count": len(hits), "hits": hits,
            "clean": not hits, "files": scanned}

def mutation_sensitivity(
    derived: Iterable[Any],
    baseline: Iterable[Any],
    *,
    delete_n: Sequence[int] = (1, 2, 3, 5),
    reverse_n: Sequence[int] = (1, 3),
) -> Dict[str, Any]:
    """判据敏感性（负对照）：对派生图做受控破坏，看 Jaccard 是否真的会掉。

    若一个判据在删边/反向边下仍然 ≥0.8，它就没有鉴别力 —— 这类"恒真判据"是审计
    里最危险的假阳性来源。这里把破坏后的 Jaccard 全部算出来并给出首次破阈的破坏量。
    """
    base = edge_set(baseline)
    base_list = sorted(base)
    rows: List[Dict[str, Any]] = []
    for n in delete_n:
        if n > len(base_list):
            continue
        mutated = base - set(base_list[:n])
        rows.append(
            {
                "mutation": f"delete_{n}_edges",
                "jaccard": round(jaccard(mutated, base), 6),
                "still_passes_0_8": jaccard(mutated, base) >= 0.8,
            }
        )
    for n in reverse_n:
        if n > len(base_list):
            continue
        flipped = set(base_list)
        for src, dst in base_list[:n]:
            flipped.discard((src, dst))
            flipped.add((dst, src))
        rows.append(
            {
                "mutation": f"reverse_{n}_edges",
                "jaccard": round(jaccard(flipped, base), 6),
                "still_passes_0_8": jaccard(flipped, base) >= 0.8,
            }
        )
    rows.append({"mutation": "duplicate_all_edges", "jaccard": round(jaccard(list(base) * 2, base), 6),
                 "still_passes_0_8": True})
    first_fail = next((r["mutation"] for r in rows if not r["still_passes_0_8"]), None)
    return {
        "baseline_edge_count": len(base),
        "derived_jaccard": round(jaccard(derived, base), 6),
        "mutations": rows,
        "first_mutation_breaking_threshold": first_fail,
        "criterion_is_discriminative": first_fail is not None,
    }
