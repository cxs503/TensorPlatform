#!/usr/bin/env python3
"""Autonomy acceptance gate (IN20-G21) — 五项工件机读化。

战役节点 IN20-G21 是「续航军」的验收门：R1..R7 各自把一件自主续航机制做成熟，
本门不再重做那些机制，只做一件事——**在新分支上把它们装进同一条时间线，真跑
一遍，把每一项的可复算数字与产物路径写成一台机器能读的账单**。

五项判据（与战役 metadata 一一对应）：

  C1 补给器对停摆样例回放可自动触发
       → 真 Postgres 沙箱里回放「2026-11-07 03:18 停摆态」（pending=0、idle=8），
         `BacklogRefillerDaemon.run_once()` 自动触发并真 INSERT mission + 审计行，
         且 `AuditLogger.rollback_latest()` 能撤。
  C2 放门 / 合并两自动化各真实执行 ≥1 例并留审计
       → C2a `ops/taskplane/gate_auto_release.py` 在沙箱 schema 上真放门
         （blocked→pending + 审计 id + 幂等 + 回滚），并有负控（工件缺 / owner_only）；
         C2b `tools/merge_master` 真合并了一个 GitHub PR，审计链哈希可验，
         合并提交经 git ancestry 独立核对确在 origin/main。
  C3 propose 通道积压清零
       → 真跑 `propose_channel_guard` 判定，`stuck`（钉死模型无活体领取且超宽限）
         必须为 0；守护能力本身由该模块 pytest 用例在案证明。
  C4 退出仪表盘三指标首份基线
       → 真跑 `tools.autonomy.dashboard baseline`，三指标均 REAL 并有 chronicle 快照。
  C5 垫池四单终态入账本
       → D1..D4（m5051..m5054）为终态且能力增减账本各有条目。

设计纪律
--------
* 判据全部可复算：数字来自被测工具自己的 JSON 输出或 SQL，本模块不手抄；
* 每个 check 返回 `status ∈ {PASS, FAIL, WITHHELD}` 与 `criteria` 数字字典，
  聚合时 **不允许把 WITHHELD 当 PASS**——门只认 PASS；
* 纯函数（`classify_stuck_actions` / `pad_four_verdict` / `aggregate`）可脱库单测，
  DB 只在 I/O 层（`pytest tests/test_autonomy_acceptance_gate.py` 的负控钉死）。

用法::

    python3 -m tools.autonomy.acceptance \
        --out artifacts/autonomy_acceptance/autonomy_acceptance.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "autonomy_acceptance"

#: 平台终态集合（R4 现场定义：claim 端把 suspended 也算终态，否则永不点火）
TERMINAL_STATES = frozenset({
    "succeeded", "failed", "revoked", "stopped", "suspended",
    "awaiting_pr_merge", "done", "cancelled", "canceled",
})

#: 垫池四单（续航军 D1..D4，见 campaign plan 21 nodes）
PAD_MISSIONS = (5051, 5052, 5053, 5054)

DEFAULT_REFILL_DSN = "postgresql://postgres@/tp_gate_refill?host=/var/run/postgresql"
DEFAULT_SBX_SCHEMA = "gate_drill_sbx"

PASS, FAIL, WITHHELD = "PASS", "FAIL", "WITHHELD"


# ---------------------------------------------------------------------------
# 纯函数（可脱库单测）
# ---------------------------------------------------------------------------


def classify_stuck_actions(actions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """把 propose 守护的判定列表分成 stuck / grace / clear。

    ``stuck``：不可领取（模型无活体心跳）**且**已超守护宽限——这才是真积压，
    必须为 0。宽限内的条目是正常在途（worker 可能下一轮就抢到），不计积压。
    """
    stuck: list[dict[str, Any]] = []
    grace: list[dict[str, Any]] = []
    clear: list[dict[str, Any]] = []
    for act in actions:
        claimable = bool(act.get("claimable"))
        reason = str(act.get("reason") or "")
        if claimable:
            clear.append(dict(act))
        elif reason.startswith("grace"):
            grace.append(dict(act))
        else:
            stuck.append(dict(act))
    return {
        "stuck": stuck,
        "grace": grace,
        "clear": clear,
        "stuck_backlog": len(stuck),
        "grace_backlog": len(grace),
    }


def pad_four_verdict(
    missions: Mapping[int, str],
    ledger_rows: Mapping[int, int],
    terminal_states: Iterable[str] = TERMINAL_STATES,
    expected: Sequence[int] = PAD_MISSIONS,
) -> dict[str, Any]:
    """垫池四单判据：四单都在终态，且账本各有条目（缺一即 FAIL）。"""
    terminal = set(terminal_states)
    per_mission = {}
    for mid in expected:
        status = missions.get(mid)
        rows = int(ledger_rows.get(mid, 0))
        per_mission[str(mid)] = {
            "status": status,
            "terminal": status in terminal,
            "ledger_rows": rows,
            "ok": status in terminal and rows > 0,
        }
    ok_count = sum(1 for v in per_mission.values() if v["ok"])
    return {
        "per_mission": per_mission,
        "expected": len(expected),
        "terminal_and_booked": ok_count,
        "ok": ok_count == len(expected),
    }


def aggregate(checks: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """五项聚合：只有全 PASS 才是 PASS；任何 WITHHELD 不得当 PASS。"""
    statuses = {k: v.get("status", WITHHELD) for k, v in checks.items()}
    failing = [k for k, s in statuses.items() if s == FAIL]
    withheld = [k for k, s in statuses.items() if s == WITHHELD]
    if failing:
        verdict = FAIL
    elif withheld:
        verdict = WITHHELD
    else:
        verdict = PASS
    return {
        "verdict": verdict,
        "criteria_total": len(statuses),
        "criteria_pass": sum(1 for s in statuses.values() if s == PASS),
        "failing": failing,
        "withheld": withheld,
        "statuses": statuses,
    }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _refill_engine(dsn: str):
    """把 DSN 变成可连的 SQLAlchemy engine（含本机 socket 方言兜底）。

    踩坑记录（2026-11-07）：SQLAlchemy 2.0 + psycopg 3.3 在本机 loong64 环境
    解析 ``select version()`` 时拿到 bytes，抛
    ``TypeError: cannot use a string pattern on a bytes-like object``；
    ``?host=/var/run/postgresql`` 的 unix socket 写法同样触发该路径。
    因此这里按方言降级：psycopg2 → pg8000(unix_sock) → psycopg，
    任何一条能连通就用它，并把实际方言记进产物（可复现性）。
    """
    from sqlalchemy import create_engine

    body = dsn.split("://", 1)[1] if "://" in dsn else dsn
    body = body.split("?", 1)[0]          # 丢掉 ?host=/var/run/... 里的斜杠干扰
    user = "postgres"
    if "@" in body:
        user = body.split("@", 1)[0].split(":")[0] or "postgres"
        body = body.split("@", 1)[1]
    dbname = body.rsplit("/", 1)[-1] or "tp_gate_refill"
    sock = os.environ.get("TP_PG_SOCKET", "/var/run/postgresql/.s.PGSQL.5432")

    attempts = []
    try:
        __import__("psycopg2")
        attempts.append(("psycopg2", lambda: create_engine(f"postgresql+psycopg2://{user}@/{dbname}",
                                                           connect_args={"host": os.path.dirname(sock)})))
    except Exception:  # noqa: BLE001
        pass
    attempts.append(("pg8000", lambda: create_engine(
        f"postgresql+pg8000:///{dbname}", connect_args={"unix_sock": sock, "user": user})))
    attempts.append(("psycopg", lambda: create_engine(
        f"postgresql+psycopg:///{dbname}", connect_args={"host": os.path.dirname(sock), "user": user})))

    last: Exception | None = None
    for name, build in attempts:
        try:
            engine = build()
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            engine.dialect_name = name  # type: ignore[attr-defined]
            return engine
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
    raise RuntimeError(f"no_sqlalchemy_pg_dialect_available: {last}")


def _dumps(obj: Any) -> str:
    """JSON 序列化（datetime/Decimal 一律按 ISO/str 落地，产物不因类型炸掉）。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _evidence(path: Path) -> dict[str, Any]:
    rel = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
    rec: dict[str, Any] = {"path": rel, "exists": path.exists()}
    if path.is_file() and path.exists():
        rec["bytes"] = path.stat().st_size
        rec["sha256"] = _sha256(path)
    return rec


