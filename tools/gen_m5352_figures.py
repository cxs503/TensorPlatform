#!/usr/bin/env python3
"""m5352 报告图与数字表生成器（程序化注入，零手抄）。

产物
----
* ``docs/reports/figs/m5352_consumer_contract.png`` —— 四消费者契约门的演练矩阵
  （基线 / 重定位 / schema 越界 / 台账被篡改 / 全灭）：每格是该消费者解析到的
  sha256 前 8 位或状态，同列同行同色 => 一致。
* ``docs/reports/figs/m5352_replay_gate.png`` —— 复现门：分域复现率 + Wilson 95%CI，
  以及五类根因账本（真实计数 + 自检可判）。
* ``docs/reports/figs/m5352_lbm_poiseuille.png`` —— 干净 venv 里 D2Q9 迭代解 vs
  Poiseuille 解析解（4 组格子/黏度），右轴相对误差。
* ``docs/reports/m5352_figures.json`` —— 上述图背后的全部数字（报告只引用本文件）。

用法：``python3 tools/gen_m5352_figures.py``
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402
from tools.replay_gate import gate as RPG  # noqa: E402
from tools.replay_gate import pool as POOL  # noqa: E402

FIGDIR = ROOT / "docs" / "reports" / "figs"
OUT_JSON = ROOT / "docs" / "reports" / "m5352_figures.json"
DOMAIN_EN = list(POOL.DOMAINS)
DOMAIN_ZH = POOL.DOMAIN_ZH


def consumer_matrix() -> dict:
    """四消费者契约门的五组演练（每格记 sha256 前缀/状态）。"""
    baseline = LP.contract_report()
    drills = {"baseline": baseline}

    # 演练②：重定位（内容不变，搬到别处）
    tmp_reloc = Path(tempfile.mkdtemp(prefix="m5352_reloc_")) / "ledger.json"
    shutil.copy2(ROOT / LP.LEDGER_REL, tmp_reloc)
    old_env = __import__("os").environ.get("TP_CAP_LEDGER")
    __import__("os").environ["TP_CAP_LEDGER"] = str(tmp_reloc)
    try:
        drills["relocated(same content)"] = LP.contract_report()
    finally:
        if old_env is None:
            __import__("os").environ.pop("TP_CAP_LEDGER", None)
        else:
            __import__("os").environ["TP_CAP_LEDGER"] = old_env

    # 演练③：schema_version 越界（负例，应红）
    tmp_schema = Path(tempfile.mkdtemp(prefix="m5352_schema_"))
    d = tmp_schema / "artifacts" / "capability_ledger"
    d.mkdir(parents=True)
    payload = json.loads((ROOT / LP.LEDGER_REL).read_text(encoding="utf-8"))
    payload["schema_version"] = "9.9"
    (d / "capability_ledger.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(ROOT / LP.RESOLVED_REL, d / "capability_ledger_resolved.json")
    drills["schema 9.9 (negative)"] = LP.contract_report(tmp_schema)

    # 演练④：台账被改但没重 pin（漂移告警，应红）
    tmp_drift = Path(tempfile.mkdtemp(prefix="m5352_drift_"))
    d2 = tmp_drift / "artifacts" / "capability_ledger"
    d2.mkdir(parents=True)
    payload2 = json.loads((ROOT / LP.LEDGER_REL).read_text(encoding="utf-8"))
    payload2["capabilities"][0]["claimed_level"] = "NOT_SUPPORTED"
    (d2 / "capability_ledger.json").write_text(json.dumps(payload2, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(ROOT / LP.RESOLVED_REL, d2 / "capability_ledger_resolved.json")
    shutil.copy2(ROOT / LP.PIN_REL, d2 / "ledger_contract.json")   # pin 仍是旧指纹
    drills["ledger tampered (drift)"] = LP.contract_report(tmp_drift)

    # 演练⑤：全灭（空仓根）
    tmp_empty = Path(tempfile.mkdtemp(prefix="m5352_empty_"))
    ref = LP.resolve_ledger(root=tmp_empty)
    drills["all sources dead"] = {
        "contract_ok": False, "status": ref["status"], "reasons": ref["reasons"],
        "canonical_sha256": None, "violations": ["NO_LEDGER"],
        "consumers": {}, "relocated": False,
    }

    matrix = {}
    for name, rep in drills.items():
        cells = {}
        for consumer, info in (rep.get("consumers") or {}).items():
            sha = info.get("sha256")
            cells[consumer] = (sha[:8] if sha else (info.get("error") or rep.get("status") or "—"))
        matrix[name] = {
            "cells": cells,
            "contract_ok": rep.get("contract_ok"),
            "status": rep.get("status") or rep.get("status"),
            "reasons": rep.get("reasons"),
            "canonical_sha256": rep.get("canonical_sha256"),
            "relocated": rep.get("relocated"),
            "violations": rep.get("violations"),
        }
    return {"consumers": list(LP.CONSUMERS), "matrix": matrix,
            "contract_version": LP.CONTRACT_VERSION,
            "schema_versions": list(LP.SCHEMA_VERSIONS),
            "pin_sha256": json.loads((ROOT / LP.PIN_REL).read_text(encoding="utf-8"))["sha256"]}


def fig_consumer_contract(data: dict) -> Path:
    consumers = data["consumers"]
    drills = list(data["matrix"])
    labels = {"audit_capabilities": "audit_capabilities\n(tool)", "cli": "CLI",
              "health": "/health", "report_gate": "report gate"}
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    n_r, n_c = len(drills), len(consumers)
    for i, drill in enumerate(drills):
        info = data["matrix"][drill]
        for j, consumer in enumerate(consumers):
            cell = (info["cells"] or {}).get(consumer, "—")
            ok = info["contract_ok"] and cell not in ("—",) and not str(cell).startswith(("DEGRADED", "NO_"))
            colour = "#2a9d8f" if ok else "#e76f51"
            ax.add_patch(plt.Rectangle((j, n_r - 1 - i), 1, 1, facecolor=colour, alpha=0.85, edgecolor="white"))
            ax.text(j + 0.5, n_r - 1 - i + 0.5, str(cell)[:8], ha="center", va="center",
                    color="white", fontsize=8, family="monospace")
        verdict = "PASS" if info["contract_ok"] else "FAIL(" + ",".join((info["reasons"] or [])[:2]) + ")"
        ax.text(-0.08, n_r - 1 - i + 0.5, f"{drill}\n{verdict}", ha="right", va="center", fontsize=8)
    ax.set_xlim(-2.2, n_c)
    ax.set_ylim(0, n_r)
    ax.set_xticks([j + 0.5 for j in range(n_c)])
    ax.set_xticklabels([labels.get(c, c) for c in consumers], fontsize=9)
    ax.set_yticks([])
    ax.set_title("Capability-ledger consumer contract gate: 4 consumers x 5 drills (m5352)", fontsize=10)
    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    FIGDIR.mkdir(parents=True, exist_ok=True)
    out = FIGDIR / "m5352_consumer_contract.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_replay_gate(rep: dict, tampered: dict) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.2), gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    doms = DOMAIN_EN
    rates = [rep["per_domain"][d]["rate"] for d in doms]
    ns = [rep["per_domain"][d]["n"] for d in doms]
    ks = [rep["per_domain"][d]["reproduced"] for d in doms]
    cis = [RPG.wilson_ci(k, n) for k, n in zip(ks, ns)]
    err_lo = [r - ci[0] for r, ci in zip(rates, cis)]
    err_hi = [ci[1] - r for r, ci in zip(rates, cis)]
    ax.bar([d.capitalize() for d in doms], rates, color="#457b9d", alpha=0.85,
           yerr=[err_lo, err_hi], capsize=4, ecolor="#1d3557")
    overall = rep["reproduction_rate"]
    ax.axhline(overall, color="#e63946", ls="--", lw=1.2,
               label=f"overall {overall:.1%} (n={rep['denominator']}, CI95 {rep['ci95'][0]:.2%}-{rep['ci95'][1]:.2%})")
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("reproduction rate (4 claims/domain, Wilson 95% CI)")
    ax.set_title("Replay gate: per-domain reproduction rate (clean venv)", fontsize=10)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="y", alpha=0.3)

    ax2 = axes[1]
    classes = list(rep["root_cause_ledger"])
    counts = [rep["root_cause_ledger"][c]["count"] for c in classes]
    sc_ok = [rep["self_check"][c]["ok"] for c in classes]
    ypos = np.arange(len(classes))
    ax2.barh(ypos, counts, color="#e76f51", alpha=0.9, label="hits in this sample")
    ax2.set_yticks(ypos)
    ax2.set_yticklabels([c.replace("_", "\n", 1) for c in classes], fontsize=8)
    ax2.invert_yaxis()
    ax2.set_xlabel("claims hit")
    ax2.set_xlim(0, max(1.4, max(counts) + 0.6))
    for y, (c, ok) in enumerate(zip(classes, sc_ok)):
        ax2.text(0.05, y + 0.28, ("self-check OK" if ok else "self-check FAILED"), fontsize=8,
                 color="#2a9d8f" if ok else "#e63946")
    ax2.set_title("Five root-cause classes (tamper injection must turn red)", fontsize=10)
    ax2.grid(axis="x", alpha=0.3)
    fig.suptitle(f"gate: seed={rep['seed']}, sample sha256={rep['sample']['sha256'][:12]}..., "
                 f"tamper detected={tampered['tamper']['detected']}", fontsize=9)
    fig.tight_layout()
    FIGDIR.mkdir(parents=True, exist_ok=True)
    out = FIGDIR / "m5352_replay_gate.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_lbm(rep: dict) -> dict:
    rows = [r for r in rep["results"] if r["kernel"] == "lbm_channel_umax"]
    rows.sort(key=lambda r: r["id"])
    analytic = [r["declared"] for r in rows]
    computed = [r["computed"] for r in rows]
    rel = [r["rel_err"] * 100 for r in rows]
    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    lim = [0, max(analytic) * 1.15]
    ax.plot(lim, lim, color="#888", ls="--", lw=1, label="ideal y=x")
    ax.scatter(analytic, computed, s=56, color="#1d3557", zorder=3, label="clean-venv D2Q9 iteration")
    for lab, a, c, e in zip([r["id"] for r in rows], analytic, computed, rel):
        ax.annotate(f"{e:.2f}%", (a, c), textcoords="offset points",
                    xytext=(6, -10), fontsize=7)
    ax.set_xlabel("analytic u_max = F*H^2/(8*nu)  [lattice units]")
    ax.set_ylabel("replayed u_max")
    ax.set_title("LBM replay: D2Q9 channel Poiseuille vs analytic (tolerance 3%)", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    axt = ax.twinx()
    axt.bar([a for a in analytic], rel, width=max(analytic) * 0.045, alpha=0.25, color="#2a9d8f")
    axt.set_ylabel("relative error [%]")
    axt.set_ylim(0, 6)
    fig.tight_layout()
    FIGDIR.mkdir(parents=True, exist_ok=True)
    out = FIGDIR / "m5352_lbm_poiseuille.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return {"figure": str(out.relative_to(ROOT)),
            "claims": [{"id": r["id"], "analytic": r["declared"], "computed": r["computed"],
                        "rel_err": r["rel_err"], "reproduced": r["reproduced"]} for r in rows]}


def main() -> int:
    contract = consumer_matrix()
    f1 = fig_consumer_contract(contract)

    rep = RPG.run_gate(write=True, use_clean_venv=True)
    tampered = RPG.run_gate(write=True, use_clean_venv=True, tamper="first_within:resistance")
    sysenv = RPG.run_gate(write=True, use_clean_venv=False)
    f2 = fig_replay_gate(rep, tampered)
    lbm = fig_lbm(rep)

    data = {
        "mission": 5352,
        "generated_by": "tools/gen_m5352_figures.py",
        "figures": [{"name": p.name, "path": str(p.relative_to(ROOT))}
                    for p in (f1, f2, ROOT / lbm["figure"])],
        "consumer_contract": contract,
        "replay_gate": {
            "clean_venv": {k: rep[k] for k in ("verdict", "denominator", "numerator",
                                              "reproduction_rate", "ci95", "seed",
                                              "root_cause_ledger", "per_domain",
                                              "verdict_criteria", "self_check",
                                              "env_dependency_probe", "tamper")},
            "system_env": {k: sysenv[k] for k in ("verdict", "numerator", "denominator",
                                                  "reproduction_rate", "ci95")},
            "tampered": {"verdict": tampered["verdict"], "tamper": tampered["tamper"],
                         "root_cause_ledger": tampered["root_cause_ledger"]},
            "sample_sha256": rep["sample"]["sha256"],
            "pool_sha256": rep["pool"]["sha256"],
            "pool_n": rep["pool"]["n"],
            "pool_per_domain": rep["pool"]["per_domain"],
            "lbm": lbm,
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"figures": data["figures"], "json": str(OUT_JSON.relative_to(ROOT)),
                      "rate": rep["reproduction_rate"], "verdict": rep["verdict"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())