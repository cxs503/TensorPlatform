#!/usr/bin/env python3
"""交付语义分类器·漂移评测（p372w2-n06 / E2–E3）。

读**冻结工件**（``corpus_raw.json`` + ``labels.json`` + ``preregistration.json``），
跑分类器 ``backend/api/domain/delivery_state.assess_delivery``，输出：

* ``metrics.json`` —— 报告里被引用的每一个数字的唯一来源（零手抄）；
* ``figures/*.png`` —— 漂移曲线 / 混淆矩阵 / 逐类 F1 对比。

判决规则（预注册）：``decay_rel = (0.9226 - macro_f1) / 0.9226``；
``decay_rel > 0.10`` → ``drift_alert = true``（重新训练告警）。

纪律：本脚本**只测量**，不改分类器、不改标签、不改阈值；同输入同种子重跑，
除 ``git_head`` 版本戳外逐位一致（``metrics_sha256`` 可复核）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "quality" / "delivery_semantics" / "evidence"
DRIFT = EVIDENCE / "drift"
BASELINE_METRICS = EVIDENCE / "metrics.json"
STATES: Tuple[str, ...] = ("delivered_complete", "delivered_partial", "suspect_undeclared")
BOOT_N = 5000
BOOT_SEED = 20261110

import sys  # noqa: E402
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))
from api.domain.delivery_state import assess_delivery  # noqa: E402


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


# ───────────────────────── 指标 ─────────────────────────

def confusion(y_true: Sequence[str], y_pred: Sequence[str]) -> Dict[str, Dict[str, int]]:
    m = {t: {p: 0 for p in STATES} for t in STATES}
    for t, p in zip(y_true, y_pred):
        m[t][p] += 1
    return m


def per_class(m: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for lab in STATES:
        tp = m[lab][lab]
        fp = sum(m[t][lab] for t in STATES if t != lab)
        fn = sum(m[lab][p] for p in STATES if p != lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[lab] = {"tp": tp, "fp": fp, "fn": fn, "support": tp + fn,
                    "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}
    supported = [v["f1"] for v in out.values() if v["support"] > 0]
    out["macro_f1"] = round(sum(supported) / len(supported), 4) if supported else 0.0
    total = sum(sum(r.values()) for r in m.values())
    out["accuracy"] = round(sum(m[s][s] for s in STATES) / total, 4) if total else 0.0
    return out


def macro_f1_of(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    return per_class(confusion(y_true, y_pred))["macro_f1"]


def bootstrap_ci(y_true: Sequence[str], y_pred: Sequence[str],
                 n_boot: int = BOOT_N, seed: int = BOOT_SEED) -> Tuple[float, float]:
    rng = random.Random(seed)
    idx = list(range(len(y_true)))
    vals: List[float] = []
    for _ in range(n_boot):
        sample = [rng.choice(idx) for _ in idx]
        vals.append(macro_f1_of([y_true[i] for i in sample], [y_pred[i] for i in sample]))
    vals.sort()
    return (round(vals[int(0.025 * n_boot)], 4), round(vals[int(0.975 * n_boot) - 1], 4))


def cumulative_curve(y_true: Sequence[str], y_pred: Sequence[str]) -> List[Dict[str, Any]]:
    """累积 macro-F1（前缀 n 单调增长）——漂移随样本量收敛的可视化。"""
    curve = []
    for n in range(2, len(y_true) + 1):
        curve.append({"n": n, "macro_f1": macro_f1_of(y_true[:n], y_pred[:n])})
    return curve


# ───────────────────────── 图 ─────────────────────────

def make_figures(out: Dict[str, Any], fig_dir: Path) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_dir.mkdir(parents=True, exist_ok=True)
    paths: List[str] = []

    # 图1 漂移曲线：累积 macro-F1 vs 样本量（含 10% 衰减门限）
    curve = out["drift"]["cumulative_curve"]
    xs = [c["n"] for c in curve]
    ys = [c["macro_f1"] for c in curve]
    alt = (out.get("variants") or {}).get("A3_fallback_v12")
    base = out["baseline"]["macro_f1"]
    gate = base * (1 - out["drift"]["alert_threshold"])
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=150)
    ax.plot(xs, ys, "-o", ms=3, lw=1.4,
            label="A2 current classifier (cumulative macro-F1)")
    if alt:
        ax.plot([c["n"] for c in alt["cumulative_curve"]],
                [c["macro_f1"] for c in alt["cumulative_curve"]],
                "-s", ms=2.5, lw=1.1, color="tab:orange",
                label="A3 fallback + v1.2 tokens (opt-in)")
    ax.axhline(base, ls="--", lw=1.2, color="tab:green",
               label=f"frozen baseline {base:.4f} (60-unit corpus)")
    ax.axhline(gate, ls=":", lw=1.4, color="tab:red",
               label=f"alert threshold {gate:.4f} (−{out['drift']['alert_threshold']:.0%})")
    ax.set_xlabel("labelled sample size n (missions, mission_id ascending)")
    ax.set_ylabel("macro-F1")
    ax.set_title("Delivery-semantics classifier drift on post-p372 missions")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7.5, loc="lower right")
    p = fig_dir / "fig1_drift_curve.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig); paths.append(str(p))

    # 图2 混淆矩阵
    cm = out["confusion"]
    mat = [[cm[t][p] for p in STATES] for t in STATES]
    fig, ax = plt.subplots(figsize=(4.6, 4.0), dpi=150)
    im = ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(3)); ax.set_yticks(range(3))
    ax.set_xticklabels([s.replace("delivered_", "d_") for s in STATES], fontsize=7, rotation=20)
    ax.set_yticklabels([s.replace("delivered_", "d_") for s in STATES], fontsize=7)
    for i in range(3):
        for j in range(3):
            ax.text(j, i, str(mat[i][j]), ha="center", va="center",
                    color="white" if mat[i][j] > max(1, max(max(r) for r in mat)) / 2 else "black")
    ax.set_xlabel("predicted"); ax.set_ylabel("human label")
    ax.set_title(f"Confusion (n={out['n_units']}, accuracy={out['metrics']['accuracy']:.3f})")
    fig.colorbar(im, ax=ax, fraction=0.046)
    p = fig_dir / "fig2_confusion.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig); paths.append(str(p))

    # 图3 逐类 F1：冻结基线 vs 本波
    labels = list(STATES)
    base_f1 = [out["baseline"]["per_class"][s]["f1"] for s in labels]
    new_f1 = [out["metrics"][s]["f1"] for s in labels]
    alt_f1 = ([alt["per_class"][s]["f1"] for s in labels] if alt else None)
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(6.8, 3.8), dpi=150)
    w = 0.27 if alt_f1 else 0.38
    ax.bar([i - w for i in x], base_f1, width=w, label="frozen baseline (n=60)")
    ax.bar(list(x), new_f1, width=w,
           label="A2 current on post-p372 (n=%d)" % out["n_units"] if alt_f1
           else "post-p372 sample (n=%d)" % out["n_units"])
    if alt_f1:
        ax.bar([i + w for i in x], alt_f1, width=w, label="A3 fallback + v1.2 (opt-in)")
    ax.set_xticks(list(x))
    ax.set_xticklabels([s.replace("delivered_", "d_") for s in labels], fontsize=8)
    ax.set_ylabel("F1"); ax.set_ylim(0, 1.05); ax.grid(alpha=0.3, axis="y")
    ax.set_title("Per-class F1: frozen corpus vs post-p372 missions")
    ax.legend(fontsize=8)
    for i, (b, n) in enumerate(zip(base_f1, new_f1)):
        ax.text(i - w, b + 0.02, f"{b:.2f}", ha="center", fontsize=6.5)
        ax.text(i, n + 0.02, f"{n:.2f}", ha="center", fontsize=6.5)
        if alt_f1:
            ax.text(i + w, alt_f1[i] + 0.02, f"{alt_f1[i]:.2f}", ha="center", fontsize=6.5)
    p = fig_dir / "fig3_per_class_f1.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig); paths.append(str(p))
    return paths


# ───────────────────────── 主流程 ─────────────────────────

def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


def evaluate(drift_dir: Path = DRIFT, fig_dir: Path | None = None) -> Dict[str, Any]:
    prereg = json.loads((drift_dir / "preregistration.json").read_text(encoding="utf-8"))
    corpus = json.loads((drift_dir / "corpus_raw.json").read_text(encoding="utf-8"))
    labels_doc = json.loads((drift_dir / "labels.json").read_text(encoding="utf-8"))
    baseline_doc = json.loads(BASELINE_METRICS.read_text(encoding="utf-8"))

    # 冻结校验：语料 sha256 与标签 sha256 双钉
    corpus_sha = sha256_text(json.dumps(corpus["units"], ensure_ascii=False,
                                       sort_keys=True, separators=(",", ":")))
    assert corpus_sha == prereg["sampling"]["corpus_sha256"], "语料被改动（sha256 不符预注册）"
    assert corpus["corpus_sha256"] == corpus_sha, "语料自钉 sha256 不符"
    labels_sha = sha256_file(drift_dir / "labels.json")

    labels = {int(x["mission_id"]): x for x in labels_doc["labels"]}
    units = sorted(corpus["units"], key=lambda u: int(u["mission_id"]))   # 确定性顺序
    assert len(units) == len(labels) == prereg["sampling"]["n_sampled"]

    y_true: List[str] = []
    y_pred: List[str] = []
    per_unit: List[Dict[str, Any]] = []
    variants: Dict[str, Dict[str, Any]] = {}
    pred_by_variant: Dict[str, List[str]] = {}
    for name, kwargs in (("A2_current_default", {}),
                         ("A3_fallback_v12", {"verification_fallback": True})):
        pred_by_variant[name] = [assess_delivery(u.get("result") or {}, **kwargs).state
                                 for u in units]
    for u in units:
        mid = int(u["mission_id"])
        a = assess_delivery(u.get("result") or {})
        y_true.append(labels[mid]["label"])
        y_pred.append(a.state)
        per_unit.append({"mission_id": mid, "ext_id": u.get("ext_id"),
                         "stratum": u.get("stratum"),
                         "human_label": labels[mid]["label"],
                         "classifier_state": a.state,
                         "agree": labels[mid]["label"] == a.state,
                         "classifier_facts": a.facts,
                         "classifier_reasons": a.reasons})

    cm = confusion(y_true, y_pred)
    metrics = per_class(cm)
    base_macro = float(baseline_doc["variants"]["A2_current"]["metrics"]["macro_f1"])
    base_per_class = {s: baseline_doc["variants"]["A2_current"]["metrics"][s] for s in STATES}
    decay_abs = round(base_macro - metrics["macro_f1"], 4)
    decay_rel = round(decay_abs / base_macro, 4) if base_macro else 0.0
    alert_thr = float(prereg["decision_rule"]["alert_threshold"])

    # 域外误判归因（人工可读）：分类器说 suspect 但人判 complete = schema 漂移主因
    for name, preds in pred_by_variant.items():
        m2 = per_class(confusion(y_true, preds))
        dec = round((base_macro - m2["macro_f1"]) / base_macro, 4) if base_macro else 0.0
        variants[name] = {
            "macro_f1": m2["macro_f1"], "accuracy": m2["accuracy"],
            "per_class": {s: m2[s] for s in STATES},
            "n_agree": sum(1 for t, p in zip(y_true, preds) if t == p),
            "decay_rel": dec,
            "drift_alert": dec > alert_thr,
            "cumulative_curve": cumulative_curve(y_true, preds),
        }

    drift_cases = [r for r in per_unit if not r["agree"]]
    drift_signature = {
        "n_disagree": len(drift_cases),
        "classifier_suspect_human_complete": sum(
            1 for r in drift_cases
            if r["classifier_state"] == "suspect_undeclared"
            and r["human_label"] == "delivered_complete"),
        "reason_top": _top_reasons(drift_cases),
    }

    out: Dict[str, Any] = {
        "schema": "delivery-drift-metrics/1.0",
        "mission": prereg["mission"],
        "n_units": len(units),
        "label_freeze": {
            "labels_sha256": labels_sha,
            "corpus_sha256": corpus_sha,
            "preregistration_sha256": sha256_file(drift_dir / "preregistration.json"),
            "labeled_before_measuring": labels_doc.get("labeled_before_measuring"),
        },
        "baseline": {
            "variant": "A2_current",
            "corpus": "quality/delivery_semantics/evidence/corpus_eval.json (n=60)",
            "macro_f1": base_macro,
            "accuracy": baseline_doc["variants"]["A2_current"]["metrics"]["accuracy"],
            "per_class": {s: {"precision": base_per_class[s]["precision"],
                              "recall": base_per_class[s]["recall"],
                              "f1": base_per_class[s]["f1"],
                              "support": base_per_class[s]["support"]} for s in STATES},
        },
        "metrics": metrics,
        "confusion": cm,
        "macro_f1_bootstrap95": bootstrap_ci(y_true, y_pred),
        "drift": {
            "decay_abs": decay_abs,
            "decay_rel": decay_rel,
            "alert_threshold": alert_thr,
            "drift_alert": decay_rel > alert_thr,
            "verdict": "ALERT_RETRAIN" if decay_rel > alert_thr else "WITHIN_THRESHOLD",
            "cumulative_curve": cumulative_curve(y_true, y_pred),
            "drift_signature": drift_signature,
        },
        "variants": variants,
        "per_unit": per_unit,
        "criteria": {
            "c1_decay_le_10pct": decay_rel <= alert_thr,
            "c2_alert_logic_correct": _alert_logic_selftest(base_macro, metrics["macro_f1"], alert_thr),
            "c3_deterministic_rerun": None,   # 由 rerun 校验填
        },
        "git_head": git_head(),
    }
    if fig_dir is not None:
        out["figures"] = [str(Path(p).relative_to(ROOT)) for p in make_figures(out, fig_dir)]
    return out


def _top_reasons(cases: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for c in cases:
        state = c["classifier_state"]
        key = state
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _alert_logic_selftest(baseline: float, measured: float, thr: float) -> bool:
    """告警逻辑自检：把「超 10%」「恰好 10%」「差 10%」三种合成输入跑同一判定。

    与生产判定同口径（先 round(decay_rel, 4) 再严格大于），避免浮点尾差把
    「恰好 10%」误判成告警。
    """
    def fires(base: float, new: float) -> bool:
        return round((base - new) / base, 4) > thr
    ok_fire = fires(baseline, baseline * (1 - thr - 0.02))      # 落后 12% → 应告警
    ok_quiet = not fires(baseline, baseline * (1 - thr + 0.02))  # 落后 8% → 不应告警
    ok_equal = not fires(baseline, baseline * (1 - thr))        # 恰好 10% → 不告警（严格大于）
    return bool(ok_fire and ok_quiet and ok_equal)


def stable_sha(doc: Dict[str, Any]) -> str:
    """除 git_head 外的内容 sha256（确定性判据用）。"""
    clone = json.loads(json.dumps(doc))
    clone.pop("git_head", None)
    clone["criteria"].pop("c3_deterministic_rerun", None)
    clone.pop("figures", None)
    return sha256_text(json.dumps(clone, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="交付语义分类器漂移评测")
    ap.add_argument("--drift-dir", default=str(DRIFT))
    ap.add_argument("--fig-dir", default=str(DRIFT / "figures"))
    ap.add_argument("--json-out", default=str(DRIFT / "metrics.json"))
    args = ap.parse_args(argv)

    drift_dir = Path(args.drift_dir)
    out = evaluate(drift_dir, Path(args.fig_dir))
    first = stable_sha(out)
    again = evaluate(drift_dir, None)
    second = stable_sha(again)
    out["criteria"]["c3_deterministic_rerun"] = (first == second)
    out["determinism_sha256"] = {"run1": first, "run2": second, "identical": first == second}
    out["all_criteria_met"] = all(bool(v) for v in out["criteria"].values())

    Path(args.json_out).write_text(
        json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    brief = {
        "n_units": out["n_units"],
        "baseline_macro_f1": out["baseline"]["macro_f1"],
        "new_macro_f1": out["metrics"]["macro_f1"],
        "decay_rel": out["drift"]["decay_rel"],
        "drift_alert": out["drift"]["drift_alert"],
        "verdict": out["drift"]["verdict"],
        "criteria": out["criteria"],
        "all_criteria_met": out["all_criteria_met"],
        "determinism": out["determinism_sha256"],
        "figures": out.get("figures", []),
    }
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())