def _run(cmd: Sequence[str], *, timeout: int = 900, env: Mapping[str, str] | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd or REPO_ROOT), env={**os.environ, **(env or {})}, check=False,
    )


def _pytest_count(args: Sequence[str]) -> dict[str, Any]:
    """跑一组 pytest，回报 passed/failed（作为「能力在案」的机器证据）。"""
    proc = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args])
    out = proc.stdout + proc.stderr
    passed = failed = 0
    for tok, key in (("passed", "passed"), ("failed", "failed")):
        for line in out.splitlines():
            if tok in line and ("=" in line or "," in line):
                for part in line.replace("=", " ").split(","):
                    bits = part.split()
                    if len(bits) >= 2 and bits[1] == tok and bits[0].isdigit():
                        if key == "passed":
                            passed = int(bits[0])
                        else:
                            failed = int(bits[0])
    return {"returncode": proc.returncode, "passed": passed, "failed": failed,
            "tail": out.strip().splitlines()[-1] if out.strip() else ""}


def _git(*args: str) -> str:
    proc = _run(["git", *args], timeout=120)
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# C1 补给器停摆回放
# ---------------------------------------------------------------------------


C1_STALL_SAMPLE = {
    "sampled_at": "2026-11-07T03:18:00Z",
    "pending_missions": 0,
    "idle_workers": 8,
    "note": "停摆样例：队列清零 + 一群空闲 worker，人不在场（渐退令现场）",
}

