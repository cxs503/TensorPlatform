"""端到端蒸馏流水线：报告 → 条目 → 特征 → 簇 → 提案骨架 → 新颖性门控 → 溯源链。

产物（全部落调用方指定目录，机器可复算）
--------------------------------------
* ``coverage.json``   解析覆盖率与字段覆盖率（判据 a 的分子分母都在里面）；
* ``clusters.json``   逐簇画像（成员、平均簇内相似度、代表点、真值主导率）；
* ``seeds.json``      候选种子 + 门控判定（含三路相似度、最近邻参考条目）；
* ``provenance.jsonl``每个种子的来源链（节点 ID / 报告路径 / 报告 sha256 / 原文引用 /
  引用 sha256 / 种子 sha256）；
* ``run.json``        参数指纹、输入清单（路径+sha256+字节数）、全部判据数字；
* ``MANIFEST.json``   以上每个产物的 sha256（二次复核用，防止报告手抄）。

判据由 :func:`acceptance` 程序化汇总，报告正文只允许引用这些数字。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .cluster import UNASSIGNED, auto_eps, cluster_profiles, cluster_quality, dbscan
from .extract import dedupe_items, parse_reports, report_sha256
from .features import FeatureSpace
from .novelty import ReferenceCorpus, gate
from .params import DistillParams


def _canonical(doc: Any) -> bytes:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def seed_digest(seed: Mapping[str, Any]) -> str:
    """种子指纹：只覆盖内容字段（来源链与门控判定变化不应改写种子身份）。"""
    core = {
        "title": seed.get("title"),
        "gap_description": seed.get("gap_description"),
        "repo_hint": seed.get("repo_hint"),
        "domain_hint": seed.get("domain_hint"),
        "kind_counts": seed.get("kind_counts"),
        "source_refs": sorted({s["source_sha256"] for s in seed.get("sources", [])}),
    }
    return hashlib.sha256(_canonical(core)).hexdigest()


def _majority(values: Iterable[Any]) -> Any:
    counts: dict[str, int] = {}
    for value in values:
        if value in (None, ""):
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda k: counts[k])


def _build_seeds(items: Sequence[Mapping[str, Any]], labels: Sequence[int],
                 space: FeatureSpace, params: DistillParams,
                 profiles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sim = space.pair_metric
    seeds: list[dict[str, Any]] = []
    for prof in profiles:
        if int(prof["size"]) < params.min_cluster_size:
            continue
        medoid_idx = int(prof["medoid"])
        members = sorted(prof["members"],
                         key=lambda i: (-sim(medoid_idx, i), str(items[i]["item_id"])))
        reps = [items[i] for i in members[:3]]
        kinds: dict[str, int] = {}
        for i in members:
            kinds[items[i]["kind"]] = kinds.get(items[i]["kind"], 0) + 1
        title = " ".join(str(items[medoid_idx]["text"]).split())[:params.title_max_chars]
        seed: dict[str, Any] = {
            "seed_id": "",
            "cluster_id": prof["cluster_id"],
            "title": title,
            "gap_description": " | ".join(r["quote"][:240] for r in reps),
            "repo_hint": _majority(items[i].get("repo_hint") for i in members),
            "domain_hint": _majority(items[i].get("domain_hint") for i in members),
            "kind_counts": kinds,
            "size": len(members),
            "intra_similarity": prof["intra_similarity"],
            "sources": [{
                "node_key": items[i]["node_key"],
                "mission_id": items[i]["mission_id"],
                "campaign": items[i]["campaign"],
                "kind": items[i]["kind"],
                "source_path": items[i]["source_path"],
                "source_sha256": items[i]["source_sha256"],
                "pointer": items[i]["pointer"],
                "quote": items[i]["quote"],
                "quote_sha256": items[i]["text_sha256"],
                "occurrences": items[i].get("occurrences", 1),
                "aliases": items[i].get("aliases", [items[i]["item_id"]]),
                "source_paths": items[i].get("source_paths", [items[i]["source_path"]]),
            } for i in members],
        }
        seed["novelty_text"] = f"{title} :: {seed['gap_description']}"
        seed["sha256"] = seed_digest(seed)
        seed["seed_id"] = f"seed-{seed['sha256'][:12]}"
        seeds.append(seed)
    return seeds


def distill(report_paths: Sequence[str | Path],
            reference_paths: Sequence[str | Path] | None = None,
            params: DistillParams | None = None,
            *,
            truth_by_node: Mapping[str, str] | None = None,
            truth_by_item: Mapping[str, str] | None = None,
            max_reports: int | None = None,
            out_dir: str | Path | None = None) -> dict[str, Any]:
    """跑完整条蒸馏链，返回结果字典（并可选落盘全部产物）。"""
    params = (params or DistillParams()).validate()
    parsed = parse_reports(report_paths, params, max_reports=max_reports)
    deduped = dedupe_items(parsed["items"])
    items = deduped["items"]
    coverage = {**parsed["coverage"], "dedupe": deduped["stats"]}

    corpus = ReferenceCorpus(params)
    if reference_paths:
        corpus.add_paths(reference_paths)
    corpus.ensure_index()

    space = FeatureSpace(params).fit_transform([i["text"] for i in items])
    n = len(items)
    if n == 0:
        labels: list[int] = []
        info: dict[str, Any] = {"n_points": 0, "clusters": 0, "assigned": 0,
                                "unassigned": 0, "algorithm": "dbscan(cosine)"}
        eps = params.eps
    else:
        eps = params.eps if params.eps is not None else auto_eps(
            n, space.pair_metric, min_samples=params.min_samples,
            quantile=params.eps_quantile)
        labels, info = dbscan(n, space.pair_metric, eps=float(eps),
                              min_samples=params.min_samples)
    info["eps_auto"] = params.eps is None
    truth = None
    if truth_by_item or truth_by_node:
        # 条目级真值优先（一个节点可同时报告多个议题，节点级真值会把跨议题条目算错）
        truth = [str((truth_by_item or {}).get(i["text"])
                     or (truth_by_node or {}).get(i["node_key"], "unlabelled"))
                 for i in items]
    profiles = cluster_profiles(labels, space.pair_metric, truth=truth,
                                texts=[i["text"] for i in items])
    kept = [p for p in profiles
            if int(p["size"]) >= params.min_cluster_size
            and float(p["intra_similarity"]) >= params.min_intra_similarity]
    dropped = [p for p in profiles if p not in kept]
    seeds = _build_seeds(items, labels, space, params, kept)
    info["clusters_after_filter"] = len(kept)
    info["dropped_low_cohesion"] = [
        {"cluster_id": p["cluster_id"], "size": p["size"],
         "intra_similarity": p["intra_similarity"]} for p in dropped]
    gated = gate(seeds, corpus, params)
    verdict = {d["seed_id"]: d for d in gated["decisions"]}
    for seed in seeds:
        seed["gate"] = verdict.get(seed["seed_id"], {})
    accepted = [s for s in seeds if s["gate"].get("passed")]

    result: dict[str, Any] = {
        "schema_version": "1.0",
        "params": params.as_dict(),
        "params_sha256": params.digest(),
        "coverage": coverage,
        "clustering": {**info, "profiles": profiles,
                       "purity": (round(sum(p["size"] * p["dominant_share"]
                                            for p in profiles if "dominant_share" in p) /
                                       max(1, sum(p["size"] for p in profiles
                                                  if "dominant_share" in p)), 6)
                                   if truth else None),
                       "quality": cluster_quality(profiles)},
        "reference": {"entries": len(corpus), "paths": [str(p) for p in
                                                        (reference_paths or [])]},
        "seeds": seeds,
        "accepted_seeds": accepted,
        "gate": {k: v for k, v in gated.items() if k != "decisions"},
        "unassigned_items": [items[i]["item_id"] for i, lab in enumerate(labels)
                             if lab == UNASSIGNED],
    }
    result["acceptance"] = acceptance(result, params,
                                      min_parse_coverage=0.90,
                                      min_cluster_consistency=0.80,
                                      min_seeds=3)
    if out_dir is not None:
        result["outputs"] = write_outputs(result, out_dir,
                                          report_paths=report_paths)
    return result


def acceptance(result: Mapping[str, Any], params: DistillParams, *,
               min_parse_coverage: float = 0.90,
               min_cluster_consistency: float = 0.80,
               min_seeds: int = 3,
               manual_consistency: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """把判据 (a)(b)(c)(d) 逐条算成数字（报告只许引用这里）。

    判据 (b) 的证据来源两档：有真值标签时用真值主导率自动算；没有真值时只能用人工
    抽检结果（``manual_consistency``）。**两档都没有时不得判通过**——否则"没人核过"
    会被读成"核过且通过"。
    """
    cov = result["coverage"]
    clustering = result["clustering"]
    gated = result["gate"]
    quality = clustering["quality"]
    judged = int(quality["clusters_judged"])
    if judged:
        b_value = quality["consistency_ratio"]
        b_judged_by = "truth_labels"
        b_ok = b_value >= min_cluster_consistency
        b_judged = judged
    elif manual_consistency:
        b_value = float(manual_consistency.get("consistency_ratio", 0.0))
        b_judged_by = "manual_spotcheck"
        b_judged = int(manual_consistency.get("clusters_judged", 0))
        b_ok = b_judged >= min_seeds and b_value >= min_cluster_consistency
    else:
        b_value = 0.0
        b_judged_by = "none"
        b_judged = 0
        b_ok = False
    checks = {
        "a_parse_coverage": {
            "value": cov["parse_coverage"], "field_coverage": cov["field_coverage"],
            "threshold": min_parse_coverage,
            "ok": cov["parse_coverage"] >= min_parse_coverage
                  and cov["field_coverage"] >= min_parse_coverage,
            "formula": "consistent_reports/reports_total",
        },
        "b_cluster_consistency": {
            "value": b_value,
            "clusters_judged": b_judged,
            "judged_by": b_judged_by,
            "threshold": min_cluster_consistency,
            "ok": b_ok,
            "formula": "semantically consistent clusters / clusters judged"
                       "（真值主导率 或 人工抽检，二者缺一即不判通过）",
        },
        "c_novelty_mean_sim": {
            "value": gated["mean_sim_of_passed"], "threshold": params.mean_sim_max,
            "ok": gated["passed"] > 0
                  and gated["mean_sim_of_passed"] < params.mean_sim_max,
            "novelty_floor": params.novelty_min,
            "min_novelty_observed": round(min((s["gate"]["novelty"] for s in
                                               result["seeds"] if s["gate"].get("passed")),
                                              default=0.0), 6),
        },
        "d_seed_yield": {
            "value": len(result["accepted_seeds"]), "threshold": min_seeds,
            "candidate_seeds": len(result["seeds"]),
            "ok": len(result["accepted_seeds"]) >= min_seeds,
        },
    }
    checks["all_ok"] = all(v["ok"] for v in checks.values())
    return checks


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_outputs(result: Mapping[str, Any], out_dir: str | Path,
                  *, report_paths: Sequence[str | Path] | None = None) -> dict[str, Any]:
    """落盘产物并生成 MANIFEST（输入清单 + 输出逐文件 sha256）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload.pop("outputs", None)

    files: dict[str, Any] = {}
    def dump(name: str, doc: Any) -> None:
        path = out / name
        text = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False)
        path.write_text(text + "\n", encoding="utf-8")
        files[name] = {"sha256": _sha256_file(path), "bytes": path.stat().st_size}

    dump("coverage.json", payload["coverage"])
    dump("clusters.json", payload["clustering"])
    dump("seeds.json", {"seeds": payload["seeds"],
                        "accepted": payload["accepted_seeds"],
                        "gate": payload["gate"]})
    prov = out / "provenance.jsonl"
    lines = []
    for seed in payload["seeds"]:
        lines.append(json.dumps({"seed_id": seed["seed_id"], "sha256": seed["sha256"],
                                 "sources": seed["sources"]}, ensure_ascii=False,
                                sort_keys=True))
    prov.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    files["provenance.jsonl"] = {"sha256": _sha256_file(prov), "bytes": prov.stat().st_size}

    inputs = []
    for raw in report_paths or []:
        p = Path(raw)
        if p.is_file():
            inputs.append({"path": str(p), "sha256": report_sha256(p),
                           "bytes": p.stat().st_size})
    inputs.sort(key=lambda r: r["path"])
    dump("run.json", {k: v for k, v in payload.items() if k != "seeds"} |
         {"inputs": inputs, "input_count": len(inputs)})
    manifest = {"schema_version": "1.0", "inputs": inputs,
                "outputs": files,
                "manifest_sha256": hashlib.sha256(_canonical(files)).hexdigest()}
    dump("MANIFEST.json", manifest)
    return {"dir": str(out), "manifest_sha256": manifest["manifest_sha256"],
            "files": files}


__all__ = ["acceptance", "distill", "seed_digest", "write_outputs"]