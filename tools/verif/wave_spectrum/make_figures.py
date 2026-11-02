"""m1801 出图 + 源码事实提取（数字全部从 artifacts/m1801/verify.json 读，零手抄）。"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
import reference as ref  # noqa: E402

ART = REPO / "artifacts" / "m1801"
V = json.loads((ART / "verify.json").read_text())
SHIP = Path("/srv/tensor/workspace/ship-performance-domain") # placeholder, overwritten below
SHIP = Path("/srv/tensor/workspace/ship-performance-platform")

# ---------- 事实提取：谱端点的内部频率网格（把"误差来源"钉在源码上）
src = (SHIP / "backend/api/seakeeping_advanced.py").read_text()
grid_fact = {}
m = re.search(r"frequency_range[^\n]*", src)
grid_fact["frequency_range_line"] = m.group(0).strip() if m else None
for line in src.split("\n"):
    if "np.linspace" in line or "arange" in line:
        grid_fact.setdefault("grid_lines", []).append(line.strip())
(V.setdefault("source_facts", {}))["seakeeping_advanced"] = grid_fact

# ---------- fig1 三谱族
fig, ax = plt.subplots(figsize=(7.2, 4.2))
Hs, Tp = 6.0, 10.0
w = ref.omega_grid(Tp, 3000, 5.0)
for name, f, c in [("ITTC/Bretschneider", ref.ittc_spectrum, "C0"),
                   ("JONSWAP γ=3.3", ref.jonswap_spectrum, "C3"),
                   ("PM (α=0.0081,β=0.74, 平台参数化)", ref.pm_spectrum, "C2")]:
    S = f(w, Hs, Tp)
    ax.plot(w, S, c, lw=1.8, label=f"{name}  m0={ref.trapezoid(S, w):.3f} m²")
ax.axvline(2 * math.pi / Tp, ls="--", c="k", lw=1, label=r"$\omega_p=2\pi/T_p$")
ax.set_xlim(0, 3.2); ax.set_xlabel(r"$\omega$ (rad/s)"); ax.set_ylabel(r"$S_\zeta(\omega)$ (m²·s)")
ax.set_title(f"wave spectra, Hs={Hs} m, Tp={Tp} s"); ax.legend(fontsize=7); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(ART / "fig1_spectra.png", dpi=150); plt.close(fig)

# ---------- fig2 矩收敛：数值 vs 解析
wp = 2 * math.pi / Tp; A, B = ref.ALPHA_PM * ref.G ** 2, ref.B_ITTC * wp ** 4
Ns = [100, 200, 400, 800, 1600, 3200, 6400, 12800]
e0, e2 = [], []
for n in Ns:
    g = ref.omega_grid(Tp, n, 20.0)
    mm = ref.discrete_moments(g, ref.spectrum_two_param(g, A, B))
    e0.append(ref.rel_err(mm["m0"], ref.analytic_m0(A, B)))
    e2.append(ref.rel_err(mm["m2"], ref.analytic_m2(A, B)))
fig, ax = plt.subplots(figsize=(7.2, 4.2))
ax.loglog(Ns, e0, "o-", label="m0")
ax.loglog(Ns, e2, "s-", label="m2")
ax.axhline(3.0, ls="--", c="r", label="3% gate")
ax.set_xlabel("grid points N (0<ω≤20ωp)"); ax.set_ylabel("relative error (%)")
ax.set_title("spectral-moment convergence: trapezoid vs closed form"); ax.legend(); ax.grid(alpha=.3, which="both")
fig.tight_layout(); fig.savefig(ART / "fig2_moment_convergence.png", dpi=150); plt.close(fig)

# ---------- fig3 ε 随积分上限对数漂移
facs = [x["wmax_over_wp"] for x in V["grid_dependence_eps"]]
epsv = [x["eps"] for x in V["grid_dependence_eps"]]
m4v = [x["m4"] for x in V["grid_dependence_eps"]]
fig, ax = plt.subplots(figsize=(7.2, 4.2))
ax.semilogx(facs, epsv, "o-", c="purple", label=r"$\varepsilon$")
ax.set_xlabel(r"integration upper bound $\omega_{max}/\omega_p$")
ax.set_ylabel(r"spectral width $\varepsilon$", color="purple")
ax2 = ax.twinx(); ax2.semilogx(facs, m4v, "s--", c="gray", label="m4")
ax2.set_ylabel("m4 (m²/s⁴)", color="gray")
ax.set_title(r"$\varepsilon=\sqrt{1-m_2^2/(m_0m_4)}$ is grid-dependent: $m_4$ diverges ~$\int A/\omega\,d\omega$")
ax.grid(alpha=.3, which="both")
fig.tight_layout(); fig.savefig(ART / "fig3_epsilon_divergence.png", dpi=150); plt.close(fig)

# ---------- fig4 逐判据误差 vs 3% 门
rows = V["dynamic_rows"]
names = [r["name"] for r in rows]
errs = [max(r["err_pct"], 1e-6) for r in rows]
fig, ax = plt.subplots(figsize=(7.6, max(4.0, 0.16 * len(rows))))
cols = ["C0" if e < V["gate_pct"] else "C3" for e in errs]
ax.barh(range(len(rows)), errs, color=cols)
ax.set_yticks(range(len(rows))); ax.set_yticklabels(names, fontsize=4.5)
ax.set_xscale("log"); ax.axvline(V["gate_pct"], ls="--", c="r", label="3% gate")
ax.axvline(15, ls=":", c="darkred", label="15% unacceptable")
ax.set_xlabel("relative error (%)"); ax.invert_yaxis(); ax.legend(fontsize=7)
ax.set_title(f"m1801 verdicts: {V['n_pass']} PASS / {V['n_fail']} FAIL of {V['n_dynamic']}")
fig.tight_layout(); fig.savefig(ART / "fig4_verdict.png", dpi=150); plt.close(fig)

(ART / "verify.json").write_text(json.dumps(V, ensure_ascii=False, indent=1))
print("figs written:", sorted(p.name for p in ART.glob("*.png")))
print("grid facts:", json.dumps(grid_fact, ensure_ascii=False)[:600])