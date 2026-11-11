"""Acceptance criteria (a)-(e) of the campaign node, computed from artifacts.

Each criterion returns a dict with value / threshold / pass / source, so the
report can quote machine-computed numbers instead of hand-copied ones.  Two
evidence layers are reported separately and never merged:

  layer = "injected"   estimator validation against known ground truth
  layer = "measured"   real timing on the hosts actually available

Criteria that require a real x86_64 host are marked WITHHELD when such a host
is absent -- they are not scored as PASS and not silently dropped.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence


def _c(cid: str, name: str, value: Any, threshold: str, ok: bool | None,
       source: str, layer: str, note: str = "") -> Dict[str, Any]:
    return {"id": cid, "name": name, "value": value, "threshold": threshold,
            "pass": ok, "verdict": ("PASS" if ok else "FAIL") if ok is not None else "WITHHELD",
            "source": source, "layer": layer, "note": note}


def from_injection(study: Dict[str, Any], scenario_name: str) -> List[Dict[str, Any]]:
    """Criteria (a)(b)(c)(d)(e) scored against injected ground truth."""
    t = study["target_thresholds"]
    src = f"artifacts/bench_norm/injection/{scenario_name}.json"
    alpha = study["alpha_rel_err"]
    r2 = study["r2_log"]
    cov = study["coverage"]
    dn = study["diff_norm_pct"]
    dr = study["diff_raw_pct"]
    return [
        _c("a", "normalisation factor accuracy: p95 of |alpha_hat/alpha_true - 1| over replicates",
           alpha["p95_abs"], "< 0.03", bool(t["alpha_err_p95<0.03"]), src, "injected",
           f"alpha_true = {study['alpha_true']:.4g}; mean abs err {alpha['mean_abs']:.4f}, "
           f"max {alpha['max_abs']:.4f} (full distribution reported, not hidden)"),
        _c("b", "mixed-effects model fit (log-scale R^2, mean over replicates)",
           r2["mean"], "> 0.9", bool(t["r2_mean>0.9"]), src, "injected",
           f"min over replicates {r2['min']:.4f}; raw-scale eq.1 R^2 mean {study['r2_raw_spec_model']['mean']:.4f}"),
        _c("c", "normalised performance difference < 5% (held-out, per replicate)",
           dn["rate_below_5pct"], ">= 0.95 of replicates with |diff| < 5 %",
           bool(t["norm_diff_rate_below_5pct>=0.95"]), src, "injected",
           f"true residual difference {study['delta_true_pct']:.2f}%; median |diff| "
           f"{dn['median_abs']:.3f}%, p95 {dn['p95_abs']:.3f}%, max {dn['max_abs']:.3f}%; "
           f"{dn['n_below_5pct']}/{study['n_replicates']} replicates below 5%"),
        _c("d", "95% CI coverage of the true normalised difference",
           cov["rate"], "> 0.90", bool(t["coverage>0.9"]), src, "injected",
           f"{cov['hits']}/{study['n_replicates']} replicates hit; nominal {cov['nominal']}"),
        _c("e", "negative control: un-normalised difference > 50%",
           dr["min_abs"], "> 50.0 %", bool(t["raw_diff>50pct"]), src, "injected",
           f"true un-normalised difference {study['true_raw_diff_pct']:.1f}%; min observed |diff| {dr['min_abs']:.1f}%"),
    ]


def from_measurement(real: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Real-data counterparts; verdicts reflect what the available hosts can show."""
    src = "artifacts/bench_norm/real_analysis.json"
    out: List[Dict[str, Any]] = []
    arms = real.get("arms", {})
    isas = {a: d.get("isa") for a, d in arms.items()}
    has_x86 = any(v == "x86_64" for v in isas.values())
    multi = len(arms) > 1

    if multi:
        worst = max(abs(d["alpha"] - d.get("alpha_predicted_from_spec", d["alpha"])) for d in arms.values())
        # without an independent reference there is no "true" alpha on real
        # data, so accuracy is WITHHELD and only self-consistency is reported
        out.append(_c("a", "real-alpha accuracy (needs an external reference architecture)",
                      None, "< 0.03", None, src, "measured",
                      "no independent ground truth for a real host; alpha CI reported in real_analysis.json"))
    out.append(_c("b", "real-data mixed-model fit (log-scale R^2)",
                  real.get("lmm", {}).get("r2_log"), "> 0.9",
                  (real.get("lmm", {}).get("r2_log") or 0) > 0.9 if real.get("lmm") else None,
                  src, "measured", "hosts available: " + ", ".join(sorted(arms)) if arms else "none"))
    if has_x86 and multi:
        out.append(_c("c", "real loong64 vs x86 normalised difference", None, "< 5.0 %", None, src, "measured",
                      "x86 arm present"))
        out.append(_c("d", "real CI coverage (needs repeated true differences)", None, "> 0.90", None, src, "measured",
                      "coverage is a property of the estimator; validated in the injected layer"))
        out.append(_c("e", "real un-normalised loong64 vs x86 difference", None, "> 50.0 %", None, src, "measured",
                      "x86 arm present"))
    else:
        out.append(_c("c", "real loong64 vs x86 normalised difference", None, "< 5.0 %", None, src, "measured",
                      "WITHHELD: no x86_64 host / cross-compiler / emulator in this environment; "
                      "measured arms are loongarch64 only"))
        # end-to-end mechanics on real hosts with a same-ISA build-variant arm:
        # explicitly a proxy, never reported as a loong64-vs-x86 result.
        for arm, d in (real.get("diffs") or {}).items():
            nd = d["normalised"]["difference_pct"]
            rw = d["raw"]["difference_pct"]
            out.append(_c("c_proxy", f"real-host end-to-end normalised difference ({arm})", abs(nd),
                          "< 5.0 %", abs(nd) < 5.0, src, "measured",
                          "same-ISA build-variant arm (calibration/evaluation workload split); "
                          "mechanics proxy, NOT a loong64-vs-x86 result"))
            nds = (d.get("normalised_scalar") or {}).get("difference_pct")
            if nds is not None:
                out.append(_c("c_proxy_scalar",
                              f"real-host residual with a CONSTANT scalar alpha ({arm})", abs(nds),
                              "< 5.0 %", abs(nds) < 5.0, src, "measured",
                              "diagnostic: the constant-alpha assumption is violated on real data "
                              "(alpha varies with workload size); this is why the workload-resolved "
                              "alpha(x)=exp(gamma.x) is the adopted estimator"))
            out.append(_c("e_proxy", f"real-host negative control: un-normalised difference ({arm})", abs(rw),
                          "> 50.0 %", abs(rw) > 50.0, src, "measured",
                          "same-ISA build-variant arm; proxy for criterion (e), NOT a loong64-vs-x86 result"))
        out.append(_c("d", "real CI coverage", None, "> 0.90", None, src, "measured",
                      "not estimable on real data (needs the true difference); estimator coverage validated in injected layer"))
        out.append(_c("e", "real un-normalised loong64 vs x86 difference", None, "> 50.0 %", None, src, "measured",
                      "WITHHELD: no x86_64 host available"))
    return out


def summarise(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by = {"PASS": 0, "FAIL": 0, "WITHHELD": 0}
    for it in items:
        by[it["verdict"]] += 1
    return {"counts": by, "all_pass_injected": all(it["verdict"] == "PASS"
                                                   for it in items if it["layer"] == "injected"),
            "n": len(items)}