WITHHELD_SEED = (
    # 沙箱缺口样本：中性命名（平台层不得泄露任何领域语义——见
    # tests/test_autonomy_dashboard.py::test_module_is_domain_neutral）
    ("solver_calibration_gap_a", "WITHHELD", "TensorDEM", "标定证据不足（沙箱样本）"),
    ("solver_benchmark_gap_b", "WITHHELD", "TensorLBM", "基准算例未补齐（沙箱样本）"),
    ("solver_model_gap_c", "NOT_SUPPORTED", "TensorFVM", "模型缺失（沙箱样本）"),
    ("solver_strength_gap_d", "WITHHELD", "TensorFEM", "试验对照缺失（沙箱样本）"),
    ("platform_evidence_gap_e", "WITHHELD", "TensorPlatform", "实测对照缺失（沙箱样本）"),
    ("solver_map_gap_f", "WITHHELD", "TensorSPH", "图谱未验证（沙箱样本）"),
)


def _seed_refill_sandbox(dsn: str) -> dict[str, Any]:
    """把沙箱库摆成停摆样例 + 有缺口账本的状态（真实 INSERT，不碰生产库）。"""
    from sqlalchemy import create_engine, text

    from backlog_refiller.gap_ledger import ensure_capability_ledger

    engine = _refill_engine(dsn)
    seeded = {"dsn": dsn, "withheld_rows": 0, "idle_workers": 0, "queued_missions": None}
    with engine.connect() as conn:
        ensure_capability_ledger(conn)   # 自带 commit（幂等 DDL）
        for name, level, repo, desc in WITHHELD_SEED:
            conn.execute(text(
                "INSERT INTO capability_ledger (capability_name, evidence_level, repo, description)"
                " VALUES (:n, :l, :r, :d)"
                " ON CONFLICT (capability_name) DO UPDATE SET evidence_level = :l,"
                " repo = :r, description = :d"
            ), {"n": name, "l": level, "r": repo, "d": desc})
        seeded["withheld_rows"] = conn.execute(
            text("SELECT count(*) FROM capability_ledger")).scalar()

        # 停摆样例：清掉上一轮本门自己生成的沙箱 mission（保证每轮数字可比），队列清零
        conn.execute(text("DELETE FROM tensor_dev_missions"
                          " WHERE result->>'generated_by' = 'acceptance-gate-replay'"))
        conn.execute(text("DELETE FROM backlog_refiller_audit"))
        conn.execute(text("UPDATE tensor_dev_missions SET status='succeeded' WHERE status='queued'"))
        seeded["queued_missions"] = conn.execute(
            text("SELECT count(*) FROM tensor_dev_missions WHERE status='queued'")).scalar()

        # 一群空闲 worker（心跳新鲜、无在手任务）
        conn.execute(text("DELETE FROM factory_resident_workers"))
        for i in range(C1_STALL_SAMPLE["idle_workers"]):
            conn.execute(text(
                "INSERT INTO factory_resident_workers"
                " (agent_session_id, user_id, organization_id, worker_id, role, enabled,"
                "  state, model, model_history, memory, heartbeat_at, created_at)"
                " VALUES (gen_random_uuid(), 1, 1, :w, 'impl', true, 'idle', 'qwen3.7-plus',"
                "  '[]'::jsonb, '{}'::jsonb, now(), now())"
            ), {"w": f"sandbox-worker-{i:02d}"})
        seeded["idle_workers"] = conn.execute(text(
            "SELECT count(*) FROM factory_resident_workers"
            " WHERE enabled AND heartbeat_at > now() - interval '90 seconds'")).scalar()
        conn.commit()
    engine.dispose()
    return seeded


