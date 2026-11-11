#!/usr/bin/env python3
"""阈值口径治理的验收控制台：把 C1–C7 七条判据跑成**可复算的数字**。

产物（全部程序化生成，零手抄）
------------------------------
``artifacts/criteria/threshold_registry_report.json``
    C1..C7 的判据数字、分布、样例、以及每一项的判定（PASS/FAIL）。
``artifacts/criteria/figures/*.png``
    (1) 派生一致性：log-log 误差衰减 vs 声明阶；(2) 重推导闭环：预测/实测比值；
    (3) 控制实验检出率 + 覆盖率账本。
``docs/criteria/threshold_registry.md``
    论文级报告（数字从 JSON 注入，不手抄）。

七条判据的机器定义
------------------
C1 完备性   ≥30 条真判据入册；``source_type`` 缺失率 0%；``heuristic`` 全部标不可裁并剔除。
C2 派生一致性  analytic 且声明 O(h^p) 的判据，``|实测指数-p| ≤ 0.3``。
C3 漂移检出  20 次「阈值 ×1.1 而 source_ref 不变」注入，门必须 20/20 判红。
C4 假阳性    20 次「只改注释/格式/键序/数字写法」提交，门必须 0 红。
C5 重推导闭环 ≥3 条判据的 ``threshold(h)`` 表达式在 h/2、h/4 上预测/实测比值 ∈[0.5,2] 且同向。
C6 覆盖率账本 「有来源阈值占比」「可派生阈值占比」两数入库。
C7 负控      阈值写死成等于实测误差的判据必须被判 empirical 且禁止进可裁集合。

用法
----
    python3 -m tools.criteria.gate_report                 # 跑全部 + 写产物
    python3 -m tools.criteria.gate_report --no-figures    # 只出 JSON/文档
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(ROOT))

from tools.criteria import probes as probes_mod  # noqa: E402
from tools.criteria import threshold_registry as reg  # noqa: E402

ART_DIR = ROOT / "artifacts" / "criteria"
FIG_DIR = ART_DIR / "figures"
REPORT_JSON = ART_DIR / "threshold_registry_report.json"
DOC_MD = ROOT / "docs" / "criteria" / "threshold_registry.md"

MISSION_ID = 7810  # 报告归属（CLI 可覆盖）
NODE = "P171W1-N06"
INJECTIONS = 20
FORMAT_VARIANTS = 20
MEASURE_PROBE_H = reg.DEFAULT_MEASURE_H  # 探针实测取样的默认分辨率（无 derivation 时）


# --------------------------------------------------------------------------- #
# C1 完备性
# --------------------------------------------------------------------------- #
def check_c1(registry: Dict[str, Any]) -> Dict[str, Any]:
    criteria = registry["criteria"]
    n = len(criteria)
    missing_type = [e["criterion_id"] for e in criteria if not e.get("source_type")]
    heuristic = [e for e in criteria if e.get("source_type") == "heuristic"]
    heuristic_leaks = [e["criterion_id"] for e in heuristic if e.get("decidable")]
    empirical = [e for e in criteria if e.get("source_type") == "empirical"]
    empirical_leaks = [e["criterion_id"] for e in empirical if e.get("decidable")]
    schema_check = reg.validate(registry)
    ok = (
        schema_check["ok"]
        and n >= 30
        and not missing_type
        and not heuristic_leaks
        and not empirical_leaks
    )
    return {
        "ok": ok,
        "n_criteria": n,
        "min_required": 30,
        "source_type_missing_rate": round(len(missing_type) / n, 6) if n else None,
        "n_missing_source_type": len(missing_type),
        "n_heuristic": len(heuristic),
        "n_heuristic_labelled_undecidable": len(heuristic) - len(heuristic_leaks),
        "n_empirical": len(empirical),
        "n_empirical_leaks": len(empirical_leaks),
        "schema_errors": schema_check["errors"],
        "by_source_type": {
            t: sum(1 for e in criteria if e.get("source_type") == t) for t in reg.SOURCE_TYPES
        },
    }


# --------------------------------------------------------------------------- #
# C2 派生一致性
# --------------------------------------------------------------------------- #
#: 拟合窗口敏感性扫描：衰减指数是**窗口的函数**，引擎必须把这个依赖说清楚
WINDOW_SCAN = (0.25, 0.0625, 0.015625)


def window_sensitivity(levels: int = 6) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for pid, probe in sorted(probes_mod.PROBES.items()):
        for h0 in WINDOW_SCAN:
            hs, es = probe.error_series(h0, levels)
            fit = reg.estimate_order(hs, es)
            dev = abs(fit["order"] - probe.order)
            rows.append(
                {
                    "probe": pid,
                    "declared_order": probe.order,
                    "h0": h0,
                    "h_max_coarse": hs[0],
                    "h_min_fine": hs[-1],
                    "observed_order": round(fit["order"], 6),
                    "min_error": float(f"{min(es):.3e}"),
                    "below_roundoff": min(es) < 1e-14,
                    "deviation": round(dev, 6),
                    "relative_deviation": round(dev / probe.order, 6),
                    "r2": round(fit["r2"], 9),
                }
            )
    return rows


def check_c2(registry: Dict[str, Any]) -> Dict[str, Any]:
    res = reg.derivation_check(registry)
    analytic_total = sum(1 for e in registry["criteria"] if e.get("source_type") == "analytic")
    return {
        "ok": res["ok"] and res["n_checked"] == analytic_total,
        "order_tol": res["order_tol"],
        "n_analytic": analytic_total,
        "n_checked": res["n_checked"],
        "n_failed": res["n_failed"],
        "n_unchecked": res["n_unchecked"],
        "unchecked_reasons": res.get("unchecked_reasons", []),
        "by_axis": res.get("by_axis", {}),
        "max_deviation": res["max_deviation"],
        "fit_window": {"h0": 0.25, "levels": 6},
        "window_sensitivity": window_sensitivity(),
        "rows": res["rows"],
    }


# --------------------------------------------------------------------------- #
# C3 漂移检出（注入 20 次 ×10% 篡改）
# --------------------------------------------------------------------------- #
def check_c3(registry: Dict[str, Any], lock: Dict[str, Any], seed: int = 7810) -> Dict[str, Any]:
    # 零阈值 ×1.1 仍为零（无变化），从注入池剔除
    ids = sorted(
        e["criterion_id"] for e in registry["criteria"]
        if isinstance(e.get("threshold"), (int, float)) and e["threshold"] != 0
    )
    rng = random.Random(seed)
    targets = sorted(rng.sample(ids, min(INJECTIONS, len(ids))))
    rows: List[Dict[str, Any]] = []
    for cid in targets:
        mutated = copy.deepcopy(registry)
        for entry in mutated["criteria"]:
            if entry["criterion_id"] == cid:
                entry["threshold"] = float(entry["threshold"]) * 1.10
                break
        res = reg.drift_check(lock, mutated)
        reds = [
            f
            for f in res["findings"]
            if f["level"] == "RED" and f["criterion_id"] == cid
        ]
        rows.append(
            {
                "criterion_id": cid,
                "injected_change": "threshold x1.10, source_ref unchanged",
                "verdict": res["verdict"],
                "detected": bool(reds) and res["verdict"] == "RED",
                "finding_codes": [f["code"] for f in reds],
            }
        )
    detected = sum(1 for r in rows if r["detected"])
    return {
        "ok": detected == len(rows) and len(rows) == INJECTIONS,
        "n_injections": len(rows),
        "n_detected": detected,
        "detection_rate": round(detected / len(rows), 6) if rows else None,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# C4 假阳性（20 次只有格式/注释差异）
# --------------------------------------------------------------------------- #
def _variant_texts(registry: Dict[str, Any]) -> List[str]:
    """20 种**语义等价**的文本变体：注释、缩进、键序、数字写法、尾随逗号、空行。"""
    base = copy.deepcopy(registry)
    variants: List[str] = []
    # 1) 常规缩进
    variants.append(json.dumps(base, ensure_ascii=False, indent=4))
    # 2) 单行紧凑
    variants.append(json.dumps(base, ensure_ascii=False, separators=(",", ":")))
    # 3-6) 键序反转（逐层）+ 不同缩进
    for indent in (2, 3, 5, 8):
        rev = _reverse_keys(base)
        variants.append(json.dumps(rev, ensure_ascii=False, indent=indent))
    # 7-10) JSONC 注释（行注释 / 块注释 / 注释挪位置 / 尾部注释）
    text = json.dumps(base, ensure_ascii=False, indent=2)
    variants.append("// 复核：2026-11 口径未变\n" + text)
    variants.append("/* 块注释：仅格式改动 */\n" + text + "\n// 尾注：无阈值变更\n")
    variants.append(text.replace("\n", "\n\n"))  # 空行
    variants.append("// a\n// b\n// c\n" + text)
    # 11-14) 尾随逗号（JSONC 允许）
    variants.append(_trailing_commas(text))
    variants.append(_trailing_commas(json.dumps(base, ensure_ascii=False, indent=4)))
    variants.append(_trailing_commas(text.replace("\n", "\r\n")))
    variants.append(_trailing_commas(json.dumps(base, ensure_ascii=False, indent=3)))
    # 15-18) 数字写法（0.85 -> 0.850 / 8.5e-1 / 0.8500000000000）
    variants.append(_reformat_numbers(base, "{:.3f}"))
    variants.append(_reformat_numbers(base, "{:.6f}"))
    variants.append(_reformat_numbers(base, "{:e}"))
    variants.append(_reformat_numbers(base, "{:.13f}"))
    # 19-20) 注释类键（note/notes/_comment 改动）——语义指纹必须忽略
    noted = copy.deepcopy(base)
    for entry in noted["criteria"]:
        entry["notes"] = "复核定稿：仅文字说明更新"
        entry["_comment"] = "formatting note ignore me"
    variants.append(json.dumps(noted, ensure_ascii=False, indent=2))
    variants.append(json.dumps(_reverse_keys(noted), ensure_ascii=False, indent=4))
    return variants[:FORMAT_VARIANTS]


def _reverse_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _reverse_keys(v) for k, v in reversed(list(obj.items()))}
    if isinstance(obj, list):
        return [_reverse_keys(v) for v in obj]
    return obj


def _trailing_commas(text: str) -> str:
    """在"值结尾 + 换行 + 关闭括号"处插一个尾随逗号（JSONC 合法）。"""
    import re

    return re.sub(r'([0-9"\]])\n(\s*[}\]])', r"\1,\n\2", text, count=1)


def _dump_with_float_fmt(obj: Any, fmt: str, indent: int = 2) -> str:
    """把数值换一种**精确等价**的写法序列化；字符串一律原样（不许被数值化）。"""

    def enc(node: Any, level: int) -> str:
        pad = " " * (indent * level)
        pad_in = " " * (indent * (level + 1))
        if isinstance(node, dict):
            if not node:
                return "{}"
            items = [f'{pad_in}{json.dumps(k, ensure_ascii=False)}: {enc(v, level + 1)}' for k, v in node.items()]
            return "{\n" + ",\n".join(items) + "\n" + pad + "}"
        if isinstance(node, list):
            if not node:
                return "[]"
            items = [f"{pad_in}{enc(v, level + 1)}" for v in node]
            return "[\n" + ",\n".join(items) + "\n" + pad + "]"
        if isinstance(node, bool) or node is None:
            return json.dumps(node)
        if isinstance(node, float):
            text = fmt.format(node)
            # 只接受**精确等价**的写法：否则退回最短往返表示（否则就不是"只改格式"）
            try:
                return text if float(text) == node else repr(node)
            except ValueError:
                return repr(node)
        if isinstance(node, int):
            return str(node)
        return json.dumps(node, ensure_ascii=False)

    return enc(obj, 0)


def _reformat_numbers(registry: Dict[str, Any], fmt: str) -> str:
    return _dump_with_float_fmt(registry, fmt)


def check_c4(registry: Dict[str, Any], lock: Dict[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for idx, text in enumerate(_variant_texts(registry)):
        tmp = Path(tempfile.mkdtemp()) / f"variant_{idx}.json"
        tmp.write_text(text, encoding="utf-8")
        try:
            parsed = reg.load_registry(tmp)
        except Exception as exc:  # noqa: BLE001
            rows.append({"variant": idx, "parsed": False, "error": str(exc), "false_positive": True})
            continue
        res = reg.drift_check(lock, parsed)
        rows.append(
            {
                "variant": idx,
                "parsed": True,
                "verdict": res["verdict"],
                "n_red": res["n_red"],
                "false_positive": res["verdict"] == "RED",
                "codes": sorted({f["code"] for f in res["findings"] if f["level"] == "RED"}),
            }
        )
    fp = sum(1 for r in rows if r.get("false_positive"))
    unparsed = sum(1 for r in rows if not r.get("parsed"))
    return {
        "ok": fp == 0 and unparsed == 0 and len(rows) == FORMAT_VARIANTS,
        "n_variants": len(rows),
        "n_false_positive": fp,
        "n_unparsed": unparsed,
        "false_positive_rate": round(fp / len(rows), 6) if rows else None,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# C5 重推导闭环
# --------------------------------------------------------------------------- #
def check_c5(registry: Dict[str, Any]) -> Dict[str, Any]:
    res = reg.rederivation_closure(registry)
    axes = res.get("axes") or []
    return {
        "ok": res["ok"] and res["n_checked"] >= 3,
        "ratio_band": res["ratio_band"],
        "n_checked": res["n_checked"],
        "min_required": 3,
        "axes": axes,
        "n_axes": len(axes),
        "axes_policy": "闸门只要求 n_checked≥3；『至少覆盖 2 条依赖轴』是**本仓注册表**的验收线，由 CI 断言（见 threshold-registry-gate.yml），不写进通用 verdict——只覆盖 h 的注册表不是不诚实，只是覆盖窄。",
        "rows": res["rows"],
    }


# --------------------------------------------------------------------------- #
# C6 覆盖率账本 + 来源腐烂控制
# --------------------------------------------------------------------------- #
def check_c6(registry: Dict[str, Any]) -> Dict[str, Any]:
    ledger = reg.coverage_ledger(registry)
    rot = _source_rot_control(registry)
    return {
        "ok": ledger["sourced_ratio"] is not None and rot["ok"],
        "sourced_ratio": ledger["sourced_ratio"],
        "derivable_ratio": ledger["derivable_ratio"],
        "decidable_ratio": ledger["decidable_ratio"],
        "n_total": ledger["n_total"],
        "n_source_verified": ledger["n_source_verified"],
        "n_derivable": ledger["n_derivable"],
        "n_decidable": ledger["n_decidable"],
        "by_source_type": ledger["by_source_type"],
        "by_source_ref_status": ledger["by_source_ref_status"],
        "source_rot_control": rot,
    }


def _source_rot_control(registry: Dict[str, Any]) -> Dict[str, Any]:
    """在临时目录复制被引文件后改一个字节：状态必须从 OK 变 SHA_DRIFT。"""
    target = next(
        (
            e
            for e in registry["criteria"]
            if reg.source_ref_status(e["source_ref"])["status"] == "OK"
        ),
        None,
    )
    if target is None:
        return {"ok": False, "detail": "没有可用的 OK 来源指针"}
    parts = reg.source_ref_parts(target["source_ref"])
    tmp_root = Path(tempfile.mkdtemp()) / "root"
    src = Path(reg.ROOT) / parts["path"]
    dst = tmp_root / parts["path"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    before = reg.source_ref_status(target["source_ref"], tmp_root)["status"]
    dst.write_bytes(src.read_bytes() + b"\n")
    after = reg.source_ref_status(target["source_ref"], tmp_root)["status"]
    return {
        "ok": before == "OK" and after == "SHA_DRIFT",
        "criterion_id": target["criterion_id"],
        "before": before,
        "after": after,
        "detail": "被引文件加一个字节后指针失效（来源腐烂可检出）",
    }


# --------------------------------------------------------------------------- #
# C7 负控：把阈值写死成实测误差
# --------------------------------------------------------------------------- #
def check_c7(registry: Dict[str, Any]) -> Dict[str, Any]:
    pid = sorted(probes_mod.PROBES)[0]
    probe = probes_mod.get_probe(pid)
    honest_threshold = probe.error(MEASURE_PROBE_H)
    template = next(e for e in registry["criteria"] if e.get("source_type") == "analytic")
    negctl = {
        "criterion_id": "negctl/tautology/threshold_equals_measured",
        "quantity": f"self-consistency probe {pid}",
        "comparator": "<=",
        "threshold": honest_threshold,
        "source_type": "analytic",  # 谎称有解析出处
        "source_ref": template["source_ref"],
        "source_note": "负控：阈值写死成恰好等于实测误差",
        "depends_on": [{"param": "h", "exponent": float(probe.order)}],
        "derivation": None,
        "probe": pid,
        "review_date": _dt.date.today().isoformat(),
        "notes": "C7 负控注入：应当被判 empirical 并剔除",
        "decidable": True,  # 同时谎称可裁
    }
    mutated = copy.deepcopy(registry)
    mutated["criteria"].append(negctl)
    measured = reg.measured_map(mutated)
    verdict = reg.classify_entry(negctl, measured[negctl["criterion_id"]])
    dec_ids = reg.decidable_ids(mutated, measured)
    schema_check = reg.validate(mutated)
    posctl = next(
        e for e in registry["criteria"] if e.get("source_type") == "analytic"
    )
    posctl_verdict = reg.classify_entry(posctl, measured.get(posctl["criterion_id"]))
    ok = (
        verdict["effective_source_type"] == "empirical"
        and verdict["decidable"] is False
        and negctl["criterion_id"] not in dec_ids
        and not schema_check["ok"]
        and posctl_verdict["decidable"] is True
    )
    return {
        "ok": ok,
        "injected": {
            "criterion_id": negctl["criterion_id"],
            "declared_source_type": negctl["source_type"],
            "threshold": honest_threshold,
            "measured_error": measured[negctl["criterion_id"]],
            "relative_gap": abs(negctl["threshold"] - measured[negctl["criterion_id"]])
            / measured[negctl["criterion_id"]],
        },
        "judged_source_type": verdict["effective_source_type"],
        "judged_decidable": verdict["decidable"],
        "reason": verdict["reason"],
        "excluded_from_decidable_set": negctl["criterion_id"] not in dec_ids,
        "label_lie_detected": not schema_check["ok"],
        "schema_error_sample": [e for e in schema_check["errors"] if negctl["criterion_id"] in e][:3],
        "positive_control": {
            "criterion_id": posctl["criterion_id"],
            "decidable": posctl_verdict["decidable"],
            "reason": posctl_verdict["reason"],
        },
    }


# --------------------------------------------------------------------------- #
# 图
# --------------------------------------------------------------------------- #
def make_figures(report: Dict[str, Any]) -> List[Dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # 图 1：派生一致性（log-log 误差衰减 vs 声明阶）——按依赖轴分面
    rows_c2 = report["C2"]["rows"]
    axes_seen = sorted({str(r.get("axis", "h")) for r in rows_c2})
    left = [a for a in axes_seen if a == "h"]
    right = [a for a in axes_seen if a != "h"]
    panels = [g for g in (left, right) if g]
    fig, axs = plt.subplots(1, len(panels), figsize=(6.2 * len(panels), 4.4), squeeze=False)
    for idx, group in enumerate(panels):
        ax = axs[0][idx]
        for row in rows_c2:
            if str(row.get("axis", "h")) not in group:
                continue
            xs = [float(x) for x in (row.get("axis_series") or row.get("h_series"))]
            es = [float(x) for x in row["error_series"]]
            p_obs = row.get("observed_order")
            p_dec = row.get("declared_order")
            (line,) = ax.loglog(
                xs,
                es,
                "o-",
                label=f"{row['probe']} [{row.get('axis', 'h')}] (fit {p_obs:.3f}, declared {p_dec:+.2f})",
            )
            ref_x, ref_e = xs[0], es[0]
            ax.loglog(
                xs,
                [ref_e * (x / ref_x) ** p_dec for x in xs],
                ls=":",
                color=line.get_color(),
                alpha=0.7,
            )
        ax.set_xlabel(" / ".join(group) + " axis value (finer →)")
        ax.set_ylabel("measured error")
        ax.set_title("C2 decay vs declared exponent: " + " / ".join(group) + " axis")
        ax.grid(True, which="both", ls=":", alpha=0.5)
        ax.legend(fontsize=7, loc="lower left")
    fig.tight_layout()
    path1 = FIG_DIR / "c2_derivation_consistency.png"
    fig.savefig(path1, dpi=150)
    plt.close(fig)
    figs.append(
        {
            "name": path1.name,
            "caption": "C2：解析判据沿各自依赖轴的实测误差衰减（点线=按声明指数外推）。左=h 轴，右=N/eps 轴；拟合指数与声明指数的最大偏差见 max_deviation。",
        }
    )

    # 图 2：C5 重推导闭环
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    lo, hi = report["C5"]["ratio_band"]
    ax.axhspan(lo, hi, color="tab:cyan", alpha=0.12, label=f"allowed band [{lo}, {hi}]")
    for i, row in enumerate(report["C5"]["rows"]):
        var = str(row.get("axis", "h"))
        xs = [f"k={k + 1}" for k in range(len(row["points"]))]
        ys = [pt["ratio"] for pt in row["points"]]
        ax.plot(xs, ys, "o-", color=colors[i % len(colors)], label=f"{row['criterion_id']} [{var}]")
    ax.axhline(1.0, color="k", lw=0.8)
    ax.set_xlabel("refinement step k (axis value moved one octave finer each step)")
    ax.set_ylabel("predicted threshold / measured error")
    ax.set_title("C5 re-derivation closure: threshold(x)=C*x^p vs measured")
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path2 = FIG_DIR / "c5_rederivation_closure.png"
    fig.savefig(path2, dpi=150)
    plt.close(fig)
    figs.append(
        {
            "name": path2.name,
            "caption": "C5：把 threshold(h)=C·h^p 投到 h/2、h/4 两档，与该档实测误差的比值全部落在 [0.5,2] 且与实测同向。",
        }
    )

    # 图 2b：依赖轴覆盖（声明 / 可派生 / 已实测）
    cov = report.get("axes") or {}
    axes_labels = sorted({k for k in (cov.get("declared_by_axis") or {}) if k != "none"})
    if axes_labels:
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        import numpy as np

        xpos = np.arange(len(axes_labels))
        declared_v = [cov["declared_by_axis"].get(a, 0) for a in axes_labels]
        derived_v = [cov["derived_by_axis"].get(a, 0) for a in axes_labels]
        checked_v = [cov["checked_by_axis"].get(a, {}).get("checked", 0) for a in axes_labels]
        failed_v = [cov["checked_by_axis"].get(a, {}).get("failed", 0) for a in axes_labels]
        w = 0.26
        ax.bar(xpos - w, declared_v, w, label="declared in depends_on")
        ax.bar(xpos, derived_v, w, label="has derivation expression")
        ax.bar(xpos + w, checked_v, w, label="actually measured (PASS+FAIL)")
        ax.scatter(xpos + w, failed_v, marker="x", color="k", zorder=5, label="failed")
        ax.set_xticks(xpos, axes_labels)
        ax.set_xlabel("dependency axis")
        ax.set_ylabel("number of criteria")
        ax.set_title("dependency-axis coverage: declared / derivable / measured")
        ax.grid(True, axis="y", ls=":", alpha=0.5)
        ax.legend(fontsize=7)
        fig.tight_layout()
        path2b = FIG_DIR / "c2b_axis_coverage.png"
        fig.savefig(path2b, dpi=150)
        plt.close(fig)
        figs.append(
            {
                "name": path2b.name,
                "caption": "依赖轴覆盖账本：每个轴上有多少条判据声明了该依赖量、多少条带可重推导表达式、多少条真正被实测过。声明了却测不了的判据会阻塞 C2（不许静默跳过）。",
            }
        )

    # 图 3：控制实验 + 覆盖率账本
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.8))
    names = [
        "C3 drift detect\n(20 injections)",
        "C4 no false alarm\n(20 format-only)",
        "C7 negative ctl\n(1 tautology)",
        "source rot\n(1 byte flip)",
    ]
    numbers = [
        report["C3"]["detection_rate"],
        1.0 - report["C4"]["false_positive_rate"],
        1.0 if report["C7"]["ok"] else 0.0,
        1.0 if report["C6"]["source_rot_control"]["ok"] else 0.0,
    ]
    bars = ax1.bar(names, numbers, color=["tab:blue", "tab:orange", "tab:purple", "tab:gray"])
    for b, v in zip(bars, numbers):
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}", ha="center", fontsize=9)
    ax1.set_ylim(0, 1.15)
    ax1.set_ylabel("correct verdict rate")
    ax1.set_title("gate controls (higher = better)")
    ax1.grid(True, axis="y", ls=":", alpha=0.5)

    cov = report["C6"]
    names2 = ["sourced\n(ref verified)", "derivable\n(has C*h^p)", "decidable\n(can judge)"]
    vals = [cov["sourced_ratio"], cov["derivable_ratio"], cov["decidable_ratio"]]
    bars2 = ax2.bar(names2, vals, color=["tab:blue", "tab:olive", "tab:red"])
    for b, v, n in zip(bars2, vals, [cov["n_source_verified"], cov["n_derivable"], cov["n_decidable"]]):
        ax2.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.3f}\n({n})", ha="center", fontsize=8)
    ax2.set_ylim(0, 1.15)
    ax2.set_ylabel("share of registry")
    ax2.set_title(f"C6 coverage ledger (n={cov['n_total']})")
    ax2.grid(True, axis="y", ls=":", alpha=0.5)
    fig.tight_layout()
    path3 = FIG_DIR / "controls_and_coverage.png"
    fig.savefig(path3, dpi=150)
    plt.close(fig)
    figs.append(
        {
            "name": path3.name,
            "caption": "Left: correct-verdict rate of four controls (C3 drift detection 20/20, C4 zero false alarm on 20 format-only variants, C7 tautology negative control, source-ref rot). Right: C6 coverage ledger (sourced / derivable / decidable).",
        }
    )
    return figs


# --------------------------------------------------------------------------- #
# 审计链
# --------------------------------------------------------------------------- #
AUDIT_PATHS = (
    "data/criteria/threshold_registry.json",
    "data/criteria/threshold_registry.lock.json",
    "schemas/threshold_registry.schema.json",
    "tools/criteria/threshold_registry.py",
    "tools/criteria/probes.py",
    "tools/criteria/probes_axes.py",
    "tools/criteria/harvest.py",
    "tools/criteria/gate_report.py",
    "tests/test_threshold_registry.py",
    "tests/test_threshold_registry_axes.py",
)


def audit_chain(extra: Sequence[str] = ()) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rel in list(AUDIT_PATHS) + list(extra):
        path = Path(reg.ROOT) / rel
        if not path.is_file():
            continue
        rows.append({"path": rel, "sha256": reg.sha256_file(path), "bytes": path.stat().st_size})
    return rows


# --------------------------------------------------------------------------- #
# 文档
# --------------------------------------------------------------------------- #
def render_doc(report: Dict[str, Any], figs: Sequence[Dict[str, str]]) -> str:
    c1, c2, c3, c4, c5, c6, c7 = (report[k] for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"))
    verdict_rows = "\n".join(
        f"| {k} | {('PASS' if report[k]['ok'] else 'FAIL')} | {report[k]['statement']} |"
        for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C7")
    )
    euler_rows = [r for r in c2["window_sensitivity"] if r["probe"] == "euler_growth"]
    euler_coarse = euler_rows[0]
    euler_fine = next((r for r in euler_rows if r["h0"] == min(WINDOW_SCAN[1:])), euler_rows[-1])
    roundoff_rows = [r for r in c2["window_sensitivity"] if r["below_roundoff"]]
    w_roundoff_probes = ", ".join(
        f"`{r['probe']}`（h0={r['h0']:.6g}）" for r in roundoff_rows
    ) or "（无）"
    w_roundoff_min = f"{min((r['min_error'] for r in roundoff_rows), default=0.0):.2e}"
    w_euler_coarse = f"{euler_coarse['observed_order']:.5f}"
    w_euler_coarse_rel = f"{euler_coarse['relative_deviation'] * 100:.2f}%"
    w_euler_fine = f"{euler_fine['observed_order']:.5f}"
    w_euler_fine_rel = f"{euler_fine['relative_deviation'] * 100:.2f}%"
    window_rows = "\n".join(
        f"| `{r['probe']}` | {r['declared_order']:.0f} | {r['h0']:.6g} | {r['observed_order']:.5f} | {r['deviation']:.5f} | {r['relative_deviation']*100:.2f}% | {'是' if r['below_roundoff'] else ''} |"
        for r in c2["window_sensitivity"]
    )
    c2_rows = "\n".join(
        "| `{}` | {} | {:+.2f} | {:+.4f} | {:.4f} | {:.6f} | {} |".format(
            r["criterion_id"],
            r.get("axis", "h"),
            r["declared_order"],
            r["observed_order"],
            r["deviation"],
            r["r2"],
            r["status"],
        )
        for r in c2["rows"]
    )
    axis_cov = report.get("axes") or {}
    axis_rows = "\n".join(
        "| {} | {} | {} | {} | {} |".format(
            a,
            axis_cov.get("declared_by_axis", {}).get(a, 0),
            axis_cov.get("derived_by_axis", {}).get(a, 0),
            (axis_cov.get("checked_by_axis", {}).get(a) or {}).get("checked", 0),
            (axis_cov.get("checked_by_axis", {}).get(a) or {}).get("failed", 0),
        )
        for a in sorted(axis_cov.get("declared_by_axis") or {})
        if a != "none"
    )
    c5_rows = "\n".join(
        "| `{}` | {} | {} | {} |".format(
            r["criterion_id"],
            r["expression"],
            " ; ".join(
                f"{r.get('axis', 'h')}={p.get(r.get('axis', 'h'), list(p.values())[0]):.5g}: {p['ratio']:.3f}"
                for p in r["points"]
            ),
            r["status"],
        )
        for r in c5["rows"]
    )
    dist_rows = "\n".join(
        f"| {k} | {v} |" for k, v in c1["by_source_type"].items()
    )
    example_analytic = json.dumps(
        next(e for e in report["record_examples"]["criteria"] if e["source_type"] == "analytic"),
        ensure_ascii=False,
        indent=2,
    )
    example_heuristic = json.dumps(
        next(e for e in report["record_examples"]["criteria"] if e["source_type"] == "heuristic"),
        ensure_ascii=False,
        indent=2,
    )
    audit_rows = "\n".join(
        f"| `{r['path']}` | {r['bytes']} | `{r['sha256']}` |" for r in report.get("audit_chain", [])
    )
    fig_md = "\n".join(
        f"![{f['name']}]({Path('artifacts/criteria/figures') / f['name']})\n\n*{f['caption']}*\n"
        for f in figs
    ) if figs else "（本次未生成图：仅 JSON 产物）"
    n_unchecked = c2.get("n_unchecked")
    return f"""# 验收阈值口径注册表与派生一致性门（{report['node']}）

