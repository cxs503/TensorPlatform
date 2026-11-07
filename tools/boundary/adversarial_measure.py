#!/usr/bin/env python3
"""边界门对抗度量（P20-N08 / E2-E4）：召回率、误报率、混淆矩阵、漏检清单、机理归因。

度量三层
--------
1. ``baseline``  现行门禁（IN17-B1 / m5016，纯词表匹配）
2. ``hardened``  边界门 v1.1 = 基础 + 结构性判据（``tools/boundary/structural_gate.py``）
3. ``vocab_probe`` 词表扩张的**留出法**天花板：把同义/外文映射表按奇偶切成
   「扩张集」与「留出集」，只把扩张集写进词表，分别度量两类样本的召回——
   用来证明「词表外的改写只能靠词表增长救，而词表增长对未枚举措辞没有保证」。

机器可读输出（判据 C2）
----------------------
``artifacts/boundary/adversarial_metrics.json``::

    {"baseline": {"per_class": {"<cls>": {"recall", "fp_rate", "n", ...}},
                  "overall": {"recall", "fp_rate", "precision", ...},
                  "missed": [...], "mechanism_table": [...]},
     "hardened": {...}, "vocab_probe": {...}, "control": {...}, "acceptance": {...}}

自伤控制（判据 C4）
------------------
语料里带一条「已知会被拦」的样本（明文词条裸写进代码路径）。门禁若判绿，
说明度量链路（diff 构造/路径分类/词表装载）坏了——本模块直接以非零码中止，
拒绝产出数字。

用法
----
    python3 tools/boundary/adversarial_measure.py \
        --out artifacts/boundary/adversarial_metrics.json
    python3 tools/boundary/adversarial_measure.py --check   # 与已落盘结果比对
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.boundary import pr_gate_scan as gate  # type: ignore
    from tools.boundary import adversarial_corpus as corpus_mod  # type: ignore
    from tools.boundary import structural_gate as structural_mod  # type: ignore
else:  # pragma: no cover
    from . import pr_gate_scan as gate  # type: ignore
    from . import adversarial_corpus as corpus_mod  # type: ignore
    from . import structural_gate as structural_mod  # type: ignore

SCHEMA = "tp.boundary.adversarial_metrics/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_REL = "data/boundary/adversarial_corpus.json"
SEEDS_REL = corpus_mod.SEEDS_REL
LEXICON_REL = gate.DEFAULT_LEXICON_REL
RULES_REL = structural_mod.DEFAULT_RULES_REL
RECALL_FLOOR = 0.7          # C5：整体召回低于此值必须在边界声明里写「检出能力不足」
PER_CLASS_FLOOR = 0.6       # E4 触发阈值
FP_BUDGET_PP = 2.0          # E4：加固后负样本误报上升上限（百分点）
TOP_MISSED = 20


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# 度量
# --------------------------------------------------------------------------- #
def evaluate(samples: Sequence[Dict[str, Any]], judge) -> Dict[str, Any]:
    """按类别统计 recall / fp_rate / precision / n，并给出混淆矩阵四格。"""

    per_class: Dict[str, Dict[str, int]] = {}
    rows: List[Dict[str, Any]] = []
    for sample in samples:
        verdict = judge(sample)
        rows.append({"sample": sample, "verdict": verdict, "caught": verdict == "FAIL"})
        bucket = per_class.setdefault(sample["class"], {"tp": 0, "fn": 0, "fp": 0, "tn": 0})
        if sample["label"] == "positive":
            bucket["tp" if verdict == "FAIL" else "fn"] += 1
        else:
            bucket["fp" if verdict == "FAIL" else "tn"] += 1

    table: Dict[str, Dict[str, Any]] = {}
    tot = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for cls, b in per_class.items():
        tot = {k: tot[k] + b[k] for k in tot}
        pos, neg = b["tp"] + b["fn"], b["fp"] + b["tn"]
        table[cls] = {
            "n": pos + neg,
            "positives": pos,
            "negatives": neg,
            "tp": b["tp"], "fn": b["fn"], "fp": b["fp"], "tn": b["tn"],
            "recall": round(b["tp"] / pos, 4) if pos else None,
            "fp_rate": round(b["fp"] / neg, 4) if neg else None,
            "precision": round(b["tp"] / (b["tp"] + b["fp"]), 4) if (b["tp"] + b["fp"]) else None,
        }
    pos_all, neg_all = tot["tp"] + tot["fn"], tot["fp"] + tot["tn"]
    overall = {
        "n": pos_all + neg_all,
        "positives": pos_all,
        "negatives": neg_all,
        **tot,
        "recall": round(tot["tp"] / pos_all, 4) if pos_all else None,
        "fp_rate": round(tot["fp"] / neg_all, 4) if neg_all else None,
        "precision": round(tot["tp"] / (tot["tp"] + tot["fp"]), 4) if (tot["tp"] + tot["fp"]) else None,
    }
    return {"per_class": table, "overall": overall, "rows": rows}


def missed_list(rows: Sequence[Dict[str, Any]], limit: int = TOP_MISSED) -> List[Dict[str, Any]]:
    """漏检清单（判据 C3）：只列正样本里被判绿的那些，逐条摘录。"""

    out: List[Dict[str, Any]] = []
    for row in rows:
        sample = row["sample"]
        if sample["label"] != "positive" or row["caught"]:
            continue
        out.append({
            "id": sample["id"], "class": sample["class"], "variant": sample["variant"],
            "mechanism": sample["mechanism"], "remediable_by": sample["remediable_by"],
            "path": sample["path"],
            "line": sample["lines"][0][:200] if len(sample["lines"]) == 1 else
                    "\\n".join(sample["lines"])[:200],
            "provenance": sample["provenance"],
        })
    return out[:limit]


def mechanism_table(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """机理分布（E3）：正样本按 (class, mechanism) 聚合，给出漏检占比与可救性。"""

    agg: Dict[Tuple[str, str, str], Dict[str, int]] = {}
    for row in rows:
        s = row["sample"]
        if s["label"] != "positive":
            continue
        key = (s["class"], s["mechanism"], s["remediable_by"])
        agg.setdefault(key, {"n": 0, "caught": 0, "missed": 0})
        agg[key]["n"] += 1
        agg[key]["caught" if row["caught"] else "missed"] += 1
    table = []
    for (cls, mech, remedy), v in sorted(agg.items()):
        table.append({
            "class": cls, "mechanism": mech, "remediable_by": remedy,
            "n": v["n"], "caught": v["caught"], "missed": v["missed"],
            "recall": round(v["caught"] / v["n"], 4),
        })
    return table


def attribution_check(samples: Sequence[Dict[str, Any]],
                      base_rows: Sequence[Dict[str, Any]],
                      hard_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """设计预测 vs 实测的一致性：验证「机理归因」不是事后编故事。

    每条正样本在生成期都带一个 ``predicted``（对现行门禁判决的预测）。高一致性说明
    语料的机理标签可信；不一致项本身是发现（预测会拦却漏了 = 我低估了词表覆盖）。
    """

    by_id = {row["sample"]["id"]: row for row in base_rows}
    hard_by_id = {row["sample"]["id"]: row for row in hard_rows}
    agree, total, examples = 0, 0, []
    for sample in samples:
        if sample["label"] != "positive":
            continue
        total += 1
        got = "FAIL" if by_id[sample["id"]]["caught"] else "PASS"
        if got == sample["predicted"]:
            agree += 1
        elif len(examples) < 8:
            examples.append({"id": sample["id"], "class": sample["class"],
                             "predicted": sample["predicted"], "got": got,
                             "line": sample["lines"][0][:120]})
    hardened_agree = sum(
        1 for s2 in samples
        if s2["label"] == "positive" and s2["predicted"] == "PASS"
        and hard_by_id[s2["id"]]["caught"]
    )
    return {
        "baseline_agreement": round(agree / total, 4) if total else None,
        "positives": total,
        "mismatches": examples,
        "predicted_miss_recovered_by_hardening": hardened_agree,
        "note": "predicted 是生成期对现行门禁的判决预测；基线一致率高说明机理标签可信，"
                "不一致项与『加固救回数』共同界定结构性判据的真实增益。",
    }


def holdout_measure(seeds_path: Path, lexicon_path: Path,
                    rules: "structural_mod.StructuralRules", seed: int) -> Dict[str, Any]:
    """**留出语料**：换一个种子（同模板、不同槽位/路径/遮蔽形态）重新生成，重跑两套门禁。

    结构性判据是在主语料上看过结果后才定稿的（E4 允许的加固-重测回路），因此主语料的
    加固召回是**样本内**数字；留出语料给的是不重新调参的**样本外**数字。
    """

    lex = gate.Lexicon.load(lexicon_path)
    alt = corpus_mod.build_corpus(seeds_path, lexicon_path, seed)
    base = evaluate(alt["samples"], lambda s: gate.scan_diff(corpus_mod.build_diff(s), lex).verdict)
    hard = evaluate(alt["samples"],
                    lambda s: structural_mod.scan_diff_structural(corpus_mod.build_diff(s), lex, rules)["verdict"])
    return {
        "seed": seed,
        "corpus_sha256": alt["corpus_sha256"],
        "counts": alt["counts"],
        "baseline": {"per_class": base["per_class"], "overall": base["overall"]},
        "hardened": {"per_class": hard["per_class"], "overall": hard["overall"]},
        "note": "留出语料不参与任何判据调参；其召回是加固判据的样本外估计。",
    }


# --------------------------------------------------------------------------- #
# 词表扩张留出探针
# --------------------------------------------------------------------------- #
def _expanded_lexicon(lexicon_path: Path, seeds: Dict[str, Any]) -> Tuple[gate.Lexicon, Dict[str, str], Path]:
    """把同义映射与外文映射的**去重形态**按字典序奇偶切成扩张集/留出集，只把扩张集写进临时词表。

    注意：同一形态可能在不同映射项下重复出现（例如某外文词同时是同义/外文映射的取值），
    因此先对全部形态去重建一张唯一判决表，再据此构词表——避免「记成留出但实际已进词表」
    的错配把留出集召回虚高。
    """

    raw = json.loads(lexicon_path.read_text(encoding="utf-8"))
    forms: List[Tuple[str, str]] = []
    for group, key in (("paraphrase", "map"), ("foreign", "map")):
        for value, val in sorted(seeds[group][key].items()):
            for form in (val if isinstance(val, list) else [val]):
                forms.append((group, form))
    unique = sorted({(group, form) for group, form in forms})
    uptake: Dict[str, str] = {}
    added: List[Dict[str, Any]] = []
    for idx, (group, form) in enumerate(unique):
        side = "expanded" if idx % 2 == 0 else "held_out"
        uptake[form] = side
        if side == "expanded":
            added.append({"id": f"vocab_probe_{idx:03d}", "kind": "word", "values": [form],
                          "gloss": f"留出法探针：{group} 扩张词（数据层，非结构性判据）"})
    raw["terms"] = list(raw["terms"]) + added
    raw["version"] = f"{raw.get('version', '?')}+vocab-probe"
    tmpdir = Path(tempfile.mkdtemp(prefix="tp_boundary_vocab_probe_"))
    path = tmpdir / "probe_lexicon.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return gate.Lexicon.load(path), uptake, path


def _form_side(sample: Dict[str, Any], uptake: Dict[str, str]) -> Optional[str]:
    text = "\n".join(sample["lines"])
    for form, side in uptake.items():
        if form and form in text:
            return side
    return None


def vocab_probe(samples: Sequence[Dict[str, Any]], lexicon_path: Path, seeds: Dict[str, Any]) -> Dict[str, Any]:
    lex, uptake, path = _expanded_lexicon(lexicon_path, seeds)

    def judge(sample: Dict[str, Any]) -> str:
        return gate.scan_diff(corpus_mod.build_diff(sample), lex).verdict

    out: Dict[str, Any] = {"probe_lexicon_sha256": _sha256(path),
                           "method": "同义/外文映射的**去重形态**按字典序奇偶切半：偶数项写进临时词表（扩张集），奇数项留出；只统计词表覆盖度型样本（明文命中样本排除）",
                           "per_class": {}}
    for cls in ("synonym_rewrite", "cross_language"):
        buckets = {"expanded": {"n": 0, "caught": 0}, "held_out": {"n": 0, "caught": 0},
                   "unlabelled": {"n": 0, "caught": 0}}
        for sample in samples:
            if sample["class"] != cls or sample["label"] != "positive":
                continue
            if sample["mechanism"].startswith("in_vocabulary"):
                continue  # 明文命中样本不参与词表覆盖度探针
            side = _form_side(sample, uptake) or "unlabelled"
            buckets[side]["n"] += 1
            if judge(sample) == "FAIL":
                buckets[side]["caught"] += 1
        out["per_class"][cls] = {
            side: {"n": v["n"], "caught": v["caught"],
                   "recall": round(v["caught"] / v["n"], 4) if v["n"] else None}
            for side, v in buckets.items()
        }
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def measure(corpus_path: Path = REPO_ROOT / CORPUS_REL,
            seeds_path: Path = REPO_ROOT / SEEDS_REL,
            lexicon_path: Path = REPO_ROOT / LEXICON_REL,
            rules_path: Path = REPO_ROOT / RULES_REL) -> Dict[str, Any]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
    lexicon = gate.Lexicon.load(lexicon_path)
    rules = structural_mod.StructuralRules.load(rules_path)
    samples = corpus["samples"]

    # ---- C4 自伤控制：已知必拦样本必须判红 ---------------------------------
    control = corpus["control"]
    control_sample = {"id": control["id"], "class": "control", "label": "positive",
                      "variant": "plain", "mechanism": "in_vocabulary",
                      "remediable_by": "base", "path": control["path"],
                      "lines": control["lines"], "provenance": "control"}
    control_verdict = gate.scan_diff(corpus_mod.build_diff(control_sample), lexicon).verdict
    control_block = {"id": control["id"], "expect": control["expect"], "verdict": control_verdict,
                     "ok": control_verdict == control["expect"], "line": control["lines"][0]}
    if not control_block["ok"]:
        raise SystemExit(f"C4 self-injury control violated: expected {control['expect']} got "
                         f"{control_verdict} — measurement chain is broken, refusing to emit numbers")

    baseline = evaluate(samples, lambda s: gate.scan_diff(corpus_mod.build_diff(s), lexicon).verdict)
    hardened_rows = evaluate(
        samples,
        lambda s: structural_mod.scan_diff_structural(corpus_mod.build_diff(s), lexicon, rules)["verdict"],
    )
    hardened = {"per_class": hardened_rows["per_class"], "overall": hardened_rows["overall"],
                "rows": hardened_rows["rows"]}

    fp_delta_pp = round((hardened["overall"]["fp_rate"] - baseline["overall"]["fp_rate"]) * 100, 2)
    recall_delta = round(hardened["overall"]["recall"] - baseline["overall"]["recall"], 4)

    acceptance = {
        "note": "留出语料数字见 holdout 段（不参与调参的样本外估计）",
        "C1_corpus_reproducible": None,   # 由 --check / pytest 证明，这里记录计数与哈希
        "C1_counts": corpus["counts"],
        "C1_quota": corpus["quota"],
        "C1_quota_ok": (
            corpus["counts"]["total"] >= corpus["quota"]["total_min"]
            and all(v["positive"] >= corpus["quota"]["per_class_pos_min"]
                    and v["negative"] >= corpus["quota"]["per_class_neg_min"]
                    for v in corpus["counts"]["by_class"].values())
        ),
        "C2_metrics_schema_ok": True,
        "C3_missed_list_len": len(missed_list(baseline["rows"])),
        "C4_control_ok": control_block["ok"],
        "C5_overall_recall": baseline["overall"]["recall"],
        "C5_honest_statement_required": baseline["overall"]["recall"] < RECALL_FLOOR,
        "E4_classes_below_floor": sorted(
            cls for cls, v in baseline["per_class"].items() if (v["recall"] or 0) < PER_CLASS_FLOOR),
        "E4_classes_below_floor_after_hardening": sorted(
            cls for cls, v in hardened["per_class"].items() if (v["recall"] or 0) < PER_CLASS_FLOOR),
        "E4_fp_delta_pp": fp_delta_pp,
        "E4_fp_budget_pp": FP_BUDGET_PP,
        "E4_fp_budget_ok": fp_delta_pp <= FP_BUDGET_PP,
        "E4_recall_delta": recall_delta,
        "E4_holdout_overall_recall_baseline": None,
        "E4_holdout_overall_recall_hardened": None,
    }

    attribution = attribution_check(samples, baseline["rows"], hardened_rows["rows"])
    holdout = holdout_measure(seeds_path, lexicon_path, rules, seed=corpus["seed"] + 1)

    acceptance["E4_holdout_overall_recall_baseline"] = holdout["baseline"]["overall"]["recall"]
    acceptance["E4_holdout_overall_recall_hardened"] = holdout["hardened"]["overall"]["recall"]
    acceptance["E4_holdout_fp_rate_hardened"] = holdout["hardened"]["overall"]["fp_rate"]

    return {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "corpus": {
            "path": str(corpus_path.relative_to(REPO_ROOT)) if corpus_path.is_absolute() else str(corpus_path),
            "sha256": corpus["corpus_sha256"], "seed": corpus["seed"],
            "counts": corpus["counts"], "quota": corpus["quota"],
        },
        "gates": {
            "baseline": {"module": "tools/boundary/pr_gate_scan.py",
                         "sha256": _sha256(REPO_ROOT / "tools/boundary/pr_gate_scan.py"),
                         "lexicon": {"path": lexicon.path, "sha256": lexicon.sha256,
                                     "version": lexicon.version}},
            "hardened": {"module": "tools/boundary/structural_gate.py",
                         "sha256": _sha256(REPO_ROOT / "tools/boundary/structural_gate.py"),
                         "rules": rules.describe(), "rules_sha256": _sha256(rules_path)},
        },
        "baseline": {"per_class": baseline["per_class"], "overall": baseline["overall"],
                     "missed": missed_list(baseline["rows"]),
                     "mechanism_table": mechanism_table(baseline["rows"])},
        "hardened": {"per_class": hardened["per_class"], "overall": hardened["overall"],
                     "missed": missed_list(hardened_rows["rows"]),
                     "mechanism_table": mechanism_table(hardened_rows["rows"])},
        "attribution_check": attribution,
        "holdout": holdout,
        "vocab_probe": vocab_probe(samples, lexicon_path, seeds),
        "control": control_block,
        "acceptance": acceptance,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="边界门对抗度量（召回/误报/漏检/机理）")
    ap.add_argument("--corpus", default=str(REPO_ROOT / CORPUS_REL))
    ap.add_argument("--seeds", default=str(REPO_ROOT / SEEDS_REL))
    ap.add_argument("--lexicon", default=str(REPO_ROOT / LEXICON_REL))
    ap.add_argument("--rules", default=str(REPO_ROOT / RULES_REL))
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts/boundary/adversarial_metrics.json"))
    ap.add_argument("--check", action="store_true", help="与已落盘 metrics 比对（复现性自检）")
    args = ap.parse_args(argv)

    result = measure(Path(args.corpus), Path(args.seeds), Path(args.lexicon), Path(args.rules))
    body = {k: v for k, v in result.items() if k != "generated_at"}
    if args.check:
        existing = json.loads(Path(args.out).read_text(encoding="utf-8"))
        same = {k: v for k, v in existing.items() if k != "generated_at"} == body
        print(json.dumps({"stable": same, "out": args.out}, ensure_ascii=False))
        return 0 if same else 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "baseline_overall": result["baseline"]["overall"],
        "hardened_overall": result["hardened"]["overall"],
        "acceptance": result["acceptance"],
        "out": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())