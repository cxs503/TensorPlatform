"""Post the P233W2-N08 report to the platform report API (figures embedded)."""

import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "dispatch_readiness")
DOC = os.path.join(ROOT, "docs", "reports", "P233W2-N08_interface_readiness_gate.md")
MISSION_ID = 10679


def env():
    out = {}
    for line in open("/root/tp-agent/agent.env"):
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value.strip().strip('"').strip("'")
    return out


E = env()
BASE = E["TENSOR_API_BASE"]


def login():
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=json.dumps({"username": E["TENSOR_AGENT_USER"], "password": E["TENSOR_AGENT_PASS"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    payload = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return payload.get("access_token") or payload["data"]["access_token"]


CAPTIONS = {
    "roc_curves": "ROC of the readiness gate at three reconstructed moments (@consult primary, @stop, @dispatch). AUC 0.806 for the primary target; the dotted vertical line is the frozen 5% FPR budget.",
    "confusion_matrix": "Primary confusion matrix (2509 labelled nodes): TP=11, FN=6, FP=113, TN=2379 -> recall 64.7%, FPR 4.53%.",
    "mode_sensitivity": "Contract requirement-mode policy frontier: FPR on 2440 healthy (done) nodes vs coverage of the 17 upstream-gap blocks. declared = platform's contract-parallel policy; delivered/terminal_success = strict.",
    "permutation_null": "AC3 negative control: 200 label permutations, AUC 95% interval [0.475, 0.564] covers 0.5 while the observed AUC 0.806 sits far outside it.",
    "label_strata": "Label strata of the 2802 terminal-state node universe (log scale): 2440 done, 52 blocked without gap, 17 upstream-gap positives, 293 excluded proven infra failures.",
}


def main():
    token = login()
    markdown = open(DOC, "r", encoding="utf-8").read()
    figures = []
    for name in sorted(os.listdir(os.path.join(ART, "figures"))):
        if not name.endswith(".png"):
            continue
        stem = os.path.splitext(name)[0]
        with open(os.path.join(ART, "figures", name), "rb") as handle:
            figures.append(
                {
                    "name": name,
                    "caption": CAPTIONS.get(stem, stem),
                    "png_base64": base64.b64encode(handle.read()).decode(),
                }
            )
    body = {
        "title": "P233W2-N08 派单前置接口-就绪机器门成熟化：契约三态判定 + 40+ 历史终态节点回测",
        "summary": (
            "Pre-dispatch interface readiness gate matured: contract-driven READY/NOT_READY/CONFLICT with "
            "fail-closed deferral and human-readable attribution; replayed on 2802 historical terminal nodes "
            "(2509 labelled, 17 upstream-gap positives): recall 64.7% (CI 41-83%), FPR 4.53% (CI 3.8-5.4%), "
            "AUC 0.806, permutation null 95% [0.475,0.564] covers 0.5. Honest finding: plan 233's four blocked "
            "nodes were infra (agent_lost), not upstream delivery gaps."
        ),
        "scenario_key": "platform",
        "mission_id": MISSION_ID,
        "tags": ["dispatch-readiness", "campaign-dag", "machine-gate", "backtest", "zero-domain-semantics"],
        "markdown": markdown,
        "figures": figures,
    }
    req = urllib.request.Request(
        BASE + "/tensor/reports",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        print(resp.status, resp.read()[:400].decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read()[:600].decode("utf-8", "ignore"))
        sys.exit(1)


if __name__ == "__main__":
    main()