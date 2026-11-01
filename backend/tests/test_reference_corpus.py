"""Tests for the reference-value corpus: schema, anchors, digitisation, interpolation.

Run:  cd backend && python -m pytest tests/test_reference_corpus.py -q
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.reference_corpus import (  # noqa: E402
    DualPassViolation,
    EntryNotFound,
    IntegrityError,
    OutOfRangeError,
    ReferenceCorpus,
    SchemaViolation,
    interpolation_consistency,
    linear,
    pchip,
    read_table,
    require_dual_pass,
    sha256_file,
)
from services.reference_corpus.digitise import pointwise_rel_diff  # noqa: E402

ROOT = BACKEND_DIR / "data" / "reference_corpus"


@pytest.fixture(scope="module")
def corpus() -> ReferenceCorpus:
    return ReferenceCorpus(ROOT)


# ---------------------------------------------------------------------------
# (a) schema completeness
# ---------------------------------------------------------------------------


def test_corpus_loads_expected_entries(corpus):
    assert len(corpus) >= 8
    assert "ittc1957.cf_friction_line" in corpus
    assert "abramson1966.cyl_sloshing_omega1" in corpus


def test_schema_validation_zero_errors(corpus):
    assert corpus.schema_errors == []
    audit = corpus.audit()
    assert audit["schema_valid"] is True


def test_schema_completeness_is_100_percent(corpus):
    comp = corpus.audit()["schema_completeness"]
    for key in ("source", "sha256", "tolerance", "validity_range"):
        assert comp[key] == pytest.approx(1.0), key


def test_every_entry_declares_tolerance_and_coverage_factor(corpus):
    for entry in corpus.all():
        tol = entry.tolerance
        assert tol["type"] in ("relative", "absolute")
        assert tol["value"] > 0
        assert tol["basis"]
        assert entry.raw["k"] > 0


def test_every_entry_declares_source_with_document_and_table(corpus):
    for entry in corpus.all():
        src = entry.source
        assert src["type"] in ("JOURNAL", "BOOK", "STANDARD", "REPORT", "RULES", "DATASHEET", "DERIVED")
        assert len(src["doc"]) > 5
        assert src["table"]


def test_every_entry_has_digitisation_error_and_provenance(corpus):
    for entry in corpus.all():
        assert entry.raw["digitisation_error"]["value"] >= 0
        assert entry.raw["digitisation_error"]["unit"]
        assert entry.provenance in ("MEASURED", "ANALYTIC", "FITTED")
        assert entry.raw["supersedes"] is None or isinstance(entry.raw["supersedes"], str)


def test_schema_rejects_entry_missing_tolerance(tmp_path):
    root = _clone_corpus(tmp_path)
    path = root / "entries" / "ittc1957.cf_friction_line.json"
    raw = json.loads(path.read_text())
    raw.pop("tolerance")
    path.write_text(json.dumps(raw))
    with pytest.raises(SchemaViolation):
        ReferenceCorpus(root)


def test_schema_rejects_malformed_sha256(tmp_path):
    root = _clone_corpus(tmp_path)
    path = root / "entries" / "ittc1957.cf_friction_line.json"
    raw = json.loads(path.read_text())
    raw["sha256_of_scan"] = "not-a-sha256"
    path.write_text(json.dumps(raw))
    with pytest.raises(SchemaViolation):
        ReferenceCorpus(root)


def test_schema_rejects_inline_metadata_disagreeing_with_artefact(tmp_path):
    root = _clone_corpus(tmp_path)
    path = root / "entries" / "ittc1957.cf_friction_line.json"
    raw = json.loads(path.read_text())
    raw["table"]["n_points"] = raw["table"]["n_points"] + 1
    path.write_text(json.dumps(raw))
    with pytest.raises(SchemaViolation):
        ReferenceCorpus(root)


def _clone_corpus(tmp_path: Path) -> Path:
    dest = tmp_path / "corpus"
    shutil.copytree(ROOT, dest)
    return dest


# ---------------------------------------------------------------------------
# (b) dual independent digitisation
# ---------------------------------------------------------------------------


def test_dual_pass_within_gate_for_all_entries(corpus):
    audit = corpus.audit()
    assert audit["dual_pass_ok"] is True
    assert audit["max_dual_pass_rel_diff"] <= 1e-3
    for entry in corpus.all():
        rep = entry.dual_pass(gate=1e-3)
        assert rep["max_rel_diff"] <= 1e-3, entry.id
        assert rep["n_points"] == len(entry.xs)


def test_dual_pass_uses_two_distinct_artefacts(corpus):
    for entry in corpus.all():
        a = ROOT / entry.raw["table"]["path"]
        b = ROOT / entry.raw["table"]["pass_b_path"]
        assert a != b
        assert sha256_file(a) != sha256_file(b)


def test_dual_pass_gate_rejects_disagreement():
    rep = {"max_rel_diff": 5e-3, "n_points": 10}
    with pytest.raises(DualPassViolation):
        require_dual_pass("synthetic.entry", rep, gate=1e-3)
    ok = require_dual_pass("synthetic.entry", {"max_rel_diff": 1e-5, "n_points": 10}, gate=1e-3)
    assert ok["pass"] is True


def test_pointwise_rel_diff_detects_transcription_error():
    xs = [0.1, 0.2, 0.3]
    rep = pointwise_rel_diff(xs, [1.0, 2.0, 3.0], xs, [1.0, 2.0, 3.3])
    assert rep["max_rel_diff"] == pytest.approx(0.3 / 3.3)
    assert rep["worst_row"] == 2


def test_pointwise_rel_diff_rejects_abscissa_mismatch():
    with pytest.raises(ValueError):
        pointwise_rel_diff([0.1, 0.2], [1.0, 2.0], [0.1, 0.25], [1.0, 2.0])


def test_read_table_rejects_non_monotonic_abscissa(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("0.1,1.0\n0.3,2.0\n0.2,3.0\n")
    with pytest.raises(ValueError):
        read_table(bad)


# ---------------------------------------------------------------------------
# (d) idempotence & provenance: 1 flipped bit -> refuse to serve
# ---------------------------------------------------------------------------


def test_anchor_matches_for_all_entries(corpus):
    assert all(corpus.verify_all().values())
    for entry in corpus.all():
        assert corpus.verify_integrity(entry.id) == entry.raw["sha256_of_scan"]


@pytest.mark.parametrize("entry_id", ReferenceCorpus(ROOT).ids())
def test_one_bit_flip_is_refused(entry_id, tmp_path):
    root = _clone_corpus(tmp_path)
    path = root / "entries" / f"{entry_id}.json"
    raw = json.loads(path.read_text())
    artefact = root / raw["table"]["path"]
    data = bytearray(artefact.read_bytes())
    data[len(data) // 2] ^= 0x01  # flip exactly one bit
    artefact.write_bytes(bytes(data))
    tampered = ReferenceCorpus(root)
    with pytest.raises(IntegrityError):
        tampered.verify_integrity(entry_id)
    with pytest.raises(IntegrityError):
        tampered.evaluate(entry_id, float(raw["validity_range"]["x_min"]))


def test_evaluate_is_idempotent(corpus):
    entry = corpus.get("ittc1957.cf_friction_line")
    x = 8.4
    first = corpus.evaluate("ittc1957.cf_friction_line", x)["value"]
    second = corpus.evaluate("ittc1957.cf_friction_line", x)["value"]
    assert first == second == pytest.approx(float(entry.evaluate(x)))


# ---------------------------------------------------------------------------
# (c) interpolation consistency + validity range
# ---------------------------------------------------------------------------


def test_interpolation_consistency_gate_for_all_entries(corpus):
    audit = corpus.audit()
    assert audit["interp_ok"] is True
    assert audit["max_pchip_vs_linear_rel_diff"] <= 1e-3
    for entry in corpus.all():
        cons = interpolation_consistency(entry.xs, entry.ys)
        assert cons["max_rel_diff"] <= 1e-3, entry.id


@pytest.mark.parametrize("entry_id", ReferenceCorpus(ROOT).ids())
def test_out_of_range_raises(entry_id, corpus):
    entry = corpus.get(entry_id)
    lo = float(entry.validity["x_min"])
    hi = float(entry.validity["x_max"])
    with pytest.raises(OutOfRangeError):
        entry.evaluate(hi + 0.5 * max(abs(hi), 1.0))
    if lo > 0:
        with pytest.raises(OutOfRangeError):
            entry.evaluate(lo * 0.5)


def test_pchip_interpolates_nodes_exactly(corpus):
    entry = corpus.get("ittc1957.cf_friction_line")
    for i in (0, len(entry.xs) // 2, len(entry.xs) - 1):
        assert pchip(entry.xs, entry.ys, [entry.xs[i]])[0] == pytest.approx(entry.ys[i], rel=1e-12)


def test_pchip_preserves_monotonicity_on_stiff_data():
    xs = [0.0, 0.5, 1.0, 1.5, 2.0]
    ys = [0.0, 0.02, 0.5, 0.98, 1.0]
    q = [0.05 * k for k in range(0, 40)]
    yq = pchip(xs, ys, q)
    assert all(yq[i] <= yq[i + 1] + 1e-15 for i in range(len(yq) - 1))


def test_linear_and_pchip_agree_on_a_linear_table():
    xs = [0.0, 1.0, 2.0]
    ys = [1.0, 3.0, 5.0]
    q = [0.3, 1.2, 1.9]
    assert pchip(xs, ys, q) == pytest.approx(linear(xs, ys, q), rel=1e-12)


def test_ittc1957_closed_form_value(corpus):
    """Cf(Re=1e9) = 0.075/(9-2)^2 = 0.001530612... -- the classic 1957 value."""
    v = corpus.evaluate("ittc1957.cf_friction_line", 9.0)["value"]
    assert v == pytest.approx(0.075 / 49.0, rel=1e-9)


def test_ittc1978_roughness_endpoints(corpus):
    lo = corpus.evaluate("ittc1978.dcf_roughness_allowance", -6.0)["value"]
    hi = corpus.evaluate("ittc1978.dcf_roughness_allowance", -4.0)["value"]
    assert lo == pytest.approx(0.41e-3, rel=1e-6)
    assert hi == pytest.approx((105.0 * 1e-4 ** (1 / 3) - 0.64) * 1e-3, rel=1e-6)
    assert 0.4e-3 < lo < hi < 5e-3


def test_sloshing_limit_consistency(corpus):
    """The tabulated dispersion must reproduce omega ~ sqrt(g k h) for kh<<1.

    With L = 1 and h/L = 0.1: omega*sqrt(L/g) = sqrt(k tanh(k h)) ~ sqrt(k^2 h).
    """
    ratio = corpus.evaluate("faltinsen2000.rect_sloshing_omega1", 0.1)["value"]
    k = math.pi
    h = 0.1
    # exact table value
    assert ratio == pytest.approx(math.sqrt(k * math.tanh(k * h)), rel=1e-6)
    # and it is within 3% of the shallow-water asymptote sqrt(g k^2 h) (L=g=1)
    assert ratio == pytest.approx(math.sqrt(k * k * h), rel=3e-2)


def test_plate_kernel_and_westergaard_closed_form(corpus):
    """-kei(0) = pi/4 -> w_max = P/(8 sqrt(Dk)); the table must carry that value."""
    v0 = corpus.evaluate("westergaard1926.plate_kei_winkler", 0.0)["value"]
    assert v0 == pytest.approx(math.pi / 4.0, rel=1e-6)


def test_pasternak_factor_reduces_to_winkler_at_zero(corpus):
    assert corpus.evaluate("kerr1964.pasternak_peak_factor", 0.0)["value"] == pytest.approx(1.0, rel=1e-9)
    assert corpus.evaluate("kerr1964.pasternak_peak_factor", 0.5)["value"] == pytest.approx(0.7698, abs=2e-3)


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


def test_query_by_quantity(corpus):
    hits = corpus.query(quantity="friction_coefficient")
    assert [h.id for h in hits] == ["ittc1957.cf_friction_line"]


def test_query_by_validity_excludes_entries_outside_range(corpus):
    hits = corpus.query(quantity="sloshing_natural_frequency_ratio_mode1", validity=0.5)
    assert len(hits) == 1
    assert corpus.query(quantity="sloshing_natural_frequency_ratio_mode1", validity=99.0) == []


def test_query_by_domain_and_provenance(corpus):
    assert {h.id for h in corpus.query(domain="seakeeping")} == {
        "faltinsen2000.rect_sloshing_omega1",
        "faltinsen2000.rect_sloshing_omega2",
        "faltinsen2000.rect_sloshing_omega3",
        "abramson1966.cyl_sloshing_omega1",
    }
    assert corpus.query(provenance="MEASURED") == []
    assert len(corpus.query(provenance="ANALYTIC")) >= 8


def test_unknown_entry_raises(corpus):
    with pytest.raises(EntryNotFound):
        corpus.get("does.not_exist")