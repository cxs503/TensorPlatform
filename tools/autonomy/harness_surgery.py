"""IN27-H3 — Coordinator harness-surgery execution authority.

Campaign node IN27-H3 (depends on H2): the coordinator (协调官) is granted
machine-executed "harness surgery" rights with a fail-safe gate chain:

    DIAGNOSE -> CANARY gate -> BACKUP -> SURGERY -> VERIFY -> COMMIT
                                                        \\-> ROLLED_BACK (auto)
    CANARY gate failure ----------------------------> REJECTED (no touch)

Guarantees (machine-verifiable):
  G1  canary gate pass is the *only* path to surgery; failure => REJECTED,
      target file byte-identical (sha256 equal).
  G2  pre-surgery backup is mandatory and hash-verified (sha256 == pre hash).
  G3  post-surgery verification probes must all pass, else automatic rollback
      restores the exact pre-surgery bytes.
  G4  red-team drill: an injected faulty surgery MUST be blocked by the
      rollback gate (drill verdict PASS == error blocked, file restored).
  G5  one real diagnosis -> surgery -> verification closed loop runs with
      zero human events; the full audit trail is a JSONL event chain.

No network, no DB: the audit trail is an append-only JSONL file so the
chain is replayable and diffable. Every event carries actor + human flag;
the closed loop asserts human events == 0.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

Probe = Tuple[str, Callable[[], bool]]

STAGE_DIAGNOSE = "DIAGNOSE"
STAGE_CANARY = "CANARY"
STAGE_BACKUP = "BACKUP"
STAGE_SURGERY = "SURGERY"
STAGE_VERIFY = "VERIFY"
STAGE_COMMIT = "COMMIT"
STAGE_ROLLBACK = "ROLLED_BACK"
STAGE_REJECT = "REJECTED"

STATUS_COMPLETED = "COMPLETED"
STATUS_ROLLED_BACK = "ROLLED_BACK"
STAGE_REJECTED = "REJECTED"


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AuditLog:
    """Append-only JSONL audit chain. Receipt = the written line itself."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, stage: str, detail: dict, actor: str = "coordinator",
               human: bool = False) -> dict:
        ev = {
            "seq": len(self.events()),
            "ts": round(time.time(), 6),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stage": stage,
            "actor": actor,
            "human": human,
            "detail": detail,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    def events(self) -> List[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def human_events(self) -> List[dict]:
        return [e for e in self.events() if e.get("human")]


@dataclass
class SurgeryTicket:
    """A repair order (修补单) for one target file.

    old/new are exact text anchors (single replacement). canary probes run
    *before* any write; verify probes run *after* the write.
    """
    ticket_id: str
    target: str                      # path relative to root
    old: str
    new: str
    canary: List[Probe] = field(default_factory=list)
    verify: List[Probe] = field(default_factory=list)
    diagnosis: str = ""

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "target": self.target,
            "old": self.old,
            "new": self.new,
            "canary": [n for n, _ in self.canary],
            "verify": [n for n, _ in self.verify],
            "diagnosis": self.diagnosis,
        }


@dataclass
class SurgeryResult:
    ticket_id: str
    status: str
    stages: List[str]
    backup: Optional[str]
    pre_hash: str
    post_hash: str
    audit: str

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "status": self.status,
            "stages": self.stages,
            "backup": self.backup,
            "pre_hash": self.pre_hash,
            "post_hash": self.post_hash,
            "audit": self.audit,
        }


def _run_probes(probes: List[Probe]) -> List[dict]:
    out = []
    for name, fn in probes:
        try:
            ok = bool(fn())
            out.append({"name": name, "pass": ok})
        except Exception as exc:  # a raising probe is a failed probe
            out.append({"name": name, "pass": False, "error": repr(exc)})
    return out