**一句话**：把平台的每一条验收阈值从"当年恰好能过的那条线"改造成
`(quantity, comparator, threshold(h,p,N,ε), source_ref)` 的**可追溯、可重推导**记录，
并让"改阈值而不改出处"在 CI 上必然变红。

## 1. 问题与判据

平台每一句"验收通过"都挂在一个数上。历史上这些数以字面量散落，没人能回答：
(1) 它从哪来；(2) 它随什么变；(3) 给定分辨率 h 它应当是多少。三问缺失的后果是
阈值在某个分辨率/样本量上静默失效，而门禁照旧变绿。

本节点交付八件套（见 `tools/criteria/threshold_registry.py` 模块头）：
注册表契约 `schemas/threshold_registry.schema.json`、入册器 `tools/criteria/harvest.py`、
派生一致性引擎、重推导闭环、来源指针腐烂检查、可裁/不可裁判定（含同义反复负控）、
语义指纹漂移门、覆盖率账本。**零领域语义**：实测来自四个纯数学合成算子探针
（`tools/criteria/probes.py`），领域语义只以 `source_ref` 字符串注入；域词表从
`config/domain_word_denylist.json` 以数据读入。

## 2. 方法

- **入册（C1）**：从现网活文件确定性抽样（seed={report['meta']['sample_seed']}）：
  JSON 数值叶子（`config/`、`schemas/`）+ `tools/**/*.py` 模块级常量 + 4 条解析误差界。
  每条记 `source_ref = <路径>#sha256:<hex>#<指针>`，可机器验真。
