#!/usr/bin/env python3
"""m9107 验收驱动：跑合成夹具与真实语料两条链、出图、把所有判据数字落 JSON（零手抄）。

用法
----
    python3 tools/knowledge_distill/m9107_acceptance.py [--real-reports N] [--skip-real]

产物（runs/m9107/）
-------------------
* ``synthetic/``      10 份合成报告 → 端到端判据 (a)(b)(c)(d)（含真值聚类一致性）
* ``real/``           真实 ``work/*/report.json`` 子集 + 真实提案池作参考 → 真实语料判据
* ``figures/*.png``   五张机制/统计图（报告内嵌）
* ``acceptance.json`` 全部判据数字 + 产物 sha256（报告正文只许引用这里）
* ``real/cluster_spotcheck.json`` 人工抽检抽样表（20 簇）

判据数字一律从 :func:`tools.knowledge_distill.pipeline.distill` 的返回值程序化汇总，
本脚本不写任何字面量结论。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.knowledge_distill.cluster import auto_eps, cluster_profiles, dbscan  # noqa: E402
from tools.knowledge_distill.features import FeatureSpace  # noqa: E402
from tools.knowledge_distill.novelty import ReferenceCorpus  # noqa: E402
from tools.knowledge_distill.params import DistillParams  # noqa: E402
from tools.knowledge_distill.pipeline import acceptance, distill  # noqa: E402

OUT = ROOT / "runs" / "m9107"
FIX = ROOT / "tests" / "fixtures" / "knowledge_distill"
REPORTS = sorted(str(p) for p in (FIX / "reports").glob("*.json"))
REFS = [str(FIX / "reference")]
TRUTH = json.loads((FIX / "truth_items.json").read_text(encoding="utf-8"))
REAL_REPORTS_GLOB = "/root/tp-agent/work/*/report.json"
REAL_REFERENCE_DIRS = ["/root/tp-agent/proposals", "/root/tp-agent/verified"]


def _rel(path: Path) -> str:
    """产物路径尽量给相对仓库根的写法；不在仓库内时给绝对路径（--out 可指仓库外）。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_reports(limit: int) -> list[str]:
    files = sorted(glob.glob(REAL_REPORTS_GLOB))
    return files[:limit]


def _existing(paths: list[str]) -> list[str]:
    return [p for p in paths if Path(p).exists()]


def eps_sweep(reports: list[str], params: DistillParams, quantiles: list[float],
              truth: dict[str, str] | None = None) -> list[dict]:
    """邻域半径标定：分位点 → eps / 簇数 / 归属率 / （有真值时）纯度。"""
    from tools.knowledge_distill.extract import dedupe_items, parse_reports

    parsed = parse_reports(reports, params)
    items = dedupe_items(parsed["items"])["items"]
    if not items:
        return []
    space = FeatureSpace(params).fit_transform([i["text"] for i in items])
    truth_labels = ([str(truth.get(i["node_key"], "unlabelled")) for i in items]
                    if truth else None)
    rows = []
    for q in quantiles:
        p = DistillParams(**{**params.as_dict(), "eps": None, "eps_quantile": q})
        eps = auto_eps(len(items), space.pair_metric, min_samples=p.min_samples,
                       quantile=q)
        labels, info = dbscan(len(items), space.pair_metric, eps=eps,
                              min_samples=p.min_samples)
        profiles = cluster_profiles(labels, space.pair_metric, truth=truth_labels,
                                    texts=[i["text"] for i in items])
        row = {"eps_quantile": q, "eps": round(eps, 6), "clusters": info["clusters"],
               "assigned": info["assigned"], "n_items": len(items),
               "assigned_ratio": round(info["assigned"] / len(items), 6),
               "max_cluster": max((p2["size"] for p2 in profiles), default=0)}
        if truth_labels:
            judged = [p2 for p2 in profiles if "dominant_share" in p2]
            row["purity"] = round(
                sum(p2["size"] * p2["dominant_share"] for p2 in judged) /
                max(1, sum(p2["size"] for p2 in judged)), 6) if judged else None
        rows.append(row)
    return rows


def spotcheck_sample(result: dict, k: int = 20) -> dict:
    """确定性抽取 k 个簇（按规模降序、序号升序），给人读的抽样表。"""
    profiles = sorted(result["clustering"]["profiles"],
                      key=lambda p: (-p["size"], p["cluster_id"]))
    sample = []
    for prof in profiles[:k]:
        sample.append({
            "cluster_id": prof["cluster_id"],
            "size": prof["size"],
            "intra_similarity": prof["intra_similarity"],
            "medoid_text": prof["medoid_text"],
            "members": [prof["medoid_text"]] + [
                result["clusters_text"][i] for i in prof["members"]
                if i != prof["medoid"]],
        })
    return {
        "sampled": len(sample),
        "rule": "按簇规模降序、cluster_id 升序取前 20 簇",
        "clusters": sample,
        "verdicts": {},
        "note": "verdicts 由人工抽检逐簇填写（consistent/note），比例脚本按 verdicts 计算",
    }


