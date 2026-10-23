"""基准运行器（基准协议 §4）：四臂 × 三预算档 × 全部探针，产机器判据工件。

用法：
    PYTHONPATH=<pydeps>:. python -m tools.ail.runner --out artifacts/ail_benchmark.json

除命令行外无外部依赖；所有数字由真实 token 计数与真实记录内容算出。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import AIL_VERSION, SCHEMA_VERSION
from .arms import ARM_NAMES, run_arm
from .bundle import AILBundle
from .convert import coverage_report, convert_records, item_digest
from .corpus import (DEFAULT_RETRIEVED_AT, Record, extract_all, load_snapshot,
                     snapshot_drift, write_snapshot)
from .probes import PROBES, Probe, probes_for, quad_coverage, score_context
from .tokens import default_counter

TIERS = (("T1_tight", 400), ("T2_normal", 1200), ("T3_loose", 3000))
CORPORA = ("tensorplatform", "tensorphysics", "tensordem")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_corpus_bundles(records: list[Record]) -> tuple[dict, dict]:
    """按语料分组建 bundle。返回 (bundles, corpus_meta)。"""
    bundles: dict[str, AILBundle] = {}
    meta: dict[str, dict] = {}
    grouped: dict[str, list[Record]] = {}
    for record in records:
        grouped.setdefault(record.corpus, []).append(record)
    for corpus in CORPORA:
        subset = grouped.get(corpus, [])
        if not subset:
            raise SystemExit(f"语料 {corpus} 无记录：拒绝运行（否则该语料分母为 0）")
        result = convert_records(subset, corpus=corpus)
        bundle = AILBundle.create(
            bundle_id=f"ail-{corpus}",
            session=result.session,
            generator={"tool": "tools/ail", "ail_version": AIL_VERSION,
                       "schema_version": SCHEMA_VERSION, "corpus": corpus})
        for ev in result.evidence:
            bundle.add_evidence(ev)
        for item in result.items:
            bundle.add_item(item)
        bundles[corpus] = bundle
        meta[corpus] = {
            "records": len(subset),
            "items": len(result.items),
            "evidence": len(result.evidence),
            "coverage": coverage_report(subset, result.items),
            "skipped": result.skipped,
            "item_digest": item_digest(result.items),
            "bundle_checksum": bundle.to_document()["checksum"],
            "conflicts": bundle.detect_conflicts(),
            "sources": sorted({r.source_path for r in subset}),
            "probes": len(probes_for(corpus)),
        }
    return bundles, meta


def run_benchmark(*, records: list[Record] | None = None,
                  retrieved_at: str = DEFAULT_RETRIEVED_AT,
                  arms: tuple[str, ...] = ARM_NAMES) -> dict:
    counter = default_counter()
    records = records if records is not None else extract_all(retrieved_at=retrieved_at)
    bundles, corpus_meta = build_corpus_bundles(records)

    grouped: dict[str, list[Record]] = {}
    for record in records:
        grouped.setdefault(record.corpus, []).append(record)

    results: list[dict] = []
    for probe in PROBES:
        corpus = probe.corpus
        bundle = bundles[corpus]
        subset = grouped[corpus]
        per_arm: dict[str, dict] = {}
        for arm in arms:
            tiers: dict[str, dict] = {}
            # 预算档
            for tier_name, budget in TIERS:
                # 延迟判据（Issue #74 要求）：真实墙钟耗时，取 3 次取样的中位数。
                samples = []
                for _ in range(3):
                    t0 = time.perf_counter()
                    out = run_arm(arm, records=subset, bundle=bundle, token_budget=budget,
                                  counter=counter, intent=probe.intent, query=probe.question,
                                  now=retrieved_at)
                    samples.append((time.perf_counter() - t0) * 1000.0)
                scored = score_context(probe, out.text)
                tiers[tier_name] = {"tokens": out.tokens, "chars": out.chars,
                                    "truncated": out.truncated,
                                    "latency_ms_median": round(statistics.median(samples), 3),
                                    "latency_ms_min": round(min(samples), 3),
                                    "latency_samples_ms": [round(x, 3) for x in samples],
                                    **scored,
                                    "meta": out.meta}
            # 参考：该臂的自然（无预算）成本与准确率
            natural = run_arm(arm, records=subset, bundle=bundle, token_budget=None,
                              counter=counter, intent=probe.intent, query=probe.question,
                              now=retrieved_at)
            nscored = score_context(probe, natural.text)
            per_arm[arm] = {"tiers": tiers,
                            "natural": {"tokens": natural.tokens, "chars": natural.chars,
                                        **nscored}}
        results.append({**probe.to_dict(), "arms": per_arm})

    summary = summarize(results, arms)
    return {
        "issue": "#74",
        "title": "AIL 阶段 1-2 基准：四臂 × 三预算档 × 四元组任务恢复",
        "generated_at": _now(),
        "ail_version": AIL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "python": platform.python_version(),
        "python_binary": sys.executable,
        "tokenizer": counter.info(),
        "retrieved_at": retrieved_at,
        "tiers": [{"name": n, "token_budget": b} for n, b in TIERS],
        "arms": list(arms),
        "arm_notes": {
            "raw": "A0 原始历史：记录原样拼接（现状做法）。无预算成本即被测量对象。",
            "nl_summary": "A1 自然语言摘要：确定性抽取式首句 + 数字。弱基线。",
            "json_struct": "A2 结构化 JSON：扁平数组，无 topic/value/intent 语义。弱基线。",
            "ail": "A3 AIL：MemoryItem + topic/value + intent-aware 检索 + 分层预算装配。",
            "compact": "N/A：真实 LLMLingua/SimpleMem 需模型权重或外部服务，本阶段 NOT RUN。",
        },
        "quad_coverage": quad_coverage(),
        "probe_count": len(PROBES),
        "corpora": corpus_meta,
        "probes": results,
        "summary": summary,
        "limits": [
            "判分是信息在场性判分（可追、逐字、无自信错误），不是 LLM 阅读理解判分；"
            "下游任务成功率本阶段 NOT RUN。",
            "A1/A2 是确定性抽取的弱基线，弱于真实 LLMLingua/SimpleMem；"
            "结论不得外推为“优于 LLMLingua”。",
            "语料为三仓库真实工件快照，规模有限；结论是方向性的。",
        ],
    }


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def summarize(results: list[dict], arms: tuple[str, ...]) -> dict:
    """总表：每臂 × 每档的 token 均值/最大与四元组准确率。"""
    table: dict[str, dict] = {}
    for arm in arms:
        per_tier: dict[str, dict] = {}
        for tier_name, budget in TIERS:
            scores, tokens, exact, traceable, no_conf, answerable = [], [], [], [], [], []
            latencies = []
            for probe in results:
                cell = probe["arms"][arm]["tiers"][tier_name]
                scores.append(cell["score"])
                tokens.append(cell["tokens"])
                exact.append(cell["exact"])
                traceable.append(cell["traceable"])
                no_conf.append(cell["no_confident_error"])
                answerable.append(cell["answerable"])
                if "latency_ms_median" in cell:
                    latencies.append(cell["latency_ms_median"])
            per_tier[tier_name] = {
                "token_budget": budget,
                "tokens_mean": _avg([float(t) for t in tokens]),
                "tokens_max": max(tokens) if tokens else 0,
                "recovery_accuracy": _avg(scores),
                "answerable_rate": _avg(answerable),
                "exact_rate": _avg(exact),
                "traceable_rate": _avg(traceable),
                "no_confident_error_rate": _avg(no_conf),
                "latency_ms_median_mean": _avg(latencies),
                "latency_ms_median_max": max(latencies) if latencies else None,
            }
        natural_tokens = [probe["arms"][arm]["natural"]["tokens"] for probe in results]
        natural_scores = [probe["arms"][arm]["natural"]["score"] for probe in results]
        per_arm_quad: dict[str, dict] = {}
        for quad in ("state", "decision", "evidence", "next_action"):
            quad_scores = [p["arms"][arm]["tiers"]["T2_normal"]["score"]
                           for p in results if p["quad"] == quad]
            per_arm_quad[quad] = _avg(quad_scores)
        table[arm] = {
            "natural_tokens_mean": _avg([float(t) for t in natural_tokens]),
            "natural_tokens_max": max(natural_tokens) if natural_tokens else 0,
            "natural_recovery_accuracy": _avg(natural_scores),
            "tiers": per_tier,
            "by_quad_T2_normal": per_arm_quad,
        }
    return {
        "n_probes": len(results),
        "table": table,
        "headline": _headline(table),
    }


def _headline(table: dict) -> dict:
    """把最关键的三条对比算成一句话数字（报告直接抄这里）。"""
    out: dict = {}
    if "raw" in table and "ail" in table:
        raw_t1 = table["raw"]["tiers"]["T1_tight"]
        ail_t1 = table["ail"]["tiers"]["T1_tight"]
        raw_t2 = table["raw"]["tiers"]["T2_normal"]
        ail_t2 = table["ail"]["tiers"]["T2_normal"]
        out["T1_tight"] = {"raw_tokens": raw_t1["tokens_mean"],
                           "ail_tokens": ail_t1["tokens_mean"],
                           "raw_accuracy": raw_t1["recovery_accuracy"],
                           "ail_accuracy": ail_t1["recovery_accuracy"]}
        out["T2_normal"] = {"raw_tokens": raw_t2["tokens_mean"],
                            "ail_tokens": ail_t2["tokens_mean"],
                            "raw_accuracy": raw_t2["recovery_accuracy"],
                            "ail_accuracy": ail_t2["recovery_accuracy"]}
        out["raw_natural_cost"] = {"tokens_mean": table["raw"]["natural_tokens_mean"],
                                   "tokens_max": table["raw"]["natural_tokens_max"],
                                   "accuracy": table["raw"]["natural_recovery_accuracy"]}
    if "nl_summary" in table and "ail" in table:
        nl = table["nl_summary"]["tiers"]["T2_normal"]
        ail = table["ail"]["tiers"]["T2_normal"]
        out["T2_normal_nl_vs_ail"] = {"nl_tokens": nl["tokens_mean"],
                                      "nl_accuracy": nl["recovery_accuracy"],
                                      "ail_tokens": ail["tokens_mean"],
                                      "ail_accuracy": ail["recovery_accuracy"]}
    if "json_struct" in table and "ail" in table:
        js = table["json_struct"]["tiers"]["T2_normal"]
        ail = table["ail"]["tiers"]["T2_normal"]
        out["T2_normal_json_vs_ail"] = {"json_tokens": js["tokens_mean"],
                                        "json_accuracy": js["recovery_accuracy"],
                                        "ail_tokens": ail["tokens_mean"],
                                        "ail_accuracy": ail["recovery_accuracy"]}
    return out


def write_artifacts(payload: dict, out_path: Path, bundles: dict[str, AILBundle]) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    bundle_dir = out_path.parent / "ail_bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for corpus, bundle in bundles.items():
        path = bundle_dir / f"{corpus}.jsonl"
        path.write_text(bundle.to_jsonl(), encoding="utf-8")
        written[corpus] = {"path": str(path), "checksum": bundle.to_document()["checksum"]}
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIL 基准（Issue #74）")
    parser.add_argument("--out", default="artifacts/ail_benchmark.json")
    parser.add_argument("--snapshot", default="tools/ail/corpus/records.snapshot.json",
                        help="记录快照路径（写入；--from-snapshot 时读取）")
    parser.add_argument("--from-snapshot", action="store_true",
                        help="从既有快照读取记录（用于离线复现），而不是重新抽取")
    parser.add_argument("--retrieved-at", default=DEFAULT_RETRIEVED_AT)
    parser.add_argument("--arms", default=",".join(ARM_NAMES))
    args = parser.parse_args(argv)

    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    unknown = [a for a in arms if a not in ARM_NAMES]
    if unknown:
        raise SystemExit(f"未知臂：{unknown}")

    snapshot_path = Path(args.snapshot)
    if args.from_snapshot:
        if not snapshot_path.is_file():
            raise SystemExit(f"快照不存在：{snapshot_path}（先不带 --from-snapshot 跑一次生成）")
        records, _ = load_snapshot(snapshot_path)
        source_mode = "snapshot"
        drift = snapshot_drift(snapshot_path)
        print(f"[snapshot] 读取 {len(records)} 条记录：{snapshot_path}")
        if drift:
            # 不阻断：漂移是事实，必须报出来，由人判断是否需要重建快照。
            print(f"[snapshot] 警告：{len(drift)} 个来源已变化（语料可能滞后于磁盘）：")
            for item in drift[:5]:
                print(f"  - {item['status']}: {item['path']}")
    else:
        records = extract_all(retrieved_at=args.retrieved_at)
        snapshot = write_snapshot(records, snapshot_path, retrieved_at=args.retrieved_at)
        source_mode = "live_extract"
        drift = []
        print(f"[extract] 抽取 {len(records)} 条记录 -> {snapshot_path} "
              f"({snapshot['counts_by_corpus']})")

    payload = run_benchmark(records=records, retrieved_at=args.retrieved_at, arms=arms)
    payload["corpus_source"] = {
        "mode": source_mode,
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": (hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
                            if snapshot_path.is_file() else None),
        "record_count": len(records),
        "drift": drift,
        "note": ("可复现性契约：报告数字一律以 mode=snapshot 运行产生。"
                 "mode=live_extract 会随持续增长的来源（如 agent_memory/*.jsonl）漂移，"
                 "仅供重建快照时使用。"),
    }
    bundles, _ = build_corpus_bundles(records)
    written = write_artifacts(payload, Path(args.out), bundles)
    print(f"[bench] 探针 {payload['probe_count']}，语料 {list(payload['corpora'])}")
    print(f"[bench] tokenizer={payload['tokenizer']['kind']}")
    for arm, row in payload["summary"]["table"].items():
        t2 = row["tiers"]["T2_normal"]
        print(f"  {arm:12s} natural_tokens={row['natural_tokens_mean']:8.1f} "
              f"T2 tokens={t2['tokens_mean']:7.1f} acc={t2['recovery_accuracy']:.3f}")
    print(f"[bench] 工件：{args.out} + bundles {list(written)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())