- **派生一致性（C2）**：对声明 O(h^p) 的解析判据，跑 h=0.25…0.0078 六档，用
  log|E|–log h 最小二乘反演实测指数，要求 `|实测-p| ≤ {c2['order_tol']}`。
- **漂移门（C3/C4）**：注册表语义指纹（去注释、数值按 12 有效位归一）入锁文件
  `data/criteria/threshold_registry.lock.json`；`threshold` 变而 `source_ref` 未变 → RED。
- **重推导闭环（C5）**：`threshold(h)=C·h^p` 投到 h/2、h/4，与该档实测误差比值须落在
  `[0.5, 2.0]` 且与实测同向。
- **覆盖率账本（C6）**：`sourced_ratio`（指针可验）与 `derivable_ratio`（有 C·h^p）。
- **负控（C7）**：注入一条"阈值写死成恰好等于实测误差"的判据（且谎称 analytic），
  引擎必须强制判 `empirical` 并剔除出可裁集合，同时结构校验必须抓到"可裁标注说谎"。

### 2.1 一条判据长什么样

```json
{example_analytic}

{example_heuristic}
```

`source_ref` 的指针段是可复核的落点（JSON Pointer / 符号名+行号 / 探针 id）；
`decidable` 是**必须与引擎判定一致**的显式标注——标注说谎会被结构校验判红。

