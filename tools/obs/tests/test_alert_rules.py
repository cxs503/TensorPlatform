"""Pytest suite for the observability minimal surface (alert rules + digest).

Fixtures are synthetic *and* shaped exactly like the psql json_build_object output, so the
tests pin the behaviour of the real code path (ISO timestamps as strings, ``heartbeat_at``
absent for never-heartbeated claims, ``consecutive_fails`` NULL for gate nodes).

Each positive rule has a matching negative control in the same file: a detector that cannot
demonstrate "looked at it, deliberately did not fire" is not auditable.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tools.obs import alert_rules as R
from tools.obs import obs_digest as D

AS_OF = datetime(2026, 11, 2, 20, 30, tzinfo=timezone.utc)


def ts(minutes_before: float) -> str:
    return (AS_OF - timedelta(minutes=minutes_before)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def mission(mid, repo="TensorLBM", status="running", created_min=10.0,
            hb_min=None, finished=None):
    return {
        "id": mid, "repo": repo, "status": status, "task_key": f"k{mid}",
        "created_at": ts(created_min),
        "heartbeat_at": ts(hb_min) if hb_min is not None else None,
        "finished_at": finished, "claimed_by": "worker-a", "model": "m",
        "fail_stage": None, "campaign_phase": "p1", "goal": "g",
    }


def node(nid, status="blocked", fails=0, reason="", name="n", plan_id=11, mission_id=None):
    return {"id": nid, "plan_id": plan_id, "name": name, "status": status,
            "consecutive_fails": fails, "block_reason": reason,
            "mission_id": mission_id, "node_type": "dev"}


@pytest.fixture()
def snapshot():
    return {
        "schema_version": 1,
        "generated_at": (AS_OF + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "fixture",
        "missions": [
            mission(901, created_min=400, hb_min=200),   # stuck: heartbeat 200 min old
            mission(902, created_min=3),                 # just claimed, no heartbeat yet
            mission(903, created_min=300, hb_min=0.5),   # long but alive
            mission(904, created_min=5, hb_min=1),       # plain active
            mission(905, repo="ship-performance-platform", created_min=8, hb_min=2),
            mission(906, status="succeeded", created_min=60, hb_min=30,
                    finished=ts(30)),                    # terminal -> ignored
        ],
        "nodes": [
            node(701, fails=3, reason="mission failed x3", plan_id=11),      # P1 blocked
            node(702, fails=2, reason="mission failed x2", plan_id=11),      # watch only
            node(703, fails=0, reason="总攻门待放：M3 全链 done 后手动放门", plan_id=11),
            node(704, status="done", fails=0),                               # not blocked
        ],
        "approvals": [
            {"id": 1, "kind": "dev_acceptance", "status": "pending", "mission_id": 901,
             "from_stage": None, "to_stage": None, "reason": "await owner", "created_at": ts(20)},
            {"id": 2, "kind": "dev_acceptance", "status": "approved", "mission_id": 902,
             "from_stage": None, "to_stage": None, "reason": "ok", "created_at": ts(30)},
        ],
        "plans": [{"id": 11, "title": "深水区军(TensorLBM)", "status": "active"}],
    }


@pytest.fixture()
def ev(snapshot):
    return R.evaluate(snapshot, as_of=AS_OF)


# ── R1 + negative controls ───────────────────────────────────────────────────
def test_r1_stuck_running_fires(ev):
    hits = [a for a in ev["alerts"] if a["rule"] == "R1_STUCK_RUNNING"]
    assert [a["subject"] for a in hits] == ["mission:901"]
    assert hits[0]["severity"] == "P1"
    assert hits[0]["evidence"]["heartbeat_age_min"] == 200.0
    assert ev["counts"]["stuck"] == 1


def test_s1_just_claimed_is_not_alerted(ev):
    """Negative example #1: a mission claimed 3 min ago must never raise a stall alert."""
    assert not [a for a in ev["alerts"] if a["subject"] == "mission:902"]
    sup = [s for s in ev["suppressions"] if s["subject"] == "mission:902"]
    assert [s["code"] for s in sup] == ["S1_JUST_CLAIMED"]
    assert sup[0]["observed"]["heartbeat_present"] is False


def test_s2_long_but_alive_is_not_alerted(ev):
    """Negative example #2: 300 min of runtime with a fresh heartbeat is work, not a stall."""
    assert not [a for a in ev["alerts"] if a["subject"] == "mission:903"]
    sup = [s for s in ev["suppressions"] if s["subject"] == "mission:903"]
    assert [s["code"] for s in sup] == ["S2_LONG_BUT_ALIVE"]


