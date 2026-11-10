#!/usr/bin/env python3
"""p250-n04 战役节点健康监控验收（超时 / 空转 / 资源泄漏 / 自动恢复）。

A1 3/3 超时节点识别并终止；A2 3/3 空转（N=10 心跳）；A3 2/2 资源泄漏预警；
A4 负控 0/5 误报；A5 终止后下游 DAG 边正确释放；外加纯函数层单元测试与幂等/哈希链检查。

场景全部合成命名（``synthetic-*`` / ``TO-*`` / ``ID-*`` / ``RL-*`` / ``H-*`` / ``DS-*``），
不含任何领域词条。判据数字来自场景内 ``expectations``（声明式 ground truth），
不在测试里硬编码节点结论。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ops.taskplane import campaign_health_monitor as chm  # noqa: E402

SCENARIO_PATH = REPO / "tests/fixtures/campaign_health_p250_n04_scenario.json"
T0 = datetime(2026, 11, 9, 0, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────── 夹具 ───────────────────────────────

@pytest.fixture(scope="module")
def scenario() -> dict:
    return chm.load_scenario(SCENARIO_PATH)


@pytest.fixture(scope="module")
def result(scenario: dict) -> dict:
    return chm.run_monitor(scenario)


@pytest.fixture(scope="module")
def verdicts(result: dict) -> dict:
    return {v["node_id"]: v for v in result["verdicts"]}


def _node(scenario: dict, node_id: str) -> dict:
    return next(n for n in scenario["nodes"] if n["node_id"] == node_id)


def _offset_min(ts_utc: str) -> float:
    return (chm.parse_ts(ts_utc) - T0).total_seconds() / 60.0


# ─────────────────────────────── A1 超时 ───────────────────────────────

def test_a1_timeout_all_correctly_identified_and_terminated(
    scenario: dict, result: dict, verdicts: dict
) -> None:
    expect = scenario["expectations"]["A1_timeout"]
    terminated = {t["node_id"] for t in result["salvage"]["terminations"]}
    hits = [
        nid for nid in expect["nodes"]
        if verdicts[nid]["state"] == expect["required_state"]
        and terminated and nid in terminated
        and {a["kind"] for a in verdicts[nid]["alerts"]} >= set(expect["required_kinds"])
    ]
    assert len(hits) == len(expect["nodes"]) == 3, (hits, verdicts)


def test_a1_timeout_thresholds_and_detection_offsets(
    scenario: dict, verdicts: dict
) -> None:
    for nid in scenario["expectations"]["A1_timeout"]["nodes"]:
        node = _node(scenario, nid)
        expect = node["expect"]["detect_offset_min"]
        planned = node["planned_minutes"]
        by_kind = {a["kind"]: a for a in verdicts[nid]["alerts"]}
        # 阈值 = planned 的 1.5x / 2.0x（判据数字可回算）
        assert by_kind["node_timeout"]["threshold"] == pytest.approx(1.5)
        assert by_kind["node_timeout_kill"]["threshold"] == pytest.approx(2.0)
        assert by_kind["node_timeout"]["detail"]["elapsed_minutes"] == pytest.approx(
            planned * 1.5, abs=1e-6)
        # 首次跨越采样时刻 = 预算倍率点（采样步长内取首拍）
        assert _offset_min(by_kind["node_timeout"]["ts_utc"]) == pytest.approx(
            expect["node_timeout"], abs=1e-9)
        assert _offset_min(by_kind["node_timeout_kill"]["ts_utc"]) == pytest.approx(
            expect["node_timeout_kill"], abs=1e-9)
        # 超时节点不应该被空转/资源锚误伤（单判据归因）
        assert {a["kind"] for a in verdicts[nid]["alerts"]} == set(
            node["expect"]["kinds"])


def test_a1_boundary_inclusive_warn_and_kill() -> None:
    """恰好 1.5x 触发预警、恰好 2.0x 触发终止（含边界，非严格大于）。"""
    th = chm.Thresholds()
    node = {
        "node_id": "SYN-BOUNDARY", "planned_minutes": 10,
        "started_at_utc": "2026-11-09T00:00:00Z",
        "samples": [
            {"ts": "2026-11-09T00:14:59Z", "workspace": {"max_mtime": "a"}},
            {"ts": "2026-11-09T00:15:00Z", "workspace": {"max_mtime": "b"}},
            {"ts": "2026-11-09T00:19:59Z", "workspace": {"max_mtime": "c"}},
            {"ts": "2026-11-09T00:20:00Z", "workspace": {"max_mtime": "d"}},
        ],
    }
    alerts = chm.detect_timeout(node, node["samples"], th)
    kinds = [a["kind"] for a in alerts]
    assert kinds == ["node_timeout", "node_timeout_kill"]
    # 首次跨越发生在恰好 15min / 20min 的那一拍（前 1 秒未触发）
    assert alerts[0]["ts_utc"] == "2026-11-09T00:15:00Z"
    assert alerts[1]["ts_utc"] == "2026-11-09T00:20:00Z"


# ─────────────────────────────── A2 空转 ───────────────────────────────

def test_a2_idle_all_correctly_identified(scenario: dict, result: dict,
                                          verdicts: dict) -> None:
    expect = scenario["expectations"]["A2_idle"]
    terminated = {t["node_id"] for t in result["salvage"]["terminations"]}
    hits = [
        nid for nid in expect["nodes"]
        if verdicts[nid]["state"] == expect["required_state"]
        and nid in terminated
        and {a["kind"] for a in verdicts[nid]["alerts"]} >= set(expect["required_kinds"])
    ]
    assert len(hits) == len(expect["nodes"]) == 3, (hits,)


def test_a2_idle_run_length_is_exactly_n(scenario: dict, verdicts: dict) -> None:
    n = scenario["expectations"]["A2_idle"]["idle_heartbeats"]
    for nid in scenario["expectations"]["A2_idle"]["nodes"]:
        node = _node(scenario, nid)
        alert = next(a for a in verdicts[nid]["alerts"] if a["kind"] == "node_idle")
        assert alert["threshold"] == float(n)
        assert alert["detail"]["idle_run"] == n  # 第 N 拍就报，不拖到 N+1
        # 检测时刻 = 场景声明（ID-3 证明 progress 递增期不计入空转）
        assert _offset_min(alert["ts_utc"]) == pytest.approx(
            node["expect"]["detect_offset_min"]["node_idle"], abs=1e-9)


def test_a2_progress_beacon_delays_and_prevents_idle() -> None:
    """工作区静止但 progress 递增 → 不判空转；progress 停涨后重新数 N 拍。"""
    th = chm.Thresholds(idle_heartbeats=10)
    samples = [
        {"ts": f"2026-11-09T00:{i:02d}:00Z",
         "workspace": {"max_mtime": "static", "new_files": 0, "commit": "c0"},
         "progress": i}
        for i in range(20)
    ]
    node = {"node_id": "SYN-COMPUTE", "planned_minutes": 600, "samples": samples}
    assert chm.detect_idle(node, chm._ordered_samples(node), th) == []
    trace = chm.idle_run_lengths(node, chm._ordered_samples(node))
    assert max(t["run"] for t in trace) == 1  # 每拍都有进度增量 → run 恒为 1

    # 停涨：进度冻结在第 10 拍 → 从该拍的下一拍起重新累计（第 10 拍 run=1）
    frozen = [
        {"ts": f"2026-11-09T00:{i:02d}:00Z",
         "workspace": {"max_mtime": "static", "new_files": 0, "commit": "c0"},
         "progress": min(i, 9)}
        for i in range(25)
    ]
    node2 = {"node_id": "SYN-FROZEN", "planned_minutes": 600, "samples": frozen}
    alerts = chm.detect_idle(node2, chm._ordered_samples(node2), th)
    assert len(alerts) == 1
    assert alerts[0]["detail"]["idle_run"] == 10
    assert alerts[0]["ts_utc"] == "2026-11-09T00:18:00Z"  # 第 9 拍起数 10 拍


def test_a2_idle_requires_workspace_static_not_just_one_field() -> None:
    """new_files 变化（即使 max_mtime 不变）也算有动静。"""
    th = chm.Thresholds(idle_heartbeats=3)
    samples = [
        {"ts": f"2026-11-09T00:0{i}:00Z",
         "workspace": {"max_mtime": "same", "new_files": i, "commit": "c0"}}
        for i in range(6)
    ]
    node = {"node_id": "SYN-NEWFILES", "planned_minutes": 600, "samples": samples}
    assert chm.detect_idle(node, chm._ordered_samples(node), th) == []


# ─────────────────────────────── A3 资源泄漏 ───────────────────────────────

def test_a3_resource_leak_all_warned(scenario: dict, verdicts: dict) -> None:
    expect = scenario["expectations"]["A3_leak"]
    hits = []
    for nid in expect["nodes"]:
        v = verdicts[nid]
        alerts = [a for a in v["alerts"] if a["kind"] == "resource_pressure"]
        if (v["state"] == expect["required_state"] and alerts
                and alerts[0]["metric"] == expect["metrics"][nid]):
            hits.append(nid)
            node = _node(scenario, nid)
            assert _offset_min(alerts[0]["ts_utc"]) == pytest.approx(
                node["expect"]["detect_offset_min"]["resource_pressure"], abs=1e-9)
            assert alerts[0]["value"] >= alerts[0]["threshold"] == 80.0
    assert len(hits) == len(expect["nodes"]) == 2, (hits,)


def test_a3_sustain_window_suppresses_short_spike() -> None:
    """载荷越线但 < M 分钟 → 不预警；持续 >= M 分钟 → 预警一次（不重复刷）。"""
    th = chm.Thresholds(resource_pct=80.0, resource_sustain_min=5.0)
    short = [
        {"ts": f"2026-11-09T00:{i:02d}:00Z", "mem_pct": 92.0 if i < 3 else 30.0}
        for i in range(8)
    ]
    node = {"node_id": "SYN-SPIKE", "planned_minutes": 600, "samples": short}
    assert chm.detect_resource_leak(node, chm._ordered_samples(node), th) == []

    long = [{"ts": f"2026-11-09T00:{i:02d}:00Z", "mem_pct": 92.0} for i in range(12)]
    node2 = {"node_id": "SYN-HOLD", "planned_minutes": 600, "samples": long}
    alerts = chm.detect_resource_leak(node2, chm._ordered_samples(node2), th)
    assert len(alerts) == 1 and _offset_min(alerts[0]["ts_utc"]) == pytest.approx(5.0)

    # 回落到阈值以下 → 重新武装（恢复后再涨可再报）
    rebound = (
        [{"ts": f"2026-11-09T00:{i:02d}:00Z", "mem_pct": 90.0} for i in range(7)]
        + [{"ts": f"2026-11-09T00:{i:02d}:00Z", "mem_pct": 40.0} for i in range(7, 9)]
        + [{"ts": f"2026-11-09T00:{i:02d}:00Z", "mem_pct": 90.0} for i in range(9, 15)]
    )
    node3 = {"node_id": "SYN-REARM", "planned_minutes": 600, "samples": rebound}
    alerts3 = chm.detect_resource_leak(node3, chm._ordered_samples(node3), th)
    assert len(alerts3) == 2


def test_a3_threshold_boundary_strictly_below_is_clean() -> None:
    th = chm.Thresholds(resource_pct=80.0, resource_sustain_min=5.0)
    below = [{"ts": f"2026-11-09T00:{i:02d}:00Z", "disk_pct": 79.9} for i in range(12)]
    node = {"node_id": "SYN-NEAR", "planned_minutes": 600, "samples": below}
    assert chm.detect_resource_leak(node, chm._ordered_samples(node), th) == []
    at = [{"ts": f"2026-11-09T00:{i:02d}:00Z", "disk_pct": 80.0} for i in range(12)]
    node2 = {"node_id": "SYN-AT", "planned_minutes": 600, "samples": at}
    assert len(chm.detect_resource_leak(node2, chm._ordered_samples(node2), th)) == 1


# ─────────────────────────────── A4 负控 ───────────────────────────────

def test_a4_negative_control_zero_false_positives(scenario: dict,
                                                  verdicts: dict) -> None:
    expect = scenario["expectations"]["A4_negative"]
    false_positives = {
        nid: [a["kind"] for a in verdicts[nid]["alerts"]]
        for nid in expect["nodes"]
        if verdicts[nid]["state"] != expect["required_state"]
        or len(verdicts[nid]["alerts"]) > expect["max_alerts"]
    }
    assert false_positives == {}
    assert len(expect["nodes"]) == 5
    # 误报率 = 0/5 = 0.0 (< 1%)
    rate = len(false_positives) / len(expect["nodes"])
    assert rate == 0.0 < 0.01


def test_a4_healthy_nodes_not_terminated(result: dict, verdicts: dict) -> None:
    terminated = {t["node_id"] for t in result["salvage"]["terminations"]}
    healthy = [nid for nid, v in verdicts.items() if v["state"] == "healthy"]
    assert len(healthy) == 5
    assert terminated.isdisjoint(healthy)


# ─────────────────────────────── A5 自动恢复 / DAG 释放 ───────────────────────────────

def test_a5_released_edges_match_expectation(scenario: dict, result: dict) -> None:
    expect = scenario["expectations"]["A5_salvage"]
    released = sorted((e["from"], e["to"]) for e in result["salvage"]["edge_transitions"]
                      if e["status_after"] == "released")
    assert released == sorted(tuple(pair) for pair in expect["released_edges"])
    assert len(released) == 5


def test_a5_downstream_dependency_release_states(scenario: dict, result: dict) -> None:
    expect = scenario["expectations"]["A5_salvage"]
    salvage = result["salvage"]
    assert sorted(salvage["downstream_runnable"]) == sorted(expect["runnable"])
    assert sorted(salvage["downstream_still_blocked"]) == sorted(expect["still_blocked"])
    states = salvage["dag_states_after"]
    for nid in expect["runnable"]:
        assert states[nid] == "runnable"
    # DS-A2 仍被健康节点 H-1 依赖挡住 → 不能因为 TO-1 死亡就放行
    assert states["DS-A2"] == "blocked"
    # 隔代 DS-A6 不动（上游 DS-A1 只是可运行、并未 done）
    assert states["DS-A6"] == "blocked"


def test_a5_untouched_edges_and_warn_only_no_salvage(scenario: dict,
                                                     result: dict) -> None:
    expect = scenario["expectations"]["A5_salvage"]
    untouched = sorted((e["from"], e["to"]) for e in result["salvage"]["untouched_edges"])
    assert untouched == sorted(tuple(pair) for pair in expect["untouched_edges"])
    assert all(e["status_after"] == "waiting" for e in result["salvage"]["untouched_edges"])
    # RL-1 只预警（resource_warn）→ 其下游边不动、节点不 failed
    assert result["salvage"]["dag_states_after"]["RL-1"] == "running"
    assert result["salvage"]["dag_states_after"]["DS-A5"] == "blocked"


def test_a5_salvage_records_carry_alert_and_reason(result: dict) -> None:
    alerts_by_id = {a["alert_id"]: a for a in result["alerts"]}
    for term in result["salvage"]["terminations"]:
        assert term["reason"] in ("timeout_budget_exceeded", "idle_no_progress")
        assert term["alert_id"] in alerts_by_id
        assert alerts_by_id[term["alert_id"]]["severity"] == "P1"
        assert "release_downstream_edges" in term["salvage_actions"]
        # 每个终止都必须落到 failed
        assert result["salvage"]["dag_states_after"][term["node_id"]] == "failed"


# ─────────────────────────────── 确定性 / 审计链 / 阈值配置 ───────────────────────────────

def test_monitor_is_deterministic_and_content_clocked(scenario: dict) -> None:
    first = chm.dump_json(chm.run_monitor(scenario))
    second = chm.dump_json(chm.run_monitor(scenario))
    assert first == second
    # 时钟来自场景时间戳，不读 wall clock
    assert "2026-11-09T" in first


def test_alert_log_hash_chain_and_tamper_detection(result: dict) -> None:
    lines = chm.alert_log_lines(result["alerts"])
    assert len(lines) == result["summary"]["alerts_total"] == 11
    assert chm.verify_alert_log(lines)
    tampered = list(lines)
    rec = json.loads(tampered[3])
    rec["alert"]["severity"] = "P2" if rec["alert"]["severity"] == "P1" else "P1"
    tampered[3] = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    assert not chm.verify_alert_log(tampered)


def test_thresholds_are_configurable_and_validated(scenario: dict) -> None:
    # 放宽 N=3 心跳 → 空转更早报（可配置性）
    loose = chm.run_monitor(scenario, chm.Thresholds(idle_heartbeats=3))
    id1 = next(v for v in loose["verdicts"] if v["node_id"] == "ID-1")
    assert id1["alerts"][0]["detail"]["idle_run"] == 3
    assert _offset_min(id1["alerts"][0]["ts_utc"]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        chm.Thresholds(timeout_kill_factor=1.4)
    with pytest.raises(ValueError):
        chm.Thresholds(idle_heartbeats=1)
    with pytest.raises(ValueError):
        chm.Thresholds(resource_pct=0.0)
    with pytest.raises(ValueError):
        chm.Thresholds.from_dict({"nope": 1})


def test_summary_denominators_and_counts(result: dict) -> None:
    s = result["summary"]
    assert s["nodes_monitored"] == 13
    assert s["alerts_total"] == s["alerts_p1"] + s["alerts_p2"] == 11
    assert s["terminations"] == 6
    assert s["released_edges"] == 5
    assert s["healthy_nodes_alert_free"] == s["healthy_nodes"] == 5


def test_alert_ids_and_ordering_are_stable(result: dict) -> None:
    ids = [a["alert_id"] for a in result["alerts"]]
    assert len(ids) == len(set(ids))
    keys = [(a["ts_utc"], a["node_id"], a["kind"]) for a in result["alerts"]]
    assert keys == sorted(keys)


# ─────────────────────────────── CLI 薄壳 ───────────────────────────────

def test_cli_writes_outputs_and_exit_code(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "ops/taskplane/campaign_health_monitor.py"),
         "--scenario", str(SCENARIO_PATH), "--out-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 2, proc.stderr  # 有 P1
    for name in ("monitor_result.json", "alerts.jsonl", "recovery.json"):
        assert (tmp_path / name).exists()
    lines = (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert chm.verify_alert_log(lines)
    again = subprocess.run(
        [sys.executable, str(REPO / "ops/taskplane/campaign_health_monitor.py"),
         "--scenario", str(SCENARIO_PATH), "--out-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=300,
    )
    assert again.returncode == 2
    assert (tmp_path / "monitor_result.json").read_text(encoding="utf-8") == chm.dump_json(
        json.loads((tmp_path / "monitor_result.json").read_text(encoding="utf-8")))


def test_cli_fail_closed_on_bad_input(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nodes": [{"node_id": "X", "dependencies": ["GHOST"]}]}),
                   encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO / "ops/taskplane/campaign_health_monitor.py"),
         "--scenario", str(bad), "--dry-run"],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 1
    assert "unknown node" in proc.stderr


def test_scenario_is_synthetic_and_domain_free() -> None:
    """零领域语义：节点 id/名称必须合成命名（防域词条混入共性机制）。"""
    scenario = chm.load_scenario(SCENARIO_PATH)
    for node in scenario["nodes"]:
        nid, name = node["node_id"], node["name"]
        assert nid.split("-")[0] in {"TO", "ID", "RL", "H", "DS"}, nid
        assert name.startswith("synthetic-"), name


# ─────────────────────────── 采集适配层（线上台账 → 场景） ───────────────────────────

def _rows_and_beats() -> tuple[list[dict], dict]:
    rows = [
        {"node_id": "SYN-A", "name": "synthetic-a", "status": "running",
         "planned_start": "2026-11-09T00:00:00Z", "planned_end": "2026-11-09T00:10:00Z"},
        {"node_id": "SYN-B", "name": "synthetic-b", "status": "blocked",
         "planned_start": "2026-11-09T00:00:00Z", "planned_end": "2026-11-09T00:30:00Z",
         "dependencies": ["SYN-A"]},
        {"node_id": "SYN-C", "name": "synthetic-c", "status": "pending",
         "planned_start": "2026-11-09T00:00:00Z", "planned_end": "2026-11-09T00:40:00Z"},
    ]
    beats = {
        "SYN-A": [
            {"ts": f"2026-11-09T00:{i:02d}:00Z",
             "workspace": {"max_mtime": f"m{i}", "new_files": i, "commit": f"c{i}"},
             "progress": i}
            for i in range(26)  # 0..25 min 一直在动 → 只会命中超时锚
        ]
    }
    return rows, beats


def test_adapter_builds_live_scenario_and_terminates_timeout_node() -> None:
    rows, beats = _rows_and_beats()
    scenario = chm.scenario_from_rows(rows, beats)
    result = chm.run_monitor(scenario)
    verdicts = {v["node_id"]: v for v in result["verdicts"]}
    assert set(verdicts) == {"SYN-A"}  # 无心跳节点不体检
    assert verdicts["SYN-A"]["state"] == "timeout_kill"
    assert verdicts["SYN-A"]["planned_minutes"] == pytest.approx(10.0)  # 由 planned 窗口推导
    # 终止后下游依赖释放
    assert result["salvage"]["edge_transitions"][0]["status_after"] == "released"
    assert result["salvage"]["dag_states_after"]["SYN-B"] == "runnable"
    assert result["salvage"]["dag_states_after"]["SYN-C"] == "pending"


def test_adapter_planned_minutes_precedence_and_monitored_flag() -> None:
    rows, beats = _rows_and_beats()
    rows[1]["planned_minutes"] = 7.5  # 显式值优先于 planned 窗口
    scenario = chm.scenario_from_rows(rows, beats, thresholds=chm.Thresholds(idle_heartbeats=5))
    by_id = {n["node_id"]: n for n in scenario["nodes"]}
    assert by_id["SYN-B"]["planned_minutes"] == 7.5
    assert by_id["SYN-A"]["planned_minutes"] == pytest.approx(10.0)
    assert by_id["SYN-C"]["monitored"] is False
    assert scenario["thresholds"]["idle_heartbeats"] == 5


def test_adapter_rejects_unknown_dependency() -> None:
    rows, beats = _rows_and_beats()
    rows[1]["dependencies"] = ["GHOST"]
    with pytest.raises(ValueError):
        chm.scenario_from_rows(rows, beats)
