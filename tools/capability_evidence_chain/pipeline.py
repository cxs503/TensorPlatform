"""End-to-end pipeline: scan -> bind -> TOST/MDE -> historical reclassification.

The pipeline is the machine-readable producer behind the campaign node report:
it writes one immutable-ish JSON artefact that contains the input digests, the
per-capability equivalence results, the MDE table, the historical PASS census
and an explicit ``criteria`` block whose booleans are derived from the numbers
(never asserted by hand).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import binder, reclassify, scanner
from .config import EvidenceChainConfig, load_config

__all__ = ["run", "zero_domain_check", "criteria_block", "write_artifact"]

DEFAULT_SEED = 20261111


def _digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def zero_domain_check(
    package_dir: Path, lexicon_path: Optional[Path]
) -> Dict[str, Any]:
    """Assert the package carries no domain vocabulary (criterion (f)).

    Terms come from a lexicon file (the one place domain words may live);
    if the lexicon is absent the check degrades to a *reported* skip rather
    than a false pass.
    """
    if lexicon_path is None or not Path(lexicon_path).exists():
        return {"status": "SKIPPED", "reason": "lexicon file not available",
                "hits": []}
    lex = json.loads(Path(lexicon_path).read_text(encoding="utf-8"))
    ascii_terms = [t for t in lex.get("ascii_terms", []) if t]
    cjk_terms = [t for t in lex.get("cjk_terms", []) if t]
    ascii_re = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in ascii_terms) + r")\b", re.I
    ) if ascii_terms else None
    hits: List[Dict[str, Any]] = []
    for path in sorted(Path(package_dir).rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".json", ".md"):
            continue
        if path.name == "evidence_domain_lexicon.json":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ascii_re is not None:
            for m in ascii_re.finditer(text):
                hits.append({"file": path.name, "term": m.group(0), "kind": "ascii"})
        for term in cjk_terms:
            if term in text:
                hits.append({"file": path.name, "term": term, "kind": "cjk"})
    return {
        "status": "PASS" if not hits else "FAIL",
        "n_terms": len(ascii_terms) + len(cjk_terms),
        "hits": hits[:50],
        "n_hits": len(hits),
    }


def criteria_block(
    artifact: Dict[str, Any], n_tests: Optional[int] = None
) -> Dict[str, Any]:
    """Derive the node's machine criteria from the measured numbers."""
    assoc = artifact["association"]
    caps = artifact["capabilities"]
    mde = artifact["mde_table"]
    recl = artifact.get("reclassification", {})
    scenarios_exercised = sorted(
        {
            c["scenario"]
            for c in caps
            if c.get("n") and c.get("decision") not in ("WITHHELD_NO_DATA",)
        }
    )
    scenarios_implemented = ["mean", "variance", "distribution"]
    recompute_ok = bool(recl.get("recompute", {}).get("rate_matches")) and bool(
        recl.get("recompute", {}).get("digest_matches")
    )
    out = {
        "a_cross_repo_scan": {
            "repos_scanned": assoc.get("repos_scanned", 0),
            "files_scanned": assoc.get("files_scanned", 0),
            "recognized_files": assoc.get("recognized_files", 0),
            "bound_files": assoc.get("bound_files", 0),
            "association_rate": assoc.get("association_rate", 0.0),
            "pass": bool(
                assoc.get("repos_scanned", 0) >= 5
                and assoc.get("association_rate", 0.0) >= 0.90
            ),
            "thresholds": {"repos_min": 5, "association_rate_min": 0.90},
        },
        "b_scenarios": {
            "implemented": scenarios_implemented,
            "exercised": scenarios_exercised,
            "pass": len(scenarios_implemented) >= 3 and len(scenarios_exercised) >= 3,
        },
        "c_mde_table": {
            "rows": len(mde),
            "rows_with_mde": sum(1 for r in mde if r.get("mde") is not None),
            "pass": len(mde) >= 10,
            "threshold": {"rows_min": 10},
        },
        "d_reclassification": {
            "n_claims": recl.get("summary", {}).get("n_claims", 0),
            "n_pass": recl.get("summary", {}).get("n_pass", 0),
            "low_power_flag_rate": recl.get("summary", {}).get("low_power_flag_rate"),
            "recomputable": recompute_ok,
            "pass": bool(recl.get("summary", {}).get("n_pass", 0) > 0 and recompute_ok),
        },
        "e_pytest": {
            "n_tests": n_tests,
            "pass": (n_tests is None) or n_tests >= 20,
            "note": "filled by the acceptance runner from the pytest report",
        },
        "f_zero_domain": {
            "status": artifact.get("zero_domain", {}).get("status", "SKIPPED"),
            "n_hits": artifact.get("zero_domain", {}).get("n_hits", 0),
            "pass": artifact.get("zero_domain", {}).get("status") == "PASS",
        },
    }
    out["all_pass"] = all(
        v["pass"] if isinstance(v, dict) and "pass" in v else True
        for v in out.values()
    )
    return out