def check_refill_stall_replay(dsn: str | None = None, *, keep: bool = False) -> dict[str, Any]:
    """C1：在真 Postgres 沙箱里回放停摆样例，验证补给器自动触发。"""
    dsn = dsn or os.environ.get("TP_REFILL_SANDBOX_DSN") or DEFAULT_REFILL_DSN
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from backlog_refiller.audit import AuditLogger
    from backlog_refiller.daemon import BacklogRefillerDaemon

    evidence: list[Path] = []
    record: dict[str, Any] = {"criterion": "C1_refill_stall_replay", "dsn": dsn}

    try:
        seeded = _seed_refill_sandbox(dsn)
    except Exception as exc:  # noqa: BLE001 - 沙箱不可用即 WITHHELD（不许当 PASS）
        record.update({"status": WITHHELD, "reason": f"sandbox_unavailable:{exc}"})
        return record

    engine = _refill_engine(dsn)
    seeded["dialect"] = getattr(engine, "dialect_name", engine.dialect.name)
    factory = sessionmaker(bind=engine)
    daemon = BacklogRefillerDaemon(factory, api_base="http://127.0.0.1:8010",
                                   daemon_id="acceptance-gate-replay")
    try:
        result = daemon.run_once()
    except Exception as exc:  # noqa: BLE001
        record.update({"status": FAIL, "reason": f"refill_cycle_error:{exc}", "seeded": seeded})
        engine.dispose()
        return record

    generated = result.get("generated_missions") or []
    rollback: dict[str, Any] = {}
    audit_trail: list[dict[str, Any]] = []
    session = factory()
    try:
        audit = AuditLogger(session)
        audit_trail = audit.get_audit_trail(limit=20)
        if generated:
            rb = audit.rollback_latest()
            rollback = rb or {}
    finally:
        session.close()
    engine.dispose()

    # 回滚是否真的撤掉了那条 mission：回库里数行数（不靠工具自述）
    rollback_verified = None
    rb_mid = rollback.get("original", {}).get("mission_id") if isinstance(rollback, Mapping) else None
    if rb_mid:
        from sqlalchemy import create_engine, text as _text
        eng = _refill_engine(dsn)
        try:
            with eng.connect() as conn:
                rollback_verified = conn.execute(
                    _text("SELECT count(*) FROM tensor_dev_missions WHERE id = :m"),
                    {"m": int(rb_mid)}).scalar() == 0
        finally:
            eng.dispose()

    refill_rows = [r for r in audit_trail
                   if r.get("action") == "refill"
                   and (r.get("details") or {}).get("daemon_id") == daemon.daemon_id]
    criteria = {
        "replay_pending_missions": C1_STALL_SAMPLE["pending_missions"],
        "replay_idle_workers": C1_STALL_SAMPLE["idle_workers"],
        "seeded_withheld_rows": seeded.get("withheld_rows"),
        "observed_pending_missions": (result.get("queue_depth") or {}).get("pending_missions"),
        "observed_idle_workers": (result.get("queue_depth") or {}).get("idle_workers"),
        "triggered": bool(result.get("triggered")),
        "missions_generated": len(generated),
        "refill_audit_rows": len(refill_rows),
        "rollback_recorded": bool(rollback.get("audit_id")),
        "rollback_verified_absent": bool(rollback_verified),
        "rollback_mission_id": rb_mid,
        "gap_source_status": result.get("gap_sources") or {},
    }
    ok = (
        criteria["triggered"]
        and criteria["missions_generated"] >= 1
        and criteria["refill_audit_rows"] >= 1
        and criteria["rollback_recorded"]
        and criteria["rollback_verified_absent"]
    )

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACT_DIR / "refill_stall_replay.json"
    out.write_text(_dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stall_sample": C1_STALL_SAMPLE,
        "seeded": seeded,
        "run_once": result,
        "audit_trail": audit_trail,
        "rollback": rollback,
        "criteria": criteria,
        "verdict": PASS if ok else FAIL,
    }), encoding="utf-8")
    evidence.append(out)

    record.update({
        "status": PASS if ok else FAIL,
        "criteria": criteria,
        "evidence": [_evidence(p) for p in evidence],
    })
    if not keep:
        record["sandbox_cleanup"] = "database kept for reproducibility (tp_gate_refill)"
    return record


# ---------------------------------------------------------------------------
# C2a 放门自动化真执行
# ---------------------------------------------------------------------------


GATE_SANDBOX_FIXTURES = (
    # id, name, node_type, status, deps, metadata
    (9001, "SBX-UP", "task", "done", [], {}),
    (9002, "SBX-GATE-OK", "gate", "blocked", [9001],
     {"repo": "TensorPlatform",
      "gate_criteria": {"artifacts": ["artifacts/autonomy_acceptance/merge_exec/audit.jsonl"]}}),
    (9003, "SBX-GATE-NOART", "gate", "blocked", [9001],
     {"repo": "TensorPlatform",
      "gate_criteria": {"artifacts": ["artifacts/does_not_exist_xyz.json"]}}),
    (9004, "SBX-GATE-GOVERNANCE", "gate", "blocked", [9001],
     {"repo": "TensorPlatform", "gate_class": "policy",
      "gate_criteria": {"artifacts": ["artifacts/autonomy_acceptance/merge_exec/audit.jsonl"]}}),
)

_GATE_SANDBOX_DDL = """
DROP SCHEMA IF EXISTS {schema} CASCADE;
CREATE SCHEMA {schema};
CREATE TABLE {schema}.campaign_nodes (LIKE public.campaign_nodes INCLUDING ALL);
CREATE TABLE {schema}.gate_release_audit (LIKE public.gate_release_audit INCLUDING ALL);
ALTER TABLE {schema}.campaign_nodes DROP CONSTRAINT IF EXISTS campaign_nodes_plan_id_fkey;
"""


def _psql(sql: str) -> list[list[str]]:
    proc = _run(["psql", "-X", "-q", "-tA", "-F", "|", "-v", "ON_ERROR_STOP=1",
                 "-h", "/var/run/postgresql", "-U", "postgres", "-d", "tensorplatform",
                 "-c", sql])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:400])
    return [line.split("|") for line in proc.stdout.strip().splitlines() if line]


def _seed_gate_sandbox(schema: str) -> None:
    """建可丢弃的沙箱 schema + 四个判据夹具（真表真写，不碰战役真节点）。"""
    import shlex

    _psql(_GATE_SANDBOX_DDL.format(schema=schema))
    for nid, name, ntype, status, deps, meta in GATE_SANDBOX_FIXTURES:
        dep_lit = "ARRAY[%s]" % ",".join(str(d) for d in deps) if deps else "'{}'::integer[]"
        _psql(
            f"INSERT INTO {schema}.campaign_nodes"
            " (id, plan_id, name, node_type, status, dependencies, metadata, created_at, updated_at)"
            f" VALUES ({nid}, 999, {shlex.quote(name) if False else repr(name)}, '{ntype}',"
            f" '{status}', {dep_lit}, $json${json.dumps(meta, ensure_ascii=False)}$json$::jsonb,"
            " now(), now())"
        )


