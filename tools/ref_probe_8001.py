#!/usr/bin/env python3
"""Probe the live ship-performance platform (:8001) against the domain anchors.

The probe is deliberately *anchor driven*: it loads the three anchor documents
under artifacts/ref/anchors/ and turns every platform answer into a machine
readable verdict record

    {subsystem, status_code, quantity, value, anchor, rel_err, verdict, ts, ...}

with verdict in {PASS, FAIL, ERROR}.  A HTTP 5xx / traceback is recorded as
FAIL while keeping status_code, because "the endpoint crashed" is a result the
regression gate must see (not an excuse to skip the probe).

Emitted artifact: artifacts/ref/probe_verdicts_r1.json

Usage:
    python3 tools/ref_probe_8001.py
    python3 tools/ref_probe_8001.py --base-url http://127.0.0.1:8001 --out x.json
Exit status is 0 whenever the verdict file was written (the verdicts themselves
carry the pass/fail information); pass --strict to exit 1 on any FAIL.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ANCHOR_DIR = os.path.join(ROOT, "artifacts", "ref", "anchors")
DEFAULT_OUT = os.path.join(ROOT, "artifacts", "ref", "probe_verdicts_r1.json")
DEFAULT_BASE = "http://127.0.0.1:8001"

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"

# platform payloads used for the three subsystems (all within anchor validity)
KVLCC2_REQUEST = {
    "length": 320.0, "beam": 58.0, "draft": 20.8,
    "displacement": 312622.0, "cb": 0.8098, "speed": 15.5,
    "water_density": 1025.0, "water_viscosity": 1.19e-6,
}
ITTCT8_REQUEST = {"Lwl": 320.0, "B": 58.0, "T": 20.8, "displacement": 312622.0,
                  "Cb": 0.8098, "Cm": 0.9950, "wetted_surface": 27194.0,
                  "speed": 15.5, "form_factor": 1.15}
SPECTRUM_REQUEST = {"Hs": 5.0, "Tp": 9.5, "spectrum_type": "ITTC", "N": 3}


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------
def http_request(path, body=None, method=None, base_url=DEFAULT_BASE, timeout=30):
    """Return (status_code, text).  Transport failures come back as (None, msg)."""
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # connection refused, timeout, DNS, ...
        return None, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _load_anchor(name):
    with open(os.path.join(ANCHOR_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)["payload"]


def _entries_by_id(payload):
    return {e["id"]: e for e in payload["entries"] if not e.get("divergent")}


def _record(subsystem, status_code, quantity, value, anchor, rel_err, verdict,
            probe_path, **extra):
    rec = {
        "subsystem": subsystem,
        "status_code": status_code,
        "quantity": quantity,
        "value": value,
        "anchor": anchor,
        "rel_err": rel_err,
        "verdict": verdict,
        "probe_path": probe_path,
        "ts": _now(),
    }
    rec.update(extra)
    return rec


def _rel_err(value, anchor):
    if value is None or anchor in (None, 0):
        return None
    try:
        return abs(float(value) - float(anchor)) / abs(float(anchor))
    except (TypeError, ValueError):
        return None


def _find_first(obj, keys):
    """Depth-first search for the first numeric value under any of ``keys``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        for v in obj.values():
            got = _find_first(v, keys)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_first(v, keys)
            if got is not None:
                return got
    return None


def _find_pairs(obj, xkeys, ykeys):
    """Extract (x, y) sample pairs from a spectrum-like JSON structure."""
    pairs = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and v and all(isinstance(i, (int, float)) for i in v):
                lk = k.lower()
                if lk in xkeys or ("freq" in lk) or ("omega" in lk) or lk in ("w", "f"):
                    pairs.append(("x", [float(i) for i in v]))
                elif lk in ykeys or ("densit" in lk) or ("spect" in lk) or lk in ("s", "s_w"):
                    pairs.append(("y", [float(i) for i in v]))
            elif isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
                xs = [i.get("omega", i.get("w", i.get("frequency", i.get("f")))) for i in v]
                ys = [i.get("S", i.get("density", i.get("S_w", i.get("spectrum")))) for i in v]
                if all(isinstance(i, (int, float)) for i in xs) and \
                        all(isinstance(i, (int, float)) for i in ys):
                    pairs.append(("xy", list(zip([float(i) for i in xs], [float(i) for i in ys]))))
            else:
                pairs.extend(_find_pairs(v, xkeys, ykeys))
    elif isinstance(obj, list):
        for v in obj:
            pairs.extend(_find_pairs(v, xkeys, ykeys))
    return pairs


