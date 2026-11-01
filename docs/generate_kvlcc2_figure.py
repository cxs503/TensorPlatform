#!/usr/bin/env python3
"""Generate KVLCC2 resistance validation figure for mission 1768 report."""
import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

G = 9.81
RHO_SEA = 1025.0
NU_SEA = 1.19e-6

def ittc57_cf(re):
    lg = math.log10(re) - 2.0
    return 0.075 / (lg * lg)

def wetted_surface(lwl, b, t, cb, cm, cwp, abt=0.0):
    s = (lwl * (2.0 * t + b) * math.sqrt(cm) *
         (0.453 + 0.4425 * cb - 0.2862 * cm - 0.003467 * b / t + 0.3696 * cwp))
    if abt > 0.0:
        s += 2.38 * abt / cb
    return s

def form_factor_1pk(lwl, b, t, cb, cm, vol, cstern=0.0, lcb_pct=0.0):
    c14 = 1.0 + 0.011 * cstern
    cp = cb / cm
    lr = lwl * (1.0 - cp + 0.06 * cp * lcb_pct / (4.0 * cp - 1.0))
    if lr <= 0:
        lr = lwl
    return (0.93 + 0.487118 * c14 *
            (b / lwl) ** 1.06806 *
            (t / lwl) ** 0.46106 *
            (lwl / lr) ** 0.121563 *
            (lwl ** 3 / vol) ** 0.36486 *
            (1.0 - cp) ** (-0.604247))

def roughness_allowance(lwl):
    return 0.006 * (lwl + 100.0) ** (-0.16) - 0.00205

def holtrop_wave_resistance(lwl, b, t, cb, cm, cwp, vol, fn,
                            lcb_pct=0.0, abt=0.0, at=0.0, cstern=0.0,
                            rho=RHO_SEA):
    cp = cb / cm
    lr = lwl * (1.0 - cp + 0.06 * cp * lcb_pct / (4.0 * cp - 1.0))
    if lr <= 0:
        lr = lwl
    lv = vol ** (1.0 / 3.0)
    expo = ((lwl / b) ** 0.80856 * (1.0 - cwp) ** 0.30484 *
            (1.0 - cp - 0.0225 * lcb_pct) ** 0.6367 *
            (lr / b) ** 0.34574 *
            (100.0 * lv ** 3 / lwl ** 3) ** 0.16302)
    ie = 1.0 + 89.0 * math.exp(-expo)
    bl = b / lwl
    if bl < 0.11:
        c7 = 0.229577 * bl ** (1.0 / 3.0)
    elif bl <= 0.25:
        c7 = bl
    else:
        c7 = 0.5 - 0.0625 * lwl / b
    c1 = 2223105.0 * c7 ** 3.78613 * (t / b) ** 1.07961 * (90.0 - ie) ** (-1.37565)
    c2 = 1.0
    c5 = 1.0 - 0.8 * at / (b * t * cm)
    if cp < 0.8:
        c16 = 8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
    else:
        c16 = 1.73014 - 0.7067 * cp
    m1 = (0.0140407 * lwl / t - 1.75254 * vol ** (1.0 / 3.0) / lwl -
          4.79323 * b / lwl - c16)
    lb = lwl / b
    lam = 1.446 * cp - 0.03 * lb if lb < 12.0 else 1.446 * cp - 0.36
    l3v = lwl ** 3 / vol
    if l3v < 512.0:
        c15 = -1.69385
    elif l3v > 1727.0:
        c15 = 0.0
    else:
        c15 = -1.69385 + (lwl / vol ** (1.0 / 3.0) - 8.0) / 2.36
    m4 = c15 * 0.4 * math.exp(-0.034 * fn ** (-3.29))
    rw = (c1 * c2 * c5 * vol * rho * G *
          math.exp(m1 * fn ** (-0.9) + m4 * math.cos(lam * fn ** (-2))))
    return max(0.0, rw)

