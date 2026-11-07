#!/usr/bin/env python3
"""tp-stall-detector —— 停摆自检自愈 daemon（IN20-R5，续航军 R5）。

为什么存在
----------
2026-11-07 00:52→03:18 平台停了约 2h26m：脉搏表最后一行 00:52:35，下一行 03:20:08
（`saturation_pulse` 实测 gap=147.6 min），期间舰队在线 52 席而只有个位数在干活、
可派 DAG 节点为 0，但没有任何机制为此响过一声。人的注意力被当成唯一检测器，
这正是 owner 渐退令要消灭的单点。

本 daemon 把「今晚怎么发现的」变成周期体检，独立于任何人类会话与 AI 协调者会话：
systemd / 容器 daemon 拉起，只读平台台账，三规则 + 持续窗 + 冷却 + 恢复；
动作（补给 / 舰队重启 / 自证存活）走子进程；全部落 hash 链审计 JSONL，带回滚路径。

三条规则（机器可裁，阈值集中在 :class:`Thresholds`）
----------------------------------------------------
R1_STARVED              断粮：可派 DAG 节点=0 且排队任务≤queue_floor 且
                        idle 席位≥starve_min_idle 且 idle/online≥starve_idle_ratio，
                        持续 ≥ starve_sustain_s(900 s) → 触发补给器（确保 selfstarter
                        存活）并出 P1 alarm digest。
R2_FLEET_HEARTBEAT_LOSS 舰队大面积心跳丢失：席位≥fleet_min_workers 且失联比例
                        ≥fleet_loss_ratio，持续 ≥ fleet_sustain_s(300 s) → 走
                        fleet_supervisor 重启路径（systemd unit 优先，否则
                        TERM + 分离重启），P1。
R3_PULSE_STALL          脉搏表停滞：`saturation_pulse.max(ts)` 老化 ≥
                        pulse_stall_s(1800 s) → 调节器 daemon 自证存活（写带
                        selfproof 标记的 beacon），把「脉搏生产者死」与「检测器死」
                        分开；P1。`pulse_last_ts` 缺失 ≠ 停滞（只出 P2 unknown）。

判据先于实现
------------
* 阳性：2026-11-07 00:52→03:18 停摆时间线回放 → R1、R3 MUST fire 且 MUST 触发动作；
* 阴性：健康态 2 h → fire=0，且观测次数 N 可数（「不告警」≠「没检查」）；
* 误报抑制：10 min 断粮即恢复 → 只有 onset/recovered。

用法
----
  python3 ops/taskplane/stall_detector.py --from-db --once \
      --out runs/stall_detector --action-mode dry
  python3 ops/taskplane/stall_detector.py --from-db --daemon --interval 60 \
      --out runs/stall_detector --action-mode live
  python3 ops/taskplane/stall_detector.py \
      --fixture tests/fixtures/stall_20261107_0052_0318.json \
      --replay --out artifacts/in20_r5 --action-mode dry
  python3 ops/taskplane/stall_detector.py --rollback --out runs/stall_detector

退出码：0=无 P1；2=本次运行有 fire；1=输入/台账错误（fail closed）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[2]
TP_ROOT = Path(os.environ.get("TP_ROOT", "/root/tp-agent"))

RULE_STARVED = "R1_STARVED"
RULE_FLEET = "R2_FLEET_HEARTBEAT_LOSS"
RULE_PULSE = "R3_PULSE_STALL"
RULES = (RULE_STARVED, RULE_FLEET, RULE_PULSE)
RULE_ACTION = {RULE_STARVED: "refill", RULE_FLEET: "fleet_restart", RULE_PULSE: "selfproof"}
SEVERITY = {RULE_STARVED: "P1", RULE_FLEET: "P1", RULE_PULSE: "P1"}
RULE_NAME = {RULE_STARVED: "R1 断粮", RULE_FLEET: "R2 舰队心跳丢失", RULE_PULSE: "R3 脉搏停滞"}

# 可派 DAG 节点状态白名单（小写，只允许 [a-z_]；拼 SQL 前强校验）
DISPATCHABLE_DEFAULT = ("pending", "queued", "ready", "todo", "not_started")
FRESH_MISSION_S = 150


# ── 时间工具 ────────────────────────────────────────────────────────────────
def parse_ts(value: Any) -> Optional[datetime]:
    """宽松解析 ISO/epoch → aware UTC；None/空串 → None。"""
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else parse_ts(dt).astimezone(UTC).isoformat(timespec="seconds")


# ── 阈值 ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Thresholds:
    """全部阈值集中一处，digest 原样打印（判据可审计、可回放）。"""

    starve_idle_ratio: float = 0.50
    starve_min_idle: int = 8
    starve_sustain_s: int = 900
    queue_floor: int = 0
    fleet_loss_ratio: float = 0.50
    fleet_min_workers: int = 5
    fleet_sustain_s: int = 300
    pulse_stall_s: int = 1800
    cooldown_s: int = 1800
    persist_log_s: int = 600
    heartbeat_fresh_s: int = 300

    def sustain_for(self, rule: str) -> int:
        return {RULE_STARVED: self.starve_sustain_s,
                RULE_FLEET: self.fleet_sustain_s,
                RULE_PULSE: 0}[rule]  # R3 的持续量已由「脉搏老化」本身表达

    def as_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_env(cls) -> "Thresholds":
        base = cls()
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):
            raw = os.environ.get("TP_STALL_" + f.name.upper())
            if raw not in (None, ""):
                kwargs[f.name] = type(getattr(base, f.name))(raw)
        return cls(**kwargs)


# ── 观测量 ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Sample:
    """一次体检测到的平台状态（只读快照 + 显式时钟）。"""

    ts: datetime
    dag_pending: int = 0
    plan_pending: int = 0
    queued_missions: int = 0
    workers_enrolled: int = 0
    heartbeat_fresh: int = 0
    workers_online: int = 0
    workers_idle: int = 0
    pulse_last_ts: Optional[datetime] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    source: str = "synthetic"

    @property
    def pending_nodes(self) -> int:
        return int(self.dag_pending) + int(self.plan_pending)

    @property
    def idle_ratio(self) -> float:
        return (self.workers_idle / self.workers_online) if self.workers_online else 0.0

    @property
    def lost_ratio(self) -> float:
        base = max(self.workers_enrolled, self.workers_online)
        return 0.0 if base <= 0 else max(0.0, 1.0 - self.heartbeat_fresh / base)

    @property
    def pulse_age_s(self) -> Optional[float]:
        if self.pulse_last_ts is None:
            return None
        return max(0.0, (self.ts - parse_ts(self.pulse_last_ts)).total_seconds())

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": iso(self.ts),
            "dag_pending": self.dag_pending,
            "plan_pending": self.plan_pending,
            "pending_nodes": self.pending_nodes,
            "queued_missions": self.queued_missions,
            "workers_enrolled": self.workers_enrolled,
            "heartbeat_fresh": self.heartbeat_fresh,
            "workers_online": self.workers_online,
            "workers_idle": self.workers_idle,
            "idle_ratio": round(self.idle_ratio, 4),
            "lost_ratio": round(self.lost_ratio, 4),
            "pulse_last_ts": iso(self.pulse_last_ts),
            "pulse_age_s": None if self.pulse_age_s is None else round(self.pulse_age_s, 1),
            "source": self.source,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Sample":
        known = {f.name for f in fields(cls)} - {"ts", "pulse_last_ts"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        return cls(ts=parse_ts(raw["ts"]), pulse_last_ts=parse_ts(raw.get("pulse_last_ts")),
                   **kwargs)


# ── 规则（纯函数：无 I/O、不读墙钟）──────────────────────────────────────────
@dataclass
class Evaluation:
    rule: str
    active: bool
    severity: str
    evidence: Dict[str, Any]
    action: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule, "active": self.active, "severity": self.severity,
                "action": self.action, "evidence": self.evidence}


def evaluate_sample(s: Sample, th: Thresholds) -> List[Evaluation]:
    """三规则的即时判定。`active` 只回答「此刻条件成立吗」，持续/冷却在 Detector。"""
    starved = (
        s.dag_pending == 0
        and s.plan_pending == 0
        and s.queued_missions <= th.queue_floor
        and s.workers_online > 0
        and s.workers_idle >= th.starve_min_idle
        and s.idle_ratio >= th.starve_idle_ratio
    )
    fleet_lost = (max(s.workers_enrolled, s.workers_online) >= th.fleet_min_workers
                  and s.lost_ratio >= th.fleet_loss_ratio)
    age = s.pulse_age_s
    pulse_unknown = age is None
    pulse_stalled = age is not None and age >= th.pulse_stall_s
    return [
        Evaluation(RULE_STARVED, starved, SEVERITY[RULE_STARVED], {
            "dag_pending": s.dag_pending, "plan_pending": s.plan_pending,
            "queued_missions": s.queued_missions, "queue_floor": th.queue_floor,
            "workers_online": s.workers_online, "workers_idle": s.workers_idle,
            "idle_ratio": round(s.idle_ratio, 4),
            "idle_ratio_threshold": th.starve_idle_ratio,
            "min_idle": th.starve_min_idle,
        }, RULE_ACTION[RULE_STARVED]),
        Evaluation(RULE_FLEET, fleet_lost, SEVERITY[RULE_FLEET], {
            "workers_enrolled": s.workers_enrolled, "heartbeat_fresh": s.heartbeat_fresh,
            "lost_ratio": round(s.lost_ratio, 4),
            "lost_ratio_threshold": th.fleet_loss_ratio,
            "min_workers": th.fleet_min_workers,
            "fresh_window_s": th.heartbeat_fresh_s,
        }, RULE_ACTION[RULE_FLEET]),
        Evaluation(RULE_PULSE, pulse_stalled,
                   "P2" if pulse_unknown else SEVERITY[RULE_PULSE], {
                       "pulse_last_ts": iso(s.pulse_last_ts),
                       "pulse_age_s": None if age is None else round(age, 1),
                       "stall_after_s": th.pulse_stall_s,
                       "observed": not pulse_unknown,
                   }, RULE_ACTION[RULE_PULSE]),
    ]


# ── 事件 + 状态机 ───────────────────────────────────────────────────────────
@dataclass
class Event:
    seq: int
    ts: datetime
    rule: str
    kind: str          # onset | fire | persist | suppressed | recovered
    severity: str
    action: Optional[str]
    evidence: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "ts": iso(self.ts), "rule": self.rule, "kind": self.kind,
                "severity": self.severity, "action": self.action, "evidence": self.evidence}


@dataclass
class RuleState:
    since: Optional[datetime] = None
    last_fire: Optional[datetime] = None
    fired_in_episode: bool = False
    suppressed_logged: bool = False
    last_note: Optional[datetime] = None
    unknown_logged: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {"since": iso(self.since), "last_fire": iso(self.last_fire),
                "fired_in_episode": self.fired_in_episode,
                "suppressed_logged": self.suppressed_logged,
                "last_note": iso(self.last_note),
                "unknown_logged": self.unknown_logged}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "RuleState":
        return cls(since=parse_ts(raw.get("since")), last_fire=parse_ts(raw.get("last_fire")),
                   fired_in_episode=bool(raw.get("fired_in_episode")),
                   suppressed_logged=bool(raw.get("suppressed_logged")),
                   last_note=parse_ts(raw.get("last_note")),
                   unknown_logged=bool(raw.get("unknown_logged")))


class Detector:
    """规则 + 持续窗 + 冷却 + 恢复。状态可序列化，以便跨进程续命与回滚。"""

    def __init__(self, thresholds: Optional[Thresholds] = None):
        self.th = thresholds or Thresholds()
        self.states: Dict[str, RuleState] = {r: RuleState() for r in RULES}
        self.seq = 0
        self.observations = 0
        self.fires = {r: 0 for r in RULES}
        self.recoveries = {r: 0 for r in RULES}
        self.last_sample: Optional[Sample] = None
        self.evaluations: List[Evaluation] = []

    def state_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "thresholds": self.th.as_dict(),
                "rules": {r: st.as_dict() for r, st in self.states.items()},
                "fires": dict(self.fires), "recoveries": dict(self.recoveries),
                "observations": self.observations,
                "last_sample": None if self.last_sample is None else self.last_sample.as_dict()}

    def load_state(self, raw: Dict[str, Any]) -> None:
        self.seq = int(raw.get("seq", 0))
        for rule, st in (raw.get("rules") or {}).items():
            if rule in self.states:
                self.states[rule] = RuleState.from_dict(st)
        for key in ("fires", "recoveries"):
            for rule, value in (raw.get(key) or {}).items():
                if rule in getattr(self, key):
                    getattr(self, key)[rule] = int(value)
        self.observations = int(raw.get("observations", 0))
        if raw.get("last_sample"):
            self.last_sample = Sample.from_dict(raw["last_sample"])

    def _emit(self, sample: Sample, rule: str, kind: str, severity: str,
              evidence: Dict[str, Any], action: Optional[str] = None) -> Event:
        self.seq += 1
        return Event(self.seq, sample.ts, rule, kind, severity, action, evidence)

    def observe(self, sample: Sample) -> List[Event]:
        """喂一个快照，返回本周期事件（规则序）。"""
        self.observations += 1
        self.last_sample = sample
        self.evaluations = evaluate_sample(sample, self.th)
        out: List[Event] = []
        for ev in self.evaluations:
            st = self.states[ev.rule]
            if not ev.active:
                if st.since is not None:
                    payload = dict(ev.evidence)
                    payload.update({"since": iso(st.since),
                                    "episode_s": round(
                                        (sample.ts - st.since).total_seconds(), 1),
                                    "fired": st.fired_in_episode})
                    self.recoveries[ev.rule] += 1
                    out.append(self._emit(sample, ev.rule, "recovered", "INFO", payload))
                # 观测盲区（如脉搏行缺失）：不是停滞，也不是 clear——单独留痕一次
                blind = ev.evidence.get("observed") is False
                if blind and not st.unknown_logged:
                    st.unknown_logged = True
                    out.append(self._emit(sample, ev.rule, "unknown", "P2",
                                          dict(ev.evidence)))
                self.states[ev.rule] = RuleState(
                    last_fire=st.last_fire,
                    unknown_logged=(st.unknown_logged if blind else False))
                continue
            if st.since is None:
                st.since = sample.ts
                out.append(self._emit(sample, ev.rule, "onset", ev.severity,
                                      dict(ev.evidence)))
            elapsed = (sample.ts - st.since).total_seconds()
            if st.fired_in_episode:
                if st.last_note is None or \
                        (sample.ts - st.last_note).total_seconds() >= self.th.persist_log_s:
                    st.last_note = sample.ts
                    payload = dict(ev.evidence)
                    payload.update({"sustained_s": round(elapsed, 1), "since": iso(st.since)})
                    out.append(self._emit(sample, ev.rule, "persist", "INFO", payload))
                continue
            if elapsed < self.th.sustain_for(ev.rule):
                continue
            cooling = (st.last_fire is not None and
                       (sample.ts - st.last_fire).total_seconds() < self.th.cooldown_s)
            if cooling:
                if not st.suppressed_logged:
                    st.suppressed_logged = True
                    payload = dict(ev.evidence)
                    payload.update({"sustained_s": round(elapsed, 1), "since": iso(st.since),
                                    "last_fire": iso(st.last_fire),
                                    "cooldown_s": self.th.cooldown_s})
                    out.append(self._emit(sample, ev.rule, "suppressed", ev.severity, payload))
                continue
            st.fired_in_episode = True
            st.last_fire = sample.ts
            st.suppressed_logged = False
            self.fires[ev.rule] += 1
            payload = dict(ev.evidence)
            payload.update({"sustained_s": round(elapsed, 1), "since": iso(st.since),
                            "sustain_gate_s": self.th.sustain_for(ev.rule)})
            out.append(self._emit(sample, ev.rule, "fire", ev.severity, payload,
                                  action=ev.action))
        return out


# ── 审计链 ──────────────────────────────────────────────────────────────────
def _canon(record: Dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class AuditLog:
    """append-only JSONL，逐条 hash 链（prev_sha → sha），可机械验证未被改写。"""

    def __init__(self, path: Path, enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled
        self.prev_sha = "0" * 64
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self.prev_sha = _chain_head(self.path)

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(record)
        body["prev_sha"] = self.prev_sha
        body["sha"] = hashlib.sha256((self.prev_sha + _canon(record)).encode()).hexdigest()
        self.prev_sha = body["sha"]
        if self.enabled:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(body, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return body


def _chain_head(path: Path) -> str:
    head = "0" * 64
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                head = json.loads(line).get("sha", head)
            except json.JSONDecodeError:
                continue
    return head


def verify_chain(path: Path) -> Dict[str, Any]:
    """重算整条链；{ok, records, first_bad, head}。篡改/截断都会被指出。"""
    path = Path(path)
    prev, n = "0" * 64, 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return {"ok": False, "records": n, "first_bad": n, "reason": "unparsable"}
            got, got_prev = rec.pop("sha", None), rec.pop("prev_sha", None)
            want = hashlib.sha256((prev + _canon(rec)).encode()).hexdigest()
            if got_prev != prev or got != want:
                return {"ok": False, "records": n, "first_bad": n, "reason": "hash_mismatch"}
            prev = got
    return {"ok": True, "records": n, "first_bad": None, "head": prev}


# ── 动作面（子进程；sh/popen 可注入 → 可测、可 dry）─────────────────────────
class Ops:
    """补给器 / 舰队重启 / 自证存活。

    mode: off（什么都不做）| dry（只读探活 + 返回 would_*，不改系统）|
    live（真动作）。dry 仍允许只读探活（pgrep/systemctl cat）——
    「不动作」不等于「不检查」。
    """

    def __init__(self, mode: str = "dry", tp_root: Optional[Path] = None,
                 out_dir: Optional[Path] = None,
                 sh: Optional[Callable[[Sequence[str]], Any]] = None,
                 popen: Optional[Callable[[Sequence[str], Path], Any]] = None):
        if mode not in {"off", "dry", "live"}:
            raise ValueError(f"action mode 必须是 off/dry/live，收到 {mode!r}")
        self.mode = mode
        self.tp_root = Path(tp_root) if tp_root else TP_ROOT
        self.out_dir = Path(out_dir) if out_dir else REPO_ROOT / "runs" / "stall_detector"
        self._sh = sh or self._default_sh
        self._popen = popen or self._default_popen
        self.calls: List[List[str]] = []

    @staticmethod
    def _default_sh(argv: Sequence[str]) -> Any:
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=60)

    @staticmethod
    def _default_popen(argv: Sequence[str], log_path: Path) -> Any:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        fh = Path(log_path).open("ab")
        return subprocess.Popen(list(argv), stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)

    def _sh_calls(self, argv: Sequence[str]) -> Any:
        self.calls.append(list(argv))
        return self._sh(argv)

    def _pgrep(self, pattern: str) -> List[str]:
        try:
            out = self._sh_calls(["pgrep", "-af", pattern]).stdout or ""
            return [ln.strip() for ln in out.splitlines() if ln.strip()]
        except Exception as exc:  # noqa: BLE001  探活失败=未知，不当成缺席
            return [f"<pgrep_error:{exc!r}>"]

    @staticmethod
    def _first_existing(cands: Sequence[Path]) -> Path:
        for cand in cands:
            if Path(cand).exists():
                return Path(cand)
        return Path(cands[0])

    @property
    def refill_script(self) -> Path:
        return self._first_existing((self.tp_root / "ops/taskplane/selfstarter.py",
                                     self.tp_root / "selfstarter.py",
                                     REPO_ROOT / "ops/taskplane/selfstarter.py"))

    @property
    def supervisor_script(self) -> Path:
        return self._first_existing((self.tp_root / "ops/taskplane/fleet_supervisor.sh",
                                     self.tp_root / "fleet_supervisor.sh",
                                     REPO_ROOT / "ops/taskplane/fleet_supervisor.sh"))

    def ensure_refiller(self) -> Dict[str, Any]:
        """R1 动作：确保补给器（selfstarter）活着；已在跑则据实记 already_running。"""
        alive = self._pgrep("selfstarter.py")
        real = [ln for ln in alive if not ln.startswith("<pgrep_error")]
        info: Dict[str, Any] = {"action": "refill", "refiller_running": bool(real),
                                "refiller_script": str(self.refill_script)}
        if real:
            info.update({"status": "already_running", "procs": real[:5],
                         "note": "补给器在跑仍断粮 ⇒ 饥饿不是'没人拉车'而是'没货可拉'，P1 保留"})
            return info
        argv = ["/usr/bin/python3", str(self.refill_script)]
        if self.mode == "off":
            info.update({"status": "skipped", "reason": "action mode=off", "cmd": argv})
            return info
        if self.mode == "dry":
            info.update({"status": "would_launch", "cmd": argv})
            return info
        try:
            proc = self._popen(argv, self.out_dir / "refill_launch.log")
            info.update({"status": "launched", "cmd": argv,
                         "pid": getattr(proc, "pid", None)})
        except Exception as exc:  # noqa: BLE001
            info.update({"status": "launch_failed", "error": repr(exc), "cmd": argv})
        return info

    def restart_fleet_supervisor(self, unit: str = "tp-fleet-supervisor",
                                 script: Optional[Path] = None) -> Dict[str, Any]:
        """R2 动作：systemd unit 优先，否则 TERM + 分离重启（沿用 autoheal 口径）。"""
        script = Path(script) if script else self.supervisor_script
        info: Dict[str, Any] = {"action": "fleet_restart", "supervisor_script": str(script)}
        try:
            has_unit = self._sh_calls(["systemctl", "cat", unit]).returncode == 0
        except Exception:  # noqa: BLE001  容器里无 systemd = 无 unit
            has_unit = False
        info["unit"] = unit if has_unit else None
        if self.mode == "off":
            info.update({"status": "skipped", "reason": "action mode=off"})
            return info
        if has_unit:
            cmd = ["systemctl", "restart", unit]
            if self.mode == "dry":
                info.update({"status": "would_restart", "route": "systemd", "cmd": cmd})
                return info
            proc = self._sh_calls(cmd)
            info.update({"status": "restarted" if proc.returncode == 0 else "restart_failed",
                         "route": "systemd", "cmd": cmd, "returncode": proc.returncode,
                         "stderr": (proc.stderr or "").strip()[:200]})
            return info
        running = self._pgrep("fleet_supervisor")
        info.update({"route": "pgrep_kill_relaunch", "running_before": len(running)})
        cmd = ["/usr/bin/bash", str(script)]
        if self.mode == "dry":
            info.update({"status": "would_relaunch", "cmd": cmd})
            return info
        killed = 0
        for line in running:
            if line.startswith("<pgrep_error"):
                continue
            pid = line.split()[0]
            if pid.isdigit() and self._sh_calls(["kill", "-TERM", pid]).returncode == 0:
                killed += 1
        rc = self._sh_calls(cmd)
        info.update({"status": "relaunched" if rc.returncode == 0 else "relaunch_failed",
                     "killed": killed, "cmd": cmd, "returncode": rc.returncode})
        return info

    def write_beacon(self, payload: Dict[str, Any]) -> Path:
        """原子写 beacon：观测机制的心跳（外部判死用 age > 2×interval）。"""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / "beacon.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def selfproof(self, sample: Sample, seq: int) -> Dict[str, Any]:
        """R3 动作：「调节器 daemon 自证存活」。

        脉搏表停 ≥30 min 时 beacon 仍按周期前进 ⇒ 死的是脉搏生产者，不是观测者；
        反之 beacon 也停 ⇒ 观测者本身死了（外部看门狗据此升级）。所以自证动作不是
        「写一条日志」，而是把**机制存活**与**被观测对象停摆**解耦。
        """
        payload = {
            "mechanism": "stall_detector",
            "mechanism_alive": True,
            "selfproof": True,
            "seq": seq,
            "pid": os.getpid(),
            "ts": iso(sample.ts),
            "observed_pulse_last_ts": iso(sample.pulse_last_ts),
            "observed_pulse_age_s": (None if sample.pulse_age_s is None
                                     else round(sample.pulse_age_s, 1)),
            "statement": "观测机制存活；脉搏生产者停摆（两者由此可分）",
        }
        path = self.write_beacon(payload)
        return {"action": "selfproof", "status": "beacon_written", "path": str(path),
                "pulse_age_s": payload["observed_pulse_age_s"]}


def check_beacon(path: Path, max_age_s: float, now: Optional[datetime] = None) -> Dict[str, Any]:
    """外部视角：beacon 老化 ≤ max_age_s ⇒ 检测器活着。缺失/过期 ⇒ 升级。"""
    now = now or datetime.now(UTC)
    path = Path(path)
    if not path.exists():
        return {"alive": False, "reason": "missing", "age_s": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"alive": False, "reason": "unparsable", "age_s": None}
    stamped = parse_ts(payload.get("ts"))
    if stamped is None:
        return {"alive": False, "reason": "no_ts", "age_s": None}
    age = (now - stamped).total_seconds()
    return {"alive": age <= max_age_s, "reason": "fresh" if age <= max_age_s else "stale",
            "age_s": round(age, 1), "payload": payload}


# ── 台账取数（只读 SELECT，psql 可注入）─────────────────────────────────────
def _status_list(statuses: Sequence[str]) -> str:
    clean = []
    for st in statuses:
        token = str(st).strip().lower()
        if not token or not all(ch.isalnum() or ch == "_" for ch in token):
            raise ValueError(f"非法 pending 状态词：{st!r}")
        clean.append(token)
    if not clean:
        raise ValueError("pending 状态白名单不能为空")
    return ",".join("'" + st + "'" for st in clean)


def _default_psql(sql: str) -> str:
    """只读 SELECT，凭据口径与 tools/obs/obs_digest.py 一致（env → 本地 socket）。"""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PGPASSWORD": os.environ.get("TP_PG_PASSWORD", ""),
        "PGHOST": os.environ.get("TP_PG_HOST", "127.0.0.1"),
        "PGUSER": os.environ.get("TP_PG_USER", "foundation"),
        "PGDATABASE": os.environ.get("TP_PG_DB", "tensorplatform"),
    }
    attempts = [(["psql", "-tAc", sql], env),
                (["runuser", "-u", "postgres", "--", "psql",
                  "-d", env["PGDATABASE"], "-tAc", sql], {"PATH": env["PATH"]})]
    last = ""
    for cmd, cmd_env in attempts:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, env=cmd_env, timeout=120)
        except FileNotFoundError as exc:
            last = repr(exc)
            continue
        if proc.returncode == 0:
            return proc.stdout
        last = (proc.stderr or "").strip()[:300]
    raise RuntimeError(f"psql failed: {last}")


SQL_SUPPLY = """
select json_build_object(
  'dag_pending', (select count(*) from campaign_nodes where lower(status) in ({statuses})),
  'plan_pending', (select count(*) from campaign_plan_nodes where lower(status) in ({statuses})),
  'queued_missions', (select count(*) from tensor_dev_missions where status = 'queued'),
  'dag_status_hist', (select coalesce(json_object_agg(status, n), '{{}}'::json)
                      from (select status, count(*) n from campaign_nodes group by 1) s),
  'plan_status_hist', (select coalesce(json_object_agg(status, n), '{{}}'::json)
                       from (select status, count(*) n from campaign_plan_nodes group by 1) s)
)
"""

SQL_WORKERS = """
select json_build_object(
  'enrolled', count(*) filter (where enabled),
  'fresh', count(*) filter (where enabled
      and heartbeat_at > now() - interval '{fresh} seconds'),
  'idle_rows', count(*) filter (where enabled and state = 'idle'
      and current_mission_id is null)
) from factory_resident_workers
"""

SQL_BUSY = """
select json_build_object(
  'claimants', count(distinct coalesce(claimed_by, claimed_user_id::text)),
  'running', count(*)
) from tensor_dev_missions
where status in ('running', 'claimed')
  and heartbeat_at > now() - interval '{fresh150} seconds'