def test_claim_without_heartbeat_past_grace_is_stuck(snapshot):
    """A claim that never heartbeated and is older than the grace window is a real stall."""
    snapshot["missions"] = [mission(950, created_min=150)]
    out = R.evaluate(snapshot, as_of=AS_OF)
    assert [a["subject"] for a in out["alerts"] if a["rule"] == "R1_STUCK_RUNNING"] == ["mission:950"]


# ── R2 / S3 boundary ────────────────────────────────────────────────────────
def test_blocked_thrice_boundary(ev):
    p1 = [a for a in ev["alerts"] if a["rule"] == "R2_BLOCKED_THRICE"]
    assert [a["subject"] for a in p1] == ["node:701"]
    assert p1[0]["evidence"]["consecutive_fails"] == 3
    watch = [s for s in ev["suppressions"] if s["code"] == "S3_SUBTHRESHOLD"]
    assert [s["subject"] for s in watch] == ["node:702"]          # x2 -> observation ledger only
    assert ev["ledgers"]["watch"][0]["consecutive_fails"] == 2


def test_threshold_override_moves_the_line(snapshot):
    out = R.evaluate(snapshot, as_of=AS_OF, thresholds={**R.THRESHOLDS, "block_fails": 2})
    assert [a["subject"] for a in out["alerts"] if a["rule"] == "R2_BLOCKED_THRICE"] == [
        "node:701", "node:702"]


# ── R3 gates ────────────────────────────────────────────────────────────────
def test_pending_gate_listed_and_approvals(ev):
    gates = ev["ledgers"]["gates"]
    assert [g["id"] for g in gates] == [703]
    assert any(a["rule"] == "R3_PENDING_GATE" and a["subject"] == "node:703" for a in ev["alerts"])
    assert [a["id"] for a in ev["pending_approvals"]] == [1]


def test_fleet_rollup(snapshot, ev):
    fleets = {f["fleet"]: f for f in ev["fleets"]}
    assert fleets["solver"]["running"] == 4      # 901/902/903/904
    assert fleets["ship"]["running"] == 1        # 905
    assert fleets["solver"]["stuck"] == 1
    assert R.fleet_of("ship-performance-platform") == "ship"
    assert R.fleet_of("TensorCAD") == "solver"
    assert R.fleet_of("TensorPlatform") == "platform"


# ── determinism / rendering / CLI contract ──────────────────────────────────
def test_evaluation_is_deterministic(snapshot):
    a = json.dumps(R.evaluate(snapshot, as_of=AS_OF), sort_keys=True)
    b = json.dumps(R.evaluate(snapshot, as_of=AS_OF), sort_keys=True)
    assert a == b


def test_digest_has_all_five_sections(snapshot, ev):
    md = D.render_digest(snapshot, ev, "cmd")
    for marker in ("① 三军状态", "② 异常清单", "③ 待放门", "④ 告警规则与负例", "⑤ 审计链"):
        assert marker in md
    assert "R1_STUCK_RUNNING" in md and "S1_JUST_CLAIMED" in md
    assert "mission:901" in md


def test_dry_run_writes_nothing(tmp_path, snapshot, monkeypatch, capsys):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    rc = D.main(["--snapshot", str(snap), "--as-of", "2026-11-02T20:30:00Z",
                 "--dry-run", "--quiet", "--figure", str(tmp_path / "f.png")])
    assert rc == 0
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / "f.png").exists()
    assert "dry-run" in capsys.readouterr().err


def test_cli_writes_md_and_json(tmp_path, snapshot):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(snapshot), encoding="utf-8")
    md, js = tmp_path / "digest.md", tmp_path / "alerts.json"
    rc = D.main(["--snapshot", str(snap), "--as-of", "2026-11-02T20:30:00Z", "--no-project",
                 "--md-out", str(md), "--json-out", str(js)])
    assert rc == 0
    payload = json.loads(js.read_text(encoding="utf-8"))
    assert payload["counts"]["alerts_p1"] == 2          # stuck mission + x3 blocked node
    assert payload["counts"]["suppressions_total"] == 3  # S1 + S2 + S3
    assert payload["snapshot_sha256"] == D.snapshot_digest(snapshot)
    assert "昨夜战况一页" in md.read_text(encoding="utf-8")