def holtrop_resistance(lwl, b, t, cb, cm, cwp, vol, speed_ms,
                       lcb_pct=0.0, abt=0.0, at=0.0, cstern=0.0,
                       rho=RHO_SEA, nu=NU_SEA):
    s = wetted_surface(lwl, b, t, cb, cm, cwp, abt)
    re = speed_ms * lwl / nu
    cf = ittc57_cf(re)
    fn = speed_ms / math.sqrt(G * lwl)
    one_pk = form_factor_1pk(lwl, b, t, cb, cm, vol, cstern, lcb_pct)
    ca = roughness_allowance(lwl)
    q = 0.5 * rho * speed_ms ** 2
    rf = q * s * cf
    rw = holtrop_wave_resistance(lwl, b, t, cb, cm, cwp, vol, fn,
                                  lcb_pct, abt, at, cstern, rho)
    rt = one_pk * rf + q * s * ca + rw
    ct = rt / (q * s)
    return {"speed_ms": speed_ms, "fn": fn, "ct": ct, "rt": rt, "s": s,
            "cf": cf, "form_factor_1pk": one_pk, "ca": ca, "rf": rf, "rw": rw}

# KVLCC2 geometry
L, B, T = 320.0, 58.0, 20.8
CB, CM, CWP = 0.8098, 0.98, 0.82
VOL = L * B * T * CB

# Speed range: 8-22 kn
speeds_kn = np.linspace(8, 22, 50)
speeds_ms = speeds_kn * 0.514444

results = [holtrop_resistance(L, B, T, CB, CM, CWP, VOL, v) for v in speeds_ms]
cts = [r["ct"] for r in results]
rts = [r["rt"] for r in results]

# Reference values (bare-hull decomposed)
ref_speeds_kn = [12, 15, 18]
ref_ct_bare = [2.55e-3, 2.50e-3, 2.40e-3]
ref_ct_full = [4.20e-3, 3.95e-3, 3.75e-3]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

# Left: Ct vs speed
ax1.plot(speeds_kn, [c*1e3 for c in cts], 'b-', linewidth=2, label='Holtrop-Mennen (bare hull)')
ax1.scatter(ref_speeds_kn, [c*1e3 for c in ref_ct_bare], c='red', s=80, zorder=5,
           label='Reference (bare hull, decomposed)')
ax1.scatter(ref_speeds_kn, [c*1e3 for c in ref_ct_full], c='orange', s=80, zorder=5,
           marker='^', label='Reference (full ship, SIMMAN 2008)')
ax1.axvline(x=15, color='gray', linestyle='--', alpha=0.5, label='Design speed (15 kn)')
ax1.set_xlabel('Speed [kn]')
ax1.set_ylabel('Ct × 10³')
ax1.set_title('KVLCC2: Total Resistance Coefficient vs Speed')
ax1.legend(fontsize=9)
ax1.grid(True, alpha=0.3)

# Right: Rt vs speed
ax2.plot(speeds_kn, [r/1e6 for r in rts], 'b-', linewidth=2, label='Holtrop-Mennen (bare hull)')
# Estimate full-ship Rt from full-ship Ct
ref_rts_full = [c * 0.5 * RHO_SEA * (v*0.514444)**2 * results[0]["s"] for c, v in zip(ref_ct_full, ref_speeds_kn)]
ax2.scatter(ref_speeds_kn, [r/1e6 for r in ref_rts_full], c='orange', s=80, zorder=5,
           marker='^', label='Reference (full ship)')
ax2.axvline(x=15, color='gray', linestyle='--', alpha=0.5, label='Design speed (15 kn)')
ax2.set_xlabel('Speed [kn]')
ax2.set_ylabel('Rt [MN]')
ax2.set_title('KVLCC2: Total Resistance vs Speed')
ax2.legend(fontsize=9)
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('/srv/tensor/agent/work/m1768/docs/kvlcc2_resistance_validation.png', dpi=150, bbox_inches='tight')
plt.close()

# Print summary
print("=== KVLCC2 Resistance Validation Summary ===")
for v_kn, ct in zip(speeds_kn[::10], cts[::10]):
    print(f"  V={v_kn:.0f} kn: Ct={ct:.6e}")
print(f"\nDesign point (15 kn):")
r15 = results[int((15-8)/(22-8)*49)]
print(f"  Ct = {r15['ct']:.6e}")
print(f"  Rt = {r15['rt']:.0f} N = {r15['rt']/1e6:.2f} MN")
print(f"  Fn = {r15['fn']:.4f}")
print(f"  (1+k) = {r15['form_factor_1pk']:.4f}")
print(f"\nFigure saved to docs/kvlcc2_resistance_validation.png")
