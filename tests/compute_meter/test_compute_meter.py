"""计算调度与计量（tools/compute_meter）单测。

覆盖面：
  policy     —— 策略校验（返修>主线>自驱）、重计算外移、本机帽、负载帽、无容量排队、
                权重选点、强制本机的 blocking 违例、派发序（优先级+跳过队头）
  meter      —— cpu_seconds 记账、幂等覆盖、治理字段不被回填抹掉、每单/按节点聚合、估计
  sampler    —— /proc 采样（假 /proc 注入）、system cpu 差分
  dashboard  —— 等待分位、队列深度、违例认定、门 judrer（pass/fail/insufficient）
  panorama   —— 标记块幂等注入、半截块拒绝、校验
  workload   —— 分片可校验结果
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tools.compute_meter import dashboard, meter, panorama, policy as pol, sampler
from tools.compute_meter.workload import burn

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / "config" / "compute_policy.v1.json"


# ── fixtures ─────────────────────────────────────────────────────────
@pytest.fixture()
def policy() -> pol.Policy:
    return pol.load_policy(POLICY_PATH)


def _write_policy(tmp_path: pathlib.Path, mutate) -> pathlib.Path:
    raw = json.loads(POLICY_PATH.read_text())
    mutate(raw)
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(raw))
    return p


def _rec(store_path, **kw):
    base = dict(source_ref="local-x", placement="local", cpu_seconds=10.0,
                mission_id=1, repo="R", priority_class="mainline", priority_rank=2,
                policy_version="v1")
    base.update(kw)
    return meter.JsonlStore(store_path).add(meter.make_record(**base))


# ── policy：配置校验 ─────────────────────────────────────────────────
def test_policy_loads_and_priority_order(policy):
    assert list(policy.priority_classes) == list(pol.PRIORITY_ORDER)
    assert (policy.cls("rework").rank > policy.cls("mainline").rank
            > policy.cls("self_driven").rank)
    assert policy.local_share_target == 0.2
    assert [n.id for n in policy.nodes] == ["n315", "n330"]
    assert policy.cls("nonsense").name == "self_driven"      # 未知优先级降级到最低档


def test_policy_rejects_wrong_priority_order(tmp_path):
    def mut(raw):
        raw["priority_classes"]["rework"]["rank"] = 1     # 返修不再高于自驱
    with pytest.raises(pol.PolicyError, match="优先级序"):
        pol.load_policy(_write_policy(tmp_path, mut))


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r["local"].pop("local_share_target"), "local_share_target"),
    (lambda r: r["cluster"].__setitem__("on_unavailable", "silently-local"), "on_unavailable"),
    (lambda r: r["cluster"]["nodes"].append(dict(r["cluster"]["nodes"][0])), "重复"),
    (lambda r: r["cluster"]["nodes"][0].__setitem__("weight", 0), "weight"),
])
def test_policy_rejects_bad_config(tmp_path, mutate, needle):
    with pytest.raises(pol.PolicyError, match=needle):
        pol.load_policy(_write_policy(tmp_path, mutate))


def test_policy_missing_file():
    with pytest.raises(pol.PolicyError, match="不存在"):
        pol.load_policy("/nonexistent/compute_policy.json")


# ── policy：放置决策 ─────────────────────────────────────────────────
def _nodes(n315=(0.10, 0, 0), n330=(0.20, 0, 0), ok=True):
    return [
        {"id": "n315", "ok": ok, "loadr": n315[0], "running_jobs": n315[1],
         "used_cores": n315[2]},
        {"id": "n330", "ok": ok, "loadr": n330[0], "running_jobs": n330[1],
         "used_cores": n330[2]},
    ]


def test_heavy_job_goes_cluster(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 600, "priority": "mainline"},
                   cluster_nodes=_nodes())
    assert d.placement == pol.PLACEMENT_CLUSTER
    assert d.node == "n315"            # 负载率/权重更低者胜
    assert "heavy_threshold" in d.reason


def test_light_job_stays_local(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 5, "priority": "mainline"},
                   cluster_nodes=_nodes())
    assert d.placement == pol.PLACEMENT_LOCAL and d.node is None


def test_priority_changes_heavy_threshold(policy):
    """同一估算：返修单（阈值 120s）留本机，自驱单（阈值 60s）出机。"""
    req = {"repo": "R", "est_cpu_seconds": 90, "priority": "rework"}
    assert pol.decide(policy, req, cluster_nodes=_nodes()).placement == pol.PLACEMENT_LOCAL
    req["priority"] = "self_driven"
    assert pol.decide(policy, req, cluster_nodes=_nodes()).placement == pol.PLACEMENT_CLUSTER


def test_local_hour_budget_forces_cluster(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 40, "priority": "self_driven"},
                   local_usage_cpu_seconds=280, cluster_nodes=_nodes())
    assert d.placement == pol.PLACEMENT_CLUSTER
    assert "local_hour_budget" in d.reason


def test_control_node_load_cap_forces_cluster(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 5, "priority": "rework"},
                   local_load_ratio=0.9, cluster_nodes=_nodes())
    assert d.placement == pol.PLACEMENT_CLUSTER
    assert "control_load" in d.reason


def test_no_capacity_queues_instead_of_local(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 600, "priority": "mainline"},
                   cluster_nodes=_nodes(ok=False))
    assert d.placement == pol.PLACEMENT_QUEUE
    assert d.node is None
    assert "cluster_unavailable" in d.reason


def test_on_unavailable_local_is_a_violation(tmp_path):
    def mut(raw):
        raw["cluster"]["on_unavailable"] = "local"
    p = pol.load_policy(_write_policy(tmp_path, mut))
    d = pol.decide(p, {"repo": "R", "est_cpu_seconds": 600, "priority": "mainline"},
                   cluster_nodes=_nodes(ok=False))
    assert d.placement == pol.PLACEMENT_LOCAL
    assert [v["code"] for v in d.violations] == [pol.V_CLUSTER_DOWN_LOCAL]


def test_forced_local_heavy_is_blocking_violation(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 600, "priority": "mainline"},
                   cluster_nodes=_nodes(), forced_placement="local")
    assert d.placement == pol.PLACEMENT_LOCAL
    codes = {(v["code"], v["severity"]) for v in d.violations}
    assert (pol.V_HEAVY_ON_CONTROL, "blocking") in codes


def test_node_pick_respects_weight():
    """n330 权重 1.2：同样 0.5 负载率时 n330 分更低（更能吃活）。"""
    d = pol.decide(pol.load_policy(POLICY_PATH),
                   {"repo": "R", "est_cpu_seconds": 600, "cores": 8, "priority": "mainline"},
                   cluster_nodes=[{"id": "n315", "ok": True, "loadr": 0.5, "running_jobs": 0,
                                   "used_cores": 0},
                                  {"id": "n330", "ok": True, "loadr": 0.5, "running_jobs": 0,
                                   "used_cores": 0}])
    assert d.node == "n330"


def test_node_pick_skips_full_or_short_nodes(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 600, "cores": 8,
                            "priority": "mainline"},
                   cluster_nodes=[{"id": "n315", "ok": True, "loadr": 0.1, "running_jobs": 12,
                                   "used_cores": 0},
                                  {"id": "n330", "ok": True, "loadr": 0.2, "running_jobs": 0,
                                   "used_cores": 125}])
    assert d.placement == pol.PLACEMENT_QUEUE      # 两节点都不满足（并发满 / 剩核不够）


def test_unregistered_node_ignored(policy):
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 600, "priority": "mainline"},
                   cluster_nodes=[{"id": "someones-laptop", "ok": True, "loadr": 0.0,
                                   "running_jobs": 0, "used_cores": 0}])
    assert d.placement == pol.PLACEMENT_QUEUE      # 未登记节点不参与放置


def test_order_queue_priority_then_fifo(policy):
    rows = [{"id": 1, "priority": "self_driven", "submitted_at": "2026-11-13T01:00:00+00:00"},
            {"id": 2, "priority": "rework", "submitted_at": "2026-11-13T03:00:00+00:00"},
            {"id": 3, "priority": "mainline", "submitted_at": "2026-11-13T02:00:00+00:00"},
            {"id": 4, "priority": "rework", "submitted_at": "2026-11-13T02:30:00+00:00"}]
    out = pol.order_queue(policy, rows)
    assert [r["id"] for r in out] == [4, 2, 3, 1]   # 返修按提交时刻先到先派


# ── meter ────────────────────────────────────────────────────────────
def test_make_record_rejects_bad_placement_and_negative_cpu():
    with pytest.raises(meter.MeterError, match="placement"):
        meter.make_record(source_ref="x", placement="moon", cpu_seconds=1)
    with pytest.raises(meter.MeterError, match="cpu_seconds"):
        meter.make_record(source_ref="x", placement="local", cpu_seconds=-1)
    with pytest.raises(meter.MeterError, match="source_ref"):
        meter.make_record(source_ref="", placement="local", cpu_seconds=1)


def test_empty_timestamp_becomes_null():
    """cluster_jobs 里有 finished_at='' 的历史行：账本必须落 NULL 而不是空串。"""
    rec = meter.make_record(source_ref="cj1", placement="cluster", cpu_seconds=1,
                           finished_at="", started_at="2026-11-09 06:27:29.418458+00")
    assert rec["finished_at"] is None
    assert meter._ts(rec["started_at"]).tzinfo is not None


def test_cpu_seconds_from_job():
    assert meter.cpu_seconds_from_job(cores=8, wall_seconds=100) == 800
    assert meter.cpu_seconds_from_job(cores=1, wall_seconds=100, shard_nodes=2,
                                     shard_node_share=2) == 400   # shard：2 节点 × 2 核


def test_jsonl_store_idempotent(tmp_path):
    store_path = tmp_path / "ledger.jsonl"
    store = meter.JsonlStore(store_path)
    rec = meter.make_record(source_ref="cj7", placement="cluster", cpu_seconds=100,
                            mission_id=7, node="n315")
    assert store.add(rec) is True
    assert store.add({**rec, "cpu_seconds": 250}) is False      # 覆盖而非追加
    rows = store.records()
    assert len(rows) == 1 and rows[0]["cpu_seconds"] == 250


def test_backfill_keeps_governance_fields(tmp_path):
    """submit 占位行（带 policy_version）被 ingest 实测行刷新时，治理字段不许被抹掉。"""
    store_path = tmp_path / "ledger.jsonl"
    store = meter.JsonlStore(store_path)
    store.add(meter.make_record(source_ref="cj9", placement="cluster", cpu_seconds=0.0,
                               policy_version="v1", decision_reason="cluster:heavy",
                               mission_id=11, priority_class="mainline"))
    store.add(meter.make_record(source_ref="cj9", placement="cluster", cpu_seconds=999.0,
                               node="n330", policy_version=None, decision_reason=None))
    row = store.records()[0]
    assert row["cpu_seconds"] == 999.0 and row["node"] == "n330"
    assert row["policy_version"] == "v1" and row["mission_id"] == 11
    assert row["priority_class"] == "mainline"


def test_cluster_row_to_record_math():
    row = {"id": 42, "mode": "run", "cores": 8, "shards": None, "mid": 5, "repo": "R",
           "state": "succeeded", "node": "n315",
           "submitted_at": "2026-11-13T00:00:00+00:00",
           "started_at": "2026-11-13T00:01:00+00:00",
           "finished_at": "2026-11-13T00:03:00+00:00"}
    rec = meter.cluster_row_to_record(row)
    assert rec["source_ref"] == "cj42" and rec["cpu_seconds"] == 960.0  # 8 核 × 120s
    assert rec["placement"] == "cluster" and rec["mission_id"] == 5


def test_orphan_running_job_wall_is_capped():
    """孤儿 running 行（worker 掉了）不许把 now-started 全记成 CPU 秒。"""
    row = {"id": 26, "mode": "run", "cores": 4, "shards": None, "mid": 1, "repo": "R",
           "state": "running", "node": "n315", "timeout": 3600,
           "submitted_at": "2026-11-09T18:35:00+00:00",
           "started_at": "2026-11-09T18:35:48+00:00", "finished_at": ""}
    rec = meter.cluster_row_to_record(row)
    assert rec["wall_seconds"] <= 3600 * 1.1
    assert rec["cpu_seconds"] <= 4 * 3600 * 1.1
    assert "wall_capped_by_timeout" in rec["decision_reason"]


def test_terminal_job_wall_capped_by_grace():
    row = {"id": 27, "mode": "run", "cores": 1, "shards": None, "mid": 1, "repo": "R",
           "state": "failed", "node": "n315", "timeout": 60,
           "submitted_at": "2026-11-09T18:35:00+00:00",
           "started_at": "2026-11-09T18:35:48+00:00",
           "finished_at": "2026-11-09T19:35:48+00:00"}      # 实测 3600s ≫ timeout 60s
    rec = meter.cluster_row_to_record(row)
    assert rec["wall_seconds"] == 60 + 900.0
    assert "wall_capped_by_timeout" in rec["decision_reason"]


def test_cluster_row_without_start_skipped():
    assert meter.cluster_row_to_record({"id": 1, "state": "queued", "started_at": None}) is None


def test_ingest_is_idempotent(tmp_path):
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    rows = [{"id": 1, "mode": "run", "cores": 2, "shards": None, "mid": 1, "repo": "R",
             "state": "succeeded", "node": "n315",
             "submitted_at": "2026-11-13T00:00:00+00:00",
             "started_at": "2026-11-13T00:00:00+00:00",
             "finished_at": "2026-11-13T00:10:00+00:00"}]
    first = meter.ingest_cluster_jobs(store, rows=rows)
    second = meter.ingest_cluster_jobs(store, rows=rows)
    assert first["new"] == 1 and second["new"] == 0
    assert len(store.records()) == 1
    assert first["cpu_seconds"] == 1200.0            # 2 核 × 600s


def test_summarize_and_per_mission(tmp_path):
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    for ref, pl, node, cpu, mid in (("a", "local", "node315", 100, 1),
                                    ("b", "cluster", "n315", 300, 1),
                                    ("c", "cluster", "n330", 600, 2)):
        store.add(meter.make_record(source_ref=ref, placement=pl, node=node,
                                   cpu_seconds=cpu, mission_id=mid, priority_class="mainline"))
    s = meter.summarize(store.records())
    assert s["total_cpu_seconds"] == 1000.0
    assert s["local_share"] == 0.1
    assert s["by_node"]["n315"]["cpu_seconds"] == 300.0
    pm = meter.per_mission(store.records())
    m1 = next(p for p in pm if p["mission_id"] == 1)
    assert (m1["cpu_seconds"], m1["local_cpu_seconds"], m1["cluster_cpu_seconds"]) == (400.0, 100.0, 300.0)
    assert set(m1["nodes"]) == {"node315", "n315"}


def test_estimate_uses_history_p90():
    recs = [{"repo": "R", "cmd_kind": "pytest", "cpu_seconds": v} for v in
            (100, 200, 300, 400, 1000)]
    est, basis = meter.estimate_cpu_seconds(recs, repo="R", cmd_kind="pytest")
    assert est == 1000.0 and "history:repo=R,cmd_kind=pytest" in basis
    est2, basis2 = meter.estimate_cpu_seconds(recs, repo="OTHER", cmd_kind="pytest")
    assert est2 == 0.0 and basis2 == "default:no_history"


def test_percentile_nearest_rank():
    assert meter.percentile([], 50) is None
    assert meter.percentile([5.0], 99) == 5.0
    assert meter.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5.0
    assert meter.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9.0


# ── sampler ──────────────────────────────────────────────────────────
def _fake_proc(root: pathlib.Path, pid: int, utime: int, stime: int, cmd: str = "python3 x"):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stat").write_text("%d (proc) S 1 1 1 0 -1 0 0 0 0 0 %d %d 0 0 20 0 1 0 0 0\n"
                            % (pid, utime, stime))
    (d / "cmdline").write_bytes(cmd.encode() + b"\x00")


def test_sampler_reads_cpu_seconds(tmp_path):
    proc = tmp_path / "proc"
    _fake_proc(proc, 101, utime=100, stime=50)
    v = sampler.proc_cpu_seconds(101, proc)
    assert v == pytest.approx(150.0 / sampler.CLK_TCK)
    assert sampler.proc_cpu_seconds(999, proc) is None


def test_sampler_scan_and_delta(tmp_path):
    proc = tmp_path / "proc"
    _fake_proc(proc, 201, 10, 10, cmd="python3 -m pytest")
    _fake_proc(proc, 202, 10, 10, cmd="bash idle")
    assert sampler.scan_pids("pytest", proc) == [201]
    before = sampler.snapshot([201], proc)
    _fake_proc(proc, 201, 210, 10)
    after = sampler.snapshot([201], proc)
    assert sampler.delta_cpu_seconds(before, after) == pytest.approx(200.0 / sampler.CLK_TCK)


def test_system_cpu_between(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("cpu  100 0 100 800 0 0 0 0\n")
    a = sampler.system_cpu_seconds(proc)
    assert (a["busy"], a["total"]) == (200.0 / sampler.CLK_TCK, 1000.0 / sampler.CLK_TCK)
    (proc / "stat").write_text("cpu  150 0 150 900 0 0 0 0\n")
    d = sampler.system_busy_between(a, sampler.system_cpu_seconds(proc))
    assert d["busy_cpu_seconds"] == pytest.approx(100.0 / sampler.CLK_TCK)


# ── dashboard + gate ─────────────────────────────────────────────────
def _cluster_rows(n_queued=3, wait_s=60.0, state="succeeded"):
    rows = []
    for i in range(n_queued):
        rows.append({"id": i + 1, "state": state, "cores": 4, "node": "n315",
                     "submitted_at": "2026-11-13T00:00:00+00:00",
                     "started_at": "2026-11-13T00:%02d:00+00:00" % int(wait_s / 60),
                     "finished_at": "2026-11-13T00:%02d:30+00:00" % int(wait_s / 60)})
    return rows


def test_queue_metrics_depth_and_waits(policy):
    rows = [{"id": 1, "state": "queued", "submitted_at": "2026-11-01T00:00:00+00:00"},
            {"id": 2, "state": "queued", "submitted_at": "2026-11-12T00:00:00+00:00"},
            {"id": 3, "state": "running", "submitted_at": "2026-11-12T21:55:00+00:00",
             "started_at": "2026-11-12T22:00:00+00:00"}]
    q = dashboard.queue_metrics(rows, now=dashboard._parse_ts("2026-11-13T01:00:00+00:00"))
    assert q["depth"] == 2 and q["running"] == 1
    assert q["wait_p50_seconds"] == 300.0 and q["dispatched_last_hour"] == 0
    assert q["oldest_queued_wait_seconds"] > 86400
    assert q["hol_suspect"] is True          # 队列深 + 久等 + 近 1h 无派发


def test_no_hol_when_dispatches_recent(policy):
    rows = [{"id": 1, "state": "queued", "submitted_at": "2026-11-01T00:00:00+00:00"},
            {"id": 2, "state": "running", "submitted_at": "2026-11-13T00:30:00+00:00",
             "started_at": "2026-11-13T00:35:00+00:00"}]
    q = dashboard.queue_metrics(rows, now=dashboard._parse_ts("2026-11-13T01:00:00+00:00"))
    assert q["hol_suspect"] is False


def test_violation_heavy_local_detected(policy, tmp_path):
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    store.add(meter.make_record(source_ref="local-1", placement="local", cpu_seconds=500,
                                mission_id=3, priority_class="mainline", policy_version="v1"))
    m = dashboard.metrics(store.records(), [], policy)
    codes = {v["code"]: v for v in m["violations"]}
    assert pol.V_HEAVY_ON_CONTROL in codes
    assert codes[pol.V_HEAVY_ON_CONTROL]["severity"] == "blocking"
    assert codes[pol.V_HEAVY_ON_CONTROL]["evidence"] == [("local-1", 500.0, 3, "mainline")]


def test_violation_ungoverned_is_only_warn(policy, tmp_path):
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    store.add(meter.make_record(source_ref="cj1", placement="cluster", cpu_seconds=100,
                                mission_id=1))
    m = dashboard.metrics(store.records(), [], policy)
    codes = {v["code"]: v for v in m["violations"]}
    assert codes[pol.V_UNGOVERNED]["severity"] == "warn"


def test_gate_insufficient_on_empty(policy):
    m = dashboard.metrics([], [], policy)
    g = dashboard.evaluate(m, min_cpu_seconds=600)
    assert g["verdict"] == dashboard.INSUFFICIENT and g["exit_code"] == 4


def test_gate_fails_on_local_heavy_ledger(policy, tmp_path):
    """红队面：本机烧重活 + 本机占比超目标 → 门必须 FAIL（exit 3）。"""
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    for i in range(5):
        store.add(meter.make_record(source_ref="local-%d" % i, placement="local",
                                    cpu_seconds=600, mission_id=i,
                                    priority_class="mainline", policy_version="v1"))
    m = dashboard.metrics(store.records(), [], policy)
    g = dashboard.evaluate(m, min_cpu_seconds=600)
    assert g["verdict"] == dashboard.FAIL and g["exit_code"] == 3
    assert pol.V_HEAVY_ON_CONTROL in g["blocking"]
    assert any("超目标" in r for r in g["reasons"])


def test_gate_passes_on_cluster_heavy_ledger(policy, tmp_path):
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    for i in range(5):
        store.add(meter.make_record(source_ref="cj%d" % i, placement="cluster", node="n315",
                                    cpu_seconds=600, mission_id=i,
                                    priority_class="mainline", policy_version="v1",
                                    exit_code=0))
    store.add(meter.make_record(source_ref="local-a", placement="local", cpu_seconds=30,
                                mission_id=99, priority_class="mainline",
                                policy_version="v1", exit_code=0))
    m = dashboard.metrics(store.records(), [], policy)
    g = dashboard.evaluate(m, min_cpu_seconds=600)
    assert g["verdict"] == dashboard.PASS and g["exit_code"] == 0
    assert g["local_share"] == pytest.approx(30 / 3030, rel=1e-3)


def test_gate_fails_when_share_over_target_even_without_heavy(policy, tmp_path):
    """本机占比超目标本身就是 blocking 违例（哪怕每单都不算重活）。"""
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    for i in range(10):
        store.add(meter.make_record(source_ref="local-%d" % i, placement="local",
                                    cpu_seconds=10, mission_id=i,
                                    priority_class="rework", policy_version="v1"))
    m = dashboard.metrics(store.records(), [], policy)
    g = dashboard.evaluate(m, min_cpu_seconds=10)
    assert g["verdict"] == dashboard.FAIL
    assert pol.V_LOCAL_SHARE_OVER_TARGET in g["blocking"]


def test_markdown_has_markers_and_numbers(policy, tmp_path):
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    store.add(meter.make_record(source_ref="cj1", placement="cluster", node="n315",
                                cpu_seconds=3600, mission_id=1, priority_class="mainline",
                                policy_version="v1", exit_code=0))
    m = dashboard.metrics(store.records(), _cluster_rows(), policy)
    md = dashboard.render_markdown(m, window_label="最近 1 天",
                                   gate=dashboard.evaluate(m, min_cpu_seconds=1))
    assert dashboard.BEGIN in md and dashboard.END in md
    assert "本机占比" in md and "队列深度" in md and "每单计量" in md
    assert "n315" in md


# ── panorama 注入 ────────────────────────────────────────────────────
def test_panorama_inject_is_idempotent():
    doc = "# PANORAMA\n\n已有内容\n"
    block = dashboard.BEGIN + "\n## 计算调度\n旧版本\n" + dashboard.END + "\n"
    once = panorama.inject(doc, block)
    twice = panorama.inject(once, block)
    assert once == twice
    assert once.count(dashboard.BEGIN) == 1
    newer = dashboard.BEGIN + "\n## 计算调度\n新版本 42%\n" + dashboard.END + "\n"
    three = panorama.inject(twice, newer)
    assert "新版本 42%" in three and "旧版本" not in three
    assert three.startswith("# PANORAMA")


def test_panorama_rejects_half_block():
    doc = "# X\n" + dashboard.BEGIN + "\n被切坏的块\n"
    with pytest.raises(panorama.InjectionError, match="END"):
        panorama.inject(doc, dashboard.BEGIN + "\n新块\n" + dashboard.END + "\n")


def test_panorama_block_requires_markers():
    with pytest.raises(panorama.InjectionError, match="BEGIN"):
        panorama.inject("doc", "无标记块")


def test_panorama_inject_file_dry_run(tmp_path):
    p = tmp_path / "PANORAMA.md"
    p.write_text("# PANORAMA\n")
    block = dashboard.BEGIN + "\n块\n" + dashboard.END + "\n"
    res = panorama.inject_file(p, block, dry_run=True)
    assert res["changed"] is True and p.read_text() == "# PANORAMA\n"
    res2 = panorama.inject_file(p, block)
    assert res2["changed"] is True
    res3 = panorama.inject_file(p, block)
    assert res3["changed"] is False
    chk = panorama.check_file(p)
    assert chk["has_block"] is True


# ── workload ─────────────────────────────────────────────────────────
def test_workload_is_deterministic_and_verifiable():
    a = burn(0.05, "0/2")
    b = burn(0.05, "1/2")
    assert a["cpu_seconds"] >= 0.04 and b["cpu_seconds"] >= 0.04
    assert a["checksum"] != b["checksum"]
    assert burn(0.05, "0/2")["shard"] == "0/2"

# ── 孤儿 running 行对账（准入饥饿的根因）─────────────────────────────
def test_audit_running_splits_stale(caplog_unused=None):
    now = dashboard._parse_ts("2026-11-13T06:00:00+00:00")
    rows = [{"id": 1, "state": "running", "node": "n315", "cores": 2, "timeout": 3600,
             "started_at": "2026-11-09T06:00:00+00:00"},        # 4 天前 → 孤儿
            {"id": 2, "state": "running", "node": "n315", "cores": 2, "timeout": 7200,
             "started_at": "2026-11-13T05:30:00+00:00"},        # 30min → 活
            {"id": 3, "state": "queued", "node": None, "started_at": None}]
    a = meter.audit_running(rows, now=now, factor=1.5)
    assert a["running"] == 2 and a["live"] == 1
    assert [x["id"] for x in a["stale"]] == [1]
    assert a["live_by_node"] == {"n315": 1}


def test_violation_stale_running_blocks(policy):
    now = dashboard._parse_ts("2026-11-13T06:00:00+00:00")
    rows = [{"id": i, "state": "running", "node": "n315", "cores": 2, "timeout": 3600,
             "started_at": "2026-11-09T06:00:00+00:00"} for i in range(1, 5)]
    m = dashboard.metrics([], rows, policy, now=now)
    codes = {v["code"]: v for v in m["violations"]}
    assert codes[pol.V_STALE_RUNNING]["count"] == 4
    assert codes[pol.V_STALE_RUNNING]["severity"] == "blocking"


def test_policy_config_allows_more_than_four_jobs_per_node(policy):
    """4 并发帽 = 把 128 核节点当 4 核用（702 实测只用 28%）——配置须按核+负载准入。"""
    assert all(n.max_jobs >= 8 for n in policy.nodes)
    d = pol.decide(policy, {"repo": "R", "est_cpu_seconds": 600, "cores": 8,
                            "priority": "mainline"},
                   cluster_nodes=[{"id": "n315", "ok": True, "loadr": 0.30,
                                   "running_jobs": 4, "used_cores": 32},
                                  {"id": "n330", "ok": True, "loadr": 0.44,
                                   "running_jobs": 4, "used_cores": 44}])
    assert d.placement == pol.PLACEMENT_CLUSTER and d.node == "n315"


# ── 多账本叠加 ───────────────────────────────────────────────────────
def test_union_store_dedupes_by_source_ref(tmp_path):
    a = meter.JsonlStore(tmp_path / "a.jsonl")
    b = meter.JsonlStore(tmp_path / "b.jsonl")
    a.add(meter.make_record(source_ref="cj1", placement="cluster", cpu_seconds=100))
    a.add(meter.make_record(source_ref="local-1", placement="local", cpu_seconds=10))
    b.add(meter.make_record(source_ref="cj1", placement="cluster", cpu_seconds=999))
    b.add(meter.make_record(source_ref="ab-1", placement="cluster", cpu_seconds=50))
    u = meter.UnionStore([a, b])
    rows = {r["source_ref"]: r for r in u.records()}
    assert set(rows) == {"cj1", "local-1", "ab-1"}
    assert rows["cj1"]["cpu_seconds"] == 100      # 靠前 store 优先
    u.add(meter.make_record(source_ref="x", placement="cluster", cpu_seconds=1))
    assert any(r["source_ref"] == "x" for r in a.records())   # 写只落第一个


def test_gate_scope_isolates_share_from_hygiene(policy, tmp_path):
    """本机占比达标，但调度卫生被僵尸行破坏：scope=share 应 pass，scope=all 应 fail。"""
    now = dashboard._parse_ts("2026-11-13T06:00:00+00:00")
    store = meter.JsonlStore(tmp_path / "l.jsonl")
    store.add(meter.make_record(source_ref="cj1", placement="cluster", node="n315",
                                cpu_seconds=600, mission_id=1, priority_class="mainline",
                                policy_version="v1", exit_code=0))
    rows = [{"id": i, "state": "running", "node": "n315", "cores": 2, "timeout": 3600,
             "started_at": "2026-11-09T06:00:00+00:00"} for i in range(1, 3)]
    m = dashboard.metrics(store.records(), rows, policy, now=now)
    g_share = dashboard.evaluate(m, min_cpu_seconds=60, scope="share")
    g_all = dashboard.evaluate(m, min_cpu_seconds=60, scope="all")
    g_hyg = dashboard.evaluate(m, min_cpu_seconds=60, scope="hygiene")
    assert g_share["verdict"] == dashboard.PASS
    assert g_all["verdict"] == dashboard.FAIL and pol.V_STALE_RUNNING in g_all["blocking"]
    assert g_hyg["verdict"] == dashboard.FAIL


def test_parse_ts_accepts_psql_short_offset():
    d = dashboard._parse_ts("2026-11-10 23:45:10.221755+00")
    assert d is not None and d.tzinfo is not None
    assert dashboard._parse_ts("") is None and dashboard._parse_ts("garbage") is None


def test_queue_drain_estimate():
    now = dashboard._parse_ts("2026-11-13T06:00:00+00:00")
    rows = [{"id": i, "state": "queued", "submitted_at": "2026-11-13T05:00:00+00:00"}
            for i in range(4)]
    rows.append({"id": 9, "state": "running", "submitted_at": "2026-11-13T05:30:00+00:00",
                 "started_at": "2026-11-13T05:40:00+00:00"})
    q = dashboard.queue_metrics(rows, now=now)
    assert q["dispatched_last_hour"] == 1 and q["drain_hours_estimate"] == 4.0
    q0 = dashboard.queue_metrics([r for r in rows if r["state"] == "queued"], now=now)
    assert q0["drain_hours_estimate"] is None