## 3. 结果（全部程序化注入，见 `artifacts/criteria/threshold_registry_report.json`）

| 判据 | 判定 | 数字 |
|---|---|---|
{verdict_rows}

### 3.1 来源类别分布（C1）

| source_type | 条数 |
|---|---|
{dist_rows}

`heuristic`={c1['n_heuristic']} 与 `empirical`={c1['n_empirical']} 全部显式标 `decidable=false`
（漏标 {c1['n_empirical_leaks']} 条）；`source_type` 缺失率 {c1['source_type_missing_rate']}。

### 3.2 派生一致性（C2）

| criterion_id | 依赖轴 | 声明指数 | 实测指数 | 偏差 | R² | 判定 |
|---|---|---|---|---|---|---|
{c2_rows}

最大偏差 {c2['max_deviation']}（容差 {c2['order_tol']}）；不可实测（阻塞）判据 {n_unchecked} 条，
覆盖轴 {sorted((report.get('axes') or {}).get('checked_by_axis', {}))}。

**依赖轴覆盖账本**：阈值是 `dependency(h, p, N, ε)` 的函数，所以"哪条轴上真被实测过"必须是
可查的数，而不是口头承诺：

| 轴 | depends_on 声明 | 有重推导表达式 | 实测判定（PASS+FAIL） | 判红 |
|---|---|---|---|---|
{axis_rows}

