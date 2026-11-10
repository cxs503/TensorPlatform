"""Publish the G-OPT maturation report to the platform report API.

Credentials come from the environment (TENSOR_API_BASE / TENSOR_AGENT_USER /
TENSOR_AGENT_PASS); nothing is hardcoded.  Figures are attached as base64 PNG
exactly as they exist on disk (no regeneration, so the report and the artifacts
cannot diverge).
"""
import base64
import json
import os
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def post(path, payload, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


FIGS = [
    ("fig1_objectives_vs_LB.png", "artifacts/gopt/slender_body_lb/fig1_objectives_vs_LB.png",
     "C_D (wetted-area basis) and R_t at fixed displacement vs L/D; stars mark each curve optimum"),
    ("fig2_normalised_with_bands.png", "artifacts/gopt/slender_body_lb/fig2_normalised_with_bands.png",
     "Normalised objectives vs L/D with the literature band (4.5-5.0) and practice band (7.0-8.5); "
     "only the viscous+form-factor objective has an interior optimum (L/D = 6.36)"),
    ("fig3_model_vs_experiment.png", "artifacts/gopt/slender_body_lb/fig3_model_vs_experiment.png",
     "Platform resistance model vs the canonical published experimental C_t; shaded band is outside "
     "the calibrated Froude envelope (Fr > 0.62)"),
    ("fig4_optimizer_convergence.png", "artifacts/gopt/slender_body_lb/fig4_optimizer_convergence.png",
     "CMA-ES best-so-far history and both optimizers landing on the same interior optimum"),
    ("fig5_benchmark_errors.png", "artifacts/gopt/fig5_benchmark_errors.png",
     "CMA-ES and SLSQP absolute error against closed-form optima over all reference problems (log scale)"),
]

SUMMARY = (
    "G-OPT matured in optimizer/gopt: CMA-ES (restarts, noise resampling), own SLSQP with a dense-QP KKT "
    "certificate, adjoint protocol with a Taylor/FD/negative-control auditor. 40 pytest cases; "
    "closed-form optima reached (CMA-ES 97.5%, SLSQP 100%, scipy 5e-7). Fixed-displacement L/D case: "
    "geometry gate passes (area +1.36%, Cb 0.667); model -7.0%/+2.5% in envelope, +43.7% out; C_D "
    "monotone -> interior premise falsified; viscous+form optimum L/D=6.36 is +27% above the literature "
    "band -> honest FAIL."
)


def main() -> dict:
    token = post("/auth/login", {"username": USER, "password": PASS})
    token = token.get("access_token") or token.get("token")
    with open(os.path.join(REPO, "docs/gopt/GOPT_MATURATION_REPORT.md")) as fh:
        md = fh.read()
    figures = []
    for name, rel, cap in FIGS:
        with open(os.path.join(REPO, rel), "rb") as fh:
            figures.append({"name": name, "caption": cap,
                            "png_base64": base64.b64encode(fh.read()).decode()})
    payload = {
        "title": "G-OPT optimization library maturation: CMA-ES / SLSQP / adjoint protocol / "
                 "fixed-displacement L/D case (m9047)",
        "summary": SUMMARY,
        "scenario_key": "campaign_dag:P475W1-N01",
        "mission_id": 9047,
        "tags": ["gopt", "optimization", "cma-es", "slsqp", "adjoint", "tensorplatform", "P475W1-N01"],
        "markdown": md,
        "figures": figures,
    }
    return post("/tensor/reports", payload, token)


if __name__ == "__main__":
    res = main()
    print(json.dumps({k: v for k, v in res.items() if k != "markdown"}, ensure_ascii=False)[:1200])