#!/usr/bin/env python3
"""Post the p351w1_n01 conservation-contract report to the tensor platform."""

import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

login = urllib.request.Request(
    f"{api_base}/auth/login",
    data=json.dumps({"username": user, "password": passwd}).encode(),
    headers={"Content-Type": "application/json"},
)
token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]

md = open(os.path.join(ROOT, "docs/reports/p351w1_n01_conservation_contract.md"),
          encoding="utf-8").read()
figs = []
for name, caption in [
    ("fig1_observed_order", "Interface transfer: observed order vs design order, with a "
                            "fixed-amplitude negative control"),
    ("fig2_interface_flux", "Two-sided interface flux against the closed form and the "
                            "pointwise error at two mesh sizes"),
    ("fig3_coupled_loop", "Certified fixed-point loop: residual histories and early "
                          "divergence detection (rho=1.2)"),
]:
    path = os.path.join(ROOT, "artifacts/conservation_contract_p351w1_n01/figures", f"{name}.png")
    with open(path, "rb") as fh:
        figs.append({"name": name, "caption": caption,
                     "png_base64": base64.b64encode(fh.read()).decode()})

report = {
    "title": "跨求解器守恒契约与耦合循环收敛证书：残差门禁 · 观测阶证书 · Aitken 证书化定点迭代",
    "summary": (
        "Conservation-law contract + interface residual/order gates + certified fixed-point loop. "
        "Diffusion fixture 192^2: two-sided flux residual 4.4e-7, closed-form error 6.7e-5 (<=1e-4); "
        "jittered-flux control 0.256 FAIL. Orders 1.03/2.04/3.07 (gates 0.95/1.95), noisy control -0.013. "
        "rho=1.2 DIVERGED at iter 3; Aitken -72.5% iterations; sensitivity 6.6e-13; 62 pytest; "
        "domain-scan hits 0. Limits: 1-D operator, analytic walls."
    ),
    "scenario_key": "p351w1-n01",
    "mission_id": 10720,
    "tags": [
        "coupling", "conservation-contract", "interface-operator", "convergence-order",
        "fixed-point-iteration", "aitken-relaxation", "certificate", "negative-control",
        "zero-domain-semantics", "TensorPlatform",
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
    resp = urllib.request.urlopen(req, timeout=60)
    print("posted:", json.loads(resp.read()))
except Exception as exc:  # noqa: BLE001
    body = exc.read().decode() if hasattr(exc, "read") else ""
    print(f"post failed: {exc} {body}", file=sys.stderr)
    sys.exit(1)