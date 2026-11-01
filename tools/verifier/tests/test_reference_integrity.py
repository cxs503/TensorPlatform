"""
Reference integrity + fault-injection tests for the verification instrument.

Two questions are answered here:

 1. Is the question bank's reference value *independent* of the candidate?
    (before this work, the Holtrop-Mennen references were synthesised by the
    same private expression the pilot candidate implemented — circular)

 2. Does the verifier actually *detect* wrong implementations?
    Self-consistency of a verifier proves nothing; it has to be shown to reject
    MOCK / mutated candidates.  The mutation matrix below perturbs a correct
    implementation and asserts the verdict flips to `fail`.
"""
from __future__ import annotations

import math
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.question_bank import DEFAULT_QBANK_PATH, QuestionBank  # noqa: E402
from verifier.reference import holtrop_mennen_1982 as hm  # noqa: E402
from verifier.reference import integrity  # noqa: E402
from verifier.reverify_question_bank import load_rows  # noqa: E402
from verifier.verifier import Verifier  # noqa: E402

CASES = [
    dict(Cb=0.6, L=100.0, B=15.0, T=6.0, Cm=0.98, lcb=0.010),
    dict(Cb=0.7, L=150.0, B=22.0, T=8.5, Cm=0.985, lcb=0.010),
    dict(Cb=0.8, L=200.0, B=32.0, T=11.0, Cm=0.99, lcb=0.010),
]

#: The retired private expression, kept verbatim as the "before" specimen.
def retired_expression(Cb, L, B, T, **_ignored):
    return 0.93 + 0.487118 * (Cb ** (-0.9249)) * (L / B) ** 0.15 * (B / T) ** 0.2 * 0.1


def retired_case(case):
    return retired_expression(case["Cb"], case["L"], case["B"], case["T"])


def published(spec):
    return hm.form_factor_1pk(
        Cb=spec["Cb"], L=spec["L"], B=spec["B"], T=spec["T"],
        Cm=spec["Cm"], lcb=spec.get("lcb", 0.0), C_stern=spec.get("C_stern", 0.0),
    )


class TestPublishedFormula(unittest.TestCase):
    """The reference module is a faithful transcription of the published form."""

    def test_monotone_and_in_window(self):
        values = [published(c) for c in CASES]
        for a, b in zip(values, values[1:]):
            self.assertLess(a, b, f"1+k must increase with fullness: {values}")
        for v in values:
            lo, hi = integrity.ONE_PK_WINDOW
            self.assertTrue(lo <= v <= hi, f"1+k={v} outside published window")

    def test_two_assembly_paths_agree(self):
        """Chained multiplication vs log-space assembly must agree to machine epsilon."""
        for case in CASES:
            gap = hm.check_assembly_consistency(**case)
            self.assertLess(gap, 1e-12, f"assembly paths diverge for {case}: {gap}")

    def test_stern_parameter_enters_only_through_c14(self):
        base = hm.form_factor_terms(**CASES[2])
        stern = hm.form_factor_terms(**CASES[2], C_stern=10.0)
        self.assertAlmostEqual(base.c14, 1.0, places=15)
        self.assertAlmostEqual(stern.c14, 1.0 + 0.011 * 10.0, places=15)
        # L_R, Cp, nabla must be untouched by the stern parameter
        self.assertAlmostEqual(base.L_R, stern.L_R, places=12)
        self.assertAlmostEqual(base.Cp, stern.Cp, places=12)

    def test_run_length_follows_prismatic_deficit(self):
        """With lcb=0 the published run length is L_R = L*(1-Cp): the full-form
        hull has the short steep run, the fine hull the long one."""
        previous = None
        for Cb, Cm in ((0.60, 0.98), (0.70, 0.985), (0.80, 0.99), (0.85, 0.86)):
            r = hm.form_factor_terms(Cb=Cb, L=200.0, B=32.0, T=11.0, Cm=Cm, lcb=0.0)
            self.assertAlmostEqual(r.L_R / 200.0, 1.0 - r.Cp, places=12)
            if previous is not None:
                self.assertLess(r.L_R, previous, "L_R must shrink as the hull fills out")
            previous = r.L_R

    def test_applicability_box_flags_out_of_range(self):
        inside = hm.within_applicability(0.8, 200.0, 32.0, 11.0, 0.99)
        self.assertTrue(all(inside.values()), inside)
        outside = hm.within_applicability(0.8, 60.0, 6.0, 20.0, 0.99)
        self.assertFalse(all(outside.values()), outside)

    def test_invalid_geometry_rejected(self):
        with self.assertRaises(ValueError):
            hm.form_factor_1pk(Cb=0.9, L=1.0, B=1.0, T=1.0, Cm=0.8)  # Cb > Cm


