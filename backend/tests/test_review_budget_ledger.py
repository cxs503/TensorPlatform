"""SPEC §6.5 审查预算账本：四类记账 / 只增不改 / 周聚合 / API 口径。

夹具全部使用**合成域名**（alpha/beta 工件名），本仓零领域语义：
被测模块不得出现任何领域专名，因此本文件的断言与夹具也不得引用真实领域术语。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.review_budget import router as review_budget_router
from services.review_budget_ledger import (
    CAP_RATIO,
    CONCLUSIONS,
    KIND_EXEMPTION,
    KIND_PRODUCTION,
    KIND_REVIEW,
    REVIEW_TYPES,
    LedgerImmutabilityError,
    LedgerValidationError,
    ReviewBudgetLedger,
    aggregate_window,
    latest_complete_week_start,
    render_weekly_markdown,
    week_window,
)
from services.review_budget_weekly import (
    WEEKLY_INTERVAL_S,
    write_weekly_report as weekly_aggregate,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)  # 任取窗口内时刻


def _clock(value: datetime):
    return lambda: value


@pytest.fixture()
def ledger(tmp_path):
    return ReviewBudgetLedger(tmp_path / "ledger.jsonl", clock=_clock(NOW))


def test_four_review_types_all_recorded(ledger):
    for review_type in REVIEW_TYPES:
        entry = ledger.record_review(
            review_type,
            subject=f"artifact::{review_type}_alpha",
            duration_s=60.0,
            conclusion="approved",
            reviewer="reviewer-alpha",
        )
        assert entry["kind"] == KIND_REVIEW
        assert entry["review_type"] == review_type
    rows = ledger.entries()
    assert [row["review_type"] for row in rows] == list(REVIEW_TYPES)
    assert [row["seq"] for row in rows] == [0, 1, 2, 3]
    assert ledger.verify_chain()["ok"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"review_type": "ship_resistance"},          # 非四类之一
        {"review_type": "execution", "conclusion": "exempted"},
        {"review_type": "execution", "duration_s": -1.0},
        {"review_type": "execution", "duration_source": "guessed"},
        {"review_type": "execution", "subject": "  "},
        {"review_type": "execution", "reviewer": ""},
    ],
)
def test_invalid_records_rejected(ledger, kwargs):
    payload = dict(
        review_type="execution",
        subject="artifact::alpha",
        duration_s=10.0,
        conclusion="approved",
        reviewer="reviewer-alpha",
    )
    payload.update(kwargs)
    with pytest.raises(LedgerValidationError):
        ledger.record_review(
            payload["review_type"],
            payload["subject"],
            payload["duration_s"],
            payload["conclusion"],
            payload["reviewer"],
            duration_source=payload.get("duration_source", "reported"),
        )
    assert ledger.entries() == []


def test_not_observable_duration_must_be_zero(ledger):
    entry = ledger.record_review(
        "technical",
        "artifact::beta",
        duration_s=0.0,
        conclusion="inconclusive",
        reviewer="gate-alpha",
        duration_source="not_observable",
    )
    assert entry["duration_source"] == "not_observable"
    with pytest.raises(LedgerValidationError):
        ledger.record_review(
            "technical",
            "artifact::beta",
            12.0,
            "approved",
            "gate-alpha",
            duration_source="not_observable",
        )


def test_append_only_chain_detects_rewrite_and_blocks_append(ledger):
    ledger.record_review("execution", "artifact::alpha", 30.0, "approved", "reviewer-alpha")
    ledger.record_review("technical", "artifact::beta", 20.0, "approved_with_findings", "gate-beta")
    path = ledger.path

    # 正常链：ok
    assert ledger.verify_chain() == {
        "ok": True,
        "entries": 2,
        "broken_at": None,
        "head_hash": ledger.entries()[-1]["entry_hash"],
    }

    # 改写历史行（把耗时从 30s 改成 1s）→ 链在 0 号条目处断裂
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    lines[0]["duration_s"] = 1.0
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for row in lines)
        + "\n",
        encoding="utf-8",
    )
    state = ledger.verify_chain()
    assert state["ok"] is False and state["broken_at"] == 0
    # 只增不改：链坏即拒绝继续追加（不会被静默追加洗白）
    with pytest.raises(LedgerImmutabilityError):
        ledger.record_review("outcome", "artifact::gamma", 5.0, "approved", "reviewer-alpha")


def test_ledger_exposes_no_update_or_delete(ledger):
    public = [name for name in dir(ledger) if not name.startswith("_")]
    assert not [n for n in public if n.split("_")[0] in {"update", "delete", "remove", "edit", "rewrite"}]


def test_weekly_aggregation_ratio_coverage_and_per_type(ledger):
    ledger.record_review("execution", "artifact::alpha", 600.0, "approved", "reviewer-alpha",
                         duration_source="observed")
    ledger.record_review("technical", "artifact::beta", 300.0, "changes_requested", "gate-beta",
                         duration_source="reported")
    ledger.record_review("outcome", "artifact::gamma", 0.0, "inconclusive", "gate-gamma",
                         duration_source="not_observable")
    ledger.record_review("innovation", "proposal::alpha", 0.0, "inconclusive", "gate-alpha",
                         duration_source="not_observable")
    ledger.record_exemption("artifact::delta", "机器可裁定部分已由契约测试覆盖", "gate-beta",
                            review_type="technical")
    ledger.record_production("run::alpha", 4000.0, actor="worker-alpha", duration_source="observed")

    report = aggregate_window(ledger.entries(), NOW)
    gov = report["governance"]
    assert gov["events"] == 5 and gov["reviews"] == 4 and gov["exemptions"] == 1
    assert gov["duration_s"] == 900.0
    assert report["production"] == {"events": 1, "duration_s": 4000.0}
    ratios = report["ratios"]
    assert ratios["duration_ratio_governance_over_production"] == pytest.approx(0.225)
    assert ratios["event_ratio_governance_over_production"] == pytest.approx(5.0)
    assert ratios["event_ratio_reviews_only"] == pytest.approx(4.0)
    # 覆盖率口径 = 有可观测/申报耗时的治理事件 / 全部治理事件（审查+豁免）
    assert ratios["governance_duration_coverage"] == pytest.approx(2 / 5)
    assert ratios["cap_status"] == "insufficient_duration_coverage"
    assert ratios["cap_ratio_spec"] == CAP_RATIO and CAP_RATIO == 0.25
    per_type = {bucket["review_type"]: bucket for bucket in gov["per_type"]}
    assert per_type["execution"]["reviews"] == 1
    assert per_type["technical"]["exemptions"] == 1
    assert per_type["technical"]["conclusions"] == {"changes_requested": 1}
    assert per_type["innovation"]["reviews"] == 1 and per_type["innovation"]["duration_s"] == 0.0


def test_measured_cap_status_when_coverage_sufficient(ledger):
    ledger.record_review("execution", "artifact::alpha", 500.0, "approved", "reviewer-alpha")
    ledger.record_review("outcome", "artifact::beta", 500.0, "approved", "gate-beta")
    ledger.record_production("run::alpha", 5000.0, actor="worker-alpha")
    ratios = aggregate_window(ledger.entries(), NOW)["ratios"]
    assert ratios["governance_duration_coverage"] == pytest.approx(1.0)
    assert ratios["cap_status"] == "measured"
    assert ratios["duration_ratio_governance_over_production"] == pytest.approx(0.2)
    assert ratios["over_cap_by_minutes"] is False
    assert ratios["governance_share_of_total_duration"] == pytest.approx(1000 / 6000, abs=1e-5)


def test_window_boundaries_and_exemption_requires_reason(ledger):
    start, end = week_window(NOW)
    ledger.record_review("execution", "artifact::alpha", 10.0, "approved", "reviewer-alpha",
                         recorded_at=start - timedelta(seconds=1))
    ledger.record_review("execution", "artifact::beta", 20.0, "approved", "reviewer-alpha",
                         recorded_at=start)
    ledger.record_review("execution", "artifact::gamma", 40.0, "approved", "reviewer-alpha",
                         recorded_at=end - timedelta(seconds=1))
    ledger.record_review("execution", "artifact::delta", 80.0, "approved", "reviewer-alpha",
                         recorded_at=end)
    report = aggregate_window(ledger.entries(), NOW)
    assert report["governance"]["events"] == 2  # 半开区间 [start, end)
    assert report["governance"]["duration_s"] == 60.0
    with pytest.raises(LedgerValidationError):
        ledger.record_exemption("artifact::eps", "   ", "gate-beta")
    exempt = ledger.record_exemption("artifact::eps", "低风险抽检未抽中", "gate-beta")
    assert exempt["kind"] == KIND_EXEMPTION and exempt["conclusion"] == "exempted"
    assert exempt["evidence"]["reason"] == "低风险抽检未抽中"


def test_module_is_domain_neutral():
    """零领域语义：模块与 API 源码中不得出现领域专名（域词由调用方/域仓提供）。"""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    sources = [
        backend / "services" / "review_budget_ledger.py",
        backend / "api" / "review_budget.py",
        backend / "tasks" / "review_budget_tasks.py",
    ]
    forbidden = ("ship", "hull", "propulsion", "resistance", "seakeeping", "船舶", "阻力", "船体")
    for source in sources:
        text = source.read_text(encoding="utf-8").lower()
        hits = [word for word in forbidden if word in text]
        assert not hits, f"{source.name} 命中领域词 {hits}"
    assert set(REVIEW_TYPES) == {"execution", "technical", "outcome", "innovation"}


def test_api_four_types_entries_and_weekly(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_REVIEW_BUDGET_LEDGER", str(tmp_path / "api_ledger.jsonl"))
    app = FastAPI()
    app.include_router(review_budget_router, prefix="/api/v1")
    client = TestClient(app)

    types = client.get("/api/v1/governance/review-budget/types")
    assert types.status_code == 200
    assert [item["id"] for item in types.json()["review_types"]] == list(REVIEW_TYPES)
    assert types.json()["conclusions"] == list(CONCLUSIONS)

    for index, review_type in enumerate(REVIEW_TYPES):
        resp = client.post(
            "/api/v1/governance/review-budget/reviews",
            json={
                "review_type": review_type,
                "subject": f"artifact::alpha_{index}",
                "duration_s": 120.0,
                "conclusion": "approved",
                "reviewer": "reviewer-alpha",
                "duration_source": "observed",
                "recorded_at": NOW.isoformat(),
                "evidence": {"run_id": f"run-alpha-{index}"},
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["chain"]["ok"] is True

    assert client.post(
        "/api/v1/governance/review-budget/production",
        json={"subject": "run::alpha", "duration_s": 2400.0, "actor": "worker-alpha",
              "recorded_at": NOW.isoformat()},
    ).status_code == 201

    bad = client.post(
        "/api/v1/governance/review-budget/reviews",
        json={"review_type": "not_a_type", "subject": "artifact::x", "reviewer": "r"},
    )
    assert bad.status_code == 422

    listed = client.get(
        "/api/v1/governance/review-budget/entries",
        params={"review_type": "outcome", "week_start": NOW.date().isoformat()},
    ).json()
    assert listed["total"] == 1 and listed["items"][0]["review_type"] == "outcome"

    weekly = client.get(
        "/api/v1/governance/review-budget/weekly",
        params={"week_start": NOW.date().isoformat()},
    ).json()
    assert weekly["governance"]["events"] == 4
    assert weekly["ratios"]["duration_ratio_governance_over_production"] == pytest.approx(0.2)
    assert weekly["ledger"]["chain"]["ok"] is True


def test_weekly_task_writes_report_to_docs_and_artifacts(tmp_path, monkeypatch):
    ledger_path = tmp_path / "task_ledger.jsonl"
    ledger = ReviewBudgetLedger(ledger_path, clock=_clock(NOW))
    ledger.record_review("execution", "artifact::alpha", 300.0, "approved", "reviewer-alpha")
    ledger.record_production("run::alpha", 3000.0, actor="worker-alpha")
    monkeypatch.setenv("TP_REVIEW_BUDGET_DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("TP_REVIEW_BUDGET_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    result = weekly_aggregate(
        week_start=latest_complete_week_start(NOW).date().isoformat(),
        ledger_path=str(ledger_path),
    )
    md = result["markdown_path"]
    assert md.endswith(".md") and open(md, encoding="utf-8").read().startswith("# 审查预算周报")
    assert json.load(open(result["json_path"], encoding="utf-8"))["iso_week"] == result["report"]["iso_week"]
    text = open(md, encoding="utf-8").read()
    assert "| 类型 | 审查数 | 豁免数 |" in text and "§6.5" in text
    for review_type in REVIEW_TYPES:
        assert review_type in text
    assert render_weekly_markdown(result["report"]) == text

def test_celery_task_wrapper_wires_weekly_job():
    """后台周聚合任务存在，且指向与纯函数同一实现（Celery 缺席时如实降级）。"""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    source = (backend / "tasks" / "review_budget_tasks.py").read_text(encoding="utf-8")
    assert "review_budget.weekly_aggregate" in source
    assert "beat_schedule" in source
    assert "write_weekly_report" in source
    assert WEEKLY_INTERVAL_S == 7 * 24 * 3600
