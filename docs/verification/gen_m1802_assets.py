"""m1802 验证资产复位复算：从存档证据重算关键量并出图（零手抄，全部程序读取）。

输入（全部为版本化资产）:
  tests/data/verifier_assets/hydrostatics/hydro_curves_kcs_snapshot.json
  tests/data/verifier_assets/propulsion/prop_openwater_fixture.json
输出:
  artifacts/m1802/metrics.json
  artifacts/m1802/fig1_hydro_curves_identities.png
  artifacts/m1802/fig2_propulsion_openwater_gate.png
"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ASSETS = os.path.join(ROOT, "tests", "data", "verifier_assets")
OUT = os.path.join(ROOT, "artifacts", "m1802")
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "verifier"))
sys.path.insert(0, os.path.join(ROOT, "tools", "verifier", "geometry"))
import audit_hydro_curves as A            # noqa: E402
import propulsion_openwater_gate as G     # noqa: E402

snap = json.load(open(os.path.join(ASSETS, "hydrostatics", "hydro_curves_kcs_snapshot.json")))
res = A.audit(snap)

cur = snap["curves"]
meta = snap["meta"]
T = float(meta["T"])
drafts = np.asarray(cur["drafts"], float)
nab = np.asarray(cur["displacement"], float) / 1.025      # t -> m^3
awp = np.asarray(cur["Awp"], float)
cb = np.asarray(cur["Cb"], float)
vcb = np.asarray(cur["VCB"], float)

dNab_dT = np.gradient(nab, drafts)
i3 = {c["id"]: c for c in res["criteria"]}["I3"]
i1 = {c["id"]: c for c in res["criteria"]}["I1"]
vcb_rel_T = float(np.max(np.abs(vcb / drafts - 1.0)))

fx_path = os.path.join(ASSETS, "propulsion", "prop_openwater_fixture.json")
fx = json.load(open(fx_path))
gres = G.run(fx_path)
b_rows = [r for r in fx["b_series"] if isinstance(r.get("resp"), dict) and "KT" in r["resp"]]
J = np.array([r["req"]["J"] for r in b_rows], float)
KT = np.array([r["resp"]["KT"] for r in b_rows], float)
KQ = np.array([r["resp"]["KQ"] for r in b_rows], float)
ETA = np.array([r["resp"]["eta0"] for r in b_rows], float)
ETA_ID = J * KT / (2 * np.pi * KQ)                        # 恒等式：全部 J>0 情形
mask = J > 0
eta_dev = (ETA[mask] - ETA_ID[mask]) / ETA_ID[mask]

fig, ax = plt.subplots(1, 2, figsize=(11.0, 4.2))
ax[0].plot(drafts, cb, "o-", label="platform Cb (captured)")
ax[0].axhline(meta["Cb"], ls="--", c="k", lw=1, label=f"declared Cb = {meta['Cb']}")
ax[0].set_xlabel("draft T [m]"); ax[0].set_ylabel("block coefficient Cb [-]")
ax[0].set_title("Cb must stay <= 1 and vary with draft"); ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)
ax[1].plot(drafts, dNab_dT, "s-", label="d∇/dT from table")
ax[1].plot(drafts, awp, "^--", label="Awp from table")
ax[1].set_xlabel("draft T [m]"); ax[1].set_ylabel("m^3/m  vs  m^2")
ax[1].set_title(f"identity d∇/dT = Awp: rel.err = {i3['rel_err']*100:.1f}% (gate 1%)")
ax[1].grid(alpha=.3); ax[1].legend(fontsize=8)
fig.suptitle("KCS hydro-curve evidence (m1802 replay of captured probe)", fontsize=10)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig1_hydro_curves_identities.png"), dpi=130); plt.close(fig)

fig, ax = plt.subplots(1, 2, figsize=(11.0, 4.2))
ax[0].plot(J, KT, "o-", label="KT")
ax[0].plot(J, 10 * KQ, "s-", label="10 KQ")
ax[0].set_xlabel("advance ratio J"); ax[0].set_ylabel("KT / 10KQ")
ax[0].set_title("KT, 10KQ vs J (Wageningen B, Z=4, P/D=0.6)")
ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)
ax[1].plot(J[mask], ETA[mask], "o-", label="platform eta0")
ax[1].plot(J[mask], ETA_ID[mask], "--", label="momentum identity J·KT/(2πKQ)")
ax[1].set_xlabel("advance ratio J"); ax[1].set_ylabel("open-water efficiency eta0")
ax[1].set_title(f"eta0 hard-capped: max dev = {abs(eta_dev).max()*100:.1f}%")
ax[1].grid(alpha=.3); ax[1].legend(fontsize=8)
fig.suptitle("Propulsion open-water gate evidence (m1584 fixture replay)", fontsize=10)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig2_propulsion_openwater_gate.png"), dpi=130); plt.close(fig)

metrics = {
    "hydrostatics": {
        "n_drafts": int(drafts.size), "drafts": drafts.tolist(),
        "Cb_values": cb.tolist(), "Cb_spread": float(np.ptp(cb)),
        "Cb_declared": meta["Cb"],
        "criterion_I1": {"rel_err": i1["rel_err"], "pass": i1["pass"]},
        "identity_dNab_dT_vs_Awp_rel_err": i3["rel_err"],
        "VCB_over_draft_max_rel_dev": vcb_rel_T,
        "audit_verdict": res["verdict"],
    },
    "propulsion": {
        "n_points": int(mask.sum()),
        "eta0_max": float(ETA[mask].max()),
        "eta0_identity_max_rel_dev": float(abs(eta_dev).max()),
        "gate_verdict": gres.get("verdict"),
        "n_fail": gres.get("n_fail"), "n_pass": gres.get("n_pass"),
    },
}
json.dump(metrics, open(os.path.join(OUT, "metrics.json"), "w"), ensure_ascii=False, indent=2)
print(json.dumps(metrics, ensure_ascii=False, indent=1)[:1800])