def _pairs_by_truth(items: list[dict], space: FeatureSpace, truth: dict[str, str],
                    *, item_level: bool = False) -> dict[str, list[float]]:
    within, cross = [], []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if item_level:
                a = truth.get(items[i]["text"], "unlabelled")
                b = truth.get(items[j]["text"], "unlabelled")
            else:
                a = truth.get(items[i]["node_key"], "unlabelled")
                b = truth.get(items[j]["node_key"], "unlabelled")
            if a == "unlabelled" or b == "unlabelled":
                continue
            (within if a == b else cross).append(space.pair_metric(i, j))
    return {"within": within, "cross": cross}


def two_tier_equivalence(reports: list[str], reference_dirs: list[str],
                         params: DistillParams, max_refs: int = 800) -> dict:
    """两级检索等价性抽检：全量精确 vs 词元检索+头部精确。"""
    from tools.knowledge_distill.extract import parse_reports

    parsed = parse_reports(reports, params)
    texts = [i["text"] for i in parsed["items"]][:40]
    corpus = ReferenceCorpus(params)
    corpus.add_paths(reference_dirs)
    if len(corpus.entries) > max_refs:
        corpus.entries = corpus.entries[:max_refs]
        corpus._ensured = False
    corpus.ensure_index()
    diffs, comparisons = [], []
    for text in texts:
        fast = corpus.max_similarity(text, top_k=64)
        exact = corpus.max_similarity(text, top_k=None)
        diffs.append(abs(fast["max_sim"] - exact["max_sim"]))
        comparisons.append({"fast": fast["max_sim"], "exact": exact["max_sim"]})
    return {
        "queries": len(texts),
        "refs_used": len(corpus),
        "max_abs_diff": round(max(diffs), 9) if diffs else 0.0,
        "exact_matches": sum(1 for c in comparisons if abs(c["fast"] - c["exact"]) < 1e-9),
        "examples": comparisons[:3],
    }


