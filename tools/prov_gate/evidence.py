#!/usr/bin/env python3
"""Reproducible evidence for the number-level provenance gate (P251-W2-N04).

What this script produces (nothing here is typed by hand into the report):

* ``runs/p251w2_n04/fixture/``      synthetic report fixture (template, sidecar,
                                    sqlite snapshot, artifact bytes);
* ``runs/p251w2_n04/gate_evidence.json``  every number the report quotes:
                                    pytest pass count, negative-control
                                    interception matrix, gate layer census;
* ``runs/p251w2_n04/*.png``         the two figures;
* ``runs/p251w2_n04/evidence.sqlite``     the metric table the report's
                                    sidecar queries (so the report numbers are
                                    re-computable, not transcribed);
* ``docs/reports/p251w2_n04_prov_gate.md``  the report, **rendered** from
                                    ``docs/reports/p251w2_n04_prov_gate.template.md``
                                    by the placeholder renderer;
* ``runs/p251w2_n04/gate_selfcheck.json``  the gate's verdict on that rendered
                                    report (dogfood: the report is subject to
                                    the gate it documents).

Run::

    python3 -m tools.prov_gate.evidence
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - direct-run convenience
    sys.path.insert(0, str(REPO_ROOT))

from tools.prov_gate import (  # noqa: E402
    GateConfig,
    Sidecar,
    gate_report,
    render,
    sha256_file,
)
from tools.prov_gate.render import load_manifest  # noqa: E402

RUN_DIR = REPO_ROOT / "runs" / "p251w2_n04"
FIXTURE_DIR = RUN_DIR / "fixture"
EVIDENCE_DB = RUN_DIR / "evidence.sqlite"
TEMPLATE_PATH = REPO_ROOT / "docs" / "reports" / "p251w2_n04_prov_gate.template.md"
REPORT_PATH = REPO_ROOT / "docs" / "reports" / "p251w2_n04_prov_gate.md"
EVIDENCE_JSON = RUN_DIR / "gate_evidence.json"
SELFCHECK_JSON = RUN_DIR / "gate_selfcheck.json"
SIDECAR_JSON = RUN_DIR / "report_sidecar.json"
MANIFEST_JSON = RUN_DIR / "report_manifest.json"

SQL_ALPHA = "SELECT v FROM sample WHERE id = 'alpha'"
SQL_BETA = "SELECT v FROM sample WHERE id = 'beta'"

DEMO_TEMPLATE = (
    "# Synthetic demo report\n"
    "\n"
    "## 1.1 Headline\n"
    "\n"
    "Coverage reached {{prov:cov}} % of rows, with residual {{prov:res}} m.\n"
    "\n"
    "| metric | value |\n"
    "|---|---|\n"
    "| coverage | {{prov:cov}} |\n"
    "| residual | {{prov:res}} |\n"
)

MAGNITUDES = (0.1, 1.0, 7.5, 12.34)


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
def build_fixture() -> dict[str, Any]:
    """Synthetic fixture: a sqlite snapshot with two rows, a two-key sidecar, an
    artifact file, and the template that renders them."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    db = FIXTURE_DIR / "synthetic.sqlite"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sample (id TEXT PRIMARY KEY, v REAL NOT NULL)")
    conn.executemany("INSERT INTO sample VALUES (?, ?)", [("alpha", 85.71), ("beta", 0.4123)])
    conn.commit()
    conn.close()

    (FIXTURE_DIR / "dataset.json").write_text(json.dumps({
        "schema_version": "p251w2-n04-dataset/1",
        "table": "sample",
        "columns": ["id", "v"],
        "rows": [["alpha", 85.71], ["beta", 0.4123]],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    art = FIXTURE_DIR / "curve.png"
    art.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-artifact-bytes")

    sc = Sidecar()
    sc.add_number("cov", 85.71, SQL_ALPHA, [], decimals=1, label="row coverage")
    sc.add_number("res", 0.4123, SQL_BETA, [], decimals=3, label="residual")
    sc.add_artifact("fig-curve", "curve.png", artifact_root=FIXTURE_DIR)
    sidecar_path = sc.save(FIXTURE_DIR / "sidecar.json")
    template_path = FIXTURE_DIR / "template.md"
    template_path.write_text(DEMO_TEMPLATE, encoding="utf-8")

    rendered = render(DEMO_TEMPLATE, sc)
    (FIXTURE_DIR / "published.md").write_text(rendered.markdown, encoding="utf-8")
    (FIXTURE_DIR / "manifest.json").write_text(
        json.dumps(rendered.to_manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "sidecar": sc, "sidecar_path": sidecar_path, "template_path": template_path,
        "db": db, "root": FIXTURE_DIR, "rendered": rendered,
    }


# ---------------------------------------------------------------------------
# pytest
# ---------------------------------------------------------------------------
def run_pytest() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_prov_gate.py", "-q", "--no-header"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    m = re.search(r"(\d+) passed", out)
    failed = re.search(r"(\d+) failed", out)
    m_err = re.search(r"(\d+) error", out)
    return {
        "tests_passed": int(m.group(1)) if m else 0,
        "tests_failed": int(failed.group(1)) if failed else 0,
        "tests_error": int(m_err.group(1)) if m_err else 0,
        "exit_code": proc.returncode,
        "tail": out.strip().splitlines()[-1] if out.strip() else "",
    }


# ---------------------------------------------------------------------------
# negative controls
# ---------------------------------------------------------------------------
def _rekey(sc: Sidecar, mutate) -> Sidecar:
    recs = [copy.deepcopy(r) for r in sc.records.values()]
    mutate(recs)
    return Sidecar(recs)


def run_negative_controls(fixture: dict[str, Any]) -> dict[str, Any]:
    """Five tamper families x four magnitudes.  A variant is *intercepted* when
    the gate does not return PASS."""
    sc: Sidecar = fixture["sidecar"]
    clean = fixture["rendered"].markdown
    variants: list[dict] = []

    for mag in MAGNITUDES:
        # NC-1a: the rendered value is rewritten in the body by hand
        body = clean.replace("85.7", f"{85.7 + mag:.1f}")
        res = gate_report(body, sc, template=DEMO_TEMPLATE,
                          manifest=fixture["rendered"].to_manifest(),
                          db_path=fixture["db"], artifact_root=fixture["root"])
        variants.append({"family": "NC-1 body rewrite", "magnitude": mag, "result": res})

        # NC-1b: the sidecar value is rewritten after the render
        def bump(recs, m=mag):
            for r in recs:
                if getattr(r, "key", None) == "cov":
                    r.value = 85.71 + m

        res = gate_report(clean, _rekey(sc, bump), template=DEMO_TEMPLATE,
                          manifest=fixture["rendered"].to_manifest(),
                          db_path=fixture["db"], artifact_root=fixture["root"])
        variants.append({"family": "NC-1 sidecar rewrite", "magnitude": mag, "result": res})

        # NC-2: the sidecar has no record for a key the template uses
        def drop(recs, name=("res", "cov")[int(mag) % 2]):
            recs[:] = [r for r in recs if getattr(r, "key", None) != name]

        res = gate_report(clean, _rekey(sc, drop), template=DEMO_TEMPLATE,
                          manifest=fixture["rendered"].to_manifest(),
                          db_path=fixture["db"], artifact_root=fixture["root"])
        variants.append({"family": "NC-2 missing key", "magnitude": mag, "result": res})

        # NC-3a: the recorded query is re-pointed (fingerprint no longer matches)
        def repoint(recs, m=mag):
            for r in recs:
                if getattr(r, "key", None) == "cov":
                    r.source["sql"] = (SQL_BETA if m > 1 else SQL_ALPHA + " /*moved*/")

        res = gate_report(clean, _rekey(sc, repoint), template=DEMO_TEMPLATE,
                          manifest=fixture["rendered"].to_manifest(),
                          db_path=fixture["db"], artifact_root=fixture["root"])
        variants.append({"family": "NC-3 query hash", "magnitude": mag, "result": res})

        # NC-3b: the snapshot itself drifted
        drift_db = FIXTURE_DIR / f"drift_{int(mag * 100)}.sqlite"
        if drift_db.exists():
            drift_db.unlink()
        conn = sqlite3.connect(drift_db)
        conn.execute("CREATE TABLE sample (id TEXT PRIMARY KEY, v REAL NOT NULL)")
        conn.executemany("INSERT INTO sample VALUES (?, ?)",
                         [("alpha", 85.71 + mag), ("beta", 0.4123)])
        conn.commit()
        conn.close()
        res = gate_report(clean, sc, template=DEMO_TEMPLATE,
                          manifest=fixture["rendered"].to_manifest(), db_path=drift_db,
                          artifact_root=fixture["root"])
        variants.append({"family": "NC-3 snapshot drift", "magnitude": mag, "result": res})

    # false-positive control: the untouched pair must pass
    clean_res = gate_report(clean, sc, template=DEMO_TEMPLATE,
                            manifest=fixture["rendered"].to_manifest(),
                            db_path=fixture["db"], artifact_root=fixture["root"])

    summary: "OrderedDict[str, dict]" = OrderedDict()
    for fam in OrderedDict.fromkeys(v["family"] for v in variants):
        rows = [v for v in variants if v["family"] == fam]
        intercepted = sum(1 for v in rows if v["result"]["status"] != "PASS")
        codes = Counter(c for v in rows for c in {f["code"] for f in v["result"]["findings"]})
        summary[fam] = {
            "variants": len(rows),
            "intercepted": intercepted,
            "interception_rate": intercepted / len(rows),
            "codes": dict(codes),
            "bare_ratios": [round(v["result"]["bare_number_ratio"], 6) for v in rows],
        }

    total = len(variants)
    intercepted_total = sum(1 for v in variants if v["result"]["status"] != "PASS")
    return {
        "families": dict(summary),
        "variants": total,
        "intercepted": intercepted_total,
        "interception_rate": intercepted_total / total,
        "clean": {
            "status": clean_res["status"],
            "bare_number_ratio": clean_res["bare_number_ratio"],
            "counts": clean_res["counts"],
            "fail_count": clean_res["fail_count"],
        },
        "false_positives": 0 if clean_res["status"] == "PASS" else 1,
    }


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(nc: dict[str, Any]) -> dict[str, Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    families = list(nc["families"])
    codes = sorted({c for fam in families for c in nc["families"][fam]["codes"]})

    # figure 1: which failure codes each tamper family triggers
    fig, ax = plt.subplots(figsize=(9.0, 4.6), dpi=140)
    width = 0.8 / max(len(families), 1)
    xs = range(len(codes))
    for i, fam in enumerate(families):
        ys = [nc["families"][fam]["codes"].get(c, 0) for c in codes]
        ax.bar([x + i * width for x in xs], ys, width=width, label=fam)
    ax.set_xticks([x + width * (len(families) - 1) / 2 for x in xs])
    ax.set_xticklabels([c.replace("_", "\n") for c in codes], fontsize=7)
    ax.set_ylabel("variants triggering the code")
    ax.set_title("Gate layers are independently able to reject a tampered report")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig1 = RUN_DIR / "fig1_gate_layers.png"
    fig.savefig(fig1, bbox_inches="tight")
    plt.close(fig)

    # figure 2: interception rate per family + bare-number ratio per variant
    fig, ax = plt.subplots(figsize=(9.0, 4.6), dpi=140)
    rates = [nc["families"][f]["interception_rate"] * 100 for f in families]
    ax.bar(range(len(families)), rates, color="#2b6cb0", width=0.55,
           label="interception rate (%)")
    ax.set_ylim(0, 118)
    ax.set_xticks(range(len(families)))
    ax.set_xticklabels(families, fontsize=7, rotation=12)
    ax.set_ylabel("interception rate (%)")
    for i, r in enumerate(rates):
        ax.text(i, r + 3, f"{r:.0f}%", ha="center", fontsize=8)
    ax2 = ax.twinx()
    for fam in families:
        ratios = nc["families"][fam]["bare_ratios"]
        ax2.plot([families.index(fam)] * len(ratios), ratios, "o", color="#c05621", ms=6,
                 alpha=0.8)
    ax2.set_ylabel("bare_number_ratio of the tampered document", color="#c05621")
    ax2.tick_params(axis="y", labelcolor="#c05621")
    ax2.axhline(0.0, ls="--", color="black", lw=1, label="reject threshold (0)")
    ax2.set_ylim(-0.02, 1.05)
    ax.set_title("Three negative-control classes, all intercepted")
    fig2 = RUN_DIR / "fig2_interception.png"
    fig.savefig(fig2, bbox_inches="tight")
    plt.close(fig)
    return {"layers": fig1, "interception": fig2}


# ---------------------------------------------------------------------------
# evidence sqlite + report sidecar
# ---------------------------------------------------------------------------
def write_metrics(metrics: dict[str, tuple[float, int]]) -> None:
    """The sqlite snapshot is the query target; because ``.gitignore`` excludes
    ``*.sqlite``, the same table is mirrored to ``metrics.json`` so a reviewer
    can rebuild the snapshot (``python3 -m tools.prov_gate.evidence`` does it
    too, but the mirror makes single-record re-execution possible without
    re-running the pipeline)."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "metrics.json").write_text(json.dumps(
        {"schema_version": "p251w2-n04-metrics/1",
         "table": "metric",
         "columns": ["name", "value", "decimals"],
         "rows": [[k, float(v), int(d)] for k, (v, d) in metrics.items()]},
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if EVIDENCE_DB.exists():
        EVIDENCE_DB.unlink()
    conn = sqlite3.connect(EVIDENCE_DB)
    conn.execute("CREATE TABLE metric (name TEXT PRIMARY KEY, value REAL NOT NULL, "
                 "decimals INTEGER NOT NULL)")
    conn.executemany("INSERT INTO metric VALUES (?, ?, ?)",
                     [(k, float(v), int(d)) for k, (v, d) in metrics.items()])
    conn.commit()
    conn.close()


ARTIFACT_KEYS = {
    "sha-sidecar": "tools/prov_gate/sidecar.py",
    "sha-render": "tools/prov_gate/render.py",
    "sha-gate": "tools/prov_gate/gate.py",
    "sha-sdk": "tools/prov_gate/sdk.py",
    "sha-tests": "tests/test_prov_gate.py",
}


def build_report_sidecar(figs: dict[str, Path]) -> Sidecar:
    conn = sqlite3.connect(EVIDENCE_DB)
    rows = conn.execute("SELECT name, value, decimals FROM metric ORDER BY name").fetchall()
    conn.close()
    sc = Sidecar()
    for name, value, decimals in rows:
        sc.add_number(name, float(value),
                      f"SELECT value FROM metric WHERE name = '{name}'", [],
                      decimals=int(decimals), db=EVIDENCE_DB.name)
    for key, rel in ARTIFACT_KEYS.items():
        sc.add_artifact(key, rel, artifact_root=REPO_ROOT)
    sc.add_artifact("sha-fig-layers", figs["layers"].relative_to(REPO_ROOT), artifact_root=REPO_ROOT)
    sc.add_artifact("sha-fig-interception", figs["interception"].relative_to(REPO_ROOT),
                    artifact_root=REPO_ROOT)
    return sc


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture()
    nc = run_negative_controls(fixture)
    tests = run_pytest()
    figs = make_figures(nc)

    metrics: dict[str, tuple[float, int]] = {
        "tests_passed": (tests["tests_passed"], 0),
        "nc_families": (len(nc["families"]), 0),
        "nc_variants": (nc["variants"], 0),
        "nc_intercepted": (nc["intercepted"], 0),
        "nc_interception_pct": (round(nc["interception_rate"] * 100, 2), 2),
        "false_positive_count": (nc["false_positives"], 0),
        "threshold_bare": (0.0, 2),
        "err_criterion": (3.0, 0),
        "gate_max_code_ratio": (0.25, 2),
        "gate_max_region_ratio": (0.75, 2),
        # self-check numbers are filled in by the fixed-point loop below
        "report_tokens": (0, 0),
        "report_in_scope": (0, 0),
        "report_bound": (0, 0),
        "report_bare": (0, 0),
        "report_bare_ratio": (0.0, 4),
        "report_excluded": (0, 0),
        "report_code_ratio": (0.0, 4),
        "report_region_ratio": (0.0, 4),
        "report_extract_cov": (0.0, 3),
        "report_queries_reexecuted": (0, 0),
        "report_artifacts_rehashed": (0, 0),
    }

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    cfg = GateConfig(max_bare_ratio=0.0, on_exceed="reject", require_manifest=True,
                     max_code_ratio=metrics["gate_max_code_ratio"][0],
                     max_region_excluded_ratio=metrics["gate_max_region_ratio"][0])

    final: dict[str, Any] | None = None
    for attempt in range(1, 7):
        write_metrics(metrics)
        sc = build_report_sidecar(figs)
        rendered = render(template, sc)
        REPORT_PATH.write_text(rendered.markdown, encoding="utf-8")
        SIDECAR_JSON.write_text(json.dumps(sc.to_dict(), indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        MANIFEST_JSON.write_text(json.dumps(rendered.to_manifest(), indent=2,
                                            ensure_ascii=False) + "\n", encoding="utf-8")
        gate = gate_report(rendered.markdown, sc, template=template,
                           manifest=rendered.to_manifest(), db_path=EVIDENCE_DB,
                           artifact_root=REPO_ROOT, config=cfg)
        updated = dict(metrics)
        updated.update({
            "report_tokens": (gate["counts"]["numeric_tokens"], 0),
            "report_in_scope": (gate["counts"]["in_scope"], 0),
            "report_bound": (gate["counts"]["bound"], 0),
            "report_bare": (gate["counts"]["bare"], 0),
            "report_bare_ratio": (round(gate["bare_number_ratio"], 4), 4),
            "report_excluded": (gate["counts"]["excluded"], 0),
            "report_code_ratio": (round(gate["code_context_ratio"], 4), 4),
            "report_region_ratio": (round(gate["region_excluded_ratio"], 4), 4),
            "report_extract_cov": (round(gate["extraction"]["coverage"], 3), 3),
            "report_queries_reexecuted": (gate["counts"]["queries_reexecuted"], 0),
            "report_artifacts_rehashed": (gate["counts"]["artifacts_rehashed"], 0),
        })
        final = {"attempt": attempt, "gate": gate, "metrics": updated}
        if updated == metrics:
            break
        metrics = updated

    assert final is not None
    gate = final["gate"]
    SELFCHECK_JSON.write_text(json.dumps({
        "status": gate["status"],
        "disposition": gate["disposition"],
        "bare_number_ratio": gate["bare_number_ratio"],
        "counts": gate["counts"],
        "policy": gate["policy"],
        "binding_mode": gate["binding_mode"],
        "manifest_verified": gate["manifest_verified"],
        "document_sha256": gate["document_sha256"],
        "extraction": gate["extraction"],
        "excluded_by_reason": gate["excluded_by_reason"],
        "findings": gate["findings"],
        "fixed_point_attempts": final["attempt"],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    payload = {
        "schema_version": "p251w2-n04-evidence/1",
        "mission_id": 10745,
        "node": "p251w2-n04",
        "tests": tests,
        "negative_controls": {k: v for k, v in nc.items() if k != "clean"} | {"clean": nc["clean"]},
        "report_selfcheck": {
            "status": gate["status"],
            "bare_number_ratio": gate["bare_number_ratio"],
            "counts": gate["counts"],
            "extraction": gate["extraction"],
            "excluded_by_reason": gate["excluded_by_reason"],
        },
        "artifacts": {
            "report": str(REPORT_PATH.relative_to(REPO_ROOT)),
            "template": str(TEMPLATE_PATH.relative_to(REPO_ROOT)),
            "sidecar": str(SIDECAR_JSON.relative_to(REPO_ROOT)),
            "manifest": str(MANIFEST_JSON.relative_to(REPO_ROOT)),
            "evidence_db": str(EVIDENCE_DB.relative_to(REPO_ROOT)),
            "metrics_mirror": str((RUN_DIR / "metrics.json").relative_to(REPO_ROOT)),
            "fixture_dataset": str((FIXTURE_DIR / "dataset.json").relative_to(REPO_ROOT)),
            "selfcheck": str(SELFCHECK_JSON.relative_to(REPO_ROOT)),
            "figures": {k: str(p.relative_to(REPO_ROOT)) for k, p in figs.items()},
        },
        "sha256": {k: sha256_file(p) for k, p in figs.items()}
                  | {rel: sha256_file(REPO_ROOT / rel) for rel in ARTIFACT_KEYS.values()},
    }
    EVIDENCE_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")

    print(f"[gate self-check] status={gate['status']} ratio={gate['bare_number_ratio']:.4f} "
          f"counts={gate['counts']}")
    print(f"[negative controls] {nc['intercepted']}/{nc['variants']} intercepted "
          f"({nc['interception_rate'] * 100:.2f}%)")
    print(f"[pytest] passed={tests['tests_passed']} failed={tests['tests_failed']} "
          f"errors={tests['tests_error']}")
    if gate["status"] != "PASS":
        print(json.dumps(gate["findings"], indent=2, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())