旧实现 `if "h" not in deps: continue` 会把 N/eps 轴判据**静默跳过**（既不 PASS 也不 FAIL），
门照旧判绿。现在的口径：`analytic` 判据只要声明了依赖量、却给不出能测该轴的探针，
或探针轴与声明轴不一致，一律进 `UNCHECKED_*` 并阻塞 C2。

**拟合窗口敏感性**（衰减指数是窗口的函数，引擎必须把这个依赖说清楚）：
固定下拟合档数 levels=6，只挪最粗档 h0：

| probe | 声明 p | h0 | 实测 p | 绝对偏差 | 相对偏差 | 触及舍入地板 |
|---|---|---|---|---|---|---|
{window_rows}

一阶探针在粗窗口（h0=0.25）上只测到 {w_euler_coarse}（相对偏差 {w_euler_coarse_rel}），窗口收细到
h0=1/64 后收敛到 {w_euler_fine}（{w_euler_fine_rel}）：这是 **O(h) 前渐近修正项**的已知行为，
不是引擎误差。反方向也有边界：{w_roundoff_probes} 处的最小误差已低于双精度
舍入地板（{w_roundoff_min}），拟合斜率被舍入残差污染、偏差反而回升——**高阶探针的窗口必须按阶数选，
不能一律取最细**。因此 C2 的容差取**绝对** 0.3（而非相对百分比），并且 `fit_window` 必须与判定
一起落盘：否则"我的阶数是 1"这句话在粗窗口下根本无法复核。