def check_gate_release(sbx_schema: str = DEFAULT_SBX_SCHEMA) -> dict[str, Any]:
    """C2a：沙箱里真跑一轮放门 + 幂等复跑 + 回滚，再用纯函数独立复算。"""
    sys.path.insert(0, str(REPO_ROOT / "ops" / "taskplane"))
    import gate_auto_release as gar  # noqa: PLC0415

    out_dir = ARTIFACT_DIR / "gate_exec"
    out_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"criterion": "C2a_gate_release_executed", "schema": sbx_schema}
    mirror = Path(tempfile.mkdtemp(prefix="gate_roots_"))
    try:
        (mirror / "TensorPlatform").symlink_to(REPO_ROOT, target_is_directory=True)
        _seed_gate_sandbox(sbx_schema)

        common = [
            sys.executable, "ops/taskplane/gate_auto_release.py",
            "--sandbox-schema", sbx_schema,
            "--pg-host", "/var/run/postgresql", "--pg-user", "postgres",
            "--pg-db", "tensorplatform",
            "--artifact-roots", str(mirror / "*"),
            "--state-dir", str(out_dir),
            "--no-audit-waits",
        ]
        run1 = _run([*common, "--once", "--json-out", str(out_dir / "run1.json")])
        run2 = _run([*common, "--once", "--json-out", str(out_dir / "run2_idempotent.json")])
        audit_first = _psql(
            f"SELECT max(id) FROM {sbx_schema}.gate_release_audit WHERE action='release'")
        release_audit_id = int(audit_first[0][0]) if audit_first and audit_first[0][0] else 0
        rollback = _run([*common, "--rollback", str(release_audit_id)])
        out_dir.joinpath("rollback.json").write_text(
            json.dumps({"returncode": rollback.returncode, "stdout": rollback.stdout.strip(),
                        "stderr": rollback.stderr.strip()[-400:]}, ensure_ascii=False, indent=2),
            encoding="utf-8")

        nodes = _psql(f"SELECT id, coalesce(plan_id,0), name, node_type, status,"
                      f" coalesce(array_to_string(dependencies,','),''), metadata::text"
                      f" FROM {sbx_schema}.campaign_nodes ORDER BY id")
        audit_rows = _psql(f"SELECT id, node_id, action, from_status, to_status, reason"
                           f" FROM {sbx_schema}.gate_release_audit ORDER BY id")

        node_dicts = [{
            "id": int(nid), "plan_id": int(plan_id) or None, "name": name,
            "node_type": ntype, "status": status,
            "dependencies": [int(d) for d in deps.split(",") if d],
            "metadata": json.loads(meta or "{}"),
        } for nid, plan_id, name, ntype, status, deps, meta in nodes]
        by_id = {n["id"]: n for n in node_dicts}

        # 独立复算：夹具节点一律当回 blocked 重算，与审计动作逐条对齐
        recomputed = {}
        for node in node_dicts:
            if node["node_type"] != gar.GATE_NODE_TYPE:
                continue
            probe = dict(node, status=gar.FROM_STATUS,
                         metadata={k: v for k, v in node["metadata"].items()
                                   if k not in ("owner_only", "owner_only_reason")})
            decision = gar.evaluate_gate(probe, by_id, [str(mirror / "*")])
            recomputed[node["id"]] = {"action": decision.action, "reason": decision.reason}

        releases = [r for r in audit_rows if r[2] == "release"]
        rollbacks = [r for r in audit_rows if r[2] == "rollback"]
        owner_holds = [r for r in audit_rows if r[2] == "hold_owner_only"]
        second = json.loads((out_dir / "run2_idempotent.json").read_text())
        post_status = {n["id"]: n["status"] for n in node_dicts}

        criteria = {
            "gate_nodes": len(recomputed),
            "runs_executed": 2,
            "releases_recorded": len(releases),
            "release_node_id": int(releases[0][1]) if releases else None,
            "release_transition": f"{releases[0][3]}->{releases[0][4]}" if releases else None,
            "rollbacks_recorded": len(rollbacks),
            "owner_only_holds": len(owner_holds),
            "idempotent_second_run_releases": int(second.get("counts", {}).get("release", -1)),
            "post_rollback_status": {str(k): v for k, v in post_status.items()},
            "recompute_matches_audit": all(
                recomputed[int(r[1])]["action"] == r[2] for r in audit_rows
                if r[2] in ("release", "hold_owner_only") and int(r[1]) in recomputed
            ),
            "recomputed_decisions": {str(k): v for k, v in recomputed.items()},
            "gate_cli_returncodes": [run1.returncode, run2.returncode, rollback.returncode],
        }
        ok = (
            criteria["releases_recorded"] >= 1
            and criteria["release_transition"] == "blocked->pending"
            and criteria["rollbacks_recorded"] >= 1
            and criteria["owner_only_holds"] >= 1
            and criteria["idempotent_second_run_releases"] == 0
            and criteria["recompute_matches_audit"]
            and post_status.get(releases[0] and 9002) == "blocked"
        )
        out = ARTIFACT_DIR / "gate_release_exec.json"
        out.write_text(_dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema": sbx_schema,
            "fixtures": [
                {"id": n[0], "name": n[1], "node_type": n[2], "status": n[3],
                 "dependencies": n[4], "metadata": n[5]} for n in GATE_SANDBOX_FIXTURES
            ],
            "criteria": criteria,
            "audit_rows": audit_rows,
            "stdout_tail": {"run1": run1.stdout[-400:], "run2": run2.stdout[-400:]},
            "verdict": PASS if ok else FAIL,
        }), encoding="utf-8")
        record.update({"status": PASS if ok else FAIL, "criteria": criteria,
                       "evidence": [_evidence(out),
                                    _evidence(out_dir / "run1.json"),
                                    _evidence(out_dir / "run2_idempotent.json"),
                                    _evidence(out_dir / "rollback.json")]})
    except Exception as exc:  # noqa: BLE001
        record.update({"status": WITHHELD, "reason": f"sandbox_unavailable:{type(exc).__name__}:{exc}"})
    finally:
        shutil.rmtree(mirror, ignore_errors=True)
    return record


