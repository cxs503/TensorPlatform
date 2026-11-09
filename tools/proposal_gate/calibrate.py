"""阈值标定：把「去重阈值取多少」从拍脑袋变成可复算的曲线。

三组数据
--------
1. **改写强度曲线**：对基准提案做 k 处同义替换（k=0..8），测相似度随改写强度的衰减
   → 说明「自动拒(dup)」与「复核带(review)」各自覆盖到多少改写量级。
2. **阈值扫描（TPR/FPR）**：正样本=改写变体族与同义改写族（应命中），负样本=主题不同
   的合法提案（不得命中）。扫阈值 0.30→0.99，输出 TPR/FPR/Youden 指数，
   并标出配置里实际采用的两个工作点。
3. **真实语料标定**：真库 ``campaign_nodes``（name+goal）两两相似度分布——
   在真实数据上找「阈值平台区」（取平台区内任一值结果同构，说明取值稳健）。

零领域语义：正/负样本由合成域名（foo/bar/widget）与随机替换表生成；真库语料只统计
分布，不落任何词表。输出 JSON 供 :mod:`tools.proposal_gate.figures` 直接出图。
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics
from pathlib import Path
from typing import Any, Iterable

from .config import GateConfig, load_config
from .contract import validate_proposal
from .textsim import shingles, sim_from_sets, similarity

# 同义替换表（合成域，非学科词）：只为制造「措辞变化」，不改主题
SYNONYM_TABLE: dict[str, str] = {
    "建立": "构建",
    "机器门": "闸门机制",
    "覆盖率": "覆盖比例",
    "账本": "台账",
    "契约": "合约",
    "去重器": "查重组件",
    "通道": "链路",
    "提案": "方案",
    "产物": "产出物",
    "播种": "投放",
    "判据": "验收线",
    "复核": "复查",
}

BASE_PROPOSAL = {
    "schema_version": "2.0",
    "mission_id": 1,
    "plan_id": 1,
    "title": "foo 通道三道机器门",
    "repo": "__REPO__",  # 运行时填白名单首项（仓库名属配置数据，不改合成域名正文）
    "goal": {
        "objective": "为 foo 通道建立产物契约、去重器与覆盖率账本三道机器门",
        "criteria": ["重复率 < 0.15", "播种覆盖率 ≥ 0.80", "误判率 ≤ 0.05"],
    },
    "dependencies_hint": ["门-契约", "门-去重"],
    "is_maturity": True,
}

NEGATIVE_TOPICS = [
    ("bar 报表延迟归因", "把 bar 报表的端到端延迟拆到各阶段并定位阻滞点", ["P95 延迟 ≤ 2.5 秒", "归因覆盖率 ≥ 0.90", "误归因率 ≤ 0.10"]),
    ("widget 缓存命中率提升", "对 widget 查询路径引入分层缓存并度量收益", ["命中率 ≥ 0.75", "回源次数 ≤ 1000/min", "内存增量 ≤ 8%"]),
    ("foo 权限审计留痕", "为 foo 的写操作补全审计留痕并验证不可篡改", ["留痕完整率 = 1.00", "篡改检出率 ≥ 0.99", "额外延迟 ≤ 5ms"]),
    ("bar 分片重建", "把 bar 的冷数据按时间分片重建", ["分片偏差 ≤ 0.02", "重建耗时 ≤ 60 秒", "丢失率 ≤ 0.001"]),
    ("widget 导出对账", "为 widget 侧导出对账文件供外部审计", ["导出成功率 ≥ 0.99", "字段完整率 = 1.00", "耗时 ≤ 10 秒"]),
]

PARAPHRASE_FAMILY = [
    "建立 foo 通道的三道机器门：产物 schema 契约、跨提案去重器与播种覆盖率账本",
    "foo 通道三道机器门：产物契约（schema）、去重器、覆盖率账本，一次建齐",
    "为 foo 通道建三道机器门——产物契约、去重器、以及播种覆盖率账本",
    "foo 通道需要三道机器门：一是产物契约，二是去重器，三是覆盖率账本",
]


def base_payload(cfg: GateConfig) -> dict[str, Any]:
    """基准提案：正文用合成域名，仓库名取白名单首项（配置数据，非硬编码）。"""
    payload = json.loads(json.dumps(BASE_PROPOSAL))
    payload["repo"] = cfg.repo_whitelist[0]
    return payload


def _blob(cfg: GateConfig, payload: dict[str, Any]) -> str:
    res = validate_proposal(payload, cfg)
    if not res.ok or res.doc is None:
        raise SystemExit(f"标定样本自身未过契约门：{res.errors}")
    return res.doc.blob()


def _edit(text: str, k: int, seed: int) -> str:
    rnd = random.Random(seed)
    for key in rnd.sample(list(SYNONYM_TABLE), k):
        text = text.replace(key, SYNONYM_TABLE[key])
    return text


def edit_intensity(cfg: GateConfig, *, max_edits: int = 8, seeds: int = 10) -> list[dict[str, Any]]:
    base = _blob(cfg, base_payload(cfg))
    out: list[dict[str, Any]] = []
    for k in range(max_edits + 1):
        scores = []
        for seed in range(seeds):
            payload = base_payload(cfg)
            payload["goal"]["objective"] = _edit(BASE_PROPOSAL["goal"]["objective"], k, seed)
            payload["title"] = _edit(BASE_PROPOSAL["title"], min(k, 2), seed + 97)
            scores.append(similarity(base, _blob(cfg, payload), cfg))
        out.append(
            {
                "edits": k,
                "n": len(scores),
                "min": round(min(scores), 6),
                "mean": round(statistics.fmean(scores), 6),
                "max": round(max(scores), 6),
            }
        )
    return out


def labeled_cohort(cfg: GateConfig) -> tuple[list[float], list[float]]:
    """正样本（应命中）/负样本（不得命中）的相似度分值。"""
    base = _blob(cfg, base_payload(cfg))
    positives: list[float] = []
    for k in range(1, 6):
        for seed in range(10):
            payload = base_payload(cfg)
            payload["goal"]["objective"] = _edit(BASE_PROPOSAL["goal"]["objective"], k, seed)
            positives.append(similarity(base, _blob(cfg, payload), cfg))
    for variant in PARAPHRASE_FAMILY:
        payload = base_payload(cfg)
        payload["title"] = variant
        payload["goal"]["objective"] = variant
        positives.append(similarity(base, _blob(cfg, payload), cfg))
    negatives: list[float] = []
    for title, objective, criteria in NEGATIVE_TOPICS:
        payload = base_payload(cfg)
        payload["title"] = title
        payload["goal"] = {"objective": objective, "criteria": criteria}
        negatives.append(similarity(base, _blob(cfg, payload), cfg))
    return positives, negatives


def sweep(cfg: GateConfig, positives: list[float], negatives: list[float]) -> list[dict[str, Any]]:
    out = []
    for i in range(30, 100):
        thr = i / 100
        tp = sum(1 for s in positives if s >= thr)
        fp = sum(1 for s in negatives if s >= thr)
        tpr = tp / len(positives)
        fpr = fp / len(negatives)
        out.append(
            {
                "threshold": round(thr, 2),
                "tp": tp,
                "fn": len(positives) - tp,
                "fp": fp,
                "tn": len(negatives) - fp,
                "tpr": round(tpr, 6),
                "fpr": round(fpr, 6),
                "youden": round(tpr - fpr, 6),
            }
        )
    return out


def real_corpus(cfg: GateConfig, *, limit_plans: int = 40, cache: Path | None = None) -> dict[str, Any]:
    """真库语料两两相似度分布（只统计分布，不落词表）。"""
    texts: list[str] = []
    source = "api"
    if cache and cache.exists():
        texts = json.loads(cache.read_text(encoding="utf-8"))["texts"]
        source = "cache"
    else:
        try:
            from .collect import fetch_campaign_nodes

            nodes = fetch_campaign_nodes(limit_plans=limit_plans)
            texts = [(n["name"] + "\n" + n["goal"]).strip() for n in nodes]
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"texts": texts}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    if not texts:
        return {"status": "empty"}
    size = int(cfg.need("dedup.shingle_size"))
    grams = [shingles(t, size) for t in texts]
    scores = [sim_from_sets(grams[a], grams[b], cfg).score for a, b in itertools.combinations(range(len(grams)), 2)]
    hist_edges = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0001]
    hist = []
    for lo, hi in zip(hist_edges, hist_edges[1:]):
        hist.append(
            {"lo": lo, "hi": hi, "count": sum(1 for s in scores if lo <= s < hi)}
        )
    plateau = []
    for thr in (0.45, 0.55, 0.65, 0.75, 0.85, 0.9):
        plateau.append({"threshold": thr, "pairs_at_or_above": sum(1 for s in scores if s >= thr)})
    return {
        "status": "ok",
        "source": source,
        "texts": len(texts),
        "pairs": len(scores),
        "median": round(statistics.median(scores), 6) if scores else None,
        "p95": round(sorted(scores)[int(0.95 * (len(scores) - 1))], 6) if scores else None,
        "max": round(max(scores), 6) if scores else None,
        "hist": hist,
        "plateau": plateau,
        "near_identical_pairs": [round(s, 6) for s in sorted(scores, reverse=True)[:30]],
    }


def run(cfg: GateConfig, *, limit_plans: int = 40, cache: Path | None = None) -> dict[str, Any]:
    positives, negatives = labeled_cohort(cfg)
    curve = sweep(cfg, positives, negatives)
    operating = {
        "dup_threshold": cfg.need("dedup.dup_threshold"),
        "review_threshold": cfg.need("dedup.review_threshold"),
    }
    def _point(thr: float) -> dict[str, Any]:
        row = next((r for r in curve if abs(r["threshold"] - thr) < 1e-9), None)
        return row or {"threshold": thr, "note": "outside sweep grid"}

    return {
        "schema": "proposal-gate-calibration/1",
        "config_path": str(cfg.path),
        "edit_intensity": edit_intensity(cfg),
        "positives_n": len(positives),
        "negatives_n": len(negatives),
        "positives_min": round(min(positives), 6),
        "negatives_max": round(max(negatives), 6),
        "sweep": curve,
        "operating_points": {
            "dup": _point(float(operating["dup_threshold"])),
            "review": _point(float(operating["review_threshold"])),
        },
        "best_youden": max(curve, key=lambda r: r["youden"]),
        "real_corpus": real_corpus(cfg, limit_plans=limit_plans, cache=cache),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提案门阈值标定（合成正/负样本 + 真库语料分布）")
    ap.add_argument("--out", default="artifacts/proposal_gate/calibration.json")
    ap.add_argument("--limit-plans", type=int, default=40)
    ap.add_argument("--cache", default="artifacts/proposal_gate/nodes_cache.json")
    args = ap.parse_args(argv)
    cfg = load_config()
    result = run(cfg, limit_plans=args.limit_plans, cache=Path(args.cache))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("positives_min", "negatives_max", "operating_points", "best_youden")}, ensure_ascii=False, indent=2))
    print("real_corpus:", json.dumps({k: result["real_corpus"].get(k) for k in ("status", "texts", "pairs", "median", "max")}, ensure_ascii=False))
    print(f"written {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())