def test_fail_on_alert_exit_code(tmp_path, snapshot):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(snapshot), encoding="utf-8")
    assert D.main(["--snapshot", str(snap), "--as-of", "2026-11-02T20:30:00Z",
                   "--md-out", str(tmp_path / "d.md"), "--json-out", str(tmp_path / "a.json"),
                   "--fail-on-alert"]) == 2
    # a healthy snapshot must exit 0: all running missions fresh
    healthy = {**snapshot, "missions": [mission(960, created_min=4, hb_min=1)], "nodes": []}
    snap2 = tmp_path / "healthy.json"
    snap2.write_text(json.dumps(healthy), encoding="utf-8")
    assert D.main(["--snapshot", str(snap2), "--as-of", "2026-11-02T20:30:00Z",
                   "--md-out", str(tmp_path / "d2.md"), "--json-out", str(tmp_path / "a2.json"),
                   "--fail-on-alert"]) == 0


def test_figure_renders(tmp_path, snapshot, ev):
    pytest.importorskip("matplotlib")
    out = D.render_figure(snapshot, ev, str(tmp_path / "fig.png"))
    assert os.path.getsize(out) > 5000

# ── past-instant replay (time projection) ───────────────────────────────────
def test_projection_rebuilds_past_state(snapshot):
    """A mission that is 'succeeded' today was 'running and silent' 4 h earlier.

    This is exactly the 2026-11-02 heartbeat blackout shape: terminals recorded later, last
    heartbeat watermark far behind the instant we ask about.
    """
    late = mission(970, created_min=420, hb_min=250)
    late["finished_at"] = (AS_OF + timedelta(minutes=120)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    late["status"] = "succeeded"                      # how the row looks *today*
    early_done = mission(971, created_min=500, hb_min=380)
    early_done["finished_at"] = ts(200)               # already over at as_of
    early_done["status"] = "failed"
    snap = {**snapshot, "missions": [late, early_done], "nodes": [], "approvals": []}

    verbatim = R.evaluate(snap, as_of=AS_OF)
    assert not verbatim["alerts"]                     # stored status is terminal -> nothing alive

    replay = R.evaluate(R.project_at(snap, AS_OF), as_of=AS_OF)
    stuck = [a["subject"] for a in replay["alerts"] if a["rule"] == "R1_STUCK_RUNNING"]
    assert stuck == ["mission:970"]                   # silence 250 min >= 120 -> P1
    assert replay["projection"]["dropped"] == 1        # 971 had already finished


def test_projection_clamps_heartbeat_after_as_of(snapshot):
    """A heartbeat *after* as_of proves aliveness at as_of -> clamped, never a false stall."""
    m = mission(972, created_min=300, hb_min=-60)      # last heartbeat 60 min AFTER as_of
    snap = {**snapshot, "missions": [m], "nodes": [], "approvals": []}
    out = R.evaluate(R.project_at(snap, AS_OF), as_of=AS_OF)
    assert out["projection"]["heartbeat_clamped"] == 1
    assert not [a for a in out["alerts"] if a["rule"] == "R1_STUCK_RUNNING"]
    assert [s["code"] for s in out["suppressions"]] == ["S2_LONG_BUT_ALIVE"]


def test_projection_never_fabricates_running(snapshot):
    """'stopped' without finished_at is ambiguous history -> stays stopped, never a stall alert."""
    m = mission(973, created_min=600, hb_min=500)
    m["status"] = "stopped"
    snap = {**snapshot, "missions": [m], "nodes": [], "approvals": []}
    out = R.evaluate(R.project_at(snap, AS_OF), as_of=AS_OF)
    assert out["alerts"] == []
    assert out["states"][0]["status"] == "stopped"


def test_node_fleet_from_node_name(ev):
    """Nodes without a linked mission are attributed by the repo named in their own title."""
    nodes = [node(710, fails=0, reason="饱和池待放：idle>=6 放行", name="饱和池K4·FVM(TensorFVM)"),
             node(711, fails=0, reason="饱和池待放：idle>=6 放行", name="饱和池K9·冰航(ship)"),
             node(712, fails=3, reason="mission failed x3", name="无人认领节点")]
    out = R.evaluate({"missions": [], "nodes": nodes, "approvals": [], "plans": []}, as_of=AS_OF)
    fleets = {f["fleet"]: f for f in out["fleets"]}
    assert fleets["solver"]["gate_nodes"] == 1
    assert fleets["ship"]["gate_nodes"] == 1
    assert fleets["platform"]["blocked_nodes"] == 1