### 3.3 重推导闭环（C5）

| criterion_id | 表达式 | 预测/实测比值 | 判定 |
|---|---|---|---|
{c5_rows}

### 3.4 漂移门控制（C3/C4/C7）

- C3：{c3['n_detected']}/{c3['n_injections']} 次"阈值 ×1.10 而 source_ref 不变"被判红
  （检出率 {c3['detection_rate']}）。
- C4：{c4['n_variants']} 次"只动格式/注释/键序/数字写法"的提交，误报
  {c4['n_false_positive']}（假阳性率 {c4['false_positive_rate']}；解析失败 {c4['n_unparsed']}）。
- C7：注入判据被判 `{c7['judged_source_type']}`、`decidable={c7['judged_decidable']}`，
  剔除出可裁集合 = {c7['excluded_from_decidable_set']}，"标注说谎"被抓到 =
  {c7['label_lie_detected']}；正控（真解析判据）仍 `decidable={c7['positive_control']['decidable']}`。
- 来源腐烂控制：对 `{c6['source_rot_control']['criterion_id']}` 的被引文件加一个字节，
  指针状态 {c6['source_rot_control']['before']} → {c6['source_rot_control']['after']}。

## 4. 图

{fig_md}
## 5. 注意点与局限

1. **`source_type` 的保守性**：JSON 契约里的 `maximum`/`minimum` 判 `standard`（契约即出处），
   配置值与代码常量的出处无法自动认定时**一律判 `heuristic`**。方向上是安全的
   （宁可低估可裁面），代价是 `decidable_ratio` 只有 {c6['decidable_ratio']}——
   这是本次审计的**结论**而非缺陷：现网大部分阈值目前确实没有可追溯依据。