"""

SQL_PULSE = ("select coalesce(to_char(max(ts) at time zone 'UTC', "
             "'YYYY-MM-DD\"T\"HH24:MI:SS'), '') from saturation_pulse")


def collect_from_db(psql: Optional[Callable[[str], str]] = None,
                    now: Optional[datetime] = None,
                    statuses: Sequence[str] = DISPATCHABLE_DEFAULT,
                    th: Optional[Thresholds] = None,
                    source: str = "db:read-only") -> Sample:
    """从平台台账取一个快照（只读）。psql 可注入 → 无 DB 也能测。"""
    th = th or Thresholds()
    q = psql or _default_psql
    now = now or datetime.now(UTC)
    supply = json.loads(q(SQL_SUPPLY.format(statuses=_status_list(statuses))))
    workers = json.loads(q(SQL_WORKERS.format(fresh=th.heartbeat_fresh_s)))
    busy = json.loads(q(SQL_BUSY.format(fresh150=FRESH_MISSION_S)))
    pulse_raw = q(SQL_PULSE).strip()
    pulse_last = parse_ts(pulse_raw) if pulse_raw else None

    enrolled = int(workers.get("enrolled") or 0)
    fresh = int(workers.get("fresh") or 0)
    claimants = int(busy.get("claimants") or 0)
    online = max(fresh, claimants)
    # idle = online − busy，与 tools/pulse 的 online−busy=idle 口径一致；
    # 纯 worker 表的 state='idle' 不可信（IN15-O1：满负荷时 54 行仍自述 idle）。
    idle = max(0, online - claimants)
    detail = {
        "idle_rows_selfreported": int(workers.get("idle_rows") or 0),
        "running_missions_fresh": int(busy.get("running") or 0),
        "dag_status_hist": supply.get("dag_status_hist"),
        "plan_status_hist": supply.get("plan_status_hist"),
        "dispatchable_statuses": list(statuses),
        "reconciliation_note": "idle=online−busy（worker 表自述 idle 仅作对账，不作判据）",
    }
    return Sample(ts=now, dag_pending=int(supply.get("dag_pending") or 0),
                  plan_pending=int(supply.get("plan_pending") or 0),
                  queued_missions=int(supply.get("queued_missions") or 0),
                  workers_enrolled=enrolled, heartbeat_fresh=fresh,
                  workers_online=online, workers_idle=idle,
                  pulse_last_ts=pulse_last, detail=detail, source=source)# ── digest ──────────────────────────────────────────────────────────────────
def render_digest(sample: Optional[Sample], events: Sequence[Event],
                  actions: Dict[str, Dict[str, Any]], th: Thresholds,
                  audit_head: str, cycle: int, extra: Optional[Dict[str, Any]] = None) -> str:
    """一页 alarm digest（接 obs 告警面：md 可直接进 digest 流，actions 可进 alerts json）。"""
    fires = [e for e in events if e.kind == "fire"]
    lines = [
        "# stall detector digest（停摆自检自愈 · IN20-R5）",
        "",
        f"- cycle: {cycle}",
        f"- as-of: {iso(sample.ts) if sample else '—'}",
        f"- 观测机制: `stall_detector` pid={os.getpid()}（独立于人类/AI 会话）",
        f"- audit head: `{audit_head[:16]}…`（整链见 audit.jsonl / verify_chain）",
        "",
        "## 1. 本周期判读",
        "",
        "| 规则 | 判据 | 本周期 |",
        "|---|---|---|",
    ]
    thr_txt = {
        RULE_STARVED: (f"pending=0 且 idle≥{th.starve_min_idle} 且 idle/online≥"
                       f"{th.starve_idle_ratio} 持续 {th.starve_sustain_s}s"),
        RULE_FLEET: (f"失联比≥{th.fleet_loss_ratio} 且席位≥{th.fleet_min_workers} 持续 "
                     f"{th.fleet_sustain_s}s"),
        RULE_PULSE: f"脉搏老化≥{th.pulse_stall_s}s",
    }
    for rule in RULES:
        kinds = [e.kind for e in events if e.rule == rule]
        verdict = "fire" if "fire" in kinds else ("active" if "onset" in kinds else "clear")
        lines.append(f"| {RULE_NAME[rule]} | {thr_txt[rule]} | **{verdict}** |")
    lines += ["", "## 2. 快照（只读台账）", ""]
    if sample:
        age = sample.pulse_age_s
        lines += [
            "| 量 | 值 |", "|---|---|",
            f"| pending DAG 节点 | {sample.dag_pending} |",
            f"| pending plan 节点 | {sample.plan_pending} |",
            f"| 排队任务 | {sample.queued_missions} |",
            f"| 在线席位 | {sample.workers_online} |",
            f"| 空闲席位 | {sample.workers_idle}（idle/online={sample.idle_ratio:.2f}）|",
            f"| 心跳新鲜/登记 | {sample.heartbeat_fresh}/{sample.workers_enrolled}"
            f"（失联比={sample.lost_ratio:.2f}）|",
            f"| 脉搏最后一行 | {iso(sample.pulse_last_ts) or '无观测'}"
            f"（age={'—' if age is None else str(round(age, 1)) + 's'}）|",
            f"| 取数来源 | {sample.source} |",
        ]
    lines += ["", "## 3. 事件", ""]
    if events:
        lines += ["| ts | 规则 | kind | 动作 | 证据 |", "|---|---|---|---|---|"]
        for ev in events:
            ev_txt = ", ".join(f"{k}={v}" for k, v in ev.evidence.items()
                               if k in ("sustained_s", "since", "idle_ratio", "lost_ratio",
                                        "pulse_age_s", "episode_s", "observed"))
            lines.append(f"| {iso(ev.ts)} | {ev.rule} | {ev.kind} | {ev.action or '—'} "
                         f"| {ev_txt} |")
    else:
        lines.append("_本周期无事件（健康态）。观测次数见 audit：`无事件 ≠ 未检查`_")
    if actions:
        lines += ["", "## 4. 动作回执", ""]
        for rule, info in actions.items():
            lines.append(f"- **{rule}** → `{info.get('action')}` status=`{info.get('status')}` "
                         f"route=`{info.get('route', '—')}`")
    if fires:
        lines += ["", "## 5. P1 摘要", ""]
        for ev in fires:
            lines.append(f"- {iso(ev.ts)} **{ev.rule}**（{RULE_NAME[ev.rule]}）"
                         f"→ 动作 `{ev.action}`")
    if extra:
        lines += ["", "## 6. 附加", ""]
        for key, value in extra.items():
            lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


# ── daemon 编排 ─────────────────────────────────────────────────────────────
class StallDaemon:
    """体检循环：观测 → 规则 → 动作 → 审计 / beacon / digest。"""

    def __init__(self, detector: Optional[Detector] = None, ops: Optional[Ops] = None,
                 out_dir: Optional[Path] = None, execute_actions: bool = True,
                 digest_enabled: bool = True):
        self.out_dir = Path(out_dir) if out_dir else REPO_ROOT / "runs" / "stall_detector"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.detector = detector or Detector()
        self.ops = ops or Ops(mode="dry", out_dir=self.out_dir)
        self.action_mode = self.ops.mode
        self.execute_actions = execute_actions
        self.digest_enabled = digest_enabled
        self.state_path = self.out_dir / "state.json"
        self.audit = AuditLog(self.out_dir / "audit.jsonl")
        self.cycle = 0
        self.total_fires = 0
        self.all_events: List[Event] = []
        self.last_actions: Dict[str, Dict[str, Any]] = {}
        self.action_log: List[Dict[str, Any]] = []

    # 状态持久化（重启不重复 fire；回滚有据）
    def load_state(self) -> bool:
        if not self.state_path.exists():
            return False
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        self.detector.load_state(raw.get("detector") or {})
        self.cycle = int(raw.get("cycle", 0))
        return True

    def save_state(self) -> None:
        payload = {"schema": "stall_detector/1", "cycle": self.cycle,
                   "action_mode": self.action_mode,
                   "detector": self.detector.state_dict()}
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _act(self, ev: Event, sample: Sample) -> Dict[str, Any]:
        if not self.execute_actions:
            return {"action": ev.action, "status": "not_executed",
                    "reason": "execute_actions=False（回放/演练）"}
        if ev.action == "refill":
            return self.ops.ensure_refiller()
        if ev.action == "fleet_restart":
            return self.ops.restart_fleet_supervisor()
        if ev.action == "selfproof":
            return self.ops.selfproof(sample, ev.seq)
        return {"action": ev.action, "status": "unknown_action"}

    def step(self, sample: Sample) -> Dict[str, Any]:
        """一个体检周期。返回 {cycle, sample, events, actions, digest_path}。"""
        self.cycle += 1
        events = self.detector.observe(sample)
        self.all_events.extend(events)
        # 每周期一条观测留痕：「没告警」必须能与「没检查」区分（判据 2）
        self.audit.append({"kind": "observation", "cycle": self.cycle,
                           "ts": iso(sample.ts), "action_mode": self.action_mode,
                           "pending_nodes": sample.pending_nodes,
                           "queued_missions": sample.queued_missions,
                           "workers_online": sample.workers_online,
                           "workers_idle": sample.workers_idle,
                           "idle_ratio": round(sample.idle_ratio, 4),
                           "lost_ratio": round(sample.lost_ratio, 4),
                           "pulse_age_s": (None if sample.pulse_age_s is None
                                           else round(sample.pulse_age_s, 1)),
                           "rules_active": [e.rule for e in self.detector.evaluations
                                            if e.active]})
        # 基础 beacon：每个周期都打点，外部据此判「观测机制是否活着」。
        # 脉搏停摆期间每条 beacon 同时是「自证存活」：生产者停 vs 观测者停由此可分。
        pulse_stale = any(ev.active and ev.rule == RULE_PULSE
                          for ev in self.detector.evaluations)
        self.ops.write_beacon({
            "mechanism": "stall_detector", "mechanism_alive": True,
            "selfproof": pulse_stale,
            "cycle": self.cycle, "pid": os.getpid(), "ts": iso(sample.ts),
            "observed_pulse_last_ts": iso(sample.pulse_last_ts),
            "observed_pulse_age_s": (None if sample.pulse_age_s is None
                                     else round(sample.pulse_age_s, 1)),
            "rules_active": [e.rule for e in self.detector.evaluations if e.active],
            "fires_total": sum(self.detector.fires.values()),
        })
        actions: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            self.audit.append({"kind": "event", "cycle": self.cycle,
                               "action_mode": self.action_mode, **ev.as_dict()})
            if ev.kind == "fire":
                self.total_fires += 1
                result = self._act(ev, sample)
                actions[ev.rule] = result
                self.action_log.append({"cycle": self.cycle, "ts": iso(ev.ts),
                                        "rule": ev.rule, "action": ev.action,
                                        "result": result})
                self.audit.append({"kind": "action", "cycle": self.cycle, "ts": iso(ev.ts),
                                   "rule": ev.rule, "action": ev.action,
                                   "action_mode": self.action_mode, "result": result})
        self.last_actions = actions
        digest_path = None
        if actions and self.digest_enabled:
            digest_path = self.write_digest(sample, events, actions)
        self.save_state()
        return {"cycle": self.cycle, "sample": sample.as_dict(), "events": events,
                "actions": actions, "digest_path": digest_path}

    def write_digest(self, sample: Optional[Sample], events: Sequence[Event],
                     actions: Dict[str, Dict[str, Any]],
                     name: Optional[str] = None) -> Path:
        text = render_digest(sample, events, actions, self.detector.th,
                             self.audit.prev_sha, self.cycle,
                             extra={"state": str(self.state_path),
                                    "thresholds": json.dumps(self.detector.th.as_dict(),
                                                             ensure_ascii=False)})
        path = self.out_dir / (name or "digest.md")
        path.write_text(text, encoding="utf-8")
        self.audit.append({"kind": "digest", "cycle": self.cycle,
                           "path": str(path),
                           "sha256": hashlib.sha256(text.encode()).hexdigest()})
        return path

    def write_alerts(self, sample: Optional[Sample], events: Sequence[Event],
                     actions: Dict[str, Dict[str, Any]], name: str = "alerts.json") -> Path:
        """机读告警面（接 obs）：fires 即 P1 alerts；suppressions/observations 一并留证。"""
        payload = {
            "schema": "stall_detector/alerts/1",
            "cycle": self.cycle,
            "as_of": None if sample is None else iso(sample.ts),
            "action_mode": self.action_mode,
            "audit_head": self.audit.prev_sha,
            "thresholds": self.detector.th.as_dict(),
            "alerts": [{"rule": e.rule, "severity": e.severity, "ts": iso(e.ts),
                        "action": e.action, "evidence": e.evidence,
                        "action_result": actions.get(e.rule)}
                       for e in events if e.kind == "fire"],
            "suppressions": [{"rule": e.rule, "ts": iso(e.ts), "evidence": e.evidence}
                             for e in events if e.kind == "suppressed"],
            "observations": self.detector.observations,
            "sample": None if sample is None else sample.as_dict(),
        }
        path = self.out_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def rollback(out_dir: Path) -> Dict[str, Any]:
    """回滚路径：清所有闸闩（since/fired/cooldown），保留历史审计与 state 归档。

    机制替身必须能被「无限期关掉而不留后遗症」：清 latch 后 daemon 下次启动
    等于新装状态机；审计链只 append 不删除，回滚动作本身也是一条审计记录。
    """
    out_dir = Path(out_dir)
    state_path = out_dir / "state.json"
    audit = AuditLog(out_dir / "audit.jsonl")
    archived = None
    if state_path.exists():
        archived = out_dir / f"state.rolled_back.{int(time.time())}.json"
        archived.write_text(state_path.read_text(encoding="utf-8"), encoding="utf-8")
    fresh = Detector().state_dict()
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema": "stall_detector/1", "cycle": 0,
                               "action_mode": "off", "detector": fresh},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)
    record = audit.append({"kind": "rollback", "ts": iso(datetime.now(UTC)),
                           "archived_state": None if archived is None else str(archived),
                           "note": "latch 全清；审计与历史不删"})
    return {"ok": True, "archived_state": None if archived is None else str(archived),
            "audit_sha": record["sha"], "state": str(state_path)}


# ── 判定（回放/单次共用）──────────────────────────────────────────────────
def verdict_from_events(events: Sequence[Event], observations: int) -> Dict[str, Any]:
    fires = [e for e in events if e.kind == "fire"]
    by_rule: Dict[str, Any] = {}
    for rule in RULES:
        mine = [e for e in fires if e.rule == rule]
        by_rule[rule] = {"fired": bool(mine), "count": len(mine),
                         "first_fire_ts": iso(mine[0].ts) if mine else None,
                         "action": mine[0].action if mine else None}
    return {"observations": observations, "events": len(events), "fires": len(fires),
            "by_rule": by_rule,
            "alarm": bool(fires),
            "kinds": {k: sum(1 for e in events if e.kind == k)
                      for k in ("onset", "fire", "persist", "suppressed", "recovered")}}


def run_replay(fixture_path: Path, daemon: StallDaemon,
               statuses: Sequence[str] = DISPATCHABLE_DEFAULT) -> Dict[str, Any]:
    """按 fixture 的样本序列逐周期回放（显式时钟，不读墙钟）。"""
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    samples = [Sample.from_dict(raw) for raw in fixture.get("samples") or []]
    if not samples:
        raise ValueError(f"fixture 无样本：{fixture_path}")
    for th_key, value in (fixture.get("thresholds") or {}).items():
        if hasattr(daemon.detector.th, th_key):
            setattr(daemon.detector, "th", daemon.detector.th.__class__(
                **{**daemon.detector.th.as_dict(), th_key: value}))
    daemon.audit.append({"kind": "replay_start", "fixture": str(fixture_path),
                         "fixture_sha256": hashlib.sha256(
                             Path(fixture_path).read_bytes()).hexdigest(),
                         "samples": len(samples),
                         "provenance": fixture.get("provenance"),
                         "dispatchable_statuses": list(statuses)})
    last: Dict[str, Any] = {}
    per_sample = []
    for sample in samples:
        last = daemon.step(sample)
        per_sample.append({"ts": iso(sample.ts),
                           "events": [e.as_dict() for e in last["events"]]})
    verdict = verdict_from_events(daemon.all_events, daemon.detector.observations)
    verdict["fixture"] = str(fixture_path)
    verdict["action_log"] = list(daemon.action_log)
    verdict["as_of_range"] = [iso(samples[0].ts), iso(samples[-1].ts)]
    (daemon.out_dir / "replay_trace.json").write_text(
        json.dumps({"provenance": fixture.get("provenance"),
                    "samples": per_sample, "verdict": verdict,
                    "thresholds": daemon.detector.th.as_dict()},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict


# ── CLI ─────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="停摆自检自愈 daemon（IN20-R5）")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-db", action="store_true", help="从平台台账只读取一个快照")
    src.add_argument("--fixture", type=Path, help="从时间线 fixture 取样本")
    src.add_argument("--rollback", action="store_true", help="清闸闩（回滚路径）")
    src.add_argument("--check-beacon", type=Path,
                     help="外部视角：判 beacon 老化（不读台账，只回答机制是否活着）")
    p.add_argument("--once", action="store_true", help="只跑一个周期")
    p.add_argument("--daemon", action="store_true", help="周期循环（生产 daemon）")
    p.add_argument("--replay", action="store_true", help="回放 fixture 全部样本")
    p.add_argument("--interval", type=int, default=60, help="daemon 周期秒（默认 60）")
    p.add_argument("--cycles", type=int, default=0, help="daemon 跑几轮后退出（0=无限）")
    p.add_argument("--out", type=Path,
                   default=REPO_ROOT / "runs" / "stall_detector",
                   help="产物目录（audit/beacon/state/digest）")
    p.add_argument("--action-mode", choices=("off", "dry", "live"), default="dry")
    p.add_argument("--no-execute-actions", action="store_true",
                   help="只判定不执行动作（演练/回放）")
    p.add_argument("--pending-statuses", default=",".join(DISPATCHABLE_DEFAULT),
                   help="可派 DAG 节点状态白名单（逗号分隔）")
    p.add_argument("--json-out", type=Path, help="把本次判定写成 JSON")
    p.add_argument("--max-age", type=float, default=300.0,
                   help="--check-beacon 的存活窗（秒，默认 300 = 5×周期）")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    statuses = [s for s in args.pending_statuses.split(",") if s.strip()]
    th = Thresholds.from_env()
    detector = Detector(th)
    ops = Ops(mode=args.action_mode, out_dir=out_dir)
    daemon = StallDaemon(detector=detector, ops=ops, out_dir=out_dir,
                         execute_actions=not args.no_execute_actions)
    daemon.load_state()

    if args.check_beacon:
        result = check_beacon(args.check_beacon, args.max_age)
        if not args.quiet:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["alive"] else 2

    if args.rollback:
        result = rollback(out_dir)
        if not args.quiet:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.fixture:
        verdict = run_replay(args.fixture, daemon, statuses)
        alerts = daemon.write_alerts(None, daemon.all_events, daemon.last_actions)
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(verdict, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        if not args.quiet:
            print(json.dumps(verdict, ensure_ascii=False, indent=2))
            print(f"alerts: {alerts}")
        return 2 if verdict["alarm"] else 0

    def one_cycle() -> Dict[str, Any]:
        sample = collect_from_db(statuses=statuses, th=th)
        result = daemon.step(sample)
        daemon.write_alerts(sample, result["events"], result["actions"])
        return result

    if args.daemon:
        rounds = 0
        while True:
            try:
                result = one_cycle()
                rounds += 1
                if not args.quiet:
                    fires = [e.as_dict() for e in result["events"] if e.kind == "fire"]
                    print(json.dumps({"cycle": result["cycle"], "as_of":
                                      result["sample"]["ts"], "fires": fires,
                                      "actions": result["actions"]},
                                     ensure_ascii=False))
                if args.cycles and rounds >= args.cycles:
                    return 2 if result["actions"] else 0
            except Exception as exc:  # noqa: BLE001  fail closed：不静默、不退出
                err = {"kind": "cycle_error", "cycle": daemon.cycle + 1, "error": repr(exc),
                       "ts": iso(datetime.now(UTC))}
                daemon.audit.append(err)
                print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
                if args.cycles and rounds >= args.cycles:
                    return 1
            time.sleep(max(5, args.interval))

    result = one_cycle()
    verdict = verdict_from_events(result["events"], detector.observations)
    verdict["as_of"] = result["sample"]["ts"]
    verdict["actions"] = result["actions"]
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(verdict, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    if not args.quiet:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 2 if verdict["alarm"] else 0


if __name__ == "__main__":
    sys.exit(main())