# ---------------------------------------------------------------------------
# C2b 合并自动化真执行
# ---------------------------------------------------------------------------


def check_merge_execution(audit: Path | None = None, plan: Path | None = None) -> dict[str, Any]:
    """C2b：审计链哈希可验 + 合并提交经 git ancestry 独立核对在 main 上。"""
    base = ARTIFACT_DIR / "merge_exec"
    audit = audit or (base / "audit.jsonl")
    plan = plan or (base / "plan_exec.json")
    record: dict[str, Any] = {"criterion": "C2b_merge_executed"}
    if not audit.exists() or not plan.exists():
        record.update({"status": WITHHELD, "reason": "merge_execution_artifacts_missing"})
        return record

    verify = _run([sys.executable, "-m", "tools.merge_master.cli", "verify-audit",
                   "--audit", str(audit)])
    try:
        verify_json = json.loads(verify.stdout)
    except Exception:  # noqa: BLE001
        verify_json = {"ok": False, "raw": (verify.stdout or verify.stderr)[:400]}

    plan_data = json.loads(plan.read_text())
    merged = [d for d in plan_data.get("decisions", []) if d.get("action") == "MERGE"
              and d.get("merge_commit")]
    main_sha = _git("rev-parse", "origin/main") or _git("rev-parse", "HEAD")
    ancestry = []
    for d in merged:
        sha = d["merge_commit"]
        proc = _run(["git", "merge-base", "--is-ancestor", sha, main_sha])
        ancestry.append({"pr": d["pr"]["number"] if isinstance(d.get("pr"), dict) else d.get("pr"),
                         "merge_commit": sha, "in_main": proc.returncode == 0})

    criteria = {
        "audit_chain_ok": bool(verify_json.get("ok")),
        "audit_records": int(verify_json.get("records", 0)),
        "audit_head": verify_json.get("head"),
        "merges_executed": len(merged),
        "main_ref_sha": main_sha,
        "merges_confirmed_in_main": sum(1 for a in ancestry if a["in_main"]),
        "ancestry": ancestry,
    }
    ok = criteria["audit_chain_ok"] and criteria["merges_executed"] >= 1 \
        and criteria["merges_confirmed_in_main"] >= 1
    record.update({"status": PASS if ok else FAIL, "criteria": criteria,
                   "evidence": [_evidence(audit), _evidence(plan),
                                _evidence(base / "plan_exec.summary.json")]})
    return record


# ---------------------------------------------------------------------------
# C3 propose 通道积压
# ---------------------------------------------------------------------------


PROPOSE_APPLY_DIR = REPO_ROOT / "artifacts" / "propose_channel_guard"


