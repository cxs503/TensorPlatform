"""p474w1-n01 acceptance runner: evidence chain -> artefacts, tables, figures.

Run::

    python3 scripts/run_p474w1_n01.py            # full run (~2 min, 7 repos)

Writes:
  artifacts/p474w1_n01/evidence_chain.json   full machine artefact
  artifacts/p474w1_n01/mde_table.csv         MDE / power table (criterion c)
  artifacts/p474w1_n01/reclass_claims.csv    historical PASS census (criterion d)
  artifacts/p474w1_n01/association.json      association audit (criterion a)
  docs/p474w1_n01/acceptance.json            criteria + digests + pytest count
  docs/p474w1_n01/fig_*.png                  report figures

Nothing is hand-copied: every number in the report comes from these files.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.capability_evidence_chain import equivalence as eq  # noqa: E402
from tools.capability_evidence_chain import pipeline  # noqa: E402

NODE = "p474w1-n01-evidence-chain"
OUT_ART = ROOT / "artifacts" / "p474w1_n01"
OUT_DOC = ROOT / "docs" / "p474w1_n01"
CONFIG = ROOT / "tools" / "capability_evidence_chain" / "configs" / "ecosystem_solvers.json"


def count_tests() -> int:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "tools/capability_evidence_chain/tests",
             "--collect-only", "-q"],
            capture_output=True, text=True, timeout=300, cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in reversed(out.stdout.splitlines()):
        line = line.strip()
        if "test" in line and (" " in line) and line.split()[0].isdigit():
            return int(line.split()[0])
    return sum(1 for ln in out.stdout.splitlines() if "::" in ln)


def write_tables(artifact: dict) -> None:
    OUT_ART.mkdir(parents=True, exist_ok=True)
    table = artifact["mde_table"]
    with (OUT_ART / "mde_table.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = ["capability", "scenario", "unit", "n", "estimate", "margin",
                "ci_low", "ci_high", "power", "mde", "mde_ratio", "decision",
                "underpowered"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in table:
            w.writerow(row)

    claims = artifact.get("reclassification", {}).get("claims", [])
    with (OUT_ART / "reclass_claims.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = ["source", "pointer", "status", "value", "threshold", "n", "sd",
                "classification", "needs_reverification", "power", "mde", "reason"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for c in claims:
            w.writerow(c)

    (OUT_ART / "association.json").write_text(
        json.dumps(artifact["association"], indent=2), encoding="utf-8"
    )


def fig_power_curves(path: Path) -> None:
    """Analytic TOST power vs true deviation for the three scenarios."""
    fig, axes = plt.subplots(1, 3, figsize=(12.6, 4.0))
    dev = np.linspace(0.0, 1.0, 60)

    ax = axes[0]
    for n, sd in ((10, 0.06), (30, 0.06), (100, 0.06)):
        ys = [eq.power_mean_one_sample(n, sd, d * 0.15, 0.15, 0.05) for d in dev]
        ax.plot(dev * 0.15, ys, label=f"n={n}, sd={sd}")
    ax.axhline(0.8, color="k", ls=":", lw=1)
    ax.set_xlabel("true mean deviation |mu| (margin = 0.15)")
    ax.set_ylabel("power 1-beta")
    ax.set_title("mean equivalence (one-sample TOST)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    for n in (30, 80, 200):
        ys = [eq.power_variance_ratio(n, n, 1.0 + d * 1.0, 2.0, 0.05) for d in dev]
        ax.plot(1.0 + dev, ys, label=f"n1=n2={n}")
    ax.axhline(0.8, color="k", ls=":", lw=1)
    ax.axvline(2.0, color="r", ls="--", lw=1, label="margin theta")
    ax.set_xlabel("true variance ratio")
    ax.set_ylabel("power 1-beta")
    ax.set_title("variance equivalence (theta = 2)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[2]
    for n in (20, 60, 150):
        ys = [eq.power_distribution_ks(n, n, d * 0.35, 0.35, 0.05, n_boot=150, n_sim=60)
              for d in dev[::6]]
        ax.plot(dev[::6] * 0.35, ys, "o-", label=f"n1=n2={n}")
    ax.axhline(0.8, color="k", ls=":", lw=1)
    ax.set_xlabel("true sup-CDF distance (margin = 0.35)")
    ax.set_ylabel("power 1-beta")
    ax.set_title("distribution equivalence (KS interval)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.suptitle("TOST power for the three equivalence scenarios (analytic / simulated)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_forest(artifact: dict, path: Path) -> None:
    """Estimate with 90 % dual interval vs the declared margin."""
    rows = [r for r in artifact["capabilities"] if r.get("estimate") is not None]
    rows.sort(key=lambda r: (r["scenario"], r["capability"]))
    fig, ax = plt.subplots(figsize=(9.2, 0.42 * len(rows) + 1.8))
    for i, r in enumerate(rows):
        y = len(rows) - i
        color = "#1a9850" if r.get("equivalent") else "#d73027"
        ax.plot([r["ci_low"], r["ci_high"]], [y, y], color=color, lw=2)
        ax.plot([r["estimate"]], [y], "o", color=color, ms=4)
        margin = r["margin"]
        if r["scenario"] == "variance":
            ax.plot([1.0 / margin, margin], [y, y], color="k", ls=":", lw=0.8)
        else:
            ax.plot([-margin, margin], [y, y], color="k", ls=":", lw=0.8)
    ax.set_yticks(range(1, len(rows) + 1))
    ax.set_yticklabels([r["capability"] for r in reversed(rows)], fontsize=7)
    ax.set_xlabel("estimate with 1-2*alpha interval (dotted = declared equivalence bounds)")
    ax.set_title("Evidence-chain equivalence decisions on live artefacts")
    ax.grid(alpha=0.25, axis="x")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_association(artifact: dict, path: Path) -> None:
    assoc = artifact["association"]
    repos = sorted(assoc["recognized_by_repo"])
    rec = [assoc["recognized_by_repo"][r] for r in repos]
    bnd = [assoc["bound_by_repo"].get(r, 0) for r in repos]
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.2))
    ax = axes[0]
    x = np.arange(len(repos))
    ax.bar(x - 0.2, rec, 0.4, label="recognized artefacts")
    ax.bar(x + 0.2, bnd, 0.4, label="bound to a capability entry")
    ax.set_xticks(x)
    ax.set_xticklabels(repos, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("artefacts")
    ax.set_title(
        f"association rate {assoc['association_rate']:.3f} "
        f"({assoc['bound_files']}/{assoc['recognized_files']}), "
        f"{assoc['files_scanned']} files scanned"
    )
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, axis="y")

    ax = axes[1]
    counts = artifact["reclassification"]["summary"]["classification"]
    labels = list(counts)
    vals = [counts[k] for k in labels]
    colors = ["#1a9850" if k == "ADEQUATE" else
              "#fc8d59" if k == "FAIL_RECORDED" else "#d73027" for k in labels]
    ax.barh(labels, vals, color=colors)
    for i, v in enumerate(vals):
        ax.text(v + 0.5, i, str(v), va="center", fontsize=8)
    ax.set_xlabel("claims")
    ax.set_title(
        "historical PASS census\n"
        f"flag rate {artifact['reclassification']['summary']['low_power_flag_rate']:.3f}"
    )
    ax.grid(alpha=0.25, axis="x")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_mde(artifact: dict, path: Path) -> None:
    """MDE / margin ratio and required sample size for the live capabilities."""
    rows = [r for r in artifact["mde_table"] if r.get("mde") is not None]
    fig, ax = plt.subplots(figsize=(9.4, 4.2))
    xs = np.arange(len(rows))
    ratios = [(r["mde"] / r["margin"]) if r["margin"] else 0.0 for r in rows]
    ax.bar(xs, ratios, color=["#1a9850" if r > 0.999 else "#fdae61" for r in ratios])
    ax.axhline(1.0, color="k", ls=":", lw=1, label="MDE = margin")
    ax.set_xticks(xs)
    ax.set_xticklabels([r["capability"].split(".")[-1] for r in rows],
                       rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("MDE / margin")
    ax.set_title("Sensitivity of each capability certificate: MDE relative to its margin")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    os.chdir(ROOT)          # the census base "." means: this repository
    OUT_ART.mkdir(parents=True, exist_ok=True)
    OUT_DOC.mkdir(parents=True, exist_ok=True)
    n_tests = count_tests()
    artifact = pipeline.run(
        CONFIG,
        root=os.environ.get("TP_ECOSYSTEM_ROOT"),
        ks_boot=2000,
        package_dir=ROOT / "tools" / "capability_evidence_chain",
        lexicon_path=ROOT / "tools" / "evidence_domain_lexicon.json",
        node=NODE,
    )
    artifact["criteria"] = pipeline.criteria_block(artifact, n_tests=n_tests)
    artifact["n_pytest_tests"] = n_tests
    pipeline.write_artifact(artifact, OUT_ART / "evidence_chain.json")
    write_tables(artifact)

    fig_power_curves(OUT_DOC / "fig1_power_curves.png")
    fig_forest(artifact, OUT_DOC / "fig2_equivalence_forest.png")
    fig_association(artifact, OUT_DOC / "fig3_association_and_reclass.png")
    fig_mde(artifact, OUT_DOC / "fig4_mde_vs_margin.png")

    proposals = artifact["ledger_proposals"]
    with (OUT_ART / "ledger_proposals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(proposals[0]), extrasaction="ignore")
        w.writeheader()
        for row in proposals:
            w.writerow(row)

    acceptance = {
        "node": NODE,
        "ledger_actions": {a: sum(1 for p in proposals if p["action"] == a)
                           for a in sorted({p["action"] for p in proposals})},
        "criteria": artifact["criteria"],
        "digests": artifact["digests"],
        "config": artifact["config"],
        "scan_stats": artifact["scan_stats"],
        "seed": artifact["seed"],
        "artifacts": {
            "evidence_chain": "artifacts/p474w1_n01/evidence_chain.json",
            "mde_table": "artifacts/p474w1_n01/mde_table.csv",
            "reclass_claims": "artifacts/p474w1_n01/reclass_claims.csv",
            "association": "artifacts/p474w1_n01/association.json",
            "ledger_proposals": "artifacts/p474w1_n01/ledger_proposals.csv",
            "figures": [
                "docs/p474w1_n01/fig1_power_curves.png",
                "docs/p474w1_n01/fig2_equivalence_forest.png",
                "docs/p474w1_n01/fig3_association_and_reclass.png",
                "docs/p474w1_n01/fig4_mde_vs_margin.png",
            ],
        },
    }
    (OUT_DOC / "acceptance.json").write_text(
        json.dumps(acceptance, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "criteria": {k: (v.get("pass") if isinstance(v, dict) else v)
                     for k, v in artifact["criteria"].items()},
        "association_rate": artifact["association"]["association_rate"],
        "mde_rows": len(artifact["mde_table"]),
        "reclass": artifact["reclassification"]["summary"],
        "n_tests": n_tests,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())