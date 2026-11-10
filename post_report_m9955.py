#!/usr/bin/env python3
"""Post the uq_framework v1 acceptance report to the tensor platform.

Six-element paper-grade report (case / method / numbers / figures / notes /
error judgement) with figures embedded from artifacts/uq_framework_v1/.
"""
import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")
MISSION = int(os.environ.get("MISSION_ID", "9955"))

login = urllib.request.Request(
    f"{api_base}/auth/login",
    data=json.dumps({"username": user, "password": passwd}).encode(),
    headers={"Content-Type": "application/json"},
)
token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]

summary_json = json.load(open(os.path.join(ROOT, "artifacts/uq_framework_v1/acceptance_summary.json")))
md = open(os.path.join(ROOT, "docs/uq_framework_v1.md"), encoding="utf-8").read()

FIGS = [
    ("fig_uq_overview", "Left: Monte Carlo moment error vs sample budget against the sqrt(2/n) "
                        "statistical scale and the 2% acceptance line. Right: observed GCI order vs "
                        "formal order for schemes of order 1, 2 and 4."),
    ("full_matrix_case_ci", "All three uncertainty legs in one case: mean, CI of the mean and 5-95% "
                            "output range for Monte Carlo and for the chaos surrogate."),
    ("full_matrix_case_sensitivity", "Sobol' first-order and total-order variance shares of the "
                                     "full-matrix case (closure = 1 by construction)."),
    ("in_quadratic_interaction_ci", "Interaction case (x0*x1 + x2): the surrogate reproduces the "
                                    "analytic mean to 1e-15 while Monte Carlo carries 7e-3 error."),
    ("num_elliptic_p2_sensitivity", "Numerical-error case has no uncertain inputs, so the sensitivity "
                                    "panel is deliberately empty -- the report never invents indices."),
]
figs = []
for name, caption in FIGS:
    path = os.path.join(ROOT, "artifacts/uq_framework_v1", f"{name}.png")
    if not os.path.exists(path):
        print(f"missing figure {path}", file=sys.stderr)
        continue
    with open(path, "rb") as fh:
        figs.append({"name": name, "caption": caption,
                     "png_base64": base64.b64encode(fh.read()).decode()})

report = {
    "title": "系统化不确定度量化框架 v1（输入传播 MC+PCE / 数值误差 GCI / 模型形式 BMA）· 10 案例全绿",
    "summary": (
        "Domain-free UQ framework (config -> run_uq -> result -> JSON report): input propagation "
        "(LHS MC + gPC), numerical error (3-grid GCI + order check + residual/round-off gates), "
        "model form (spread, BMA, total-variance split). 10 cases pass, 65 pytest. MC mean err "
        "2.7e-4, variance 1.9% at n=1000; chaos moments exact to 1e-15 at 56-77x fewer evals; GCI "
        "recovers orders 1/2/4 within 3.2/0.06/0.78%. Honest FAIL: BMA = selection when validation "
        "error is negligible (15.95% vs 2.00% equal weights)."
    ),
    "scenario_key": "p337w1-n05-uq-framework",
    "mission_id": MISSION,
    "tags": [
        "uncertainty-quantification", "polynomial-chaos", "monte-carlo", "sobol-sensitivity",
        "GCI", "model-form-uncertainty", "bayesian-model-averaging", "zero-domain-semantics",
        "TensorPlatform", "honest-FAIL",
    ],
    "markdown": md,
    "figures": figs,
}

req = urllib.request.Request(
    f"{api_base}/tensor/reports",
    data=json.dumps(report).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)
try:
    resp = urllib.request.urlopen(req, timeout=120)
    print("posted:", json.loads(resp.read()))
except Exception as exc:  # noqa: BLE001
    body = exc.read().decode() if hasattr(exc, "read") else ""
    print(f"post failed: {exc} {body}", file=sys.stderr)
    sys.exit(1)
print("verdict:", json.dumps(summary_json["verdict"]))