def figures(syn: dict, real: dict, sweep_syn: list[dict], sweep_real: list[dict],
            margin: dict, out: Path) -> list[dict]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.mkdir(parents=True, exist_ok=True)
    made: list[dict] = []

    def save(fig, name: str, caption: str) -> None:
        path = out / name
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        made.append({"path": _rel(path), "caption": caption, "sha256": _sha(path)})

    # 1) 解析覆盖率 / 字段覆盖率 / 条目构成
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    labels = ["synthetic(10)", f"real({real['coverage']['reports_total']})"]
    parse = [syn["coverage"]["parse_coverage"], real["coverage"]["parse_coverage"]]
    field = [syn["coverage"]["field_coverage"], real["coverage"]["field_coverage"]]
    x = range(len(labels))
    axes[0].bar([i - 0.2 for i in x], parse, width=0.4, label="parse coverage")
    axes[0].bar([i + 0.2 for i in x], field, width=0.4, label="field coverage")
    axes[0].axhline(0.9, ls="--", c="k", lw=1, label="criterion 0.90")
    axes[0].set_xticks(list(x), labels)
    axes[0].set_ylim(0, 1.05)
    axes[0].set_title("Report parsing coverage")
    axes[0].legend(fontsize=8)
    kinds = ["limitation", "next_step", "honest_fail"]
    width = 0.35
    for off, (tag, res) in zip((-width / 2, width / 2),
                               (("synthetic", syn), ("real", real))):
        vals = [res["coverage"]["items_by_kind"][k] for k in kinds]
        axes[1].bar([i + off for i in range(len(kinds))], vals, width=width, label=tag)
    axes[1].set_xticks(range(len(kinds)), kinds)
    axes[1].set_title("Extracted items by kind")
    axes[1].legend(fontsize=8)
    fig.suptitle("Fig 1  Parsing coverage and item composition", fontsize=10)
    save(fig, "fig1_coverage.png", "Fig 1 解析覆盖率与条目构成（左：覆盖率 vs 0.90 判据；右：条目类别分布）")

    # 2) 新颖性分布
    fig, ax = plt.subplots(figsize=(7, 3.6))
    novelty = [s["gate"]["novelty"] for s in real["seeds"]]
    maxsim = [s["gate"]["max_sim"] for s in real["seeds"]]
    ax.hist(novelty, bins=12, alpha=0.75, label="novelty = 1 - max_sim")
    ax.hist(maxsim, bins=12, alpha=0.55, label="max_sim to proposal pool")
    ax.axvline(0.55, ls="--", c="k", lw=1, label="novelty floor 0.55")
    ax.axvline(0.45, ls=":", c="r", lw=1, label="1 - floor (max_sim ceiling)")
    ax.set_xlabel("score")
    ax.set_ylabel("seed count")
    ax.set_title("Fig 2  Novelty gate on real corpus")
    ax.legend(fontsize=8)
    save(fig, "fig2_novelty.png", "Fig 2 真实语料种子的新颖性与最近邻相似度分布（虚线=新颖性下限 0.55）")

    # 3) 同议题 / 跨议题相似度分离度（合成真值）
    fig, ax = plt.subplots(figsize=(7, 3.6))
    bins = [i / 40 for i in range(41)]
    ax.hist(margin["within"], bins=bins, alpha=0.75, density=True,
            label=f"same topic (n={len(margin['within'])})")
    ax.hist(margin["cross"], bins=bins, alpha=0.55, density=True,
            label=f"cross topic (n={len(margin['cross'])})")
    ax.set_xlabel("three-channel similarity")
    ax.set_ylabel("density")
    ax.set_title("Fig 3  Similarity separation (synthetic ground truth)")
    ax.legend(fontsize=8)
    save(fig, "fig3_similarity.png", "Fig 3 同议题/跨议题条目三通道相似度分布（合成真值，用于说明聚类可分性）")

    # 4) 邻域半径标定曲线
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax2 = ax.twinx()
    for tag, rows, style in (("synthetic", sweep_syn, "o"), ("real", sweep_real, "s")):
        if not rows:
            continue
        ax.plot([r["eps"] for r in rows], [r["clusters"] for r in rows],
                "-" + style, label=f"{tag}: clusters")
        ax2.plot([r["eps"] for r in rows], [r["assigned_ratio"] for r in rows],
                 ":" + style, alpha=0.7, label=f"{tag}: assigned ratio")
    ax.set_xlabel("eps (cosine distance)")
    ax.set_ylabel("cluster count")
    ax2.set_ylabel("assigned ratio")
    ax.set_title("Fig 4  auto_eps calibration (quantile sweep)")
    lines = ax.get_lines() + ax2.get_lines()
    ax.legend(lines, [ln.get_label() for ln in lines], fontsize=7)
    save(fig, "fig4_eps_sweep.png", "Fig 4 邻域半径自适应标定：分位点→eps / 簇数（实线）/ 归属率（虚线）")

    # 5) 种子规模与门控裁决
    fig, ax = plt.subplots(figsize=(8, 3.6))
    seeds = sorted(real["seeds"], key=lambda s: (-s["size"], s["seed_id"]))
    colors = ["#2a9d8f" if s["gate"]["passed"] else "#e76f51" for s in seeds]
    ax.bar(range(len(seeds)), [s["size"] for s in seeds], color=colors)
    ax.set_xlabel("seed (sorted by cluster size)")
    ax.set_ylabel("cluster size")
    ax.set_title("Fig 5  Proposal seeds: accepted (teal) / rejected (red)")
    save(fig, "fig5_seeds.png", "Fig 5 真实语料候选种子规模与门控裁决（青=通过，红=被新颖性门控拒）")
    return made


