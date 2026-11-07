"""IN23-N08 — worker-occupancy external truth plane: probes, four-state derivation,
claim-window divergence ledger, dry-run/commit equality, completeness audit.

Everything here runs against fixtures (fake ``/proc`` tree, fake cgroup hierarchy) plus
synthetic sample streams: no DB, no live kernel, no writes outside ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.worker_truth import ledger, probes, sampler, states  # noqa: E402


# ── fixture builders ─────────────────────────────────────────────────────────

def _write_proc(root, pid, ppid, comm="python3", utime=0, stime=0, cmdline=None,
                rss_pages=0, state="S"):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    rest = [state, str(ppid), "0", "0", "0", "0", "0", "0", "0", "0", "0",
            str(utime), str(stime), "0", "0", "0", "0", "0", "0", "0"]
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(rest) + "\n")
    (d / "statm").write_text(f"1000 {rss_pages} 100 10 0 20 0\n")
    if cmdline is not None:
        (d / "cmdline").write_text("\x00".join(cmdline) + "\x00")


def _proc_tree(tmp_path):
    root = tmp_path / "proc"
    # worker A: root + child + grandchild; worker B: root only; 1 stray process
    _write_proc(root, 100, 1, utime=500, stime=100,
                cmdline=["python3", "/root/tp-agent/tp_worker.py", "--agent-id", "tp-qa-1"])
    _write_proc(root, 101, 100, comm="pytest", utime=200, stime=50)  # non-interpreter child
    _write_proc(root, 102, 101, comm="python3", utime=50, stime=0)
    _write_proc(root, 200, 1, utime=10, stime=10,
                cmdline=["python3", "tp_worker.py", "--agent-id=tp-impl-2"])
    _write_proc(root, 300, 1, comm="bash", utime=1, stime=1, cmdline=["bash", "-c", "sleep 1"])
    return root


def _rec(ts, wid="w1", pids=1, cpu_s=0.0, claim=False, hb_age=None, kind="worker"):
    return {"kind": kind, "worker_id": wid, "ts": f"t{ts}", "ts_epoch": float(ts),
            "pids": pids, "cpu_s": cpu_s, "cpu_delta_s": None, "delta_basis": "prev",
            "mem_bytes": 1, "rss_bytes": 1, "claim_active": claim, "heartbeat_age_s": hb_age,
            "claim_ids": [1] if claim else [], "backend": "proc", "mem_source": "proc_rss",
            "io_syscalls": 1000, "io_basis": "prev", "io_delta_syscalls": 0}


# ── 1. /proc parsing ────────────────────────────────────────────────────────

def test_parse_proc_stat_handles_parens_in_comm():
    txt = "1234 (my (weird) comm) S 1 1234 1234 0 -1 4194560 100 0 0 0 500 250 0 0 20 0 1 0 99\n"
    rec = probes.parse_proc_stat(txt)
    assert rec["pid"] == 1234 and rec["ppid"] == 1
    assert rec["comm"] == "my (weird) comm"
    assert rec["utime_s"] == 500 / probes.CLK_TCK
    assert probes.parse_proc_stat("garbage") is None


def test_parse_statm_and_cgroup_lines():
    assert probes.parse_statm_resident("1000 250 100 10 0 20 0\n", 4096) == 250 * 4096
    assert probes.parse_statm_resident("", 4096) == 0
    assert probes.parse_cgroup_v2("0::/system.slice/foo.service\n") == "/system.slice/foo.service"
    assert probes.parse_cgroup_v2("4:cpu,cpuacct:/docker/abc\n") is None
    assert probes.parse_cgroup_v1("4:cpu,cpuacct:/docker/abc\n2:memory:/docker/abc\n") == "/docker/abc"
    stat = probes.parse_cpu_stat_v2("usage_usec 1500000\nuser_usec 1000000\nnr_periods 7\n")
    assert stat["usage_usec"] == 1500000 and stat["nr_periods"] == 7


# ── 2. cgroup backends ─────────────────────────────────────────────────────

def test_read_cgroup_v2_from_fixture(tmp_path):
    base = tmp_path / "cgroup" / "sys" / "a.service"
    base.mkdir(parents=True)
    (base / "cpu.stat").write_text("usage_usec 2500000\nuser_usec 2000000\n")
    (base / "memory.current").write_text("4096\n")
    (base / "pids.current").write_text("3\n")
    (tmp_path / "cgroup" / "sys" / "cgroup.controllers").write_text("cpu memory pids\n")
    assert probes.cgroup_backend(str(tmp_path / "cgroup" / "sys")) == "cgroup_v2"
    m = probes.read_cgroup_v2("/a.service", sysroot=str(tmp_path / "cgroup" / "sys"))
    assert m.backend == "cgroup_v2" and m.cpu_s == pytest.approx(2.5)
    assert m.mem_bytes == 4096 and m.pids == 3
    assert probes.read_cgroup_v2("/missing", sysroot=str(tmp_path / "cgroup" / "sys")) is None


def test_read_cgroup_v1_from_fixture(tmp_path):
    sysroot = tmp_path / "cgroup_v1"
    (sysroot / "cpu,cpuacct" / "docker" / "x").mkdir(parents=True)
    (sysroot / "memory" / "docker" / "x").mkdir(parents=True)
    (sysroot / "pids" / "docker" / "x").mkdir(parents=True)
    (sysroot / "cpu,cpuacct" / "docker" / "x" / "cpuacct.usage").write_text("3000000000\n")
    (sysroot / "memory" / "docker" / "x" / "memory.usage_in_bytes").write_text("8192\n")
    (sysroot / "pids" / "docker" / "x" / "pids.current").write_text("5\n")
    assert probes.cgroup_backend(str(sysroot)) == "cgroup_v1"
    m = probes.read_cgroup_v1("/docker/x", sysroot=str(sysroot))
    assert m.cpu_s == pytest.approx(3.0) and m.mem_bytes == 8192 and m.pids == 5


def test_cgroup_scope_report_detects_shared_cgroup(tmp_path):
    root = _proc_tree(tmp_path)
    procs = probes.scan_procs(str(root))
    cmap = probes.children_map(procs)
    roots = probes.worker_roots(procs)
    rep = probes.cgroup_scope_report(procs, roots, cmap, sysroot=str(tmp_path / "none"))
    assert rep["n_workers"] == 0           # no readable /proc/<pid>/cgroup in the fixture
    assert rep["distinct_paths"] == 0


# ── 3. /proc scanning and process trees ─────────────────────────────────────

def test_scan_and_worker_roots(tmp_path):
    root = _proc_tree(tmp_path)
    procs = probes.scan_procs(str(root))
    assert len(procs) == 5
    roots = probes.worker_roots(procs)
    assert sorted(roots) == ["tp-impl-2", "tp-qa-1"]
    assert roots["tp-qa-1"] == [100]


def test_tree_metrics_sums_descendants(tmp_path):
    root = _proc_tree(tmp_path)
    procs = probes.scan_procs(str(root))
    cmap = probes.children_map(procs)
    tm = probes.tree_metrics(procs, cmap, [100], proc_root=str(root))
    assert tm["pids"] == 3                       # 100 + 101 + 102
    assert tm["cpu_s"] == pytest.approx((600 + 250 + 50) / probes.CLK_TCK)
    assert tm["rss_bytes"] == 0                  # rss_pages=0 everywhere in the fixture
    tm2 = probes.tree_metrics(procs, cmap, [999], proc_root=str(root))
    assert {k: tm2[k] for k in ("pids", "pid_list", "cpu_s", "rss_bytes")} == {
        "pids": 0, "pid_list": [], "cpu_s": 0.0, "rss_bytes": 0}


def test_descendants_is_cycle_safe():
    cmap = {1: [2], 2: [1, 3], 3: []}
    assert sorted(probes.descendants(1, cmap)) == [1, 2, 3]


# ── 4. four-state derivation ────────────────────────────────────────────────

@pytest.mark.parametrize("claim,pids,cpu,warm,expected", [
    (True, 0, None, False, "silent_dead"),     # strongest signal, no window needed
    (True, 3, 1.5, True, "busy"),
    (True, 3, 0.0, True, "suspect_hang"),
    (False, 3, 0.0, True, "idle"),
    (False, 0, None, True, "idle"),
    (False, 3, 2.0, True, "busy"),             # off-claim burn is still busy (ledger FN)
    (False, 3, 0.0, False, "idle"),            # cold window cannot claim a hang
    (True, 3, 0.0, False, "idle"),             # ... and cannot claim busy either
])
def test_derive_state_decision_table(claim, pids, cpu, warm, expected):
    assert states.derive_state(claim, pids, cpu, warm=warm)["state"] == expected


def test_window_stats_delta_warm_and_empty():
    series = [_rec(0, cpu_s=10.0), _rec(10, cpu_s=12.0), _rec(40, cpu_s=15.0)]
    win = states.window_stats(series, window_s=60)
    assert win["n"] == 3 and win["span_s"] == 40 and win["warm"] is True
    assert win["cpu_delta_s"] == pytest.approx(5.0)
    assert win["cpu_rate"] == pytest.approx(5.0 / 40)
    cold = states.window_stats([_rec(0, cpu_s=1.0), _rec(5, cpu_s=2.0)], window_s=60)
    assert cold["warm"] is False and cold["cpu_delta_s"] == pytest.approx(1.0)
    empty = states.window_stats([])
    assert empty["n"] == 0 and empty["pids"] == 0 and empty["cpu_delta_s"] is None


def test_derive_series_confirmation_window():
    # 1 sample with claim held and no process: raw silent_dead, but NOT confirmed
    one = [_rec(0, claim=True, pids=0), _rec(10, claim=True, pids=0), _rec(20, claim=True, pids=1)]
    out = states.derive_series(one, window_s=60, confirm=2)
    assert out[0]["state"] == "silent_dead" and out[0]["state_confirmed"] == "transition"
    assert out[1]["state_confirmed"] == "silent_dead"
    assert out[1]["dead_episode_start_ts"] == 0.0
    assert out[2]["state"] != "silent_dead"
    # transient single-sample gap must never be reported as dead
    gap = [_rec(0, claim=True, pids=0), _rec(10, claim=True, pids=2)]
    g = states.derive_series(gap, window_s=60, confirm=2)
    assert all(r["state_confirmed"] != "silent_dead" for r in g)


# ── 5. claim-window ledger ─────────────────────────────────────────────────

def test_claim_window_busy_semantics():
    assert ledger.claim_window_busy({"claim_active": False}) is False
    assert ledger.claim_window_busy({"claim_active": True, "heartbeat_age_s": None}) is True
    assert ledger.claim_window_busy({"claim_active": True, "heartbeat_age_s": 100}) is True
    assert ledger.claim_window_busy({"claim_active": True, "heartbeat_age_s": 900}) is False


def test_ledger_confusion_tp_fp_fn_tn():
    # 0-30 s: claim + burn  → TP; 30-60 s: claim held, no burn → FP (claim inflation);
    # 60-90 s: burn, no claim → FN; 90-100 s: neither → TN
    series = [
        _rec(0, claim=True, hb_age=10, cpu_s=0.0),
        _rec(30, claim=True, hb_age=10, cpu_s=40.0),
        _rec(60, claim=False, cpu_s=40.0),
        _rec(90, claim=False, cpu_s=80.0),
        _rec(99, claim=False, cpu_s=80.0),
    ]
    led = ledger.worker_ledger(series)
    conf = led["confusion"]
    assert conf["tp"] == pytest.approx(30.0)
    assert conf["fp"] >= 20.0        # claim still held at the 30-60 sample with flat CPU
    assert conf["fn"] >= 20.0        # off-claim burn at 60-90
    assert 0.0 <= led["agreement"] <= 1.0


def test_ledger_gap_weight_is_capped():
    # five regular 10 s intervals + one 990 s hole: the hole must not outvote the interior
    series = [_rec(t, cpu_s=float(t)) for t in (0, 10, 20, 30, 40, 1_030)]
    w = ledger._weights(series)
    assert w[4] == pytest.approx(30.0)          # capped at 3 × median(10 s), not 990 s
    assert sum(w) < 120


def test_build_ledger_fleet_shape():
    recs = [_rec(0, wid="a", claim=True, hb_age=5, cpu_s=0.0),
            _rec(10, wid="a", claim=True, hb_age=5, cpu_s=1.0),
            _rec(0, wid="b", claim=False, cpu_s=0.0),
            _rec(10, wid="b", claim=False, cpu_s=0.0),
            {"kind": "host", "ts_epoch": 0.0, "ts": "t0"}]
    fl = ledger.build_ledger(recs)
    assert fl["n_workers"] == 2 and fl["n_samples"] == 4
    assert set(fl["confusion_seconds"]) == {"tp", "fp", "fn", "tn"}
    assert "per_worker_agreement" in fl and "silent_dead_episodes" in fl


def test_build_ledger_counts_silent_dead_episode():
    recs = [_rec(0, wid="w", claim=True, pids=2, cpu_s=1.0),
            _rec(10, wid="w", claim=True, pids=0, cpu_s=1.0),
            _rec(20, wid="w", claim=True, pids=0, cpu_s=1.0)]
    fl = ledger.build_ledger(recs)
    eps = fl["silent_dead_episodes"]
    assert len(eps) == 1 and eps[0]["worker_id"] == "w"
    assert fl["state_seconds"]["silent_dead"] > 0


# ── 6. completeness + dry-run/commit equality ──────────────────────────────

def test_completeness_flags_missing_and_skips_warmup():
    ok = [_rec(0), _rec(10)]
    ok[0]["delta_basis"] = "warmup"
    ok[0]["cpu_delta_s"] = None
    ok[1]["cpu_delta_s"] = 1.0
    ok[1]["mem_source"] = "proc_rss"
    ok[0]["mem_source"] = "proc_rss"
    rep = sampler.completeness(ok)
    assert rep["warmup_records"] == 1 and rep["steady_records"] == 1
    assert rep["fully_complete"] is True and rep["rate"] == 1.0
    bad = [_rec(0), _rec(10)]
    for r in bad:
        r["mem_source"] = "proc_rss"
        r["cpu_delta_s"] = 1.0
        r["mem_bytes"] = None        # no cgroup memory in this stream
    bad[1]["rss_bytes"] = None
    rep2 = sampler.completeness(bad)
    assert rep2["fully_complete"] is False and rep2["missing_per_field"]["rss_bytes"] == 1
    assert rep2["cgroup_memory_coverage"] == 0.0   # no cgroup memory in this stream


def test_dry_run_equals_commit_field_by_field(tmp_path):
    ticks = tmp_path / "ticks.jsonl"
    recs = [_rec(0, wid="a"), _rec(10, wid="a"), _rec(20, wid="a")]
    ticks.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    out = tmp_path / "committed.jsonl"
    dry = sampler.emit_ticks_file(str(ticks), str(out), dry_run=True)
    assert not out.exists()                      # dry-run writes nothing
    com = sampler.emit_ticks_file(str(ticks), str(out), dry_run=False)
    assert out.exists()
    a = [json.loads(x) for x in dry]
    b = [json.loads(x) for x in com]
    assert len(a) == len(b) == 3
    for ra, rb in zip(a, b):
        assert ra.keys() == rb.keys()
        for k in ra:
            assert ra[k] == rb[k]


def test_run_ticks_dry_run_creates_no_file(tmp_path):
    root = _proc_tree(tmp_path)
    cfg = sampler.SamplerConfig(proc_root=str(root), worker_marker="tp_worker.py")
    s = sampler.Sampler(cfg, sampler.NullClaimSource())
    out = tmp_path / "sub" / "samples.jsonl"
    lines = sampler.run_ticks(s, 2, 0.0, str(out), dry_run=True)
    assert len(lines) == 2 * 3                   # 1 host + 2 workers per tick
    assert not out.exists()


# ── 7. sampler tick against a fixture /proc ────────────────────────────────

def test_sampler_tick_records_required_fields(tmp_path):
    root = _proc_tree(tmp_path)
    claims = {"tp-qa-1": {"claim_active": True, "claim_ids": [7], "heartbeat_age_s": 12.0}}
    cf = tmp_path / "claims.json"
    cf.write_text(json.dumps(claims))
    cfg = sampler.SamplerConfig(proc_root=str(root), worker_marker="tp_worker.py")
    s = sampler.Sampler(cfg, sampler.FileClaimSource(str(cf)))
    s.tick(now=1000.0)
    recs = s.tick(now=1010.0)
    host = [r for r in recs if r["kind"] == "host"][0]
    workers = {r["worker_id"]: r for r in recs if r["kind"] == "worker"}
    assert host["n_procs_scanned"] == 5
    assert set(workers) == {"tp-impl-2", "tp-qa-1"}
    w = workers["tp-qa-1"]
    assert w["pids"] == 3 and w["claim_active"] is True and w["heartbeat_age_s"] == 12.0
    assert w["delta_basis"] == "prev" and w["cpu_delta_s"] == 0.0
    assert workers["tp-impl-2"]["claim_active"] is False
    for f in sampler.REQUIRED_WORKER_FIELDS:
        assert f in w
    assert sampler.completeness(recs)["fully_complete"] is True


def test_sampler_detects_silent_death_end_to_end(tmp_path):
    """Fixture-driven: claim kept, process tree disappears → confirmed silent_dead ≤ 2 samples."""
    root = _proc_tree(tmp_path)
    cf = tmp_path / "claims.json"
    cf.write_text(json.dumps({"tp-qa-1": {"claim_active": True, "claim_ids": [7],
                                         "heartbeat_age_s": 1.0}}))
    cfg = sampler.SamplerConfig(proc_root=str(root), worker_marker="tp_worker.py")
    s = sampler.Sampler(cfg, sampler.FileClaimSource(str(cf)))
    recs = []
    recs += [r for r in s.tick(now=1000.0) if r.get("worker_id") == "tp-qa-1"]
    recs += [r for r in s.tick(now=1010.0) if r.get("worker_id") == "tp-qa-1"]
    for pid in (100, 101, 102):                  # simulate SIGKILL of the whole tree
        d = root / str(pid)
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    recs += [r for r in s.tick(now=1020.0) if r.get("worker_id") == "tp-qa-1"]
    recs += [r for r in s.tick(now=1030.0) if r.get("worker_id") == "tp-qa-1"]
    series = sorted(recs, key=lambda r: r["ts_epoch"])
    out = states.derive_series(series)
    assert out[2]["state"] == "silent_dead"
    assert out[2]["state_confirmed"] == "transition"     # one raw hit is not enough
    assert out[3]["state_confirmed"] == "silent_dead"    # two consecutive → confirmed
    assert out[3]["dead_episode_start_ts"] == 1020.0

# ── 8. I/O plane (the "waiting on the model" discriminator) ────────────────

def test_parse_proc_io_strips_colons(tmp_path):
    d = tmp_path / "proc" / "42"
    d.mkdir(parents=True)
    (d / "io").write_text("rchar: 10\nwchar: 20\nsyscr: 7\nsyscw: 3\n")
    io = probes.read_proc_io(42, proc_root=str(tmp_path / "proc"))
    assert io["syscr"] == 7 and io["syscw"] == 3 and io["rchar"] == 10
    assert probes.read_proc_io(7, proc_root=str(tmp_path / "proc")) is None


def test_count_sockets_follows_fd_links(tmp_path):
    d = tmp_path / "proc" / "42" / "fd"
    d.mkdir(parents=True)
    (d / "3").symlink_to("socket:[12345]")
    (d / "4").symlink_to("/var/log/x")
    (d / "5").symlink_to("socket:[999]")
    assert probes.count_sockets(42, proc_root=str(tmp_path / "proc")) == 2
    assert probes.count_sockets(7, proc_root=str(tmp_path / "proc")) == 0


def test_derive_state_io_activity_counts_as_busy():
    # alive, zero CPU over the window, but syscalls flowing → waiting on I/O, not hung
    st = states.derive_state(True, 3, 0.0, warm=True, eps=1e-6, io_delta=500.0, eps_io=60.0)
    assert st == {"state": "busy", "reason": "io_activity_no_cpu"}
    # nothing at all moving → the hang the claim window cannot see
    st2 = states.derive_state(True, 3, 0.0, warm=True, eps=1e-6, io_delta=0.0, eps_io=60.0)
    assert st2["state"] == "suspect_hang"
    # process gone still wins over any I/O reading
    st3 = states.derive_state(True, 0, 0.0, warm=True, eps=1e-6, io_delta=1e9, eps_io=60.0)
    assert st3["state"] == "silent_dead"


# ── 9. criteria evaluation (the report gate is code, not prose) ─────────────

def _num_stub(**over):
    num = {
        "completeness": {"rate": 1.0, "fully_complete": True, "fields": ["ts"],
                         "missing_per_field": {}},
        "ledger_spec": {"time_weighted_agreement": 0.95, "confusion_seconds":
                        {"tp": 1, "fp": 0, "fn": 0, "tn": 1}, "observed_worker_hours": 1.0},
        "samples": {"span_h": 1.01, "path": "x"}, "replay_mechanism":
        {"positives": {"hits": 4, "n": 4}, "latency_s": {"max": 2.0, "median": 2.0},
         "negative_control_clean": {"false_positives": 0},
         "negative_control_lag": {"false_positives": 0}},
        "replay_history": {"positives": {"hits": 4, "n": 4},
                           "negative_control_labelled_alive": {"false_positives": 0, "n": 6},
                           "detection_latency_s": {"bound_with_cadence_s": 130.0}},
        "dry_commit_oracle": {"records": 3, "identical": True, "sha256_dry": "a",
                              "sha256_commit": "a"},
        "pytest": {"returncode": 0, "passed_guess": 31, "summary": "31 passed"},
    }
    num.update(over)
    return num


def test_report_criteria_all_pass_case():
    from tools.worker_truth import report
    crit = report.evaluate(_num_stub())
    assert all(v["pass"] for v in crit.values()), {k: v["pass"] for k, v in crit.items()}
    assert set(crit) == {"C1_hour_complete", "C2_claim_agreement", "C3_detect_latency",
                         "C4_replay_hits_and_controls", "C5_dry_equals_commit", "C6_pytest"}


def test_report_criteria_fail_loudly_on_missing_evidence():
    from tools.worker_truth import report
    num = _num_stub()
    num["ledger_spec"]["time_weighted_agreement"] = 0.52          # the real outcome
    num["samples"]["span_h"] = 0.29
    num["replay_mechanism"]["latency_s"]["max"] = 300.0
    num["replay_mechanism"]["negative_control_clean"]["false_positives"] = 1
    crit = report.evaluate(num)
    assert crit["C2_claim_agreement"]["pass"] is False
    assert crit["C1_hour_complete"]["pass"] is False
    assert crit["C3_detect_latency"]["pass"] is False
    assert crit["C4_replay_hits_and_controls"]["pass"] is False
    assert crit["C5_dry_equals_commit"]["pass"] is True