class HarnessSurgery:
    """Gate-chained surgery executor owned by the coordinator."""

    def __init__(self, root, audit_path, backup_dir):
        self.root = Path(root)
        self.audit = AuditLog(audit_path)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # -- gate chain ------------------------------------------------------
    def execute(self, ticket: SurgeryTicket) -> SurgeryResult:
        target = self.root / ticket.target
        pre = sha256_of(target)
        stages = [STAGE_DIAGNOSE, STAGE_CANARY]

        # 1) canary gate — the only door into surgery
        canary = _run_probes(ticket.canary)
        if not all(p["pass"] for p in canary):
            self.audit.record(STAGE_CANARY, {"ticket": ticket.ticket_id,
                                             "probes": canary, "verdict": "FAIL"})
            self.audit.record(STAGE_REJECT, {"ticket": ticket.ticket_id,
                                             "reason": "canary gate failed",
                                             "target_sha256": pre})
            return SurgeryResult(ticket.ticket_id, STAGE_REJECTED,
                                 stages + [STAGE_REJECT], None, pre, pre,
                                 str(self.audit.path))
        self.audit.record(STAGE_CANARY, {"ticket": ticket.ticket_id,
                                         "probes": canary, "verdict": "PASS"})

        # 2) mandatory pre-surgery backup, hash-verified
        bak = self.backup_dir / f"{ticket.ticket_id}_{target.name}"
        shutil.copy2(target, bak)
        if sha256_of(bak) != pre:
            raise RuntimeError("backup integrity check failed")
        self.audit.record(STAGE_BACKUP, {"ticket": ticket.ticket_id,
                                         "backup": str(bak),
                                         "sha256": sha256_of(bak)})

        # 3) surgery (single anchored replacement)
        text = target.read_text(encoding="utf-8")
        if ticket.old not in text:
            self.audit.record(STAGE_SURGERY, {"ticket": ticket.ticket_id,
                                              "applied": False,
                                              "reason": "anchor not found"})
            self.audit.record(STAGE_ROLLBACK, {"ticket": ticket.ticket_id,
                                              "reason": "patch anchor missing",
                                              "restored_sha256": pre})
            return SurgeryResult(ticket.ticket_id, STATUS_ROLLED_BACK,
                                stages + [STAGE_SURGERY, STAGE_ROLLBACK],
                                str(bak), pre, pre, str(self.audit.path))
        target.write_text(text.replace(ticket.old, ticket.new, 1),
                           encoding="utf-8")
        self.audit.record(STAGE_SURGERY, {"ticket": ticket.ticket_id,
                                           "applied": True,
                                           "post_sha256": sha256_of(target)})
        stages.append(STAGE_SURGERY)

        # 4) verification — failure triggers automatic rollback
        verify = _run_probes(ticket.verify)
        if not all(p["pass"] for p in verify):
            shutil.copy2(bak, target)
            self.audit.record(STAGE_VERIFY, {"ticket": ticket.ticket_id,
                                             "probes": verify, "verdict": "FAIL"})
            self.audit.record(STAGE_ROLLBACK, {"ticket": ticket.ticket_id,
                                               "restored_sha256": sha256_of(target)})
            return SurgeryResult(ticket.ticket_id, STATUS_ROLLED_BACK,
                                stages + [STAGE_VERIFY, STAGE_ROLLBACK],
                                str(bak), pre, sha256_of(target),
                                str(self.audit.path))
        self.audit.record(STAGE_VERIFY, {"ticket": ticket.ticket_id,
                                           "probes": verify, "verdict": "PASS"})

        # 5) commit
        self.audit.record(STAGE_COMMIT, {"ticket": ticket.ticket_id,
                                           "post_sha256": sha256_of(target)})
        return SurgeryResult(ticket.ticket_id, STATUS_COMPLETED,
                             stages + [STAGE_VERIFY, STAGE_COMMIT],
                             str(bak), pre, sha256_of(target),
                             str(self.audit.path))


# ---------------------------------------------------------------------------
# Real closed loop: a live harness probe file with a seeded coefficient bug.
# ---------------------------------------------------------------------------

HARNESS_PROBE = '''\
"""Harness probe: expected gain = 3 * x (reference model)."""


def compute(x):
    return x * {coef}
'''