def parse_spectrum_m0(text):
    """Best-effort m0 extraction from a wave-spectrum response body."""
    try:
        body = json.loads(text)
    except Exception:
        return None
    direct = _find_first(body, {"m0", "m_0", "area", "variance"})
    if direct is not None:
        return direct
    pairs = _find_pairs(body, {"omega", "w", "freq", "frequency"}, {"s", "s_w"})
    xs, ys = [], []
    for kind, val in pairs:
        if kind == "x" and not xs:
            xs = val
        elif kind == "y" and not ys:
            ys = val
        elif kind == "xy" and not xs:
            xs = [p[0] for p in val]
            ys = [p[1] for p in val]
    if len(xs) >= 3 and len(xs) == len(ys):
        m0 = 0.0
        for i in range(len(xs) - 1):
            m0 += 0.5 * (ys[i] + ys[i + 1]) * (xs[i + 1] - xs[i])
        return m0
    return None


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------
def probe_spectrum(fetch):
    """Subsystem 1: seakeeping / wave spectrum (m0 against the ITTC-78 anchor)."""
    payload = _load_anchor("spectra_ittc_issc.json")
    entries = _entries_by_id(payload)
    anchor = entries["ittc78_hs5_t1_9p5_m0"]
    recs = []
    for path in ("/api/v1/wave-spectrum/calculate",
                 "/api/v1/seakeeping-advanced/wave-spectrum"):
        status, text = fetch(path, SPECTRUM_REQUEST)
        subsystem = "seakeeping/wave-spectrum" if "advanced" not in path else \
            "seakeeping-advanced/wave-spectrum"
        if status is None:
            recs.append(_record(subsystem, None, "m0", None, anchor["value"], None, ERROR,
                                path, error=text[:400]))
            continue
        if status >= 500:
            recs.append(_record(subsystem, status, "m0", None, anchor["value"], None, FAIL,
                                path, error=f"http_{status}: {text[:200]}"))
            continue
        if status >= 400:
            recs.append(_record(subsystem, status, "m0", None, anchor["value"], None, FAIL,
                                path, error=f"http_{status}: {text[:200]}"))
            continue
        m0 = parse_spectrum_m0(text)
        rel = _rel_err(m0, anchor["value"])
        verdict = PASS if (rel is not None and rel <= anchor["tolerance_rel"]) else FAIL
        recs.append(_record(subsystem, status, "m0", m0, anchor["value"], rel, verdict, path,
                            tolerance_rel=anchor["tolerance_rel"],
                            error=None if m0 is not None else "m0_not_parseable"))
    return recs


def probe_propeller(fetch):
    """Subsystem 2: Wageningen B4-70 open water (KT / 10KQ / eta0 vs anchor table)."""
    payload = _load_anchor("propeller_wageningen_b4_70.json")
    entries = _entries_by_id(payload)
    geom = payload["geometry"]
    recs = []
    path = "/api/v1/propeller-design/wageningen-b"
    for J in [0.1 * i for i in range(1, 9)]:
        status, text = fetch(path, {"J": round(J, 1), "P_D": geom["P_D"],
                                    "Z": int(geom["Z"]), "Ae_Ao": geom["Ae_Ao"]})
        tag = f"b4_70_J{J:.1f}"
        if status is None or status >= 400:
            recs.append(_record("propulsion/wageningen-b", status, "KT", None,
                                entries[f"{tag}_KT"]["value"], None,
                                ERROR if status is None else FAIL, path,
                                J=round(J, 1), error=(text or "")[:200]))
            continue
        try:
            body = json.loads(text)
        except Exception:
            recs.append(_record("propulsion/wageningen-b", status, "KT", None,
                                entries[f"{tag}_KT"]["value"], None, FAIL, path,
                                J=round(J, 1), error="body_not_json"))
            continue
        kt = body.get("KT", body.get("kt"))
        kq = body.get("KQ", body.get("kq"))
        tenkq = body.get("10KQ", 10.0 * kq if isinstance(kq, (int, float)) else None)
        eta = body.get("eta0", body.get("eta"))
        for qty, value in (("KT", kt), ("10KQ", tenkq), ("eta0", eta)):
            anchor = entries[f"{tag}_{qty}"]["value"]
            rel = _rel_err(value, anchor)
            verdict = PASS if (rel is not None and rel <= entries[f"{tag}_{qty}"]["tolerance_rel"]) else FAIL
            recs.append(_record("propulsion/wageningen-b", status, qty,
                                None if value is None else float(value), anchor, rel, verdict,
                                path, J=round(J, 1),
                                tolerance_rel=entries[f"{tag}_{qty}"]["tolerance_rel"]))
        # placeholder detector: a constant KQ/KT ratio across J is not the 1975 series
        if isinstance(kt, (int, float)) and isinstance(kq, (int, float)) and kt:
            recs[-1]["kq_over_kt"] = kq / kt
    ratios = [r["kq_over_kt"] for r in recs if "kq_over_kt" in r]
    if ratios and max(ratios) - min(ratios) <= 1e-9:
        for r in recs:
            r["placeholder_flag"] = "KQ/KT constant across J"
    return recs


