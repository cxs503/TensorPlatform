"""IN20-R5 停摆自检自愈（stall detector）验收测试。

判据（先于实现）：
1. 阳性：2026-11-07 00:52→03:18 停摆时间线回放，R1（断粮）与 R3（脉搏停滞）
   MUST fire 且 MUST 触发动作（refill / selfproof），R2 在本案例 MUST 沉默；
2. 阴性：健康态 2 h → fire=0，且 observations=120（「不告警」≠「没检查」）；
3. 误报抑制：10 min 断粮即恢复 → 只 onset/recovered，无 fire；
4. 舰队面：大面积心跳丢失 → R2 fire + fleet_supervisor 重启路径（dry 不落副作用）；
5. 自证存活：脉搏停 ≥30 min → beacon 带 selfproof 标记，可区分「生产者死」与「检测器死」；
6. 冷却/恢复/回滚：同事件不重复轰击；恢复后重开新事件；rollback 清闸闩且审计留痕；
7. 审计链：hash 链可机械验证，篡改必被抓出；
8. 观测盲区 ≠ 停滞：`pulse_last_ts` 缺失只出 P2 unknown，不 fire；
9. 取数面：只读 SQL 结果解析正确，非法状态词拒绝（SQL 注入面收敛）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ops.taskplane import stall_detector as sd  # noqa: E402

UTC = timezone.utc
FIXTURE = REPO / "tests/fixtures/stall_20261107_0052_0318.json"
T0 = datetime(2026, 11, 7, 0, 35, tzinfo=UTC)


def sample(minute: float, **kw) -> sd.Sample:
    """以 T0 为原点造样本（minute 可为小数）。"""
    base = dict(dag_pending=3, plan_pending=0, queued_missions=2, workers_enrolled=57,
                heartbeat_fresh=57, workers_online=52, workers_idle=10,
                pulse_last_ts=T0 + timedelta(minutes=minute), source="test")
    base.update(kw)
    return sd.Sample(ts=T0 + timedelta(minutes=minute), **base)


def daemon(tmp_path: Path, mode: str = "dry", **kw) -> sd.StallDaemon:
    ops = sd.Ops(mode=mode, out_dir=tmp_path, sh=kw.pop("sh", lambda argv: _cp(1)))
    return sd.StallDaemon(ops=ops, out_dir=tmp_path, **kw)


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def kinds(events, kind: str) -> list:
    return [e for e in events if e.kind == kind]


# ── 1. 阳性：历史停摆时间线 ────────────────────────────────────────────────
def test_replay_historical_stall_timeline_must_alarm_and_trigger(tmp_path):
    d = daemon(tmp_path)
    verdict = sd.run_replay(FIXTURE, d)

    assert verdict["alarm"] is True
    assert verdict["observations"] == 176, "回放必须逐分钟看满 176 个样本"
    r1, r3 = verdict["by_rule"][sd.RULE_STARVED], verdict["by_rule"][sd.RULE_PULSE]
    assert r1["fired"] and r1["action"] == "refill"
    assert r3["fired"] and r3["action"] == "selfproof"
    # 两条 P1 都必须落在停摆窗口内（00:52→03:18），且持续窗/老化门都得满足
    for rule in (sd.RULE_STARVED, sd.RULE_PULSE):
        fired_at = datetime.fromisoformat(verdict["by_rule"][rule]["first_fire_ts"])
        assert datetime(2026, 11, 7, 0, 52, tzinfo=UTC) < fired_at < \
               datetime(2026, 11, 7, 3, 18, tzinfo=UTC)
    assert verdict["by_rule"][sd.RULE_FLEET]["fired"] is False, "本案例是断粮+脉搏停，R2 应沉默"
    # 动作确实被执行过（dry 模式：探活 + 记录，不落副作用）
    log = {item["rule"]: item["result"] for item in verdict["action_log"]}
    assert set(log) == {sd.RULE_STARVED, sd.RULE_PULSE}
    assert log[sd.RULE_STARVED]["action"] == "refill"
    assert log[sd.RULE_STARVED]["status"] in {"would_launch", "launched", "already_running"}
    assert log[sd.RULE_PULSE]["status"] == "beacon_written"
    # 恢复事件存在（03:19 补给回流后两条规则都解除）
    assert verdict["kinds"]["recovered"] == 2
    # 审计链 + beacon 落盘
    assert sd.verify_chain(tmp_path / "audit.jsonl")["ok"] is True
    beacon = json.loads((tmp_path / "beacon.json").read_text(encoding="utf-8"))
    assert beacon["mechanism"] == "stall_detector" and beacon["mechanism_alive"] is True


def test_replay_fixture_provenance_is_explicit():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    prov = payload["provenance"]
    assert prov["gap"]["seconds"] > 147 * 60, "断档必须 ≥ 2h27m（实测 00:52:35→03:20:08）"
    assert prov["gap"]["evidence_grade"].startswith("WITHHELD")
    assert prov["anchors"] and prov["reconstruction_law"]["pulse_last_ts"]
    graded = {s["source"] for s in payload["samples"]}
    assert "gap_reconstruction" in graded and "post_recovery" in graded


# ── 2. 阴性：健康态 ────────────────────────────────────────────────────────
def test_healthy_fleet_two_hours_no_false_alarm(tmp_path):
    d = daemon(tmp_path)
    events = []
    for m in range(120):
        events.extend(d.step(sample(m, dag_pending=5, queued_missions=3,                                    workers_idle=12, heartbeat_fresh=57))["events"])
    assert kinds(events, "fire") == [], "健康态不得 fire"
    assert d.detector.observations == 120
    assert sd.verify_chain(tmp_path / "audit.jsonl")["records"] == 120  # 每周期一条事件面留痕
    # 「没告警」要能与「没检查」区分：beacon 逐周期前进
    beacon = json.loads((tmp_path / "beacon.json").read_text(encoding="utf-8"))
    assert beacon["cycle"] == 120


# ── 3. 误报抑制：短断粮 ────────────────────────────────────────────────────
def test_short_starvation_dip_does_not_fire(tmp_path):
    d = daemon(tmp_path)
    events = []
    for m in range(10):                       # 断粮 10 min < 15 min 持续门
        events.extend(d.step(sample(m, dag_pending=0, queued_missions=0, workers_idle=45))["events"])
    for m in range(10, 20):                   # 活回来了
        events.extend(d.step(sample(m, dag_pending=8, queued_missions=4, workers_idle=10))["events"])
    assert kinds(events, "fire") == []
    assert len(kinds(events, "onset")) == 1
    recovered = kinds(events, "recovered")
    assert len(recovered) == 1
    assert recovered[0].evidence["fired"] is False
    assert recovered[0].evidence["episode_s"] == pytest.approx(600.0)


# ── 4. 舰队面：大面积心跳丢失 → 重启路径 ──────────────────────────────────
def test_fleet_heartbeat_loss_triggers_supervisor_restart_route(tmp_path):
    calls: list = []

    def fake_sh(argv):
        calls.append(list(argv))
        if argv[:2] == ["systemctl", "cat"]:
            return _cp(1, stderr="Unit not found")
        if argv[0] == "pgrep":
            return _cp(0, stdout="")          # 无存活 supervisor
        return _cp(0)

    d = daemon(tmp_path, sh=fake_sh)
    events = []
    for m in range(8):                        # 12 席里 9 席失联（失联比 0.75）
        events.extend(d.step(sample(m, workers_enrolled=12, heartbeat_fresh=3,                                    workers_online=12, workers_idle=12,                                    pulse_last_ts=T0 + timedelta(minutes=m)))["events"])
    fires = kinds(events, "fire")
    assert [e.rule for e in fires] == [sd.RULE_FLEET]
    assert fires[0].evidence["lost_ratio"] == pytest.approx(0.75)
    info = {item["rule"]: item["result"] for item in d.action_log}[sd.RULE_FLEET]
    assert info["route"] == "pgrep_kill_relaunch"
    assert info["status"] == "would_relaunch"      # dry：只给路线，不动进程
    assert not any(c[0] in {"kill", "bash"} for c in calls), "dry 模式禁止真实动作"


def test_fleet_restart_prefers_systemd_unit_when_present(tmp_path):
    calls: list = []

    def fake_sh(argv):
        calls.append(list(argv))
        return _cp(0, stdout="Unit tp-fleet-supervisor.service")

    ops = sd.Ops(mode="dry", out_dir=tmp_path, sh=fake_sh)
    info = ops.restart_fleet_supervisor()
    assert info["route"] == "systemd" and info["unit"] == "tp-fleet-supervisor"
    assert info["cmd"] == ["systemctl", "restart", "tp-fleet-supervisor"]
    assert info["status"] == "would_restart"


# ── 5. 自证存活：脉搏停但观测机制活着 ────────────────────────────────────
def test_pulse_stall_selfproof_decouples_dead_producer_from_dead_detector(tmp_path):
    d = daemon(tmp_path)
    stale_at = T0
    events = []
    for m in range(45):                       # 脉搏冻结 45 min，其它指标全部健康
        events.extend(d.step(sample(m, dag_pending=6, queued_missions=2,                                    workers_idle=12, pulse_last_ts=stale_at))["events"])
    fires = kinds(events, "fire")
    assert [e.rule for e in fires] == [sd.RULE_PULSE]
    assert fires[0].evidence["pulse_age_s"] >= 1800
    beacon = json.loads((tmp_path / "beacon.json").read_text(encoding="utf-8"))
    assert beacon["selfproof"] is True and beacon["mechanism_alive"] is True
    assert beacon["observed_pulse_last_ts"].startswith("2026-11-07T00:35")
    # 外部视角：beacon 新鲜 ⇒ 检测器活；同一时刻脉搏死 ⇒ 结论明确指向生产者
    alive = sd.check_beacon(tmp_path / "beacon.json", max_age_s=10 ** 6,
                            now=datetime.fromisoformat(beacon["ts"]))
    assert alive["alive"] is True
    dead = sd.check_beacon(tmp_path / "beacon.json", max_age_s=60,
                           now=sd.parse_ts(beacon["ts"]) + timedelta(minutes=5))
    assert dead["alive"] is False and dead["reason"] == "stale"


def test_missing_pulse_observation_is_not_a_stall(tmp_path):
    d = daemon(tmp_path)
    events = []
    for m in range(40):
        events.extend(d.step(sample(m, dag_pending=6, pulse_last_ts=None))["events"])
    assert kinds(events, "fire") == [], "无观测 ≠ 停滞：不得 fire"
    onset = [e for e in kinds(events, "onset") if e.rule == sd.RULE_PULSE]
    blind = [e for e in kinds(events, "unknown") if e.rule == sd.RULE_PULSE]
    assert len(blind) == 1, "观测盲区只留一次痕，且不得伪装成 clear"
    assert blind[0].evidence["observed"] is False and blind[0].severity == "P2"


# ── 6. 冷却 / 恢复 / 回滚 ─────────────────────────────────────────────────
def test_cooldown_blocks_repeat_fire_but_logs_suppression(tmp_path):
    d = daemon(tmp_path)
    events = []
    for m in range(90):                       # 持续断粮 90 min（> 冷却 30 min）
        events.extend(d.step(sample(m, dag_pending=0, queued_missions=0, workers_idle=45))["events"])
    r1 = [e for e in events if e.rule == sd.RULE_STARVED]
    assert len(kinds(r1, "fire")) == 1, "同一事件内不得重复轰击"
    # 冷却窗内被抑制的尝试必须留痕（可与「没检查」区分）
    assert kinds(r1, "suppressed") == []
    assert d.detector.states[sd.RULE_STARVED].fired_in_episode is True
    # 第二次事件（恢复后再断粮）在冷却过后可再 fire
    events2 = []
    for m in range(90, 95):
        events2.extend(d.step(sample(m, dag_pending=5, queued_missions=3, workers_idle=10))["events"])
    assert len(kinds(events2, "recovered")) == 1
    for m in range(95, 130):
        events2.extend(d.step(sample(m, dag_pending=0, queued_missions=0, workers_idle=45))["events"])
    assert len(kinds(events2, "fire")) == 1
    assert d.detector.fires[sd.RULE_STARVED] == 2


def test_cooldown_across_episodes_is_enforced(tmp_path):
    th = sd.Thresholds(cooldown_s=3600)
    d = daemon(tmp_path)
    d.detector = sd.Detector(th)
    starved = dict(dag_pending=0, queued_missions=0, workers_idle=45)
    for m in range(20):
        d.step(sample(m, **starved))
    assert d.detector.fires[sd.RULE_STARVED] == 1
    d.step(sample(20, dag_pending=4, queued_missions=2, workers_idle=10))   # 恢复
    events = []
    for m in range(21, 40):                                                # 立刻再断粮
        events.extend(d.step(sample(m, **starved))["events"])
    assert d.detector.fires[sd.RULE_STARVED] == 1
    assert len(kinds([e for e in events if e.rule == sd.RULE_STARVED], "suppressed")) == 1


def test_rollback_clears_latches_keeps_audit_and_archives_state(tmp_path):
    d = daemon(tmp_path)
    for m in range(30):
        d.step(sample(m, dag_pending=0, queued_missions=0, workers_idle=45))
    assert d.detector.fires[sd.RULE_STARVED] == 1
    result = sd.rollback(tmp_path)
    assert result["ok"] is True and Path(result["archived_state"]).exists()
    # 回滚后重开：闸闩全清，不继承上一轮事件（且不立刻重放旧 fire）
    fresh = daemon(tmp_path)
    assert fresh.load_state() is True
    assert fresh.detector.states[sd.RULE_STARVED].since is None
    assert fresh.detector.states[sd.RULE_STARVED].last_fire is None
    assert fresh.detector.fires[sd.RULE_STARVED] == 0
    chain = sd.verify_chain(tmp_path / "audit.jsonl")
    assert chain["ok"] is True
    last = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["kind"] == "rollback"


def test_restart_after_fire_does_not_refire_within_sustain_gate(tmp_path):
    """进程重启不继承内存闩锁时也不许立刻重复轰击（state.json 续命）。"""
    d = daemon(tmp_path)
    for m in range(30):
        d.step(sample(m, dag_pending=0, queued_missions=0, workers_idle=45))
    fired = d.detector.fires[sd.RULE_STARVED]
    assert fired == 1
    restarted = daemon(tmp_path)
    restarted.load_state()
    restarted.step(sample(31, dag_pending=0, queued_missions=0, workers_idle=45))
    assert restarted.detector.fires[sd.RULE_STARVED] == 1
    assert restarted.detector.states[sd.RULE_STARVED].fired_in_episode is True


# ── 7. 审计链 ──────────────────────────────────────────────────────────────
def test_audit_chain_links_and_detects_tampering(tmp_path):
    log = sd.AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.append({"kind": "event", "i": i})
    good = sd.verify_chain(tmp_path / "audit.jsonl")
    assert good["ok"] is True and good["records"] == 5
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[2])
    tampered["kind"] = "rewritten"
    lines[2] = json.dumps(tampered, ensure_ascii=False)
    (tmp_path / "audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = sd.verify_chain(tmp_path / "audit.jsonl")
    assert bad["ok"] is False and bad["first_bad"] == 3 and bad["reason"] == "hash_mismatch"


# ── 8. 动作面与取数面 ─────────────────────────────────────────────────────
def test_ops_dry_never_mutates_and_live_launches(tmp_path):
    calls: list = []
    launched: list = []

    def fake_sh(argv):
        calls.append(list(argv))
        return _cp(0, stdout="")

    class FakeProc:
        pid = 4242

    def fake_popen(argv, log_path):
        launched.append(list(argv))
        return FakeProc()

    ops = sd.Ops(mode="dry", out_dir=tmp_path, sh=fake_sh, popen=fake_popen)
    dry = ops.ensure_refiller()
    assert dry["status"] == "would_launch" and launched == []
    assert dry["cmd"][0] == "/usr/bin/python3" and dry["cmd"][1].endswith("selfstarter.py")

    live = sd.Ops(mode="live", out_dir=tmp_path, sh=fake_sh, popen=fake_popen)
    done = live.ensure_refiller()
    assert done["status"] == "launched" and done["pid"] == 4242
    assert launched and launched[0][1].endswith("selfstarter.py")

    off = sd.Ops(mode="off", out_dir=tmp_path, sh=fake_sh, popen=fake_popen)
    assert off.ensure_refiller()["status"] == "skipped"
    assert off.restart_fleet_supervisor()["status"] == "skipped"


def test_collect_from_db_parses_ledger_and_rejects_bad_status_token():
    payloads = {
        "campaign_nodes": {"dag_pending": 0, "plan_pending": 0, "queued_missions": 2,
                           "dag_status_hist": {"done": 167, "in-flight": 19, "blocked": 8},
                           "plan_status_hist": {"planned": 27}},
    }

    def fake_psql(sql):
        if "campaign_nodes" in sql:
            return json.dumps(payloads["campaign_nodes"])
        if "factory_resident_workers" in sql:
            return json.dumps({"enrolled": 57, "fresh": 50, "idle_rows": 54})
        if "tensor_dev_missions" in sql:
            return json.dumps({"claimants": 19, "running": 21})
        return "2026-11-07T03:26:43"

    when = datetime(2026, 11, 7, 3, 27, tzinfo=UTC)
    s = sd.collect_from_db(psql=fake_psql, now=when)
    assert (s.dag_pending, s.plan_pending, s.queued_missions) == (0, 0, 2)
    assert s.workers_online == max(50, 19) and s.workers_idle == 31   # idle = online − busy
    assert s.heartbeat_fresh == 50 and s.workers_enrolled == 57
    assert s.lost_ratio == pytest.approx(1 - 50 / 57, abs=1e-6)
    assert s.pulse_age_s == pytest.approx(16.0, abs=1.0)
    assert s.detail["idle_rows_selfreported"] == 54, "worker 自述 idle 只作对账"
    with pytest.raises(ValueError):
        sd._status_list(("pending'; drop table x; --",))


def test_cli_exit_codes_are_machine_readable(tmp_path):
    out = tmp_path / "replay"
    rc = sd.main(["--fixture", str(FIXTURE), "--replay", "--out", str(out),
                  "--action-mode", "dry", "--quiet"])
    assert rc == 2, "有 P1 时必须返回 2（cron/unit 可据此告警）"

    healthy = tmp_path / "healthy.json"
    samples = [sample(m, dag_pending=5, queued_missions=3, workers_idle=12).as_dict()
               for m in range(10)]
    healthy.write_text(json.dumps({"provenance": {"case": "healthy"}, "samples": samples}),
                       encoding="utf-8")
    rc = sd.main(["--fixture", str(healthy), "--replay", "--out", str(tmp_path / "h"),
                  "--action-mode", "dry", "--quiet"])
    assert rc == 0, "健康态必须返回 0"
    verdict = json.loads((tmp_path / "h" / "replay_trace.json").read_text(encoding="utf-8"))
    assert verdict["verdict"]["fires"] == 0 and verdict["verdict"]["observations"] == 10
