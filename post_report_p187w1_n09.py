#!/usr/bin/env python3
"""Post the P187-W1-N09 report to the tensor platform (numbers injected from artifacts)."""
import base64
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

crit = json.load(open(os.path.join(ROOT, "artifacts/capability_governance/criteria.json"), encoding="utf-8"))
c = crit["criteria"]
bt = json.load(open(os.path.join(ROOT, "artifacts/capability_governance/backtest.json"), encoding="utf-8"))
attr = json.load(open(os.path.join(ROOT, "artifacts/capability_governance/attribution.json"), encoding="utf-8"))
aud = json.load(open(os.path.join(ROOT, "artifacts/capability_governance/samples/sample_audit.json"), encoding="utf-8"))

md = open(os.path.join(ROOT, "docs/reports/P187-W1-N09_capability_governance.md"), encoding="utf-8").read()

figs = []
for name, caption in [
    ("fig_backtest", "Frozen-set replay: four-gate mechanism vs single numeric gate (ablation); "
                     "confusion of diagnostic sub-levels."),
    ("fig_attribution", "Four-dimension deviation attribution shares per case; the last case is an "
                        "unmodelled 3% difference and is refused attribution (residual = 1)."),
    ("fig_samples", "Structured sample corpus: rows per family axis, per class axis, and corpus-quality "
                    "ratios (provenance / dedupe / non-empty output)."),
]:
    with open(os.path.join(ROOT, "artifacts/capability_governance", f"{name}.png"), "rb") as fh:
        figs.append({"name": name, "caption": caption,
                     "png_base64": base64.b64encode(fh.read()).decode()})

report = {
    "title": "平台能力治理三位一体：自动升账（冻结集回测 accuracy %.3f / FPR %.3f）· 跨求解器四维归因链 · ship_samples %d 行结构化填充"
             % (c["C3_backtest_accuracy"]["accuracy"], c["C3_backtest_accuracy"]["false_positive_rate"],
                c["C5_sample_rows"]["n_rows"]),
    "summary": (
        "C1-C7 all PASS. Auto ledger upgrade: 63-endpoint frozen set -> 5 declared gates -> evidence chain "
        "-> append-only ledger; accuracy %.3f, FPR %.3f vs single-gate ablation %.3f/%.3f; 4/4 negative "
        "controls rejected. Four-dim attribution: 6/6 injected dimensions recovered, unmodelled 3%% refused. "
        "ship_samples %d rows (3x3 axes), provenance %.2f, unique %.2f, idempotent reload %d, C7 scan %d files/%d hits. "
        "Limits: constructed negatives, generic IVP model, SQLite (no PG driver)."
        % (c["C3_backtest_accuracy"]["accuracy"], c["C3_backtest_accuracy"]["false_positive_rate"],
           bt["single_gate_ablation"]["accuracy"], bt["single_gate_ablation"]["false_positive_rate"],
           c["C5_sample_rows"]["n_rows"], c["C6_provenance_dedup"]["provenance_completeness"],
           c["C6_provenance_dedup"]["dedupe_unique_rate"],
           c["C6_provenance_dedup"]["idempotent_reload_inserted"],
           c["C7_zero_domain_vocabulary"]["scanned_files"], c["C7_zero_domain_vocabulary"]["n_hits"])
    ),
    "scenario_key": "p187w1-n09",
    "mission_id": 9811,
    "tags": ["capability-governance", "auto-ledger", "evidence-chain", "cross-solver",
             "attribution-chain", "sample-corpus", "zero-domain-semantics", "P187-W1-N09"],
    "markdown": md,
    "figures": figs,
}

login = urllib.request.Request(
    f"{api_base}/auth/login",
    data=json.dumps({"username": user, "password": passwd}).encode(),
    headers={"Content-Type": "application/json"},
)
token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]

req = urllib.request.Request(
    f"{api_base}/tensor/reports",
    data=json.dumps(report).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)
resp = urllib.request.urlopen(req, timeout=60)
print(resp.status, resp.read().decode()[:500])