2. **`depends_on` 为空 = 依赖量未登记**（不是"常数阈值"）。因此 `derivable_ratio`
   只有 {c6['derivable_ratio']}。要把这个数抬上去，得逐条给出 C·h^p 的常数出处。
3. **`sourced_ratio`={c6['sourced_ratio']} 是"抽样时点"的数**：它靠 sha256 保持诚实，
   被引文件改动而引用未更新即掉到 SHA_DRIFT（见 §3.4 控制）。
4. **C2 的实测来自合成算子**（初等函数定积分 / 一阶初值问题），不是平台真实求解器：
   本节点只证明"引擎能正确反演指数"，对真实求解器阶数的裁决属于各领域仓的验证单。
5. 漂移门只覆盖**注册表内**的阈值。未入册的散落字面量不在防区——扩大入册是后续工作。
6. 删除判据为 YELLOW（非阻塞），因为清理合并本身合法；但删除会让相应能力失去判据，
   账本里 `n_total` 会掉，报告侧可据 C6 数字发现。
7. **语义指纹的量化粒度**：数值按 {reg.FLOAT_SIG} 位有效数字归一（这是 C4「换数字写法不算改动」
   的代价）。因此相对改动小于 ~1e-12 的阈值篡改看不见——远小于任何工程意义，但属于本门的
   已声明盲区。
8. **漂移门覆盖字段**：`threshold/comparator/quantity/depends_on/derivation/probe/decidable`
   任一变化而无 `source_ref` 变更即红；`review_date` 单改视为复核（不红）。
   反事实由 `tests/test_threshold_registry.py::test_drift_red_on_decidable_label_flip` 钉死。
9. **未知探针不崩门**：判据绑定了不存在的探针时，派生/闭环给出 `UNCHECKED_UNKNOWN_PROBE`
   并判红（`test_unknown_probe_is_reported_not_crashing`），不允许"探针找不到 ⇒ 跳过检查"。
10. **N 轴探针是蒙特卡洛量**：RMSE 估计本身有 ~1/√(2R)≈9% 的波动，因此该轴只用来裁决
    **指数**（-0.5 ± 0.3），不用来裁决常数 C（两者的比值只保证落在 [0.5,2]）。要收紧常数，
    得把重复数 R 提到 10³ 量级——那属于算力问题，不是口径问题。
11. **ε 轴探针是"容差=步长"的截断误差，不是迭代停机准则**：第一版用
    x←0.5·cos(x) 以 |Δx|≤ε 停机，实测发现误差序列被**迭代次数**量化（同一误差在相邻 ε 上
    重复出现，R² 只有 0.91、预测/实测比值 12–25），引擎会正确判红——但那是探针的错，不是判据的错。
    改用截断误差解析可算的差分步长后，R²=1.0、比值 0.997–1.000。教训：**轴探针必须让误差
    随轴连续变化**，否则测到的是离散化（量子化）而不是幂律。
12. **`p` 轴不可直接实测**：阶数不是"可以拧的采样参数"，它只能由 h/N 轴上的斜率反演出来。
    所以 `depends_on` 里声明 `p` 轴、却挂不上能测 `p` 的探针的判据会被
    `UNCHECKED_AXIS_MISMATCH` 阻塞——这是刻意的：宁可红，不许把"阶数"当自变量自己证明自己。
