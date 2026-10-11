import json

from api.real_training import _merge_live_run_summary


def test_active_summary_prefers_shared_run_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    (tmp_path / "train_live.json").write_text(
        json.dumps({
            "status": "running",
            "progress": 47.6,
            "current_epoch": 21,
            "total_epochs": 100,
            "config": {"name": "动态训练", "registered_model_id": 88, "dataset_id": 180},
            "started_at": "2026-08-11T01:02:03",
        }),
        encoding="utf-8",
    )

    result = _merge_live_run_summary({
        "job_id": "train_live",
        "name": "旧名称",
        "status": "pending",
        "progress": 0,
        "current_epoch": 0,
        "total_epochs": 1,
    })

    assert result["name"] == "动态训练"
    assert result["status"] == "running"
    assert result["progress"] == 47.6
    assert result["current_epoch"] == 21
    assert result["total_epochs"] == 100
    assert result["registered_model_id"] == 88
    assert result["dataset_id"] == 180


def test_terminal_summary_is_not_replaced_by_an_old_run_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    (tmp_path / "train_done.json").write_text(
        json.dumps({"status": "running", "progress": 40}),
        encoding="utf-8",
    )

    result = _merge_live_run_summary({
        "job_id": "train_done",
        "status": "completed",
        "progress": 100,
    })

    assert result["status"] == "completed"
    assert result["progress"] == 100