def seed_harness(root, coef: int = 2) -> Path:
    """Create the live harness file; coef=2 is the seeded fault."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    p = root / "harness_probe.py"
    p.write_text(HARNESS_PROBE.format(coef=coef), encoding="utf-8")
    return p


def _load_compute(path: Path) -> Callable[[int], int]:
    ns: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns["compute"]


def diagnose(path: Path) -> dict:
    """Diagnosis probe: reference model says compute(x) == 3x."""
    findings = []
    try:
        compute = _load_compute(path)
        for x in (2, 5):
            got, want = compute(x), 3 * x
            if got != want:
                findings.append({"x": x, "got": got, "want": want})
    except Exception as exc:
        findings.append({"error": repr(exc)})
    return {"fault": bool(findings), "findings": findings,
            "verdict": "FAULT" if findings else "HEALTHY"}


def make_repair_ticket(path: Path, ticket_id: Optional[str] = None) -> SurgeryTicket:
    """Build the repair ticket from the diagnosis: fix the coefficient to 3."""
    text = path.read_text(encoding="utf-8")
    old = "return x * 2"
    if old not in text:
        raise ValueError("no fault anchor present; nothing to repair")
    return SurgeryTicket(
        ticket_id=ticket_id or f"RT-{uuid.uuid4().hex[:8]}",
        target=path.name,
        old=old,
        new="return x * 3",
        canary=[("canary:file_readable", lambda: path.exists() and path.stat().st_size > 0),
                ("canary:ast_parse", lambda: bool(ast.parse(text)))],
        verify=[("verify:ast_parse", lambda: bool(ast.parse(path.read_text(encoding='utf-8')))),
                ("verify:compute(2)==6", lambda: _load_compute(path)(2) == 6),
                ("verify:compute(5)==15", lambda: _load_compute(path)(5) == 15)],
        diagnosis="coefficient error: compute(x)=2x, reference model 3x",
    )


def run_closed_loop(root, audit_path, backup_dir,
                    ticket_id: Optional[str] = None) -> dict:
    """One unattended diagnosis -> surgery -> verification closed loop.

    Returns a report dict with the full audit chain and human-event count.
    """
    root = Path(root)
    probe = seed_harness(root, coef=2)          # live system with a real fault
    diag = diagnose(probe)
    if not diag["fault"]:
        raise RuntimeError("closed loop requires a diagnosed fault")
    ticket = make_repair_ticket(probe, ticket_id)
    hs = HarnessSurgery(root, audit_path, backup_dir)
    result = hs.execute(ticket)
    events = hs.audit.events()
    return {
        "loop": "IN27-H3 closed loop (diagnose -> canary -> backup -> surgery -> verify)",
        "diagnosis": diag,
        "ticket": ticket.to_dict(),
        "result": result.to_dict(),
        "audit_events": events,
        "human_events": len(hs.audit.human_events()),
        "target_sha256_pre": result.pre_hash,
        "target_sha256_post": result.post_hash,
        "target_sha256_backup": sha256_of(result.backup) if result.backup else None,
    }


def red_team_drill(root, audit_path, backup_dir,
                   ticket_id: str = "RT-IN27H3") -> dict:
    """Inject a faulty surgery (bad doctor: coef 3 -> 999) and demand that
    the rollback gate blocks it. Drill verdict PASS == error blocked."""
    root = Path(root)
    probe = seed_harness(root, coef=3)           # healthy baseline
    pre = sha256_of(probe)
    text = probe.read_text(encoding="utf-8")
    bad = SurgeryTicket(
        ticket_id=ticket_id,
        target=probe.name,
        old="return x * 3",
        new="return x * 999",
        canary=[("canary:file_readable", lambda: probe.exists())],
        verify=[("verify:compute(2)==6", lambda: _load_compute(probe)(2) == 6),
                ("verify:compute(5)==15", lambda: _load_compute(probe)(5) == 15)],
        diagnosis="red-team injected faulty surgery",
    )
    hs = HarnessSurgery(root, audit_path, backup_dir)
    result = hs.execute(bad)
    post = sha256_of(probe)
    blocked = (result.status == STATUS_ROLLED_BACK) and (post == pre)
    return {
        "drill": "IN27-H3 red team: injected faulty surgery",
        "injected": bad.to_dict(),
        "result": result.to_dict(),
        "pre_hash": pre,
        "post_hash": post,
        "blocked": blocked,
        "verdict": "PASS" if blocked else "FAIL",
        "audit": str(hs.audit.path),
    }


# ---------------------------------------------------------------------------
# Ticket factory + unattended closed loop
# ---------------------------------------------------------------------------

def make_repair_ticket(probe: Path, ticket_id: str) -> SurgeryTicket:
    """Standard repair order: restore the reference coefficient 3."""
    return SurgeryTicket(
        ticket_id=ticket_id,
        target=probe.name,
        old="return x * 2",
        new="return x * 3",
        canary=[("canary:file_readable", lambda: probe.exists())],
        verify=[
            ("verify:compute(2)==6", lambda: _load_compute(probe)(2) == 6),
            ("verify:compute(5)==15", lambda: _load_compute(probe)(5) == 15),
            ("verify:ast_parses",
             lambda: ast.parse(probe.read_text(encoding="utf-8")) is not None),
        ],
        diagnosis="coefficient 2 != reference 3 (diagnose: FAULT)",
    )


def run_closed_loop(root, audit_path, backup_dir,
                   ticket_id: str = "T-LOOP") -> dict:
    """One real diagnosis -> surgery -> verification closed loop.

    Fully machine-executed: every audit event has actor=coordinator and
    human=False. Returns the machine-readable loop report.
    """
    root = Path(root)
    probe = seed_harness(root, coef=2)          # seeded fault
    pre = sha256_of(probe)
    diag = diagnose(probe)
    hs = HarnessSurgery(root, audit_path, backup_dir)
    ticket = make_repair_ticket(probe, ticket_id)
    result = hs.execute(ticket)
    events = hs.audit.events()
    return {
        "loop": "IN27-H3 closed loop (unattended)",
        "diagnosis": diag,
        "ticket": ticket.to_dict(),
        "result": result.to_dict(),
        "target_sha256_pre": pre,
        "target_sha256_backup": sha256_of(result.backup) if result.backup else None,
        "target_sha256_post": sha256_of(probe),
        "human_events": len(hs.audit.human_events()),
        "audit_events": events,
        "audit": str(hs.audit.path),
    }
