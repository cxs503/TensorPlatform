"""pytest suite for the platform capability audit (tools/capability_audit).

Coverage
--------
* Ed25519 correctness against the RFC 8032 §7.1 vectors (plus tamper negatives).
* Merkle tree / inclusion proofs (all tree sizes 1..17) and canonical encoding.
* Probe engines against a *scripted* API: one physically honest endpoint set
  (must be certified REAL) and one hard-coded surrogate (must be caught as MOCK).
* End-to-end audit -> report -> anchor -> verify, including tamper detection.
* CLI wiring for the four subcommands.

The scripted API keeps the suite hermetic (no :8001 dependency), while the live
run in ``artifacts/capability_audit`` is produced by the same code path.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_audit import adapters, anchor as anchor_mod, cli, ed25519  # noqa: E402
from tools.capability_audit import benchmark, merkle, oracle, probes, report  # noqa: E402
from tools.capability_audit.transport import ScriptedTransport, TransportError  # noqa: E402

KNOT = oracle.KNOT


# --------------------------------------------------------------------------
# Ed25519 (RFC 8032 §7.1)
# --------------------------------------------------------------------------
RFC8032_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61"
     "e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e4"
     "58f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290"
     "ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("seed_hex,pub_hex,msg_hex,sig_hex", RFC8032_VECTORS)
def test_ed25519_rfc8032_vectors(seed_hex, pub_hex, msg_hex, sig_hex):
    seed, msg = bytes.fromhex(seed_hex), bytes.fromhex(msg_hex)
    assert ed25519.public_key(seed).hex() == pub_hex
    assert ed25519.sign(seed, msg).hex() == sig_hex
    assert ed25519.verify(bytes.fromhex(pub_hex), msg, bytes.fromhex(sig_hex))


def test_ed25519_rejects_tampering():
    seed = bytes.fromhex(RFC8032_VECTORS[0][0])
    pub, sig = ed25519.public_key(seed), ed25519.sign(seed, b"payload")
    assert ed25519.verify(pub, b"payload", sig)
    assert not ed25519.verify(pub, b"payload!", sig)
    bad_sig = bytearray(sig)
    bad_sig[0] ^= 0x01
    assert not ed25519.verify(pub, b"payload", bytes(bad_sig))
    other = ed25519.public_key(bytes.fromhex(RFC8032_VECTORS[1][0]))
    assert not ed25519.verify(other, b"payload", sig)


# --------------------------------------------------------------------------
# Merkle
# --------------------------------------------------------------------------
def test_canonical_encoding_is_key_order_invariant():
    a = {"b": 1, "a": [1, 2, {"y": 0.5, "x": 2}]}
    b = {"a": [1, 2, {"x": 2, "y": 0.5}], "b": 1}
    assert merkle.canonical_bytes(a) == merkle.canonical_bytes(b)
    assert merkle.leaf_hash(merkle.canonical_bytes(a)) == merkle.leaf_hash(merkle.canonical_bytes(b))


@pytest.mark.parametrize("n", list(range(1, 18)))
def test_inclusion_proofs_all_sizes(n):
    leaves = [merkle.leaf_hash(f"artifact-{i}".encode()) for i in range(n)]
    tree = merkle.build_tree(leaves)
    for idx in range(n):
        proof = merkle.inclusion_proof(leaves, idx)
        assert merkle.verify_inclusion(leaves[idx], proof, tree["root"])
        wrong = merkle.leaf_hash(f"artifact-{idx}-other".encode())
        assert not merkle.verify_inclusion(wrong, proof, tree["root"])


def test_manifest_root_changes_when_content_changes():
    base = [{"path": "a.json", "sha256": "00" * 32, "bytes": 10},
            {"path": "b.json", "sha256": "11" * 32, "bytes": 20}]
    tampered = [dict(base[0]), dict(base[1], sha256="22" * 32)]
    assert merkle.manifest_root(base)["root"] != merkle.manifest_root(tampered)["root"]


# --------------------------------------------------------------------------
# comparison engine
# --------------------------------------------------------------------------
def test_worst_diff_compares_fields_and_flags_empty_comparisons():
    worst, path, _pair, compared, _missing = probes._worst_diff(
        {"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 2.0}, None)
    assert compared == 2 and worst == 0.0 and path is not None
    worst, _p, _pair, compared, missing = probes._worst_diff({"a": 1.0}, {"b": 1.0}, None)
    assert compared == 0 and missing == []
    worst, _p, _pair, compared, missing = probes._worst_diff({"a": 1.0}, {"a": 1.0},
                                                             ["a", "zzz"])
    assert compared == 1 and missing == ["zzz"]


def test_flatten_ignores_non_numeric_and_keeps_paths():
    flat = probes.flatten({"x": 1, "y": {"z": [2.0, "text"]}, "flag": True, "s": "no"})
    assert flat == {"x": 1.0, "y.z[0]": 2.0, "flag": 1.0}


# --------------------------------------------------------------------------
# scripted API: honest implementation vs hard-coded surrogate
# --------------------------------------------------------------------------
def _honest_api(path, payload):
    rho, nu = payload.get("water_density", 1025.0), payload.get("water_viscosity", 1.19e-6)
    if path == "/calculations/resistance":
        v = payload["speed"] * (KNOT if payload.get("speed_unit", "kn") == "kn" else 1.0)
        L, S = payload["length"], 2260.59
        re = oracle.reynolds_number(v, L, nu)
        cf = oracle.ittc57_cf(re)
        dcf = 0.0004
        cr = 0.0049 * (1.0 + (payload["cb"] - 0.7) * 3.0)
        ct = cf + dcf + cr
        rt = oracle.total_resistance_kn(ct, rho, v, S)
        return {"reynolds_number": re, "froude_number": oracle.froude_number(v, L),
                "wetted_surface": S, "cf": round(cf, 6), "dcf": dcf,
                "cf_total": round(cf + dcf, 6), "cr": round(cr, 6), "ct": round(ct, 6),
                "rf": round(ct * 0.8 * 0.5 * rho * v * v * S / 1000, 2),
                "rr": round(rt * 0.2, 2), "rt": round(rt, 2), "pe": round(rt * v, 1),
                "speed_ms": round(v, 6), "speed_unit": payload.get("speed_unit", "kn"),
                "cb_from_dims": payload["cb"], "method": "scripted-ittc57+holtrop",
                "displacement_consistency_pct": 0.0}
    if path == "/resistance/predict":
        out = []
        for sp in payload["speeds"]:
            v = sp * KNOT
            rt = 0.35 * v ** 2.6 * 10.0
            out.append({"speed": sp, "total_resistance": rt, "friction_resistance": rt * 0.7,
                        "wave_resistance": rt * 0.3, "effective_power": rt * v,
                        "reynolds_number": oracle.reynolds_number(v, payload["Lwl"], nu),
                        "froude_number": oracle.froude_number(v, payload["Lwl"]),
                        "method": "scripted-holtrop"})
        return out
    if path == "/stability/evaluate":
        cw = 0.8
        kb = oracle.kb_wall_sided(payload["T"], payload["Cb"], cw)
        bm = oracle.bm_wall_sided(cw, payload["B"], payload["Cb"], payload["T"])
        km = kb + bm
        gm = oracle.gm_identity(kb, bm, payload["KG"])
        return {"GM": gm, "GZ_max": max(0.0, 0.75 * gm), "GZ_angle": 20.0,
                "KB": kb, "BM": bm, "KM": km, "Cw": cw, "vanishing_angle": None,
                "heel_validity_limit_deg": 20.0, "method": "scripted-wall-sided"}
    if path == "/propulsion/predict":
        v = payload["speed"] * KNOT
        wake, d = 0.3, payload["Dp"]
        j = oracle.advance_ratio(v, wake, payload["n"], d)
        kt = (0.62 * payload["pitch_ratio"] * max(0.0, 1.0 - j / 0.9)
              * (1.0 + (payload["ae_ao"] - 0.55) * 0.75))
        kq = 0.15 * kt
        eta0 = oracle.eta_open_water(j, kt, kq) if kq else 0.0
        ct_loading = 2.3
        return {"thrust": 200.0, "efficiency": 0.55, "power": 1800.0,
                "propeller_diameter": d, "n_rps": payload["n"], "advance_ratio": j,
                "kt": kt, "kq": kq, "eta_open_water": eta0, "ct_loading": ct_loading,
                "eta_ideal": oracle.eta_ideal_from_ct(ct_loading),
                "wake_fraction": wake, "method": "scripted-bemt"}
    if path == "/added-resistance/calculate":
        hs, tp = payload["Hs"], payload["Tp"]
        r_aw = 4.0165 * hs * hs
        return {"R_aw_kN": r_aw, "R_aw_N": r_aw * 1000.0,
                "lambda_p_m": oracle.deep_water_wavelength(tp),
                "lambda_p_over_L": oracle.deep_water_wavelength(tp) / payload["L"],
                "domain": "short_wave_bridge", "envelope_satisfied": True,
                "heading_factor": 1.0, "speed_dependent": False, "method": "scripted-stawave1"}
    if path == "/maneuvering/turning":
        # HARD-CODED surrogate: everything is a fixed multiple of L
        return {"advance": 3.5 * payload["L"], "transfer": 2.0 * payload["L"],
                "tactical_diameter": 4.5 * payload["L"],
                "steady_diameter": 3.5 * payload["L"],
                "final_speed": 4.32, "turning_rate": 0.5}
    raise TransportError(f"unscripted path {path}")


def _client():
    return ScriptedTransport(_honest_api)


# --------------------------------------------------------------------------
# probe verdicts
# --------------------------------------------------------------------------
def test_honest_capabilities_are_certified_real():
    for cap_id in ("calc.resistance", "calc.stability", "propeller.open_water",
                   "calc.added_resistance"):
        spec = adapters.spec_by_id(cap_id)
        result = probes.audit_capability(_client(), spec)
        assert result["observed_level"] == "REAL", (cap_id, result["reasons"])
        assert result["status"] == "PASS"
        assert not result["overclaim"]


def test_hardcoded_maneuvering_model_is_caught():
    spec = adapters.spec_by_id("calc.maneuvering")
    result = probes.audit_capability(_client(), spec)
    assert result["observed_level"] == "MOCK"
    failed = {c["name"] for c in result["checks"] if c["passed"] is False and c["fatal"]}
    assert "diameter_responds_to_rudder" in failed
    assert "advance_responds_to_speed" in failed
    assert "diameter_widens_as_rudder_reduces" in failed


def test_analytic_detects_a_perturbed_constant():
    """A model that gets eta0 right but J wrong must fail the analytic knife."""
    def broken(path, payload):
        resp = _honest_api(path, payload)
        if path == "/propulsion/predict":
            resp["advance_ratio"] *= 1.10
        return resp

    result = probes.audit_capability(ScriptedTransport(broken),
                                     adapters.spec_by_id("propeller.open_water"))
    assert result["observed_level"] == "MOCK"
    assert any(c["family"] == "analytic" and c["passed"] is False for c in result["checks"])


def test_unreachable_endpoint_yields_withheld():
    def offline(path, payload):
        raise TransportError("connection refused")

    result = probes.audit_capability(ScriptedTransport(offline),
                                     adapters.spec_by_id("calc.resistance"))
    assert result["observed_level"] == "WITHHELD"
    assert result["status"] == "WITHHELD"


def test_benchmark_corpus_policy_is_respected():
    cases = benchmark.load_cases()
    assert len(cases) == 12
    adjudicable = {c["case_id"] for c in cases if c["adjudicable"]}
    assert adjudicable == {"SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"}
    for case in cases:
        if not case["adjudicable"]:
            assert case["reference_status"] == "WITHHELD"
            assert "corpus policy" in case["reason"]
        else:
            assert case["payload"]["body_type"] == "submerged"
            assert case["payload"]["water_density"] == pytest.approx(998.2)


def test_benchmark_probe_judges_only_adjudicable_rows():
    spec = adapters.spec_by_id("calc.resistance")
    cases = benchmark.load_cases()
    suboff = next(c for c in cases if c["case_id"] == "SUBOFF-01")
    withheld = next(c for c in cases if not c["adjudicable"])

    good = probes.run_benchmark(
        ScriptedTransport(lambda path, payload: {"ct": suboff["reference_ct"] * 1.05,
                                                 "wetted_surface": 5.99}), spec, suboff)
    assert good.passed is True and good.rel_error == pytest.approx(0.05)

    bad = probes.run_benchmark(
        ScriptedTransport(lambda path, payload: {"ct": suboff["reference_ct"] * 1.5,
                                                 "wetted_surface": 5.99}), spec, suboff)
    assert bad.passed is False and bad.rel_error > 0.15

    skipped = probes.run_benchmark(ScriptedTransport(lambda p, q: {}), spec, withheld)
    assert skipped.passed is None and "WITHHELD" in skipped.detail


def test_benchmark_accuracy_is_separate_from_mechanism_verdict():
    """A real algorithm that misses the reference must read REAL/FAIL, not MOCK."""
    spec = adapters.spec_by_id("calc.resistance")
    result = probes.audit_capability(_client(), spec)
    assert result["observed_level"] == "REAL"        # mechanism verified by four families
    assert result["accuracy_level"] in {"PASS", "FAIL"}
    assert result["counts"]["benchmark"]["total"] == 12
    assert all(f not in [c["name"] for c in result["checks"] if c["passed"] is False and c["fatal"]]
               for f in ["SUBOFF-02", "SUBOFF-03", "SUBOFF-04"])


def test_audit_all_counts_meet_acceptance_thresholds():
    payload = probes.audit_all(adapters.SPECS, _client(), api_base="scripted",
                               reachable=True, reachability_note="tests")
    crit = payload["criteria"]
    assert crit["invariance_covered"] >= 5
    assert crit["analytic_covered"] >= 3
    assert crit["monotonic_covered"] >= 3
    assert crit["capabilities_audited"] == len(adapters.SPECS)
    assert crit["checks_failed"] >= 3          # the manoeuvring surrogate must fail


# --------------------------------------------------------------------------
# anchor / verify round-trip
# --------------------------------------------------------------------------
@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "artifacts" / "capability_audit" / "figures").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "audit@example.org"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "audit"], check=True)
    return repo


_COUNTERS = {"invariance_covered": 6, "analytic_covered": 5, "monotonic_covered": 6,
             "checks_total": 50, "checks_passed": 46, "checks_failed": 4}


def _populate(art_dir: Path, text: str = "baseline") -> None:
    art_dir.joinpath("audit_results.json").write_text(
        json.dumps({"run": text, "criteria": dict(_COUNTERS)}), encoding="utf-8")
    art_dir.joinpath("criteria.json").write_text(
        json.dumps({"criteria": {**dict(_COUNTERS), "invariance_capabilities": 6,
                                 "analytic_capabilities": 5,
                                 "monotonic_capabilities": 6},
                    "tag": text}), encoding="utf-8")
    art_dir.joinpath("report.md").write_text(f"# report {text}\n", encoding="utf-8")
    art_dir.joinpath("figures").mkdir(exist_ok=True)
    art_dir.joinpath("figures", "fig01.png").write_bytes(b"\x89PNG" + text.encode())


def test_anchor_verify_roundtrip_and_tamper_detection(git_repo):
    art = git_repo / "artifacts" / "capability_audit"
    _populate(art)
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "artifacts"], check=True)

    key_file = art / "keys" / "signing.key"
    record = anchor_mod.create_anchor(art, git_repo, key_file=key_file,
                                      prev_anchor=None, index=1)
    (art / "anchor.json").write_text(json.dumps(record), encoding="utf-8")

    verification = anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)
    assert verification["ok"], verification["checks"]
    names = {c["name"]: c for c in verification["checks"]}
    assert names["ed25519_signature"]["ok"]
    assert names["artifact_digests"]["ok"]
    assert names["merkle_root"]["ok"]
    assert names["inclusion_proofs"]["ok"]
    assert names["external_time_anchor"]["ok"]
    assert names["external_time_anchor"]["artifacts_anchored_in_commit"] >= 2
    assert names["criteria_consistency"]["ok"]

    # tamper 1a: edit an anchored artifact (the machine evidence) after anchoring
    art.joinpath("audit_results.json").write_text(json.dumps({"run": "TAMPERED"}),
                                                  encoding="utf-8")
    ver = anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)
    assert not ver["ok"]
    assert not {c["name"]: c for c in ver["checks"]}["artifact_digests"]["ok"]
    art.joinpath("audit_results.json").write_text(
        json.dumps({"run": "baseline", "criteria": dict(_COUNTERS)}), encoding="utf-8")

    # tamper 1b: edit the derived narrative report -> not in the anchored set, but the
    # figure set it renders from is; editing the *numbers* is what gets caught
    art.joinpath("figures", "fig01.png").write_bytes(b"\x89PNG" + b"tampered")
    assert not anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)["ok"]
    art.joinpath("figures", "fig01.png").write_bytes(b"\x89PNG" + b"baseline")

    # tamper 1c: derived criteria.json counters that disagree with the anchored results
    art.joinpath("criteria.json").write_text(
        json.dumps({"criteria": {**dict(_COUNTERS), "invariance_capabilities": 1,
                                 "checks_total": 999}}), encoding="utf-8")
    ver = anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)
    assert not {c["name"]: c for c in ver["checks"]}["criteria_consistency"]["ok"]
    art.joinpath("criteria.json").write_text(
        json.dumps({"criteria": {**dict(_COUNTERS), "invariance_capabilities": 6,
                                 "analytic_capabilities": 5,
                                 "monotonic_capabilities": 6}}), encoding="utf-8")

    # tamper 2: flip the signature
    broken = json.loads((art / "anchor.json").read_text())
    sig = bytearray(bytes.fromhex(broken["signature"]["signature"]))
    sig[10] ^= 0xFF
    broken["signature"]["signature"] = sig.hex()
    (art / "anchor.json").write_text(json.dumps(broken), encoding="utf-8")
    ver = anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)
    assert not ver["ok"]
    assert not {c["name"]: c for c in ver["checks"]}["ed25519_signature"]["ok"]

    # tamper 3: rewrite the recorded root
    broken = json.loads((art / "anchor.json").read_text())
    broken["signature"]["signature"] = record["signature"]["signature"]
    broken["artifact_root"] = "00" * 32
    (art / "anchor.json").write_text(json.dumps(broken), encoding="utf-8")
    assert not anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)["ok"]


def test_commit_message_latch(git_repo):
    art = git_repo / "artifacts" / "capability_audit"
    _populate(art)
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "artifacts"], check=True)
    record = anchor_mod.create_anchor(art, git_repo, key_file=art / "k.key", index=1)
    (art / "anchor.json").write_text(json.dumps(record), encoding="utf-8")

    ver = anchor_mod.verify_anchor(art / "anchor.json", art, git_repo,
                                   require_commit_message=True)
    latch = {c["name"]: c for c in ver["checks"]}["commit_message_anchor"]
    assert not latch["ok"]           # root is not quoted anywhere yet

    subprocess.run(["git", "-C", str(git_repo), "commit", "--allow-empty", "-qm",
                    "anchor: CapabilityAudit-Anchor-Root: " + record["artifact_root"]],
                   check=True)
    ver = anchor_mod.verify_anchor(art / "anchor.json", art, git_repo,
                                   require_commit_message=True)
    latch = {c["name"]: c for c in ver["checks"]}["commit_message_anchor"]
    assert latch["ok"], latch["detail"]
    assert ver["ok"]


def test_relocated_artifact_copy_still_verifies(git_repo, tmp_path):
    """A third party holding a copy of the bytes outside the repo must be able to
    reproduce the root: no repo-relative path may leak into the leaf payload."""
    art = git_repo / "artifacts" / "capability_audit"
    _populate(art)
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "artifacts"], check=True)
    record = anchor_mod.create_anchor(art, git_repo, key_file=art / "k.key", index=1)
    (art / "anchor.json").write_text(json.dumps(record), encoding="utf-8")

    copy = tmp_path / "elsewhere"
    (copy / "figures").mkdir(parents=True)
    for name in ("audit_results.json", "criteria.json", "anchor.json"):
        (copy / name).write_bytes((art / name).read_bytes())
    for png in art.glob("figures/*.png"):
        (copy / "figures" / png.name).write_bytes(png.read_bytes())

    ver = anchor_mod.verify_anchor(copy / "anchor.json", copy, git_repo)
    checks = {c["name"]: c for c in ver["checks"]}
    assert checks["merkle_root"]["ok"], checks["merkle_root"]["detail"]
    assert checks["artifact_digests"]["ok"]
    # the git-side latch still resolves through the recorded repo path
    assert checks["external_time_anchor"]["artifacts_anchored_in_commit"] >= 2


def test_anchor_chain_detects_missing_predecessor(git_repo):
    art = git_repo / "artifacts" / "capability_audit"
    _populate(art)
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "artifacts"], check=True)
    anchor1 = anchor_mod.create_anchor(art, git_repo, key_file=art / "k1.key", index=1)
    old = art / "old_anchor.json"
    old.write_text(json.dumps(anchor1), encoding="utf-8")
    anchor2 = anchor_mod.create_anchor(art, git_repo, key_file=art / "k2.key",
                                       prev_anchor=old, index=2)
    (art / "anchor.json").write_text(json.dumps(anchor2), encoding="utf-8")
    assert anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)["ok"]
    old.unlink()
    ver = anchor_mod.verify_anchor(art / "anchor.json", art, git_repo)
    assert not {c["name"]: c for c in ver["checks"]}["anchor_chain"]["ok"]


# --------------------------------------------------------------------------
# CLI end-to-end (hermetic: scripted transport, temporary git repo)
# --------------------------------------------------------------------------
def test_cli_audit_report_anchor_verify(git_repo, monkeypatch, capsys):
    art = git_repo / "artifacts" / "capability_audit"
    art.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli, "HttpTransport", lambda *a, **k: ScriptedTransport(_honest_api))
    monkeypatch.setattr(cli, "probe_transport", lambda *a, **k: (True, "scripted"))

    assert cli.main(["audit", "--out", str(art), "--api-base", "scripted://"]) == 0
    results = json.loads((art / "audit_results.json").read_text())
    assert results["criteria"]["capabilities_audited"] == len(adapters.SPECS)

    # first report pass (no anchor yet) then anchor then report again with the anchor figure
    assert cli.main(["report", "--out", str(art)]) == 0
    crit = json.loads((art / "criteria.json").read_text())
    assert crit["pass"]["invariance"] and crit["pass"]["analytic"] and crit["pass"]["monotonic"]
    assert crit["pass"]["signature"] is False

    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "audit artifacts"], check=True)

    assert cli.main(["anchor", "--out", str(art), "--repo", str(git_repo),
                     "--key-file", str(art / "keys" / "cli.key")]) == 0
    anchor = json.loads((art / "anchor.json").read_text())
    assert anchor["signature"]["alg"] == "ed25519"
    assert anchor["artifact_count"] >= 5

    assert cli.main(["verify", "--out", str(art), "--repo", str(git_repo)]) == 0
    verification = json.loads((art / "verification.json").read_text())
    assert verification["ok"]

    assert cli.main(["report", "--out", str(art)]) == 0
    crit = json.loads((art / "criteria.json").read_text())
    assert crit["pass"]["signature"] is True and crit["all_passed"] is True
    assert "8. 复跑方式" in (art / "report.md").read_text()
    assert (art / "figures" / "anchor" / "fig05_merkle_anchor.png").exists()


def test_cli_strict_exit_code_on_falsified_capability(git_repo, monkeypatch):
    art = git_repo / "artifacts" / "capability_audit"
    art.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "HttpTransport", lambda *a, **k: ScriptedTransport(_honest_api))
    monkeypatch.setattr(cli, "probe_transport", lambda *a, **k: (True, "scripted"))
    code = cli.main(["audit", "--out", str(art), "--api-base", "scripted://", "--strict"])
    assert code == 1  # the hard-coded manoeuvring surrogate keeps the audit non-clean


def test_report_renders_from_audit_machine_data(tmp_path):
    payload = probes.audit_all(adapters.SPECS, _client(), api_base="scripted", reachable=True)
    audit = {"schema": cli.SCHEMA, "generated_at": "2026-01-01T00:00:00+00:00",
             "api_base": "scripted", "caps": payload["caps"], "criteria": payload["criteria"]}
    rendered = report.render_report(audit, tmp_path)
    md = (tmp_path / "report.md").read_text()
    assert "输入扰动不变性" in md and "防造假" in md
    assert all(Path(tmp_path / "figures" / f["name"]).exists() for f in rendered["figures"])
    crit = json.loads((tmp_path / "criteria.json").read_text())
    assert crit["criteria"]["invariance_capabilities"] >= 5