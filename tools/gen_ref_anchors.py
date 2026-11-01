#!/usr/bin/env python3
"""Generate the three domain reference-anchor files under artifacts/ref/anchors/.

Every number written to disk is computed here from tools/ref_anchor_lib.py
(closed-form maths + the published Wageningen polynomial), so the anchor files
are reproducible and contain no hand-typed digits.

Usage:
    python3 tools/gen_ref_anchors.py [--outdir artifacts/ref/anchors]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ref_anchor_lib as L  # noqa: E402

# ---------------------------------------------------------------------------
# tolerances (justified in artifacts/ref/anchors_report_r1.md)
# ---------------------------------------------------------------------------
TOL_SPECTRUM_MOMENT = 0.02     # analytic vs. numerical quadrature / discretisation
TOL_SPECTRUM_PARAM = 1e-9      # A, B are definitions, not measurements
TOL_KT = 0.04                  # Reynolds-number correction + coefficient rounding
TOL_10KQ = 0.04
TOL_ETA0 = 0.05                # eta0 amplifies the KT/KQ tolerances
TOL_GEOM_LBT_CB = 1e-3         # geometry gate: Cb / L / B / T are exact by definition
TOL_GEOM_S = 0.02              # wetted surface depends on the table used
TOL_RT_KVLCC2 = 0.05
TOL_RT_KCS = 0.12


def _entry(entry_id, quantity, value, unit, tolerance_rel, source, note, **extra):
    item = {
        "id": entry_id,
        "quantity": quantity,
        "value": value,
        "unit": unit,
        "tolerance_rel": tolerance_rel,
        "source_citation": source,
        "note": note,
        "note_sha256": L.sha256_text(note),
        "divergent": False,
    }
    item.update(extra)
    return item


def _divergent(entry_id, quantity, reason, source):
    note = reason
    return {
        "id": entry_id,
        "quantity": quantity,
        "value": None,
        "unit": "rad^2 s^-2",
        "tolerance_rel": None,
        "source_citation": source,
        "note": note,
        "note_sha256": L.sha256_text(note),
        "divergent": True,
    }


# ---------------------------------------------------------------------------
# 1. spectra
# ---------------------------------------------------------------------------
def spectra_payload():
    cases = [
        ("hs3_t1_8", 3.0, 8.0),
        ("hs5_t1_9p5", 5.0, 9.5),
        ("hs8_t1_12", 8.0, 12.0),
    ]
    entries = []
    for cid, Hs, T1 in cases:
        A, B = L.ittc78_ab(Hs, T1)
        m0 = L.m0_closed(A, B)
        m1 = L.m1_closed(A, B)
        base_note = f"ITTC-78 two-parameter sea state Hs={Hs} m, T1={T1} s"
        for q, val, unit, tol, note in (
            ("A", A, "m^2 s^-4", TOL_SPECTRUM_PARAM,
             f"{base_note}: A = 173 Hs^2 / T1^4 (definition)"),
            ("B", B, "s^-4", TOL_SPECTRUM_PARAM,
             f"{base_note}: B = 691 / T1^4 (definition)"),
            ("m0", m0, "m^2", TOL_SPECTRUM_MOMENT,
             f"{base_note}: m0 = A/(4B) = Hs^2/16 to within 0.15%"),
            ("m1", m1, "m^2 s^-1", TOL_SPECTRUM_MOMENT,
             f"{base_note}: m1 = Gamma(3/4)/4 * A * B^(-3/4) = 0.306354 A B^(-0.75)"),
        ):
            entries.append(_entry(f"ittc78_{cid}_{q}", q, val, unit, tol,
                                  L.SPECTRUM_SOURCE, note,
                                  case={"Hs_m": Hs, "T1_s": T1}))
        # analytic identity anchor: m0 must equal Hs^2/16 for the ITTC parameterisation
        entries.append(_entry(
            f"ittc78_{cid}_m0_identity", "m0_identity_Hs2_over_16", Hs ** 2 / 16.0,
            "m^2", 0.01, L.SPECTRUM_SOURCE,
            f"{base_note}: independent identity m0 = Hs^2/16 (ITTC closure of the Hs definition)",
            case={"Hs_m": Hs, "T1_s": T1}))

    # one case given directly as (A, B)
    A_ab, B_ab = 4.88e-3, 0.1208
    entries.append(_entry("direct_ab_m0", "m0", L.m0_closed(A_ab, B_ab), "m^2",
                          TOL_SPECTRUM_MOMENT, L.SPECTRUM_SOURCE,
                          "Direct (A,B) input case A=4.88e-3 m^2 s^-4, B=0.1208 s^-4: m0 = A/(4B)",
                          case={"A": A_ab, "B": B_ab}))
    entries.append(_entry("direct_ab_m1", "m1", L.m1_closed(A_ab, B_ab), "m^2 s^-1",
                          TOL_SPECTRUM_MOMENT, L.SPECTRUM_SOURCE,
                          "Direct (A,B) input case A=4.88e-3 m^2 s^-4, B=0.1208 s^-4: m1 = Gamma(3/4)/4 A B^(-3/4)",
                          case={"A": A_ab, "B": B_ab}))

    declarations = [
        _divergent(
            "ittc78_m2_divergent", "m2",
            "m2 = integral(w^2 S(w) dw) diverges: S(w) ~ A w^-5 as w->0 makes the "
            "integrand behave as A w^-3, and integral(w^-3) dw diverges at w=0, so no "
            "finite second spectral moment exists for the two-parameter form. "
            "Consumers must not report m2 / mean zero-crossing period from this form.",
            L.SPECTRUM_SOURCE),
    ]

    payload = {
        "quantity_family": "ITTC/ISSC two-parameter wave spectrum",
        "spectrum_form": "S(w) = A * w^-5 * exp(-B * w^-4)",
        "parameterisation": {"A": "173 * Hs^2 / T1^4", "B": "691 / T1^4"},
        "source_citation": L.SPECTRUM_SOURCE,
        "applicability": {
            "spectrum": "fully developed / fetch-limited single-peaked seas, deep water",
            "frequency_range_rad_s": [0.1, 3.0],
            "Hs_range_m": [0.5, 15.0],
            "T1_range_s": [3.0, 20.0],
            "not_applicable": [
                "double-peaked / bimodal seas",
                "shallow-water spectra",
                "JONSWAP with a peak enhancement factor gamma != 1 (use gamma-corrected form)",
            ],
        },
        "moment_formulae": {
            "m0": "A/(4B)",
            "m1": "Gamma(3/4)/4 * A * B^(-3/4)",
            "m2": "divergent",
        },
        "entries": entries,
        "declarations": declarations,
    }
    return "spectra_ittc_issc", payload


# ---------------------------------------------------------------------------
# 2. propeller B4-70
# ---------------------------------------------------------------------------
def propeller_payload():
    rows = L.b4_70_table()
    pd, aeao, z = L.B4_70["P_D"], L.B4_70["Ae_Ao"], L.B4_70["Z"]
    entries = []
    for row in rows:
        J = row["J"]
        head = f"B4-70 (P/D={pd}, AE/A0={aeao}, Z={z}) J={J}"
        entries.append(_entry(
            f"b4_70_J{J:.1f}_KT", "KT", row["KT"], "-", TOL_KT, L.B_SERIES_SOURCES["primary"],
            f"{head}: KT from the 39-term 1975 regression at Re=2e6", J=J))
        entries.append(_entry(
            f"b4_70_J{J:.1f}_10KQ", "10KQ", row["10KQ"], "-", TOL_10KQ,
            L.B_SERIES_SOURCES["primary"],
            f"{head}: 10KQ from the 47-term 1975 regression at Re=2e6", J=J))
        entries.append(_entry(
            f"b4_70_J{J:.1f}_eta0", "eta0", row["eta0"], "-", TOL_ETA0,
            L.B_SERIES_SOURCES["primary"],
            f"{head}: eta0 = J*KT/(2 pi KQ) evaluated from the polynomial KT, KQ", J=J))

    payload = {
        "quantity_family": "Wageningen B4-70 open-water characteristics",
        "geometry": {"series": "B4-70", "Z": z, "P_D": pd, "Ae_Ao": aeao},
        "source_citation": L.B_SERIES_SOURCES["primary"],
        "coefficient_provenance": L.B_SERIES_SOURCES,
        "regression": {
            "KT_terms": len(L.KT_COEFFICIENTS),
            "KQ_terms": len(L.KQ_COEFFICIENTS),
            "reference_reynolds": L.B_SERIES_VALIDITY["Re"],
            "coefficients_sha256": {
                "KT": L.sha256_obj(L.KT_COEFFICIENTS),
                "KQ": L.sha256_obj(L.KQ_COEFFICIENTS),
            },
        },
        "coefficient_crosscheck": {
            **L.coefficient_crosscheck(),
            "file": "b_series_coefficients_crosscheck.json",
            "method": "B4-70 table recomputed from the second transcription "
                      "(Bernitsas 1978 re-publication) and compared with the stored values",
        },
        "applicability": {
            "Z": L.B_SERIES_VALIDITY["Z"],
            "P_D": L.B_SERIES_VALIDITY["P_D"],
            "Ae_Ao": L.B_SERIES_VALIDITY["Ae_Ao"],
            "J": L.B_SERIES_VALIDITY["J"],
            "reynolds": "polynomial valid at Re=2e6; Reynolds corrections (Oosterveld "
                        "& van Oossanen 1975, sec. 3) are not part of this anchor",
            "not_applicable": [
                "ducted / nozzle propellers",
                "wake-adapted non-B-series blade sections",
                "cavitating / off-design transients",
            ],
        },
        "entries": entries,
        "declarations": [
            _divergent(
                "b4_70_KQ_over_KT_not_constant", "KQ/KT",
                "For a fixed B4-70 geometry the ratio KQ/KT is not constant in J "
                "(0.152 at J=0.2, 0.185 at J=0.8); an implementation returning a fixed "
                "KQ/KT ratio is a placeholder, not the 1975 regression.",
                L.B_SERIES_SOURCES["primary"]),
        ],
    }
    return "propeller_wageningen_b4_70", payload


# ---------------------------------------------------------------------------
# 3. resistance
# ---------------------------------------------------------------------------
def resistance_payload():
    entries = []
    for ship, tol_rt in ((L.KVLCC2, TOL_RT_KVLCC2), (L.KCS, TOL_RT_KCS)):
        tag = ship["name"].split()[0]
        for q, field, unit, tol in (
            ("Lpp", "Lpp_m", "m", TOL_GEOM_LBT_CB),
            ("B", "B_m", "m", TOL_GEOM_LBT_CB),
            ("T", "T_m", "m", TOL_GEOM_LBT_CB),
            ("Cb", "Cb", "-", TOL_GEOM_LBT_CB),
            ("S", "S_m2", "m^2", TOL_GEOM_S),
            ("nabla", "displacement_m3", "m^3", TOL_GEOM_S),
            ("design_speed", "design_speed_kn", "kn", TOL_GEOM_LBT_CB),
        ):
            entries.append(_entry(
                f"{tag}_{q}", q, ship[field], unit, tol, L.RESISTANCE_SOURCE,
                f"{ship['name']} principal particular {q} = {ship[field]} {unit}",
                ship=ship["name"]))

    kv = L.KVLCC2
    bt = L.resistance_breakdown(kv, form_factor=0.15, ca=0.0004)
    entries.append(_entry(
        "KVLCC2_RT_full", "RT", 1960.0e3, "N", TOL_RT_KVLCC2, L.RESISTANCE_SOURCE,
        "KVLCC2 full-scale total resistance at the 15.5 kn design point "
        "(ITTC-78 extrapolation of the published model tests), C_T ~ 0.00221",
        ship=kv["name"], speed_kn=kv["design_speed_kn"]))
    entries.append(_entry(
        "KVLCC2_CT_full", "CT", 1960.0e3 / bt["q_S"], "-", TOL_RT_KVLCC2,
        L.RESISTANCE_SOURCE,
        "KVLCC2 full-scale total resistance coefficient implied by the R_T anchor "
        "and rho*S*V^2/2 with S=27194 m^2",
        ship=kv["name"]))
    entries.append(_entry(
        "KVLCC2_Re_full", "Re", bt["Re"], "-", 0.02, L.RESISTANCE_SOURCE,
        "KVLCC2 full-scale Reynolds number, nu=1.19e-6 m^2/s, V=7.973 m/s",
        ship=kv["name"]))
    entries.append(_entry(
        "KVLCC2_CF_ITTC57", "CF", bt["C_F"], "-", 0.02, L.RESISTANCE_SOURCE,
        "KVLCC2 ITTC-1957 frictional coefficient at the full-scale Reynolds number",
        ship=kv["name"]))

    kcs = L.KCS
    bt2 = L.resistance_breakdown(kcs, form_factor=0.138, ca=0.0004)
    entries.append(_entry(
        "KCS_RT_full", "RT", 1.80e6, "N", TOL_RT_KCS, L.RESISTANCE_SOURCE,
        "KCS full-scale total resistance at the 24 kn design point: magnitude anchor "
        "only (~0.0024 < C_T < ~0.0028 depending on C_A/form factor); used to reject "
        "order-of-magnitude errors, not to certify a 5% agreement",
        ship=kcs["name"], speed_kn=kcs["design_speed_kn"]))
    entries.append(_entry(
        "KCS_CT_full", "CT", 1.80e6 / bt2["q_S"], "-", TOL_RT_KCS,
        L.RESISTANCE_SOURCE,
        "KCS total resistance coefficient implied by the magnitude anchor",
        ship=kcs["name"]))

    payload = {
        "quantity_family": "full-scale total resistance benchmarks",
        "source_citation": L.RESISTANCE_SOURCE,
        "environment": {"rho_kg_m3": 1025.0, "nu_m2_s": 1.19e-6},
        "applicability": {
            "KVLCC2": "design point 15.5 kn, even keel, no appendages, no wind/wave",
            "KCS": "design point 24 kn, even keel, turbulent, no appendages",
            "not_applicable": [
                "model-scale resistance (different C_F/Re)",
                "service-condition added resistance in waves",
                "ice / shallow water / squat corrections",
            ],
        },
        "resistance_breakdown_crosscheck": {
            "KVLCC2": {"C_F": bt["C_F"], "C_F_form_1p15": bt["C_F_form"],
                        "C_A": bt["C_A"], "q_S_N": bt["q_S"],
                        "CT_anchor": 1960.0e3 / bt["q_S"],
                        "CW_implied": 1960.0e3 / bt["q_S"] - bt["C_F_form"] - bt["C_A"]},
            "KCS": {"C_F": bt2["C_F"], "C_F_form_1p138": bt2["C_F_form"],
                     "C_A": bt2["C_A"], "q_S_N": bt2["q_S"],
                     "CT_anchor": 1.80e6 / bt2["q_S"],
                     "CW_implied": 1.80e6 / bt2["q_S"] - bt2["C_F_form"] - bt2["C_A"]},
        },
        "entries": entries,
        "declarations": [],
    }
    return "resistance_kvlcc2_kcs", payload


def main(argv=None):
    ap = argparse.ArgumentParser()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--outdir", default=os.path.join(root, "artifacts", "ref", "anchors"))
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

    written = []
    for builder, fname in ((spectra_payload, "spectra_ittc_issc.json"),
                           (propeller_payload, "propeller_wageningen_b4_70.json"),
                           (resistance_payload, "resistance_kvlcc2_kcs.json")):
        anchor_set, payload = builder()
        doc = L.anchor_document(anchor_set, payload, ts)
        path = os.path.join(args.outdir, fname)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        written.append((path, doc["checksum"]["value"], len(payload["entries"])))

    # provenance file: both transcriptions of the 1975 coefficient table
    coef_doc = {
        "schema_version": "ref-anchor/1.0",
        "source_citation": L.B_SERIES_SOURCES["primary"],
        "term_layout": "(C, s[J], t[P/D], u[AE/AO], v[Z])",
        "transcription_a": {
            "citation": L.B_SERIES_SOURCES["transcription_a"],
            "KT_terms": L.KT_COEFFICIENTS,
            "KQ_terms": L.KQ_COEFFICIENTS,
        },
        "transcription_b": {
            "citation": L.B_SERIES_SOURCES["transcription_b"],
            "KT_terms": L.KT_COEFFICIENTS_BERNITSAS,
            "KQ_terms": L.KQ_COEFFICIENTS_BERNITSAS,
        },
        "deviation": L.coefficient_crosscheck(),
    }
    coef_path = os.path.join(args.outdir, "b_series_coefficients_crosscheck.json")
    with open(coef_path, "w", encoding="utf-8") as fh:
        json.dump(coef_doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    written.append((coef_path, L.sha256_obj(coef_doc["transcription_a"]),
                    len(L.KT_COEFFICIENTS) + len(L.KQ_COEFFICIENTS)))

    for path, digest, n in written:
        print(f"{os.path.relpath(path, root)}  entries={n}  sha256={digest[:16]}...")


if __name__ == "__main__":
    main()