class TestConformanceDiscriminatesRetiredExpression(unittest.TestCase):
    """The criteria must reject the retired private expression and accept the published one."""

    def _rows(self, evaluator):
        rows = []
        for case in CASES:
            rows.append({
                "title": f"Cb={case['Cb']}",
                "specification": dict(case, method="holtrop_mennen_form_factor"),
                "expected_result": {"1+k": evaluator(case)},
                "provenance": {"expression": hm.CANONICAL_1PK_EXPRESSION},
            })
        return rows

    def test_published_values_pass_all_criteria(self):
        rows = self._rows(published)
        for result in integrity.audit_form_factor(rows):
            self.assertTrue(result.passed, f"{result.criterion}: {result.detail}")

    def test_retired_values_fail_physical_criteria(self):
        rows = self._rows(retired_case)
        # fingerprint is identical in this synthetic set, so C1 is not the discriminator
        for criterion in ("C2_monotonicity", "C3_magnitude_window", "C4_trend_anchor"):
            result = next(r for r in integrity.audit_form_factor(rows) if r.criterion == criterion)
            self.assertFalse(result.passed, f"{criterion} should reject the retired expression")

    def test_retired_values_have_no_canonical_expression(self):
        rows = self._rows(retired_case)
        for row in rows:
            row["provenance"] = {"source": "Holtrop & Mennen (1982) — simplified form factor"}
        result = integrity.check_expression_fingerprint(rows)
        self.assertFalse(result.passed, "missing canonical expression must fail C1")

    def test_trend_band_separates_retired_from_published(self):
        """Discrimination margin, stated numerically rather than assumed.

        The one-parameter anchor over-predicts k at high Cb, so the band is
        relative and generous (0.50).  It is only defensible if it still
        separates: published <= ~0.35, retired >= ~0.78.
        """
        def worst_ratio(evaluator):
            worst = 0.0
            for case in CASES:
                k = evaluator(case) - 1.0
                anchor = hm.watanabe_trend_anchor(case["Cb"], case["L"] / case["B"])
                worst = max(worst, abs(k - anchor) / abs(anchor))
            return worst

        new_ratio = worst_ratio(published)
        old_ratio = worst_ratio(retired_case)
        self.assertLess(new_ratio, integrity.TREND_RTOL)
        self.assertGreater(old_ratio, integrity.TREND_RTOL)
        self.assertGreater(old_ratio / new_ratio, 2.0,
                           "separation margin between retired and published is too thin")


class TestCommittedBankIntegrity(unittest.TestCase):
    """Regression gate on the committed question bank itself."""

    def test_every_question_is_independently_rederivable(self):
        audit = integrity.audit_bank(load_rows(DEFAULT_QBANK_PATH))
        failures = [c for c in audit["criteria"] if not c["passed"]]
        self.assertEqual(failures, [], f"question bank integrity failures: {failures}")
        self.assertTrue(audit["all_passed"])
        self.assertEqual(audit["n_questions"], 15)

    def test_no_question_references_a_private_expression(self):
        for row in load_rows(DEFAULT_QBANK_PATH):
            prov = row.get("provenance") or {}
            self.assertNotIn("simplified", str(prov.get("source", "")).lower(),
                             f"{row['title']} still claims a simplified source")
            if row["specification"].get("method") == "holtrop_mennen_form_factor":
                self.assertEqual(
                    (prov.get("expression") or "").replace(" ", ""),
                    hm.CANONICAL_1PK_EXPRESSION.replace(" ", ""),
                )
                self.assertEqual(prov.get("derive"), "independent")