def main(argv: list[str] | None = None) -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT), help="产物根目录（默认 runs/m9107）")
    ap.add_argument("--real-reports", type=int, default=300)
    ap.add_argument("--skip-real", action="store_true")
    ap.add_argument("--skip-figures", action="store_true")
    args = ap.parse_args(argv)
    OUT = Path(args.out)

    OUT.mkdir(parents=True, exist_ok=True)
    params = DistillParams()

    # ---------------- 合成夹具链 ----------------
    syn = distill(REPORTS, REFS, params, truth_by_item=TRUTH,
                  out_dir=OUT / "synthetic")
    # 合成链的同/跨议题相似度（直接复用解析结果）
    from tools.knowledge_distill.extract import dedupe_items, parse_reports
    parsed_syn = parse_reports(REPORTS, params)
    syn_items = dedupe_items(parsed_syn["items"])["items"]
    syn_space = FeatureSpace(params).fit_transform([i["text"] for i in syn_items])
    margin = _pairs_by_truth(syn_items, syn_space, TRUTH,
                             item_level=True)

    sweep_syn = eps_sweep(REPORTS, params, [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
                          truth=TRUTH)

    result = {
        "mission_id": 9107,
        "generated_by": "tools/knowledge_distill/m9107_acceptance.py",
        "params": params.as_dict(),
        "params_sha256": params.digest(),
        "synthetic": {
            "inputs": {"reports": len(REPORTS), "reference_files": len(REFS),
                       "truth_items": len(TRUTH)},
            "coverage": syn["coverage"],
            "clustering": {k: v for k, v in syn["clustering"].items() if k != "profiles"},
            "gate": syn["gate"],
            "seeds": [{"seed_id": s["seed_id"], "size": s["size"], "title": s["title"],
                       "sha256": s["sha256"], "gate": s["gate"]} for s in syn["seeds"]],
            "acceptance": syn["acceptance"],
            "outputs": syn["outputs"],
        },
        "synthetic_calibration": {
            "eps_sweep": sweep_syn,
            "similarity_margin": {
                "within_mean": round(sum(margin["within"]) / len(margin["within"]), 6)
                if margin["within"] else None,
                "cross_mean": round(sum(margin["cross"]) / len(margin["cross"]), 6)
                if margin["cross"] else None,
                "within_max": round(max(margin["within"]), 6) if margin["within"] else None,
                "cross_max": round(max(margin["cross"]), 6) if margin["cross"] else None,
                "within_pairs": len(margin["within"]),
                "cross_pairs": len(margin["cross"]),
            },
        },
    }

    # ---------------- 真实语料链 ----------------
    real_reports = _real_reports(args.real_reports)
    real_refs = _existing(REAL_REFERENCE_DIRS)
    if real_reports and not args.skip_real:
        real = distill(real_reports, real_refs, params, out_dir=OUT / "real")
        parsed_real = parse_reports(real_reports, params)
        real_items = dedupe_items(parsed_real["items"])["items"]
        spot = spotcheck_sample({
            "clustering": real["clustering"],
            "clusters_text": [i["text"] for i in real_items],
        }, k=20)
        spot["verdicts_file"] = _rel(OUT / "real" / "cluster_spotcheck_verdicts.json")
        (OUT / "real" / "cluster_spotcheck.json").write_text(
            json.dumps(spot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verdicts_path = OUT / "real" / "cluster_spotcheck_verdicts.json"
        manual = None
        if verdicts_path.exists():
            verdicts = json.loads(verdicts_path.read_text(encoding="utf-8"))
            judged = [v for v in verdicts.values() if "consistent" in v]
            consistent = [v for v in judged if v["consistent"]]
            manual = {"clusters_judged": len(judged),
                      "clusters_consistent": len(consistent),
                      "consistency_ratio": round(len(consistent) / len(judged), 6)
                      if judged else 0.0}
        if manual:
            real["acceptance"] = acceptance(real, params, manual_consistency=manual)
        result["real"] = {
            "inputs": {"reports": len(real_reports), "reference_dirs": real_refs,
                       "reference_entries": real["reference"]["entries"]},
            "coverage": real["coverage"],
            "clustering": {k: v for k, v in real["clustering"].items() if k != "profiles"},
            "gate": real["gate"],
            "seeds": [{"seed_id": s["seed_id"], "size": s["size"], "title": s["title"],
                       "sha256": s["sha256"], "repo_hint": s["repo_hint"],
                       "domain_hint": s["domain_hint"],
                       "gate": s["gate"]} for s in real["seeds"]],
            "acceptance": real["acceptance"],
            "outputs": real["outputs"],
            "manual_spotcheck": manual,
            "spotcheck_sample": spot["sampled"],
        }
        sweep_real = eps_sweep(real_reports[:120], params, [0.3, 0.5, 0.6, 0.7, 0.8])
        result["real_calibration"] = {
            "eps_sweep": sweep_real,
            "two_tier_equivalence": two_tier_equivalence(
                real_reports[:60], real_refs[:1] or real_refs, params),
        }
    else:
        result["real"] = {"skipped": True, "reason": "无真实报告或 --skip-real"}
        sweep_real = []

    if not args.skip_figures:
        result["figures"] = figures(syn, result["real"] if not result["real"].get("skipped")
                                    else syn, sweep_syn, sweep_real, margin,
                                    OUT / "figures")

    acc_path = OUT / "acceptance.json"
    acc_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    summary = {
        "acceptance": _rel(acc_path),
        "acceptance_sha256": _sha(acc_path),
        "synthetic_all_ok": syn["acceptance"]["all_ok"],
        "synthetic_seeds": len(syn["accepted_seeds"]),
        "real_all_ok": result["real"].get("acceptance", {}).get("all_ok"),
        "real_seeds": len(result["real"].get("seeds", [])),
        "real_accepted": sum(1 for s in result["real"].get("seeds", [])
                             if s["gate"].get("passed")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if syn["acceptance"]["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())