def _propose_apply_evidence(base: Path | None = None) -> dict[str, Any]:
    """R4 的**真实处置**审计复核：apply 摘要 ↔ JSONL 审计 ↔ 回滚文件三方对齐。

    这条不重复执行（处置是治理写动作），而是把上游现场产出的审计链逐项对账：
    「撤了几单」「进审计几行」「回滚文件记了几条」必须一致，否则视为证据链断裂。
    """
    base = base or PROPOSE_APPLY_DIR
    summaries = sorted(base.glob("summary_apply_*.json"))
    rollbacks = sorted(base.glob("rollback-*.json"))
    if not summaries:
        return {"available": False}
    out: dict[str, Any] = {
        "available": True, "apply_runs": len(summaries), "apply_actions": 0,
        "audit_rows": 0, "rollback_files": len(rollbacks), "rollback_records": 0,
        "ids_match": False, "actions": [],
    }
    acted: list[int] = []
    for f in summaries:
        data = json.loads(f.read_text(encoding="utf-8"))
        for ch in data.get("changed") or []:
            if ch.get("verb") in ("withdraw", "repin") and ch.get("mission_id") is not None:
                acted.append(int(ch["mission_id"]))
                out["actions"].append({"mission_id": int(ch["mission_id"]),
                                       "verb": ch.get("verb"),
                                       "policy": data.get("policy")})
    out["apply_actions"] = len(acted)
    audit_file = sorted(base.glob("audit-*.jsonl"))
    if audit_file:
        rows = [json.loads(line) for line in
                audit_file[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
        out["audit_rows"] = len(rows)
        audit_ids = sorted({int(r.get("mission_id") or r.get("after", {}).get("mission_id"))
                            for r in rows if (r.get("mission_id") or r.get("after"))})
        out["ids_match"] = bool(audit_ids) and set(audit_ids) == set(acted)
    if rollbacks:
        rb = json.loads(rollbacks[-1].read_text(encoding="utf-8"))
        recs = rb.get("records") or rb.get("entries") or rb.get("rows") or []
        out["rollback_records"] = len(recs)
    out["ok"] = (out["apply_actions"] >= 1 and out["audit_rows"] >= 1
                 and out["ids_match"] and out["rollback_files"] >= 1)
    return out


def check_propose_backlog() -> dict[str, Any]:
    """C3：真跑提案守护判定，stuck 必须为 0；守护能力由该模块 pytest 在案。"""
    record: dict[str, Any] = {"criterion": "C3_propose_backlog_zero"}
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = ARTIFACT_DIR / "propose_guard_summary.json"
    proc = _run([sys.executable, "ops/taskplane/propose_channel_guard.py",
                 "--summary-json", str(summary_path)])
    if not summary_path.exists():
        record.update({"status": WITHHELD,
                       "reason": f"guard_summary_missing:{(proc.stderr or '')[-300:]}"})
        return record
    guard = json.loads(summary_path.read_text())
    buckets = classify_stuck_actions(guard.get("actions") or [])
    suite = _pytest_count(["tests/test_propose_channel_guard.py"])

    apply_ev = _propose_apply_evidence()
    criteria = {
        "apply_path": apply_ev,
        "apply_actions": apply_ev.get("apply_actions", 0),
        "apply_audit_rows": apply_ev.get("audit_rows", 0),
        "apply_ids_match": apply_ev.get("ids_match", False),
        "scanned": int(guard.get("scanned", 0)),
        "stuck_backlog": buckets["stuck_backlog"],
        "grace_backlog": buckets["grace_backlog"],
        "claimable_backlog": len(buckets["clear"]),
        "plans_blocked": len(guard.get("plans_blocked") or []),
        "plans_advance_ready": len(guard.get("plans_advance_ready") or []),
        "guard_suite_passed": suite["passed"],
        "guard_suite_failed": suite["failed"],
        "stuck_detail": buckets["stuck"],
    }
    ok = (criteria["stuck_backlog"] == 0 and suite["failed"] == 0 and suite["passed"] > 0
          and bool(apply_ev.get("ok")))
    ev = [_evidence(summary_path)]
    for extra in sorted(PROPOSE_APPLY_DIR.glob("*")):
        if extra.is_file() and extra.suffix in (".json", ".jsonl"):
            ev.append(_evidence(extra))
    record.update({"status": PASS if ok else FAIL, "criteria": criteria, "evidence": ev})
    return record


# ---------------------------------------------------------------------------
# C4 退出仪表盘基线
# ---------------------------------------------------------------------------


def check_exit_dashboard() -> dict[str, Any]:
    """C4：真跑退出仪表盘 baseline，三指标必须 REAL 且有 chronicle 快照。"""
    record: dict[str, Any] = {"criterion": "C4_exit_dashboard_baseline"}
    out = ARTIFACT_DIR / "autonomy_baseline.json"
    md = ARTIFACT_DIR / "autonomy_baseline.md"
    fig = ARTIFACT_DIR / "autonomy_baseline.png"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    proc = _run([sys.executable, "-m", "tools.autonomy.dashboard", "baseline",
                 "--window-hours", "24", "--out", str(out), "--markdown", str(md),
                 "--figure", str(fig), "--chronicle"])
    if not out.exists():
        record.update({"status": WITHHELD,
                       "reason": f"dashboard_failed:{(proc.stderr or '')[-300:]}"})
        return record
    baseline = json.loads(out.read_text())
    raw_metrics = baseline.get("metrics") or {}
    if isinstance(raw_metrics, Mapping):
        items = list(raw_metrics.items())
    else:  # list form（兼容旧版仪表盘）
        items = [(str(m.get("id") or m.get("key") or i), m) for i, m in enumerate(raw_metrics)]
    values = {}
    for key, m in items:
        value = None
        for field in ("value_hours", "total", "percent", "value", "ratio"):
            if field in m:
                value = m[field]
                break
        values[key] = {
            "status": str(m.get("status") or "WITHHELD"),
            "value": value,
            "definition": m.get("definition"),
        }
    real_count = sum(1 for v in values.values() if v["status"].upper() == "REAL")
    chronicle_n = 0
    try:
        proc2 = _run(["psql", "-X", "-q", "-tA", "-h", "/var/run/postgresql", "-U", "postgres",
                      "-d", "tensorplatform", "-c",
                      "SELECT count(*) FROM chronicle_snapshots WHERE payload ? 'autonomy'"])
        chronicle_n = int((proc2.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        chronicle_n = 0

    criteria = {
        "metrics_present": len(values),
        "metrics_real": real_count,
        "metric_values": values,
        "chronicle_snapshots": chronicle_n,
        "window_hours": baseline.get("window_hours"),
    }
    ok = len(values) >= 3 and real_count >= 3 and chronicle_n >= 1
    criteria["chronicle_write"] = (baseline.get("chronicle") or {}).get("status")
    record.update({"status": PASS if ok else FAIL, "criteria": criteria,
                   "evidence": [_evidence(out), _evidence(md), _evidence(fig)]})
    return record


# ---------------------------------------------------------------------------
# C5 垫池四单终态入账本
# ---------------------------------------------------------------------------


def check_pad_four() -> dict[str, Any]:
    """C5：D1..D4 终态 + 能力增减账本各有条目（真 SQL，不手抄）。"""
    record: dict[str, Any] = {"criterion": "C5_pad_four_terminal_and_booked"}
    ids = ",".join(str(i) for i in PAD_MISSIONS)
    sql_status = (f"SELECT id, status FROM tensor_dev_missions WHERE id IN ({ids})")
    sql_ledger = (f"SELECT mission_id, count(*) FROM tensor_capability_deltas "
                  f"WHERE mission_id IN ({ids}) GROUP BY mission_id")
    sql_nodes = (f"SELECT n.metadata->>'ext_id', n.status, n.mission_id FROM campaign_nodes n "
                 f"WHERE n.mission_id IN ({ids}) ORDER BY n.mission_id")

    def query(sql: str) -> list[list[str]]:
        proc = _run(["psql", "-X", "-q", "-tA", "-F", "|", "-h", "/var/run/postgresql",
                     "-U", "postgres", "-d", "tensorplatform", "-c", sql])
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:300])
        return [line.split("|") for line in proc.stdout.strip().splitlines() if line]

    try:
        statuses = {int(r[0]): r[1] for r in query(sql_status)}
        ledger = {int(r[0]): int(r[1]) for r in query(sql_ledger)}
        nodes = [{"ext_id": r[0], "status": r[1], "mission_id": int(r[2])} for r in query(sql_nodes)]
    except Exception as exc:  # noqa: BLE001
        record.update({"status": WITHHELD, "reason": f"db_unavailable:{exc}"})
        return record

    verdict = pad_four_verdict(statuses, ledger)
    ledger_categories = {}
    try:
        for row in query(f"SELECT mission_id, category FROM tensor_capability_deltas "
                         f"WHERE mission_id IN ({ids})"):
            ledger_categories[str(row[0])] = row[1]
    except Exception:  # noqa: BLE001
        pass

    criteria = {
        **verdict,
        "campaign_nodes": nodes,
        "campaign_nodes_done": sum(1 for n in nodes if n["status"] == "done"),
        "ledger_categories": ledger_categories,
        "ledger_placeholder_rows": sum(1 for c in ledger_categories.values()
                                       if c == "no_substantive_delta"),
    }
    record.update({"status": PASS if verdict["ok"] else FAIL, "criteria": criteria,
                   "evidence": []})
    return record


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def build_acceptance(*, refill_dsn: str | None = None,
                     sbx_schema: str = DEFAULT_SBX_SCHEMA) -> dict[str, Any]:
    checks = {
        "C1_refill_stall_replay": check_refill_stall_replay(refill_dsn),
        "C2a_gate_release_executed": check_gate_release(sbx_schema),
        "C2b_merge_executed": check_merge_execution(),
        "C3_propose_backlog_zero": check_propose_backlog(),
        "C4_exit_dashboard_baseline": check_exit_dashboard(),
        "C5_pad_four_terminal_and_booked": check_pad_four(),
    }
    agg = aggregate(checks)
    return {
        "schema": "tp.autonomy.acceptance/v1",
        "campaign_node": "IN20-G21",
        "mission_id": 5177,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git("rev-parse", "HEAD"),
        "criteria_source": "campaign_nodes.metadata.acceptance_criteria (plan 21)",
        "aggregate": agg,
        "checks": checks,
        "artifact_root": str(ARTIFACT_DIR.relative_to(REPO_ROOT)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Autonomy acceptance gate (IN20-G21)")
    ap.add_argument("--out", default=str(ARTIFACT_DIR / "autonomy_acceptance.json"))
    ap.add_argument("--refill-dsn", default=None)
    ap.add_argument("--sandbox-schema", default=DEFAULT_SBX_SCHEMA)
    args = ap.parse_args(argv)

    payload = build_acceptance(refill_dsn=args.refill_dsn, sbx_schema=args.sandbox_schema)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_dumps(payload), encoding="utf-8")
    print(json.dumps({
        "artifact": str(out),
        "verdict": payload["aggregate"]["verdict"],
        "statuses": payload["aggregate"]["statuses"],
    }, ensure_ascii=False, indent=2))
    return 0 if payload["aggregate"]["verdict"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())