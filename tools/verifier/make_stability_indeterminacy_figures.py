"""Comparison + figures for the stability input-determinacy audit.

Reads artifacts/verifier/stability_indeterminacy/evidence.json (reference chain)
and api_captures.json (live endpoint responses), writes comparison.json and
three figures.  Every number in the figures is read from those files: nothing
is typed by hand.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stability_indeterminacy import Hull, rel_err  # noqa: E402

ART = Path("artifacts/verifier/stability_indeterminacy")
CASE_B = "B_cb070_kg60"
CASE_C = "C_cb100_kg70"
KEYS = {"gm_m": "GM [m]", "gz_max_m": "GZ_max [m]", "area_0_40_mrad": "Area 0-40 [m rad]"}
PLAT_KEY = {"gm_m": "gm", "gz_max_m": "gz_max", "area_0_40_mrad": "area_0_40"}


def load():
    ev = json.loads((ART / "evidence.json").read_text(encoding="utf-8"))
    caps = json.loads((ART / "api_captures.json").read_text(encoding="utf-8"))
    return ev, caps


def match_capture(caps, cb):
    for c in caps:
        if abs(c["payload"]["cb"] - cb) < 1e-9:
            return c
    return None


def compare(ev, caps):
    out = {}
    for cid, case in ev["cases"].items():
        cap = match_capture(caps, case["case"]["cb"])
        if cap is None:
            continue
        resp = cap["response"]
        rows = []
        true_vals = {k: [m[k] for m in case["members"]] for k in KEYS}
        for m in case["members"]:
            row = {"hull": m["hull"], "imo_pass": m["imo_pass"]}
            for k in KEYS:
                got = resp[PLAT_KEY[k]]
                row[k] = {"ref": m[k], "platform": got, "rel_err": rel_err(m[k], got)}
            rows.append(row)
        outside = {}
        for k in KEYS:
            got = resp[PLAT_KEY[k]]
            outside[k] = bool(got < min(true_vals[k]) or got > max(true_vals[k]))
        out[cid] = {
            "payload": cap["payload"],
            "platform": resp,
            "platform_kb_over_T": resp["kb"] / cap["payload"]["draft"],
            "platform_kb_closed_form_T1mCb3": cap["payload"]["draft"]
                                                 * (1 - cap["payload"]["cb"] / 3.0),
            "rows": rows,
            "platform_outside_family_range": outside,
            "family_range": {k: [min(v), max(v)] for k, v in true_vals.items()},
            "imo_verdict": case["imo_verdict"],
            "minimax_floor": {k: v["worst_case_rel_err"]
                              for k, v in case.get("minimax_floor", {}).items()},
        }
    return out


def fig_sections(ev, path):
    cb = ev["cases"][CASE_B]["case"]["cb"]
    kg = ev["cases"][CASE_B]["case"]["kg_m"]
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    for shape, style in (("box", "-"), ("trapezoid", "--"), ("knuckle", "-."), ("vee", ":")):
        try:
            hull = Hull("h", shape=shape, cb=cb, kg=kg)
        except ValueError:
            continue
        s = hull.s_scale
        zs = [hull.D * i / 200 for i in range(201)]
        from stability_indeterminacy import SHAPES

        f, _ = SHAPES[shape]
        ys = [s * 0.5 * hull.B * f(min(z / hull.T, 1.0)) for z in zs]
        ax.plot(ys, zs, style, label=f"{shape} (c_s={hull.cs:.2f}, KB={hull.kb():.2f} m)")
    ax.axhline(ev["cases"][CASE_B]["case"]["draft_m"], color="k", lw=0.6, ls=":")
    ax.text(0.2, ev["cases"][CASE_B]["case"]["draft_m"] + 0.15, "design waterline", fontsize=7)
    ax.set_xlabel("half-breadth y [m]")
    ax.set_ylabel("z [m]")
    ax.set_title(f"Same payload (L=100, B=20, T=8, Cb={cb}, KG={kg}) - different sections")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_gz(ev, cmp_, path):
    case = ev["cases"][CASE_B]
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    for m in case["members"]:
        ang = [a for a, _ in m["gz_curve"]]
        gz = [g for _, g in m["gz_curve"]]
        ax.plot(ang, gz, label=f"{m['hull']} (GM={m['gm_m']:.2f} m)")
    cap = cmp_[CASE_B]["platform"]
    ax.axhline(cap["gz_max"], color="crimson", ls="--", lw=1.2,
               label=f"platform gz_max={cap['gz_max']:.2f} m")
    ax.plot([cap["gz_max_angle"]], [cap["gz_max"]], "rx", ms=8)
    ax.axhline(0.20, color="gray", lw=0.8, ls=":",
               label="IS Code 2008 GZ_max >= 0.20 m")
    ax.set_xlabel("heel angle [deg]")
    ax.set_ylabel("GZ [m]")
    ax.set_title("One endpoint payload, three admissible hulls: GZ curves")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_bars(ev, cmp_, path):
    case = ev["cases"][CASE_B]
    fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.9))
    for ax, (k, label) in zip(axes, KEYS.items()):
        vals = [m[k] for m in case["members"]]
        ax.bar(range(len(vals)), vals, color="#4c78a8")
        ax.axhline(cmp_[CASE_B]["platform"][PLAT_KEY[k]],
                   color="crimson", ls="--", lw=1.2, label="platform answer")
        ax.set_xticks(range(len(vals)))
        ax.set_xticklabels([m["hull"].split("/")[0] for m in case["members"]],
                           rotation=25, fontsize=7)
        ax.set_ylabel(label)
        floor = case["minimax_floor"].get(k, {}).get("worst_case_rel_err")
        if floor is not None:
            ax.set_title(f"{label}\nminimax floor = {100 * floor:.0f}%", fontsize=9)
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3, axis="y")
    fig.suptitle("Identical endpoint payload (Cb=0.70): reference family vs single platform answer",
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    ev, caps = load()
    cmp_ = compare(ev, caps)
    (ART / "comparison.json").write_text(
        json.dumps(cmp_, ensure_ascii=False, indent=2), encoding="utf-8")
    os.makedirs(ART, exist_ok=True)
    fig_sections(ev, ART / "fig1_sections_same_payload.png")
    fig_gz(ev, cmp_, ART / "fig2_gz_curves.png")
    fig_bars(ev, cmp_, ART / "fig3_platform_vs_family.png")
    for cid, c in cmp_.items():
        print(f"== {cid} payload cb={c['payload']['cb']} kg={c['payload']['kg']}")
        print("   platform GM=%.3f  family GM range=%.3f..%.3f  outside=%s"
              % (c["platform"]["gm"], c["family_range"]["gm_m"][0],
                 c["family_range"]["gm_m"][1], c["platform_outside_family_range"]["gm_m"]))
        for r in c["rows"]:
            print("   %-22s GM rel_err=%+.1f%%  A0-40 rel_err=%+.1f%%  IMO=%s"
                  % (r["hull"], 100 * r["gm_m"]["rel_err"],
                     100 * r["area_0_40_mrad"]["rel_err"],
                     "PASS" if r["imo_pass"] else "FAIL"))
    print("figures + comparison.json written to", ART)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())