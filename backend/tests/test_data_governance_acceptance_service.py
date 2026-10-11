import pandas as pd

from services.data_governance_acceptance_service import evaluate_governance


def test_basic_governance_passes_clean_dataset_without_mutation():
    frame = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    original = frame.copy(deep=True)
    report = evaluate_governance(frame, "basic")
    assert report["passed"] is True
    assert report["checks_failed"] == 0
    pd.testing.assert_frame_equal(frame, original)


def test_governance_fails_incomplete_dataset_and_returns_report():
    frame = pd.DataFrame({"x": [1, None, None], "y": ["a", "b", "c"]})
    report = evaluate_governance(frame, "basic")
    assert report["passed"] is False
    failed_ids = {item["id"] for item in report["checks"] if not item["passed"]}
    assert "completeness" in failed_ids