def probe_resistance(fetch):
    """Subsystem 3+4: total resistance vs the KVLCC2 / KCS full-scale anchors."""
    payload = _load_anchor("resistance_kvlcc2_kcs.json")
    entries = _entries_by_id(payload)
    anchor_rt = entries["KVLCC2_RT_full"]
    recs = []
    targets = (
        ("resistance/calculations-resistance", "/api/v1/calculations/resistance",
         KVLCC2_REQUEST, anchor_rt, 1.0),      # answers in kN
        ("resistance/ittc78-advanced", "/api/v1/resistance-advanced/ittc78",
         ITTCT8_REQUEST, anchor_rt, 1.0),      # answers in kN
        ("resistance/predict", "/api/v1/resistance/predict",
         {"Lwl": 320.0, "Lpp": 320.0, "B": 58.0, "T": 20.8,
          "displacement": 312622.0, "Cb": 0.8098, "speeds": [15.5]},
         anchor_rt, 1.0),
    )
    keys = {"rt", "r_t", "total_resistance", "totalresistance", "resistance", "rt_kn"}
    for subsystem, path, body, anchor, scale in targets:
        status, text = fetch(path, body)
        if status is None:
            recs.append(_record(subsystem, None, "RT", None, anchor["value"], None, ERROR,
                                path, error=text[:300]))
            continue
        if status >= 400:
            recs.append(_record(subsystem, status, "RT", None, anchor["value"], None, FAIL,
                                path, error=f"http_{status}: {text[:200]}"))
            continue
        try:
            parsed = json.loads(text)
        except Exception:
            recs.append(_record(subsystem, status, "RT", None, anchor["value"], None, FAIL,
                                path, error="body_not_json"))
            continue
        rt = _find_first(parsed, keys)
        if rt is None:
            recs.append(_record(subsystem, status, "RT", None, anchor["value"], None, FAIL,
                                path, error="RT_not_parseable"))
            continue
        rt_n = rt * scale * 1.0e3 if abs(rt) < 1.0e5 else rt * scale  # kN or N heuristics
        if abs(rt) < 1000.0:  # MN
            rt_n = rt * 1.0e6
        elif abs(rt) < 1.0e5:  # kN
            rt_n = rt * 1.0e3
        rel = _rel_err(rt_n, anchor["value"])
        verdict = PASS if (rel is not None and rel <= anchor["tolerance_rel"]) else FAIL
        recs.append(_record(subsystem, status, "RT", rt_n, anchor["value"], rel, verdict, path,
                            tolerance_rel=anchor["tolerance_rel"], raw_value=rt))
    return recs


PROBES = (("seakeeping", probe_spectrum),
          ("propulsion", probe_propeller),
          ("resistance", probe_resistance))


def run_probes(fetch):
    """Run every probe with an injected transport; return (records, summary)."""
    records = []
    for name, probe in PROBES:
        try:
            records.extend(probe(fetch))
        except Exception as exc:  # a probe must never abort the whole run
            records.append(_record(name, None, "-", None, None, None, ERROR,
                                   "-", error=f"probe_crash: {type(exc).__name__}: {exc}"))
    return records, summarise(records)


def summarise(records):
    subsystems = {}
    for r in records:
        s = subsystems.setdefault(r["subsystem"], {"PASS": 0, "FAIL": 0, "ERROR": 0})
        s[r["verdict"]] += 1
    overall = FAIL if any(v["FAIL"] for v in subsystems.values()) else (
        ERROR if any(v["ERROR"] for v in subsystems.values()) else PASS)
    return {
        "n_records": len(records),
        "n_subsystems": len(subsystems),
        "subsystems": subsystems,
        "overall_verdict": overall,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("SHIP_API_BASE", DEFAULT_BASE))
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    def fetch(path, body=None, method=None):
        return http_request(path, body, method, base_url=args.base_url)

    records, summary = run_probes(fetch)
    doc = {
        "probe": "tools/ref_probe_8001.py",
        "platform_base_url": args.base_url,
        "generated_at": _now(),
        "anchor_dir": os.path.relpath(ANCHOR_DIR, ROOT),
        "summary": summary,
        "verdicts": records,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"wrote {os.path.relpath(args.out, ROOT)} "
          f"({summary['n_records']} records / {summary['n_subsystems']} subsystems)")
    for name, counts in sorted(summary["subsystems"].items()):
        print(f"  {name:42s} PASS={counts['PASS']:2d} FAIL={counts['FAIL']:2d} "
              f"ERROR={counts['ERROR']:2d}")
    print(f"overall_verdict={summary['overall_verdict']}")
    if args.strict and summary["overall_verdict"] != PASS:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())