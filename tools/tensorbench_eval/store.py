"""落库/递交/记账（SQLite / PostgreSQL 通用子集；不触碰既有表的既有列）。

三张表（本包自建，幂等 ``CREATE TABLE IF NOT EXISTS``）：

* ``tensorbench_results``      —— 一次度量的实证行（mission/benchmark/ref/got/误差/ts 全非空）；
* ``tensorbench_evaluations``  —— 递交与取证审计（触发器 fired → 递交 → 取证 → 跳过/失败原因）；
* ``tensor_capability_deltas`` —— 复用既有能力台账，只新增 ``bench_verified`` 类别。

递交语义 = 集群队列（``cluster_jobs`` 一行 queued，``node`` 留空由调度器指派）。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import contract

RESULTS_TABLE = "tensorbench_results"
EVAL_TABLE = "tensorbench_evaluations"
JOBS_TABLE = "cluster_jobs"
DELTAS_TABLE = "tensor_capability_deltas"
MISSIONS_TABLE = "tensor_dev_missions"

BENCH_VERIFIED = "bench_verified"

#: 触发器：任务挂这个锚 → 完结即评价（平台特性锚，可用环境变量改；域内容不含）
DEFAULT_ANCHOR = os.environ.get("TP_TENSORBENCH_ANCHOR", "FEAT-TENSORBENCH")
#: 触发器：声明的成熟度级别 >= 该值 → 完结即评价（L4 = 基准验证档）
DEFAULT_LEVEL_THRESHOLD = int(os.environ.get("TP_TENSORBENCH_LEVEL", "4"))

DDL = f"""
CREATE TABLE IF NOT EXISTS {RESULTS_TABLE} (
    result_id             TEXT PRIMARY KEY,
    mission_id            BIGINT NOT NULL,
    organization_id       BIGINT,
    benchmark_id          TEXT NOT NULL,
    benchmark_version     TEXT NOT NULL,
    workload_id           TEXT NOT NULL,
    workload_version      TEXT NOT NULL,
    metric                TEXT NOT NULL,
    unit                  TEXT NOT NULL DEFAULT '-',
    ref_value             DOUBLE PRECISION NOT NULL,
    got_value             DOUBLE PRECISION NOT NULL,
    abs_error             DOUBLE PRECISION NOT NULL,
    rel_error             DOUBLE PRECISION,
    tolerance_rel         DOUBLE PRECISION NOT NULL,
    passed                BOOLEAN NOT NULL,
    run_id                TEXT NOT NULL,
    implementation_project TEXT,
    implementation_revision TEXT,
    execution_status      TEXT NOT NULL,
    cluster_job_id        BIGINT NOT NULL,
    source                TEXT NOT NULL,
    spec_sha256           TEXT NOT NULL,
    result_sha256         TEXT NOT NULL,
    stdout_sha256         TEXT NOT NULL,
    recorded_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tb_results_mission ON {RESULTS_TABLE} (mission_id);
CREATE INDEX IF NOT EXISTS idx_tb_results_bench ON {RESULTS_TABLE} (benchmark_id, recorded_at);

CREATE TABLE IF NOT EXISTS {EVAL_TABLE} (
    evaluation_id   TEXT PRIMARY KEY,
    mission_id      BIGINT NOT NULL,
    benchmark_id    TEXT,
    spec_sha256     TEXT,
    cluster_job_id  BIGINT,
    state           TEXT NOT NULL,
    reason          TEXT,
    detail          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tb_eval_mission ON {EVAL_TABLE} (mission_id);
CREATE INDEX IF NOT EXISTS idx_tb_eval_state ON {EVAL_TABLE} (state, created_at);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(sql: str):
    from sqlalchemy import text
    return text(sql)


def _rows(conn, sql: str, params: Mapping[str, Any] | None = None) -> List[dict]:
    return [dict(r) for r in conn.execute(_text(sql), dict(params or {})).mappings().all()]


def _first(conn, sql: str, params: Mapping[str, Any] | None = None):
    row = conn.execute(_text(sql), dict(params or {})).mappings().first()
    return None if row is None else dict(row)


def ensure_schema(conn) -> None:
    """幂等建表（两方言通用）。"""
    for stmt in [s.strip() for s in DDL.split(";") if s.strip()]:
        conn.execute(_text(stmt))
    conn.commit()


def has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect
    try:
        bind = conn.get_bind() if hasattr(conn, "get_bind") else conn
        return bool(inspect(bind).has_table(name))
    except Exception:  # pragma: no cover
        return False


# ─────────────────────── ① 触发器 ───────────────────────

_LEVEL_RE = re.compile(r'"(?:maturity_level|gate_level|verdict_level|level)"\s*:\s*"?L?(\d)')


def declared_level(mission: Mapping[str, Any]) -> Optional[int]:
    """从 mission.result 里读声明的成熟度级别（多写法容错，读不到 = None）。"""
    raw = mission.get("result")
    blob: Any = raw
    if isinstance(raw, str):
        try:
            blob = json.loads(raw) if raw.strip() else {}
        except ValueError:
            blob = {}
    if not isinstance(blob, Mapping):
        return None
    for holder in (blob.get("verdict"), blob.get("maturity"), blob.get("evidence"), blob):
        if isinstance(holder, Mapping):
            for key in ("maturity_level", "gate_level", "verdict_level", "level"):
                value = holder.get(key)
                if isinstance(value, str):
                    m = re.fullmatch(r"L?(\d)", value.strip())
                    if m:
                        return int(m.group(1))
                elif isinstance(value, int) and not isinstance(value, bool):
                    return value
    return None


def needs_evaluation(mission: Mapping[str, Any], *, anchor: str = DEFAULT_ANCHOR,
                     level_threshold: int = DEFAULT_LEVEL_THRESHOLD) -> Dict[str, Any]:
    """触发判据（纯函数）：挂平台特性锚 **或** 声明级别 ≥ 阈值。"""
    goal = str(mission.get("goal") or "")
    anchored = bool(anchor) and anchor in goal
    level = declared_level(mission)
    level_hit = bool(level is not None and int(level) >= int(level_threshold))
    reasons = []
    if anchored:
        reasons.append(f"anchor:{anchor}")
    if level_hit:
        reasons.append(f"declared_level>={level_threshold}")
    return {"trigger": bool(anchored or level_hit), "anchored": anchored,
            "declared_level": level, "reasons": reasons}


def spec_from_mission(mission: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """评价规格由 mission.result 注入（``tensorbench: {spec|spec_path, repo, ref}``）。"""
    raw = mission.get("result")
    blob: Any = raw
    if isinstance(raw, str):
        try:
            blob = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return None
    if not isinstance(blob, Mapping):
        return None
    spec_ref = blob.get("tensorbench")
    if isinstance(spec_ref, str):  # 路径形态
        return {"spec_path": spec_ref, "repo": mission.get("repo"), "ref": "main"}
    return dict(spec_ref) if isinstance(spec_ref, Mapping) else None


def evaluator_command(spec_path: str, *, output: str = "runs/tensorbench",
                      project: str | None = None, revision: str | None = None) -> str:
    """集群里执行的命令行（TensorBench runner 本体；argv 由 runner 内部保证不用 shell）。"""
    parts = ["python3", "-m", "tensorbench.cli", "run", "--spec", spec_path, "--output", output]
    if project:
        parts += ["--project", project]
    if revision:
        parts += ["--revision", revision]
    return " ".join(parts)


def submit_evaluation(conn, mission: Mapping[str, Any], *, spec: Mapping[str, Any],
                      submitter: str = "agent", ref: str = "main", timeout: int = 1800,
                      cores: int = 1, source: str = "mission_finish_hook") -> Dict[str, Any]:
    """① 递交：把评价作业压进集群队列（``node`` 留空——调度器指派，绝不指定节点）。"""
    parsed = contract.parse_spec(spec)
    mission_id = int(mission["id"])
    ensure_schema(conn)
    cmd = evaluator_command(f"tensorbench/{parsed['benchmark']['benchmark_id']}.spec.json",
                            project=str(mission.get("repo") or ""),
                            revision=str(mission.get("head_commit") or "")) 
    job_id = conn.execute(_text(f"""
        INSERT INTO {JOBS_TABLE} (repo, ref, mode, cmd, mid, submitter, state, timeout, submitted_at, note)
        VALUES (:repo, :ref, 'run', :cmd, :mid, :submitter, 'queued', :timeout, :now, :note)
        RETURNING id
    """), {"repo": str(mission.get("repo") or "TensorBench"), "ref": ref, "cmd": cmd,
           "mid": mission_id, "submitter": submitter, "timeout": int(timeout),
           "now": _utcnow(), "note": f"tensorbench_eval:{source}"}).scalar()
    evaluation_id = f"tbe-{uuid.uuid4().hex}"
    now = _utcnow()
    conn.execute(_text(f"""
        INSERT INTO {EVAL_TABLE} (evaluation_id, mission_id, benchmark_id, spec_sha256,
                                  cluster_job_id, state, reason, detail, created_at, updated_at)
        VALUES (:eid, :mid, :bid, :sha, :jid, 'submitted', :reason, :detail, :now, :now)
    """), {"eid": evaluation_id, "mid": mission_id,
           "bid": parsed["benchmark"]["benchmark_id"], "sha": parsed["sha256"],
           "jid": int(job_id), "reason": source,
           "detail": contract.canonical_json({"cmd": cmd, "cores": int(cores),
                                              "timeout": int(timeout)}), "now": now})
    conn.commit()
    return {"evaluation_id": evaluation_id, "cluster_job_id": int(job_id),
            "state": "submitted", "benchmark_id": parsed["benchmark"]["benchmark_id"],
            "spec_sha256": parsed["sha256"], "node": None}


def record_skip(conn, mission_id: int, *, state: str, reason: str,
                benchmark_id: str | None = None, detail: Any = None) -> None:
    """触发器「跳过了」也要留痕（无规格/未挂锚），避免回路静默失踪。"""
    ensure_schema(conn)
    now = _utcnow()
    conn.execute(_text(f"""
        INSERT INTO {EVAL_TABLE} (evaluation_id, mission_id, benchmark_id, spec_sha256,
                                  cluster_job_id, state, reason, detail, created_at, updated_at)
        VALUES (:eid, :mid, :bid, NULL, NULL, :state, :reason, :detail, :now, :now)
    """), {"eid": f"tbe-{uuid.uuid4().hex}", "mid": int(mission_id), "bid": benchmark_id,
           "state": state, "reason": reason,
           "detail": contract.canonical_json(detail or {}), "now": now})
    conn.commit()


def on_mission_terminal(conn, mission_id: int, *, status: str,
                        submission: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """任务完结钩子：命中触发判据 → 递交评价作业。失败只留痕，绝不改任务终态。"""
    if status not in ("succeeded", "done", "completed"):
        return {"evaluated": False, "reason": f"status:{status}"}
    mission = _first(conn, f"SELECT id, goal, repo, result FROM {MISSIONS_TABLE} WHERE id = :mid",
                     {"mid": int(mission_id)})
    if mission is None:
        return {"evaluated": False, "reason": "mission_not_found"}
    decision = needs_evaluation(mission)
    if not decision["trigger"]:
        record_skip(conn, mission_id, state="skipped", reason="trigger_not_matched",
                    detail=decision)
        return {"evaluated": False, **decision}
    binding = spec_from_mission(mission)
    if not binding:
        record_skip(conn, mission_id, state="no_spec", reason="no_benchmark_spec_bound",
                    detail=decision)
        return {"evaluated": False, "reason": "no_benchmark_spec_bound", **decision}
    spec = binding.get("spec")
    if spec is None:
        record_skip(conn, mission_id, state="no_spec", reason="spec_path_only_not_executable",
                    detail={"spec_path": binding.get("spec_path")})
        return {"evaluated": False, "reason": "spec_path_only_not_executable", **decision}
    out = submit_evaluation(conn, mission, spec=spec, ref=str(binding.get("ref", "main")),
                            source=str(binding.get("source", "mission_finish_hook")))
    return {"evaluated": True, **decision, **out}


# ─────────────────────── ② 取证落库 ───────────────────────

def _job(conn, job_id: int) -> Optional[dict]:
    return _first(conn, f"SELECT id, repo, ref, cmd, mid, state, rc, note FROM {JOBS_TABLE} "
                        f"WHERE id = :jid", {"jid": int(job_id)})


def ingest_result(conn, *, mission_id: int, cluster_job_id: int, spec: Mapping[str, Any],
                  result: Mapping[str, Any], workload_stdout: str,
                  organization_id: int | None = None,
                  source: str = "cluster_job",
                  require_cluster_job: bool = True) -> Dict[str, Any]:
    """取证：校验集群作业绑定 → 复算 ref/got/误差 → 落 ``tensorbench_results``。

    反造假绑定：``cluster_jobs`` 必须有该作业、必须终态 ``succeeded``、
    作业 ``mid`` 与请求 mission 一致。任一条不成立即拒（不给 L4 凭据）。
    """
    ensure_schema(conn)
    job = _job(conn, cluster_job_id)
    if require_cluster_job:
        if job is None:
            raise contract.ContractError(f"cluster job {cluster_job_id} not found")
        if str(job.get("state")) != "succeeded":
            raise contract.ContractError(
                f"cluster job {cluster_job_id} state={job.get('state')!r}, need 'succeeded'")
        if job.get("mid") is not None and int(job["mid"]) != int(mission_id):
            raise contract.ContractError(
                f"cluster job {cluster_job_id} belongs to mission {job['mid']}, not {mission_id}")
        source = "cluster_job"
    elif source == "cluster_job":
        raise contract.ContractError(
            "source='cluster_job' requires require_cluster_job=True (no bypass of job binding)")

    parsed = contract.parse_spec(spec)
    meta = contract.validate_result(result, parsed)
    if meta["execution_status"] != "success":
        record_skip(conn, mission_id, state="execution_failed",
                    reason=f"execution_status:{meta['execution_status']}",
                    benchmark_id=meta["benchmark_id"], detail={"cluster_job_id": int(cluster_job_id)})
        raise contract.ContractError(
            f"benchmark execution_status={meta['execution_status']!r}, not 'success'")

    doc = contract.extract_document(workload_stdout)
    metrics = contract.evaluate_metrics(parsed, doc)
    result_sha = contract.digest_of_text(contract.canonical_json(result))
    stdout_sha = contract.digest_of_text(workload_stdout if isinstance(workload_stdout, str)
                                         else str(workload_stdout))
    now = _utcnow()
    ids: List[str] = []
    for m in metrics:
        rid = f"tbr-{uuid.uuid4().hex}"
        conn.execute(_text(f"""
            INSERT INTO {RESULTS_TABLE}
                (result_id, mission_id, organization_id, benchmark_id, benchmark_version,
                 workload_id, workload_version, metric, unit, ref_value, got_value,
                 abs_error, rel_error, tolerance_rel, passed, run_id,
                 implementation_project, implementation_revision, execution_status,
                 cluster_job_id, source, spec_sha256, result_sha256, stdout_sha256, recorded_at)
            VALUES (:rid, :mid, :org, :bid, :bver, :wid, :wver, :metric, :unit, :ref, :got,
                    :abs, :rel, :tol, :passed, :run, :proj, :rev, :est, :jid, :src,
                    :spec_sha, :res_sha, :out_sha, :now)
        """), {"rid": rid, "mid": int(mission_id), "org": organization_id,
               "bid": meta["benchmark_id"], "bver": meta["benchmark_version"],
               "wid": meta["workload_id"], "wver": meta["workload_version"],
               "metric": m["metric"], "unit": m["unit"], "ref": m["ref_value"],
               "got": m["got_value"], "abs": m["abs_error"], "rel": m["rel_error"],
               "tol": m["tolerance_rel"], "passed": bool(m["passed"]), "run": meta["run_id"],
               "proj": meta["implementation_project"], "rev": meta["implementation_revision"],
               "est": meta["execution_status"], "jid": int(cluster_job_id), "src": source,
               "spec_sha": parsed["sha256"], "res_sha": result_sha, "out_sha": stdout_sha,
               "now": now})
        ids.append(rid)

    verified = any(bool(m["passed"]) for m in metrics)
    delta = None
    if verified:
        delta = book_bench_verified_delta(
            conn, mission_id=mission_id, organization_id=organization_id,
            benchmark_id=meta["benchmark_id"], metrics=[m for m in metrics if m["passed"]],
            cluster_job_id=cluster_job_id, run_id=meta["run_id"],
            revision=meta["implementation_revision"], result_ids=ids)
    conn.execute(_text(f"""
        UPDATE {EVAL_TABLE} SET state = :st, reason = :reason, updated_at = :now
        WHERE mission_id = :mid AND cluster_job_id = :jid
    """), {"st": "ingested" if verified else "ingested_failed", "reason": "ingest_result",
           "now": now, "mid": int(mission_id), "jid": int(cluster_job_id)})
    conn.commit()
    return {"mission_id": int(mission_id), "cluster_job_id": int(cluster_job_id),
            "run_id": meta["run_id"], "benchmark_id": meta["benchmark_id"],
            "result_ids": ids, "metrics": metrics, "verified": verified,
            "delta": delta, "spec_sha256": parsed["sha256"],
            "recorded_at": now}


def rows_for_mission(conn, mission_id: int) -> List[dict]:
    if not has_table(conn, RESULTS_TABLE):
        return []
    return _rows(conn, f"SELECT * FROM {RESULTS_TABLE} WHERE mission_id = :mid "
                       f"ORDER BY metric", {"mid": int(mission_id)})


# ─────────────────────── ③ 真增量记账 ───────────────────────

def book_bench_verified_delta(conn, *, mission_id: int, organization_id: int | None,
                              benchmark_id: str, metrics: Sequence[Mapping[str, Any]],
                              cluster_job_id: int, run_id: str, revision: str,
                              result_ids: Sequence[str]) -> Dict[str, Any]:
    """真增量：``category='bench_verified'``（有实证数字，不是 no_substantive_delta）。

    已有非空类别行（真增量/缺陷修复等）不覆盖；仅把 ``no_substantive_delta``
    原地升级为 ``bench_verified``——这正是「真增量 1/6365」要堵的漏。
    """
    ensure_schema(conn)
    existing = _first(conn, f"SELECT id, category FROM {DELTAS_TABLE} WHERE mission_id = :mid",
                      {"mid": int(mission_id)})
    summary = "; ".join(f"{m['metric']}={m['got_value']:.6g} vs ref {m['ref_value']:.6g} "
                        f"(rel {m['rel_error']:.3%} <= tol {m['tolerance_rel']:.3%})"
                        for m in metrics)
    evidence = {"benchmark_id": benchmark_id, "run_id": run_id, "cluster_job_id": int(cluster_job_id),
                "implementation_revision": revision, "result_ids": list(result_ids),
                "metrics": [dict(m) for m in metrics], "schema": contract.SCHEMA_VERSION}
    payload = {"category": BENCH_VERIFIED, "summary": f"TensorBench 实证基准通过：{summary}",
               "reason": "benchmark ref/got/error 由平台侧从集群作业原始输出复算，见 evidence",
               "evidence": evidence}
    if existing is None:
        conn.execute(_text(f"""
            INSERT INTO {DELTAS_TABLE} (organization_id, mission_id, category, summary, reason,
                                        artifacts, evidence, reviewer)
            VALUES (:org, :mid, :cat, :summary, :reason, :artifacts, :evidence, :reviewer)
        """), {"org": organization_id, "mid": int(mission_id), "cat": BENCH_VERIFIED,
               "summary": payload["summary"], "reason": payload["reason"],
               "artifacts": json.dumps([f"tensorbench_results:{r}" for r in result_ids]),
               "evidence": json.dumps(evidence), "reviewer": "tensorbench_eval"})
        action = "inserted"
    elif str(existing["category"]) == "no_substantive_delta":
        conn.execute(_text(f"""
            UPDATE {DELTAS_TABLE} SET category = :cat, summary = :summary, reason = :reason,
                   artifacts = :artifacts, evidence = :evidence, reviewer = :reviewer
            WHERE id = :did
        """), {"cat": BENCH_VERIFIED, "summary": payload["summary"], "reason": payload["reason"],
               "artifacts": json.dumps([f"tensorbench_results:{r}" for r in result_ids]),
               "evidence": json.dumps(evidence), "reviewer": "tensorbench_eval",
               "did": int(existing["id"])})
        action = "upgraded"
    else:
        action = "kept_existing"
    conn.commit()
    return {"action": action, "category": BENCH_VERIFIED, **payload,
            "existing_category": None if existing is None else str(existing["category"])}
