"""Publish the tp-ver-8 report and register capability card CAP-006.

Idempotent-ish: posting twice creates two reports, so run once per mission.

    python scripts/m1652/publish_report.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
OUT = os.path.join(ROOT, "outputs", "m1652")
DOC = os.path.join(ROOT, "docs", "audit",
                   "imo_msc137_76_manoeuvrability_audit_m1652.md")

FIGURES = [
    ("fig_turning_circle.png",
     "35° 回转航迹（x/L vs y/L，两套 MMG 水动力系数集）；红色点线为 IMO "
     "MSC.137(76) C1 包络：纵距 ≤ 4.5 L、战术直径 ≤ 5 L。两条航迹均远在包络内。"),
    ("fig_zigzag.png",
     "10°/10° 与 20°/20° Z 形首向时程。红色点线为法定首过冲限值"
     "（10/10 取 L/V ≥ 2.5 s 支 → 10°+20°；20/20 → 20°+25°）。"
     "右图可见 20/20 峰值越过限值线。"),
    ("fig_compliance_matrix.png",
     "判据 × 系数集的相对余量矩阵（余量/限值；负值=违规）。可见 C1–C3 均有"
     "正余量，C4 在两套系数下均为负 → 裁决稳健。"),
]


def _post(path: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:800]}


def login() -> str:
    status, data = _post("/auth/login", {
        "username": os.environ["TENSOR_AGENT_USER"],
        "password": os.environ["TENSOR_AGENT_PASS"],
    })
    if status != 200 or "access_token" not in data:
        raise SystemExit(f"login failed {status}: {data}")
    return data["access_token"]


def main() -> int:
    token = login()
    md = open(DOC, encoding="utf-8").read()
    md5 = hashlib.md5(md.encode("utf-8")).hexdigest()

    # ---- figures -> base64 -------------------------------------------
    figs = []
    for name, caption in FIGURES:
        p = os.path.join(OUT, name)
        figs.append({"name": name, "caption": caption,
                     "png_base64": base64.b64encode(open(p, "rb").read()).decode()})

    summary = (
        "验证欠账复核：对已合并的操纵性模块（mission W3-N08 风-流-冰耦合 MMG）"
        "首次执行 IMO MSC.137(76) 法规数值判据审计。原模块仅有 code-to-code 自证，"
        "绝对精度 WITHHELD。本单新增 imo_criteria.py（C1 回转 / C2 初始回转 / "
        "C3 10-10 Z 形 / C4 20-20 Z 形），对 320 m KVLCC2 级 VLCC、15 kn 工况在"
        "两套水动力系数集下裁决：C1/C2/C3 全部合规；C4 首过冲角 26.49°(kvlcc2_ref) "
        "与 29.15°(slender_body) 均超 25° 限值 → 稳健不合规。另施加 +20% "
        "Y_v/N_r 扰动，战术直径 2.876 L→5.185 L，C1 翻为不合规，量化了合规余量的"
        "脆弱性。21 项新测试全绿；对实验的绝对精度仍 WITHHELD（正典无操纵性 EFD 量）。"
    )

    payload = {
        "title": "IMO MSC.137(76) 操纵性最低标准审计：验证欠账复核（VLCC/KVLCC2, MMG 3-DOF）",
        "summary": summary,
        "scenario_key": "maneuvering",
        "mission_id": 1652,
        "tags": ["verification-debt", "maneuvering", "IMO-MSC.137(76)",
                 "MMG", "KVLCC2", "honest-fail", "evidence-audit"],
        "markdown": md,
        "figures": figs,
    }
    status, resp = _post("/tensor/reports", payload, token)
    print("report status", status)
    print(json.dumps(resp, ensure_ascii=False)[:600])
    report_id = None
    if isinstance(resp, dict):
        report_id = (resp.get("report_id") or resp.get("id")
                     or (resp.get("data") or {}).get("report_id")
                     or (resp.get("data") or {}).get("id"))
    print("report_id =", report_id, "md5 =", md5)

    # ---- capability card CAP-006 -------------------------------------
    audit = json.load(open(os.path.join(OUT, "imo_msc137_76_audit.json"),
                           encoding="utf-8"))
    sets = list(audit["results"].keys())
    c4 = [audit["results"][s]["criteria"]["C4_zigzag_20_20"]["checks"][0]
          for s in sets]
    card = {
        "card_id": "CAP-006",
        "name": "TensorPlatform IMO MSC.137(76) Manoeuvrability Compliance "
                "(wind-current-ice coupled MMG, VLCC/KVLCC2)",
        "version": "1.0.0",
        "repo": "TensorPlatform",
        "discipline": "maneuvering",
        "interface": {
            "input": {
                "vessel": {"type": "object",
                           "description": "Vessel particulars (L, B, T, Cb, "
                                          "rudder area, propeller diameter); "
                                          "default data.VESSEL_VLCC",
                           "unit": "m"},
                "speed_ms": {"type": "number",
                             "description": "Approach speed for the trial",
                             "unit": "m/s", "range": {"min": 1.0, "max": 20.0}},
                "coefficient_sets": {
                    "type": "object",
                    "description": "Named MMG derivative sets to audit; the "
                                   "verdict must agree across all of them"},
            },
            "output": {
                "results": {"type": "object",
                            "description": "Per coefficient set: C1-C4 criteria "
                                           "with value / limit / margin / "
                                           "pass / borderline flags"},
                "overall_passed": {"type": "boolean",
                                   "description": "True only if every criterion "
                                                  "passes for every set"},
                "coefficient_set_robust": {
                    "type": "boolean",
                    "description": "True if all sets agree, making the verdict "
                                   "independent of the derivative choice"},
                "absolute_accuracy_claim": {
                    "type": "string",
                    "description": "Always WITHHELD - no EFD manoeuvring "
                                   "baseline is available offline"},
            },
            "format": "JSON",
        },
        "applicability": {
            "parameter_domain": {
                "L_pp": {"min": 100.0, "max": 400.0, "unit": "m"},
                "speed_ms": {"min": 1.0, "max": 20.0, "unit": "m/s"},
                "rudder_deg": {"min": 10.0, "max": 35.0, "unit": "deg"},
            },
            "regime": "Calm-water 3-DOF manoeuvring; statutory "
                      "minimum-manoeuvrability verdict for ships >= 100 m. "
                      "Wind/current/ice loads available in the parent module "
                      "but not used by the standards audit (IMO trials are "
                      "calm-water).",
            "geometry_type": "monohull displacement (VLCC / tanker form)",
            "dimensionality": "3D",
        },
        "dependencies": {
            "code_version": "TensorPlatform agent/m1652",
            "environment": {"python": "3.11"},
            "external_libraries": ["numpy", "matplotlib"],
        },
        "resource_requirements": {
            "gpu": False, "min_cpu_cores": 1, "min_ram_gb": 1,
            "estimated_runtime_hours": 0.01, "storage_gb": 0.01,
        },
        "validation_evidence": {
            "report_id": str(report_id or "pending"),
            "report_md5": md5,
            "verification_status": "HONEST_FAIL",
            "reference_values": {
                "C1_advance_over_L_max": {
                    "value": 4.5, "tolerance": 0.0,
                    "source": "IMO Res. MSC.137(76)"},
                "C1_tactical_diameter_over_L_max": {
                    "value": 5.0, "tolerance": 0.0,
                    "source": "IMO Res. MSC.137(76)"},
                "C2_track_distance_over_L_max": {
                    "value": 2.5, "tolerance": 0.0,
                    "source": "IMO Res. MSC.137(76)"},
                "C3_first_overshoot_deg_high_LV_max": {
                    "value": 20.0, "tolerance": 0.0,
                    "source": "IMO Res. MSC.137(76), L/V >= 2.5 s"},
                "C4_first_overshoot_deg_max": {
                    "value": 25.0, "tolerance": 0.0,
                    "source": "IMO Res. MSC.137(76)"},
            },
        },
        "failure_modes": [
            {"mode": "20/20 zigzag overshoot over-prediction",
             "symptom": f"C4 first overshoot {c4[0]['value']:.2f} deg "
                        f"({sets[0]}) and {c4[1]['value']:.2f} deg "
                        f"({sets[1]}) exceed the 25 deg statutory limit",
             "mitigation": "Calibrate the MMG derivative set against Z-test "
                           "data before any regulatory-compliance use; treat "
                           "C4 verdicts as model-capability diagnostics only",
             "severity": "major"},
            {"mode": "Wrong L/V branch selected for C3",
             "symptom": "applying the 10 deg limit to a long slow ship "
                        "(L/V = 41.5 s here) fails a compliant hull",
             "mitigation": "overshoot_limit_deg() returns the branch "
                           "explicitly and every result carries branch/LV_s",
             "severity": "critical"},
            {"mode": "Diverged simulation silently certified",
             "symptom": "NaN or infinite metric compared against a limit",
             "mitigation": "_check() fails any non-finite value and records "
                           "'value not finite, cannot certify'",
             "severity": "critical"},
            {"mode": "Compliance margin mistaken for accuracy",
             "symptom": "reading a 40 % margin below the limit as 40 % "
                        "modelling accuracy",
             "mitigation": "absolute_accuracy_claim is hard-wired to WITHHELD "
                           "and the withheld reasons are enumerated in output",
             "severity": "major"},
            {"mode": "C5/C6 absence read as compliance",
             "symptom": "stopping ability and course keeping silently treated "
                        "as passing",
             "mitigation": "run_imo_compliance() lists C5/C6 in 'withheld'",
             "severity": "minor"},
        ],
        "created_at": "2026-11-01T00:00:00Z",
        "status": "experimental",
    }
    cards_dir = os.path.join(ROOT, "docs", "capability_registry", "cards")
    cpath = os.path.join(cards_dir, "CAP-006-tensorplatform-imo-manoeuvrability.json")
    with open(cpath, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, ensure_ascii=False)
    print("wrote", cpath)

    # ---- registry index ----------------------------------------------
    ipath = os.path.join(ROOT, "docs", "capability_registry", "index.json")
    idx = json.load(open(ipath, encoding="utf-8"))
    if not any(c.get("card_id") == "CAP-006" for c in idx["cards"]):
        idx["cards"].append({
            "card_id": "CAP-006",
            "name": "TensorPlatform IMO MSC.137(76) Manoeuvrability Compliance",
            "repo": "TensorPlatform",
            "discipline": "maneuvering",
            "status": "experimental",
            "verification_status": "HONEST_FAIL",
        })
    idx["total_cards"] = len(idx["cards"])
    with open(ipath, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, indent=2, ensure_ascii=False)
    print("registry total_cards =", idx["total_cards"])

    with open(os.path.join(OUT, "report_publish_result.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"status": status, "report_id": report_id,
                   "report_md5": md5, "response": resp}, fh,
                  indent=2, ensure_ascii=False)
    return 0 if status in (200, 201) else 1


if __name__ == "__main__":
    sys.exit(main())