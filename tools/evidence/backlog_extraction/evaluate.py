"""金标评测：行级召回 / 误报率 / 根因 macro-F1 + 近重去重对准确率。

判据（任务书机读口径）
----------------------
* 行级召回 ``recall`` ≥ 0.90
* 行级误报率 ``false_positive_rate`` = 提取行中不在金标 debt 的比例 ≤ 0.10
* 根因类 ``macro_f1``（4 类）≥ 0.80
* 去重金标 40 对准确率 ≥ 0.90

匹配口径：**行级**——``source.file`` 主干前缀 + 物理行号（``loc``），不做模糊对齐。
一条报告行若在提取结果里展开成多条（多检查点），只要其中任一条与金标根因一致即计命中
（行级判定），同时另报「展开行级」根因一致率作为严格口径。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

# 调参集（诚实披露）：提取器规则是在这些文件上反复修出来的；它们**不计入** holdout 指标。
TUNED_FILE_STEMS = ("r6525", "r6550", "r6521", "r5760", "r6210", "r6231", "r5779")

GATES = {
    "recall_min": 0.90,
    "false_positive_rate_max": 0.10,
    "macro_f1_min": 0.80,
    "dedup_pair_accuracy_min": 0.90,
}
ROOT_CAUSES = (
    "reference_limited",
    "discretization_resolution",
    "implementation_defect",
    "reference_missing",
)


def loc_key(source_file: str, line: int) -> str:
    """``reports/r6242_m5347_...md``:212 → ``r6242:212``；pending 用文件名主干。"""
    name = Path(source_file).name
    if "__" in name:
        stem = name.rsplit(".", 1)[0]
    else:
        stem = name.split("_")[0]
    return f"{stem}:{line}"


def norm_loc(loc: str) -> str:
    """金标 loc 兼容写法：``完整文件名:行`` → ``短键:行``（与 ``loc_key`` 同规则）。"""
    if ".md:" in loc:
        file, _, line = loc.rpartition(":")
        if line.isdigit():
            return loc_key(file, int(line))
    return loc


def load_gold(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_backlog(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def evaluate(backlog: list[dict], gold: dict[str, Any]) -> dict[str, Any]:
    debt = {norm_loc(item["loc"]): item for item in gold.get("debt", [])}
    ok = {norm_loc(item["loc"]): item for item in gold.get("ok", [])}

    extracted_lines = {loc_key(r["source"]["file"], r["source"]["line"]) for r in backlog}
    hit = sorted(extracted_lines & set(debt))
    missed = sorted(set(debt) - extracted_lines)
    fps = sorted(extracted_lines - set(debt) - set(ok))
    ok_extracted = sorted(extracted_lines & set(ok))

    recall = len(hit) / len(debt) if debt else 0.0
    fp_rate = (len(fps) + len(ok_extracted)) / len(extracted_lines) if extracted_lines else 0.0

    # 根因：行级（多展开行时取任一匹配）
    by_line: dict[str, list[str]] = defaultdict(list)
    for row in backlog:
        by_line[loc_key(row["source"]["file"], row["source"]["line"])].append(row["rootcause"])
    line_match = {}
    root_errors = []
    for loc, item in debt.items():
        preds = by_line.get(loc, [])
        if not preds:
            continue
        if item["rootcause"] in preds:
            line_match[loc] = item["rootcause"]
        else:
            line_match[loc] = preds[0]
            root_errors.append({"loc": loc, "gold": item["rootcause"], "pred": preds[0]})

    tp = Counter()
    fp = Counter()
    fn = Counter()
    for loc, gold_label in line_match.items():
        pred = line_match[loc]
        if pred == gold_label:
            tp[gold_label] += 1
        else:
            fp[pred] += 1
            fn[gold_label] += 1
    per_class = {}
    for cls in ROOT_CAUSES:
        p = tp[cls] / (tp[cls] + fp[cls]) if (tp[cls] + fp[cls]) else 0.0
        r = tp[cls] / (tp[cls] + fn[cls]) if (tp[cls] + fn[cls]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        per_class[cls] = {
            "support": sum(1 for v in debt.values() if v["rootcause"] == cls),
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "tp": tp[cls],
            "fp": fp[cls],
            "fn": fn[cls],
        }
    macro_f1 = sum(v["f1"] for v in per_class.values()) / len(per_class)

    # 严格口径（逐提取行）：多检查点展开行各自与金标行级根因比对，不做「任一命中即算对」
    strict_tp, strict_fp, strict_fn = Counter(), Counter(), Counter()
    for row in backlog:
        loc = loc_key(row["source"]["file"], row["source"]["line"])
        if loc not in debt:
            continue
        g, pr = debt[loc]["rootcause"], row["rootcause"]
        if g == pr:
            strict_tp[g] += 1
        else:
            strict_fp[pr] += 1
            strict_fn[g] += 1
    strict = {}
    for cls in ROOT_CAUSES:
        p_ = strict_tp[cls] / (strict_tp[cls] + strict_fp[cls]) if (strict_tp[cls] + strict_fp[cls]) else 0.0
        r_ = strict_tp[cls] / (strict_tp[cls] + strict_fn[cls]) if (strict_tp[cls] + strict_fn[cls]) else 0.0
        strict[cls] = {
            "precision": round(p_, 4),
            "recall": round(r_, 4),
            "f1": round(2 * p_ * r_ / (p_ + r_), 4) if (p_ + r_) else 0.0,
            "tp": strict_tp[cls],
            "fp": strict_fp[cls],
            "fn": strict_fn[cls],
        }
    macro_f1_strict = sum(v["f1"] for v in strict.values()) / len(strict)

    # 去重金标对
    pairs = gold.get("dedup_pairs") or []
    pair_correct = 0
    pair_detail = []
    preds = _pair_predictions(backlog)
    for pair in pairs:
        key = (norm_loc(pair["a"]), norm_loc(pair["b"]))
        pred = preds.get(key, preds.get((pair["b"], pair["a"])))
        ok_pair = (pred is not None) and (pred == pair["duplicate"])
        pair_correct += int(ok_pair)
        pair_detail.append(
            {"a": pair["a"], "b": pair["b"], "gold": pair["duplicate"], "pred": pred, "ok": ok_pair}
        )
    pair_acc = pair_correct / len(pairs) if pairs else None

    holdout = _holdout_metrics(backlog, debt)
    result = {
        "gates": GATES,
        "holdout": holdout,
        "row_level": {
            "gold_debt": len(debt),
            "gold_ok": len(ok),
            "extracted_lines": len(extracted_lines),
            "hit": len(hit),
            "missed": missed,
            "false_positives": fps,
            "ok_flagged": ok_extracted,
            "recall": round(recall, 4),
            "false_positive_rate": round(fp_rate, 4),
        },
        "rootcause": {
            "per_class": per_class,
            "macro_f1": round(macro_f1, 4),
            "granularity": "line（同一行多个检查点展开时任一命中即计对，对应任务书『行级』口径）",
            "macro_f1_strict": round(macro_f1_strict, 4),
            "strict_per_class": strict,
            "mismatches": root_errors,
        },
        "dedup": {
            "n_pairs": len(pairs),
            "accuracy": None if pair_acc is None else round(pair_acc, 4),
            "detail": pair_detail,
        },
    }
    result["acceptance"] = acceptance(result)
    return result


def _pair_predictions(backlog: list[dict]) -> dict[tuple[str, str], bool]:
    """行 × 行近重预测：直接调 ``dedup.compare``（与产线同一判据，不另立标准）。

    对「与战役节点/提案池」的判定不在这里测——那部分由 ``dedup_report.json`` 单列，
    40 对金标测的是**欠账之间的近重识别**（同一 FAIL 被多份报告重复记录的情形）。
    """
    from . import dedup as dd
    from .build import _row_from_payload

    rows = [_row_from_payload(p) for p in backlog]

    def key(row) -> str:
        return f"{Path(row.source.file).name}:{row.source.line}"

    out: dict[tuple[str, str], bool] = {}
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            res = dd.compare(a, dd.Target.from_row(b))
            dup = res["decision"] in ("duplicate", "already_delivered")
            out[(key(a), key(b))] = dup
            out[(key(b), key(a))] = dup
    return out


def _holdout_metrics(backlog: list[dict], debt: dict[str, Any]) -> dict[str, Any]:
    """剔除调参集文件后的独立评估（gold 与提取器互不重叠的部分）。"""
    def stem(loc: str) -> str:
        return loc.split(":")[0].split("_")[0].replace("TensorLBM__terminal_verdict_", "")

    ho_debt = {k: v for k, v in debt.items() if stem(k) not in TUNED_FILE_STEMS and stem(k) != "report"}
    ho_rows = [
        r
        for r in backlog
        if loc_key(r["source"]["file"], r["source"]["line"]) in ho_debt
        or stem(loc_key(r["source"]["file"], r["source"]["line"])) not in TUNED_FILE_STEMS
    ]
    extracted = {loc_key(r["source"]["file"], r["source"]["line"]) for r in ho_rows}
    hit = extracted & set(ho_debt)
    fp = extracted - set(ho_debt)
    mismatch = 0
    for r in ho_rows:
        loc = loc_key(r["source"]["file"], r["source"]["line"])
        if loc in ho_debt and r["rootcause"] != ho_debt[loc]["rootcause"]:
            mismatch += 1
    return {
        "excluded_stems": list(TUNED_FILE_STEMS),
        "gold_debt": len(ho_debt),
        "hit": len(hit),
        "recall": round(len(hit) / len(ho_debt), 4) if ho_debt else None,
        "false_positive_rate": round(len(fp) / len(extracted), 4) if extracted else None,
        "rootcause_mismatches": mismatch,
    }


def acceptance(result: dict[str, Any]) -> dict[str, Any]:
    rl = result["row_level"]
    dup = result["dedup"]
    checks = {
        "recall>=0.90": rl["recall"] >= GATES["recall_min"],
        "false_positive_rate<=0.10": rl["false_positive_rate"] <= GATES["false_positive_rate_max"],
        "rootcause_macro_f1>=0.80": result["rootcause"]["macro_f1"] >= GATES["macro_f1_min"],
        "dedup_pair_accuracy>=0.90": (
            dup["accuracy"] is not None and dup["accuracy"] >= GATES["dedup_pair_accuracy_min"]
        ),
        "gold_debt>=60": rl["gold_debt"] >= 60,
    }
    return {"checks": checks, "all_pass": all(checks.values())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="evaluate backlog against gold annotations")
    ap.add_argument("--backlog", default="artifacts/backlog_extraction/backlog.jsonl")
    ap.add_argument("--gold", default="artifacts/backlog_extraction/gold.json")
    ap.add_argument("--out", default="artifacts/backlog_extraction/eval.json")
    args = ap.parse_args(argv)
    result = evaluate(load_backlog(args.backlog), load_gold(args.gold))
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    rl = result["row_level"]
    print(
        f"[eval] recall={rl['recall']} fpr={rl['false_positive_rate']} "
        f"macro_f1={result['rootcause']['macro_f1']} pass={result['acceptance']['all_pass']}",
        file=sys.stderr,
    )
    return 0 if result["acceptance"]["all_pass"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())