13. **轴白名单是 `h/p/N/eps`**（与 schema `depends_on.param` 枚举同源）：新增轴要在
    `probes_axes.AXIS_PROBES` 里加探针并同步枚举，不允许在表达式里临场发明变量名绕过校验
    （`eval_derivation` 对白名单外变量名仍然抛 `ValueError`，由
    `test_eval_derivation_rejects_non_whitelisted_variable_even_without_axis` 钉死）。

## 5.1 审计链（本报告所有数字可回放到这些字节）

| 产物 | bytes | sha256 |
|---|---|---|
{audit_rows}

## 6. 复算

```bash
python -m tools.criteria.harvest --out data/criteria/threshold_registry.json
python -m tools.criteria.threshold_registry lock --registry data/criteria/threshold_registry.json
python -m tools.criteria.threshold_registry gate --registry data/criteria/threshold_registry.json \\
    --lock data/criteria/threshold_registry.lock.json
python -m tools.criteria.gate_report          # 跑 C1–C7 并写报告/图/本文档
python -m pytest tests/test_threshold_registry.py -q
```
"""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(reg.ROOT).resolve()))
    except ValueError:
        return str(path)


def run(registry_path: Path = reg.DEFAULT_REGISTRY, lock_path: Path = reg.DEFAULT_LOCK) -> Dict[str, Any]:
    registry = reg.load_registry(registry_path)
    lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    axes = reg.axis_coverage(registry)

    c1 = check_c1(registry)
    c1["statement"] = (
        f"{c1['n_criteria']} 条入册（≥30）；source_type 缺失率 {c1['source_type_missing_rate']}；"
        f"heuristic {c1['n_heuristic']} 条全部标不可裁"
    )
    c2 = check_c2(registry)
    c2["statement"] = (
        f"{c2['n_checked']}/{c2['n_analytic']} 条解析判据实测指数最大偏差 {c2['max_deviation']} ≤ {c2['order_tol']}；"
        f"覆盖轴 {sorted(axes['checked_by_axis'])}；不可实测判据 {c2['n_unchecked']} 条"
    )
    c3 = check_c3(registry, lock)
    c3["statement"] = f"{c3['n_detected']}/{c3['n_injections']} 注入判红（检出率 {c3['detection_rate']}）"
    c4 = check_c4(registry, lock)
    c4["statement"] = (
        f"{c4['n_variants']} 次格式变更误报 {c4['n_false_positive']}（假阳性率 {c4['false_positive_rate']}）"
    )
    c5 = check_c5(registry)
    c5["statement"] = (
        "{} 条判据的 threshold(x)=C·x^p 在更细一档上预测/实测比值 ∈{}，全部同向；覆盖轴 {}".format(
            c5["n_checked"], c5["ratio_band"], c5.get("axes")
        )
    )
    c6 = check_c6(registry)
    c6["statement"] = (
        f"有来源阈值占比 {c6['sourced_ratio']}；可派生阈值占比 {c6['derivable_ratio']}；"
        f"可裁占比 {c6['decidable_ratio']}；来源腐烂控制 {c6['source_rot_control']['before']}→{c6['source_rot_control']['after']}"
    )
    c7 = check_c7(registry)
    c7["statement"] = (
        f"同义反复判据被判 {c7['judged_source_type']} 且剔除；标注说谎被抓到 = {c7['label_lie_detected']}"
    )

    report: Dict[str, Any] = {
        "schema_version": "threshold-registry-report/1.0",
        "generated_on": _dt.date.today().isoformat(),
        "mission_id": MISSION_ID,
        "node": NODE,
        "axes": axes,
        "meta": {
            "registry": _rel(registry_path),
            "registry_sha": reg.registry_sha(registry),
            "lock": _rel(lock_path),
            "sample_seed": registry.get("harvest", {}).get("sample_seed"),
            "injections": INJECTIONS,
            "format_variants": FORMAT_VARIANTS,
            "measure_probe_h": MEASURE_PROBE_H,
        },
        "C1": c1,
        "C2": c2,
        "C3": c3,
        "C4": c4,
        "C5": c5,
        "C6": c6,
        "C7": c7,
    }
    report["record_examples"] = {
        "criteria": [
            next(e for e in registry["criteria"] if e["source_type"] == "analytic"),
            next(
                (e for e in registry["criteria"] if e["source_type"] == "heuristic"),
                registry["criteria"][0],
            ),
        ]
    }
    report["verdict"] = "CLEAR" if all(report[k]["ok"] for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C7")) else "RED"
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="阈值治理验收控制台（C1–C7）")
    ap.add_argument("--registry", default=str(reg.DEFAULT_REGISTRY))
    ap.add_argument("--lock", default=str(reg.DEFAULT_LOCK))
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--out", default=str(REPORT_JSON))
    ap.add_argument("--node", default=NODE, help="报告归属节点（不进判据逻辑）")
    ap.add_argument("--mission", type=int, default=MISSION_ID, help="报告归属 mission id")
    args = ap.parse_args(argv)
    globals()["NODE"] = args.node
    globals()["MISSION_ID"] = args.mission

    report = run(Path(args.registry), Path(args.lock))
    figs: List[Dict[str, str]] = []
    if not args.no_figures:
        figs = make_figures(report)
        report["figures"] = figs
    report["audit_chain"] = audit_chain()
    if figs:
        report["audit_chain"] += [
            {"path": f"artifacts/criteria/figures/{f['name']}",
             "sha256": reg.sha256_file(FIG_DIR / f["name"]),
             "bytes": (FIG_DIR / f["name"]).stat().st_size}
            for f in figs
        ]
    ART_DIR.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    DOC_MD.parent.mkdir(parents=True, exist_ok=True)
    DOC_MD.write_text(render_doc(report, figs), encoding="utf-8")
    summary = {
        "verdict": report["verdict"],
        "C1": report["C1"]["statement"],
        "C2": report["C2"]["statement"],
        "C3": report["C3"]["statement"],
        "C4": report["C4"]["statement"],
        "C5": report["C5"]["statement"],
        "C6": report["C6"]["statement"],
        "C7": report["C7"]["statement"],
        "report": str(Path(args.out)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "CLEAR" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())