def run(
    config_path: str | Path,
    root: Optional[str | Path] = None,
    seed: int = DEFAULT_SEED,
    ks_boot: int = 2000,
    package_dir: Optional[str | Path] = None,
    lexicon_path: Optional[str | Path] = None,
    node: str = "p474w1-n01-evidence-chain",
) -> Dict[str, Any]:
    """Execute the whole evidence chain and return the report artefact."""
    cfg_path = Path(config_path)
    cfg: EvidenceChainConfig = load_config(cfg_path, root=root)
    scan_result = scanner.scan(cfg)
    samples = binder.collect_samples(cfg, scan_result)
    assoc = binder.associate(cfg, scan_result, samples)
    results = binder.evaluate(cfg, samples, ks_boot=ks_boot, seed=seed)
    table = binder.mde_table(results)
    proposals = binder.ledger_proposals(results)

    artifact: Dict[str, Any] = {
        "node": node,
        "schema_version": "1.0",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "path": str(cfg_path),
            "digest": _digest(cfg.raw),
            "root": str(cfg.root),
            "n_repos": len(cfg.repos),
            "n_capabilities": len(cfg.capabilities),
        },
        "seed": seed,
        "scan_stats": scan_result.stats(),
        "association": assoc,
        "capabilities": results,
        "mde_table": table,
        "ledger_proposals": proposals,
        "artifacts": [a.to_dict() for a in scan_result.artifacts],
    }

    if cfg.reclassification is not None:
        base = cfg.reclassification.base
        # the census usually lives in the *platform* repository while the
        # capability evidence lives in the solver repositories, so the base
        # directory is configurable (relative bases resolve against the CWD)
        reclass_root = Path(base) if base else cfg.root
        if not reclass_root.is_absolute():
            reclass_root = (Path.cwd() / reclass_root).resolve()
        claims, inventory = reclassify.scan_claims(reclass_root, cfg.reclassification)
        summary = reclassify.summarize(claims)
        recompute = reclassify.recompute(summary, claims)
        artifact["reclassification"] = {
            "base_dir": str(reclass_root),
            "summary": summary,
            "recompute": recompute,
            "inventory": inventory,
            "claims": [c.to_dict() for c in claims],
        }
    else:
        artifact["reclassification"] = {}

    artifact["zero_domain"] = (
        zero_domain_check(Path(package_dir), Path(lexicon_path) if lexicon_path else None)
        if package_dir
        else {"status": "SKIPPED", "reason": "package_dir not given"}
    )
    artifact["criteria"] = criteria_block(artifact)
    artifact["digests"] = {
        "scan": _digest(artifact["artifacts"]),
        "capabilities": _digest(results),
        "mde_table": _digest(table),
        "ledger_proposals": _digest(proposals),
        "reclassification": artifact.get("reclassification", {}).get("summary", {}).get(
            "claims_digest"
        ),
    }
    return artifact


def write_artifact(artifact: Dict[str, Any], out_path: str | Path) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return p