class TestMutationDetection(unittest.TestCase):
    """Fault injection: the verifier must reject MOCK / perturbed candidates."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="qb-mut-")
        cls.qbank = os.path.join(cls.tmpdir, "questions.db")
        shutil.copy(DEFAULT_QBANK_PATH, cls.qbank)
        os.chmod(cls.qbank, 0o600)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _verdict(self, candidate_fn, candidate_id, domain):
        verifier = Verifier(self.qbank, "mut-verifier")
        report = verifier.verify_candidate(
            candidate_id=candidate_id, candidate_fn=candidate_fn, domain=domain,
        )
        return report.overall_verdict, report

    # -- candidates ------------------------------------------------------
    @staticmethod
    def _ff_only(transform):
        def fn(spec):
            if spec.get("method") != "holtrop_mennen_form_factor":
                return _honest_other(spec)
            return {"1+k": transform(published(spec), spec)}
        return fn

    def test_correct_candidate_passes(self):
        verdict, report = self._verdict(self._ff_only(lambda v, s: v), "mut-correct", "resistance")
        self.assertEqual(verdict, "pass", [r.error_message for r in report.results])

    def test_retired_formula_is_rejected(self):
        """The exact defect this work removes: a candidate built from the retired
        private expression must NOT be able to pass the corrected bank."""
        verdict, _ = self._verdict(
            self._ff_only(lambda v, s: retired_expression(
                s["Cb"], s["L"], s["B"], s["T"])), "mut-retired", "resistance")
        self.assertEqual(verdict, "fail")

    def test_constant_mock_is_rejected(self):
        verdict, _ = self._verdict(self._ff_only(lambda v, s: 1.10), "mut-const", "resistance")
        self.assertEqual(verdict, "fail")

    def test_five_percent_bias_is_rejected(self):
        """Tolerance is 1%: a 5% bias must fail."""
        verdict, _ = self._verdict(self._ff_only(lambda v, s: v * 1.05), "mut-5pct", "resistance")
        self.assertEqual(verdict, "fail")

    def test_fifteen_percent_bias_is_rejected(self):
        verdict, _ = self._verdict(self._ff_only(lambda v, s: v * 1.15), "mut-15pct", "resistance")
        self.assertEqual(verdict, "fail")

    def test_sub_tolerance_bias_is_accepted(self):
        """Sensitivity floor: a 0.2% bias is inside the 1% band and must pass."""
        verdict, _ = self._verdict(self._ff_only(lambda v, s: v * 1.002), "mut-02pct", "resistance")
        self.assertEqual(verdict, "pass")

    def test_nan_is_rejected(self):
        verdict, _ = self._verdict(self._ff_only(lambda v, s: float("nan")), "mut-nan", "resistance")
        self.assertEqual(verdict, "fail")

    def test_missing_metric_is_rejected(self):
        def fn(spec):
            if spec.get("method") == "holtrop_mennen_form_factor":
                return {"k": 0.2}  # wrong metric name
            return _honest_other(spec)
        verdict, _ = self._verdict(fn, "mut-missing", "resistance")
        self.assertEqual(verdict, "fail")

    def test_swapped_arguments_are_rejected(self):
        def fn(spec):
            if spec.get("method") == "holtrop_mennen_form_factor":
                return {"1+k": hm.form_factor_1pk(
                    Cb=spec["Cb"], L=spec["B"], B=spec["L"], T=spec["T"],
                    Cm=spec["Cm"], lcb=spec.get("lcb", 0.0),
                )}
            return _honest_other(spec)
        verdict, _ = self._verdict(fn, "mut-swapped", "resistance")
        self.assertEqual(verdict, "fail")

    def test_detection_rate_is_full(self):
        """Aggregate: every mutant is caught, every honest variant is accepted."""
        mutants = {
            "retired": self._ff_only(lambda v, s: retired_expression(s["Cb"], s["L"], s["B"], s["T"])),
            "const": self._ff_only(lambda v, s: 1.10),
            "bias5": self._ff_only(lambda v, s: v * 1.05),
            "bias15": self._ff_only(lambda v, s: v * 1.15),
            "nan": self._ff_only(lambda v, s: float("nan")),
            "neg": self._ff_only(lambda v, s: -v),
        }
        caught = 0
        for name, fn in mutants.items():
            verdict, _ = self._verdict(fn, f"rate-{name}", "resistance")
            caught += verdict == "fail"
        rate = caught / len(mutants)
        self.assertEqual(rate, 1.0, f"mutation detection rate {rate:.2f} < 1.00")


def _honest_other(spec):
    """Correct implementations of the non-form-factor formulas in the bank."""
    formula = spec.get("formula") or spec.get("method") or ""
    if formula == "ittc_1957":
        return {"Cf": 0.075 / (math.log10(spec["Re"]) - 2) ** 2}
    if formula == "blasius_laminar":
        return {"Cf": 1.328 / math.sqrt(spec["Re_L"])}
    if formula == "prandtl_schlichting":
        return {"Cf": 0.455 / (math.log10(spec["Re_L"]) ** 2.58)}
    return {}


if __name__ == "__main__":
    unittest.main(verbosity=2)