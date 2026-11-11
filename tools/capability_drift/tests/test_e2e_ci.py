"""端到端：注入退化 → 检测 → 定位 → 恢复；文档变更零告警；CI 退出码。"""
from __future__ import annotations

import json

from tools.capability_drift import alerts, ci, dashboard, model, pipeline, sandbox


def test_inject_detect_attribute_and_recover(calibrated, key_env):
    sb, values = calibrated
    cap = sb.capabilities[0]
    baseline = values[cap.id]["energy_00"]
    commit = sandbox.inject_degradation(sb.repo, cap, 0.65, "e2e injection")

    bundle = model.load_bundle(sb.registry_dir)
    run = pipeline.monitor(bundle, changed_files=["synth/params.py"], scope=[cap.id],
                           workers=1, approval_key=key_env, repo_root=sb.repo)
    assert run.report.red, "injected degradation must be detected"
    comp = run.report.verdicts[0].comparisons[0]
    assert comp.observed > baseline
    assert comp.rel_err > comp.threshold

    suspects = pipeline.attribute_run(run, bundle)
    assert suspects[cap.id][0]["commit"] == commit

    event = alerts.build_event(run.report, suspects)
    assert event["severity"] == "red" and event["drifted"]

    sandbox.write_param(sb.repo, cap, 0.60)
    sandbox.git_commit(sb.repo, "restore baseline")
    bundle2 = model.load_bundle(sb.registry_dir)
    clean = pipeline.monitor(bundle2, changed_files=["synth/params.py"], scope=[cap.id],
                             workers=1, approval_key=key_env, repo_root=sb.repo)
    assert not clean.report.red
    assert clean.report.health()["status"] == "green"


def test_documentation_change_triggers_nothing(calibrated, key_env):
    sb, _ = calibrated
    sandbox.benign_edit(sb.repo, "docs", 1)
    sandbox.git_commit(sb.repo, "docs only")
    bundle = model.load_bundle(sb.registry_dir)
    run = pipeline.monitor(bundle, changed_files=["docs/NOTES.md"], workers=2,
                           approval_key=key_env, repo_root=sb.repo)
    assert run.skipped is True
    assert run.runs == {}
    assert run.report.health()["status"] == "green"


def test_ci_exit_zero_for_docs_and_one_for_drift(calibrated, key_env, tmp_path):
    sb, _ = calibrated
    changed = tmp_path / "changed.txt"
    changed.write_text("docs/NOTES.md\n", encoding="utf-8")
    assert ci.main(["--registry-dir", str(sb.registry_dir), "--repo", str(sb.repo),
                    "--changed-files-file", str(changed)]) == 0

    cap = sb.capabilities[1]
    sandbox.inject_degradation(sb.repo, cap, 0.68, "ci drift injection")
    changed.write_text("synth/params.py\n", encoding="utf-8")
    assert ci.main(["--registry-dir", str(sb.registry_dir), "--repo", str(sb.repo),
                    "--changed-files-file", str(changed)]) == 1


def test_ci_writes_history_events_and_json(calibrated, key_env, tmp_path):
    sb, _ = calibrated
    cap = sb.capabilities[0]
    sandbox.inject_degradation(sb.repo, cap, 0.66, "ci drift for artifacts")
    history = tmp_path / "history.jsonl"
    events = tmp_path / "events.jsonl"
    out = tmp_path / "run.json"
    rc = ci.main(["--registry-dir", str(sb.registry_dir), "--repo", str(sb.repo),
                  "--changed-files", "synth/params.py",
                  "--history", str(history), "--events", str(events),
                  "--json", str(out)])
    assert rc == 1
    assert dashboard.history_load(history)
    assert json.loads(events.read_text().splitlines()[0])["severity"] == "red"
    payload = json.loads(out.read_text())
    assert payload["run"]["health"]["red"] >= 1