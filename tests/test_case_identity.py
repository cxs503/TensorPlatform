"""算例身份键 / 版本钉扎 / 证据失效检测 / 回滚 机制层验收用例（IN99-N09，C1–C5）。

判据 → 用例映射（报告里的数字全部来自这些用例或同源 CLI 演示）：
  C1  全库唯一性约束违反时 INSERT 拒绝 → TestExtIdUniqueness
  C2  钉扎记录含 git sha + 依赖版本列表 + 证据文件 sha256 → TestVersionPin
  C3  上游依赖变更后 24h 内标 stale → TestStaleDetection
  C4  stale 节点进 blocked 并生成重算任务（幂等）+ not_supported 分支 → TestRemediation
  C5  负控：故意制造版本冲突验证 stale 检测触发（含只比版本号的朴素实现漏检对照）→
      TestNegativeControlVersionConflict
  回滚机制 → TestRollback

所有时间戳显式注入，用例无 wall-clock 依赖 → 可复算。测试夹具全用合成命名空间
（acme_ns / acme_solver），模块与用例内零领域语义。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.taskplane import case_identity as ci  # noqa: E402

T0 = datetime(2026, 11, 8, 0, 0, 0, tzinfo=timezone.utc)
NS = ["acme_ns", "acme_solver"]
GIT_A = "a" * 40


# ───────────────────────────── fixtures ─────────────────────────────
@pytest.fixture()
def conn():
    c = ci.connect(":memory:")
    ci.init_schema(c)
    yield c
    c.close()


@pytest.fixture()
def evidence_files(tmp_path):
    d = tmp_path / "evidence"
    d.mkdir()
    files = {}
    for label, body in (("fit", "alpha=0.512\nbeta=1.003\n"), ("grid", "n=200\nh=0.05\n")):
        p = d / f"{label}.txt"
        p.write_text(body, encoding="utf-8")
        files[label] = p
    return files


def _bootstrap(conn, evidence_files, *, version="1.4.0", sha=GIT_A):
    """注册一个算例 + 一个上游单元 + v001 钉扎（两条证据）。"""
    ci.record_upstream_state(conn, "acme_solver.core", version=version, git_sha=sha,
                             kind="code", changed_at=T0 - timedelta(days=5))
    case = ci.register_case(conn, "acme_ns:case_alpha", allowed_namespaces=NS, now=T0)
    pin = ci.pin_version(
        conn, case["ext_id"], version_label="v001", git_sha="d1e2f3a4" * 5,
        deps=["acme_solver.core"],
        evidence=[{"artifact_path": str(evidence_files["fit"]), "node_ref": "node-fit"},
                  {"artifact_path": str(evidence_files["grid"]), "node_ref": "node-grid"}],
        now=T0,
    )
    return case, pin


def _bump(conn, *, version="1.4.1", sha="e" * 40, at_hours=2, unit="acme_solver.core"):
    return ci.record_upstream_state(conn, unit, version=version, git_sha=sha, kind="code",
                                    artifact_sha256="", changed_at=T0 + timedelta(hours=at_hours))


# ───────────────────────────── schema / DDL ─────────────────────────────
class TestSchema:
    def test_checked_in_postgres_ddl_matches_generator(self):
        """签入的 Postgres DDL 必须逐字节等于生成器输出（防手工漂移）。"""
        path = REPO_ROOT / "backend" / "migrations" / "110_case_identity_evidence_staleness.sql"
        assert path.exists(), "migration file missing"
        assert path.read_text(encoding="utf-8") == ci.generate_ddl("postgres")

    def test_postgres_ddl_declares_unique_ext_id_and_all_tables(self):
        ddl = ci.generate_ddl("postgres")
        assert "UNIQUE (ext_id)" in ddl
        for table in ("case_registry", "case_version_pins", "case_evidence",
                      "upstream_state", "stale_events", "recompute_tasks"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
        assert f"schema_version={ci.SCHEMA_VERSION}" in ddl

    def test_sqlite_and_postgres_column_sets_agree(self, conn):
        for table in ci.SCHEMA:
            sqlite_cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table['name']})")]
            assert sqlite_cols == [c[0] for c in table["columns"]], table["name"]

    def test_schema_init_is_idempotent(self, conn):
        ci.init_schema(conn)  # 二次执行不应报错
        assert ci.audit_snapshot(conn)["cases"] == 0


# ───────────────────────────── C1 ─────────────────────────────
class TestExtIdUniqueness:
    def test_duplicate_ext_id_rejected_by_db_constraint(self, conn):
        ci.register_case(conn, "acme_ns:case_alpha", allowed_namespaces=NS, now=T0)
        with pytest.raises(ci.DuplicateExtId):
            ci.register_case(conn, "acme_ns:case_alpha", allowed_namespaces=NS, now=T0)
        assert conn.execute("SELECT COUNT(*) AS n FROM case_registry").fetchone()["n"] == 1

    def test_raw_insert_duplicate_raises_integrity_error(self, conn):
        """权威在数据库：绕过应用层直接 INSERT 也必须被 UNIQUE 约束拒绝。"""
        ci.register_case(conn, "acme_ns:case_alpha", now=T0)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO case_registry (ext_id, namespace, case_key, created_at, updated_at)"
                " VALUES ('acme_ns:case_alpha','acme_ns','case_alpha','x','x')"
            )

    def test_case_and_case_insensitive_forms_normalize_to_same_key(self, conn):
        first = ci.register_case(conn, "  ACME_NS:Case_Alpha  ", now=T0)
        assert first["ext_id"] == "acme_ns:case_alpha"
        with pytest.raises(ci.DuplicateExtId):
            ci.register_case(conn, "acme_ns:CASE_ALPHA", now=T0)

    @pytest.mark.parametrize("bad", [
        "case_alpha",            # 缺命名空间
        "acme ns:case_alpha",    # 命名空间含空格
        "acme_ns:Case Alpha",    # 键含空格
        "acme_ns:",              # 空键
        ":case_alpha",           # 空命名空间
        "acme_ns:case/alpha",    # 含斜杠
        "ACME_NS2:case_x",       # 未在册命名空间
        "9ns:case_x",            # 命名空间以数字开头
        "",
    ])
    def test_invalid_ext_ids_rejected(self, bad):
        with pytest.raises(ci.InvalidExtId):
            ci.validate_ext_id(bad, allowed_namespaces=NS)

    def test_valid_ext_ids_accepted(self):
        for good in ("acme_ns:case_alpha", "acme_ns:a1", "acme_solver:case.alpha-2"):
            assert ci.validate_ext_id(good, allowed_namespaces=NS) == good

    def test_unknown_case_lookup_raises(self, conn):
        with pytest.raises(ci.UnknownCase):
            ci.get_case(conn, "acme_ns:ghost")


# ───────────────────────────── C2 ─────────────────────────────
class TestVersionPin:
    def test_pin_records_git_sha_deps_and_evidence_sha256(self, conn, evidence_files):
        _, pin = _bootstrap(conn, evidence_files)
        assert len(pin["git_sha"]) == 40 and all(c in "0123456789abcdef" for c in pin["git_sha"])
        assert [d["unit"] for d in pin["dep_manifest"]] == ["acme_solver.core"]
        assert pin["dep_manifest"][0]["version"] == "1.4.0"
        assert len(pin["dep_digest"]) == 64 and len(pin["pin_digest"]) == 64
        for ev in pin["evidence"]:
            local = evidence_files["fit" if ev["node_ref"] == "node-fit" else "grid"]
            assert ev["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
            assert ev["size_bytes"] == local.stat().st_size
            assert ev["status"] == "valid"

    def test_pin_digest_reproducible_across_databases(self, evidence_files):
        digs = []
        for _ in range(2):
            c = ci.connect(":memory:")
            ci.init_schema(c)
            _, pin = _bootstrap(c, evidence_files)
            digs.append((pin["dep_digest"], pin["pin_digest"]))
            c.close()
        assert digs[0] == digs[1]

    def test_non_sha_git_ref_rejected(self, conn, evidence_files):
        ci.register_case(conn, "acme_ns:case_alpha", now=T0)
        for bad_sha in ("", "main", "zzzzzzzz", "abc12"):
            with pytest.raises(ci.CaseIdentityError):
                ci.pin_version(conn, "acme_ns:case_alpha", version_label="v001",
                               git_sha=bad_sha, now=T0)

    def test_missing_evidence_file_rejected(self, conn):
        ci.register_case(conn, "acme_ns:case_alpha", now=T0)
        with pytest.raises(ci.EvidenceMissing):
            ci.pin_version(conn, "acme_ns:case_alpha", version_label="v001", git_sha=GIT_A,
                           evidence=[{"artifact_path": "/nonexistent/x.json",
                                      "node_ref": "n"}], now=T0)

    def test_duplicate_version_label_rejected(self, conn, evidence_files):
        ci.register_case(conn, "acme_ns:case_alpha", now=T0)
        ev = [{"artifact_path": str(evidence_files["fit"]), "node_ref": "node-fit"}]
        ci.pin_version(conn, "acme_ns:case_alpha", version_label="v001", git_sha=GIT_A,
                       evidence=ev, now=T0)
        with pytest.raises(ci.CaseIdentityError):
            ci.pin_version(conn, "acme_ns:case_alpha", version_label="v001", git_sha="b" * 40,
                           evidence=ev, now=T0)

    def test_dep_without_recorded_state_rejected(self, conn, evidence_files):
        ci.register_case(conn, "acme_ns:case_alpha", now=T0)
        with pytest.raises(ci.CaseIdentityError):
            ci.pin_version(conn, "acme_ns:case_alpha", version_label="v001", git_sha=GIT_A,
                           deps=["acme_solver.unregistered"], now=T0)

    def test_evidence_file_drift_marks_stale(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        evidence_files["fit"].write_text("alpha=0.999\n", encoding="utf-8")
        report = ci.verify_evidence(conn, now=T0 + timedelta(hours=1))
        assert report["drift_count"] == 1
        statuses = ci.audit_snapshot(conn)["evidence_status"]
        assert statuses == {"stale": 1, "valid": 1}


# ───────────────────────────── C3 ─────────────────────────────
class TestStaleDetection:
    def test_upstream_change_marks_evidence_stale_within_24h(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        _bump(conn, at_hours=2)
        out = ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        assert out["newly_marked_evidence"] == 2
        assert out["event_count"] == 2
        assert all(e["sla_met"] for e in out["events"])
        assert max(e["latency_hours"] for e in out["events"]) == pytest.approx(1.0)
        assert out["sla_breaches"] == 0
        assert ci.audit_snapshot(conn)["evidence_status"] == {"stale": 2}
        assert ci.get_case(conn, "acme_ns:case_alpha")["status"] == "stale"

    def test_detect_is_idempotent(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        _bump(conn)
        first = ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        second = ci.detect_stale(conn, now=T0 + timedelta(hours=4))
        assert first["event_count"] == 2
        assert second["event_count"] == 0
        assert ci.audit_snapshot(conn)["stale_events"] == 2

    def test_detection_after_24h_recorded_as_sla_breach(self, conn, evidence_files):
        """超 24h 才检测到 → 照样标 stale，但 sla_met=0 如实记账（不粉饰）。"""
        _bootstrap(conn, evidence_files)
        _bump(conn, at_hours=1)
        out = ci.detect_stale(conn, now=T0 + timedelta(hours=30))
        assert out["newly_marked_evidence"] == 2
        assert out["sla_breaches"] == 2
        assert all(e["latency_hours"] == pytest.approx(29.0) for e in out["events"])

    def test_unrelated_upstream_unit_does_not_touch_evidence(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        ci.record_upstream_state(conn, "acme_ns.other_unit", version="1.0.0", git_sha="c" * 40,
                                 changed_at=T0)
        ci.record_upstream_state(conn, "acme_ns.other_unit", version="1.0.1", git_sha="d" * 40,
                                 changed_at=T0 + timedelta(hours=2))
        out = ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        assert out["event_count"] == 0
        assert ci.audit_snapshot(conn)["evidence_status"] == {"valid": 2}

    def test_detection_isolated_per_changed_unit(self, conn, evidence_files):
        """两条证据都依赖同一单元 → 每个单元变更各给每条证据记一条事件。"""
        _bootstrap(conn, evidence_files)
        _bump(conn, at_hours=2)
        ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        _bump(conn, version="1.4.2", sha="f" * 40, at_hours=5)
        out = ci.detect_stale(conn, now=T0 + timedelta(hours=6))
        assert out["event_count"] == 2
        assert ci.audit_snapshot(conn)["stale_events"] == 4


# ───────────────────────────── C4 ─────────────────────────────
class TestRemediation:
    def test_stale_evidence_blocked_and_recompute_queued(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        _bump(conn)
        ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        out = ci.remediate_stale(conn, now=T0 + timedelta(hours=4))
        assert out["blocked"] == 2 and out["tasks_created"] == 2
        assert out["tasks_queued_total"] == 2
        snap = ci.audit_snapshot(conn)
        assert snap["evidence_status"] == {"blocked": 2}
        assert snap["case_status"] == {"blocked": 1}
        task = conn.execute("SELECT * FROM recompute_tasks ORDER BY id").fetchone()
        assert task["status"] == "queued" and len(task["task_key"]) == 64

    def test_remediation_is_idempotent(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        _bump(conn)
        ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        ci.remediate_stale(conn, now=T0 + timedelta(hours=4))
        again = ci.remediate_stale(conn, now=T0 + timedelta(hours=5))
        assert again["blocked"] == 0 and again["tasks_created"] == 0
        assert again["tasks_queued_total"] == 2

    def test_no_recompute_policy_marks_not_supported(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        _bump(conn)
        ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        out = ci.remediate_stale(conn, now=T0 + timedelta(hours=4), recompute=False)
        assert out["not_supported"] == 2 and out["tasks_created"] == 0
        assert ci.audit_snapshot(conn)["evidence_status"] == {"not_supported": 2}
        assert ci.audit_snapshot(conn)["queued_recompute_tasks"] == 0

    def test_compute_task_key_unique_constraint(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        _bump(conn)
        ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        ci.remediate_stale(conn, now=T0 + timedelta(hours=4))
        key = conn.execute("SELECT task_key FROM recompute_tasks LIMIT 1").fetchone()["task_key"]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO recompute_tasks (case_id, pin_id, evidence_id, task_key, created_at)"
                " VALUES (1,1,1,?,'x')", (key,)
            )


# ───────────────────────────── C5 ─────────────────────────────
class TestNegativeControlVersionConflict:
    def test_same_version_different_sha_trips_fingerprint_detector(self, conn, evidence_files):
        """负控核心：版本号不动、代码 sha 变（版本冲突）→ 指纹检测必须触发 stale。"""
        _bootstrap(conn, evidence_files)
        conflict = _bump(conn, version="1.4.0", sha="9" * 40, at_hours=1)  # 版本号与钉扎一致
        assert conflict["changed"] is True
        assert conflict["version_conflict"] is True
        out = ci.detect_stale(conn, now=T0 + timedelta(hours=2))
        assert out["event_count"] == 2, "指纹检测器必须抓住同版本号内容变更"
        assert ci.audit_snapshot(conn)["evidence_status"] == {"stale": 2}

    def test_naive_version_only_detector_misses_the_conflict(self, conn, evidence_files):
        """对照基线：只比版本号的朴素实现对该冲突 0 命中（这就是要修掉的盲区）。"""
        _bootstrap(conn, evidence_files)
        _bump(conn, version="1.4.0", sha="9" * 40, at_hours=1)
        assert ci.naive_version_only_detect(conn) == []
        assert ci.detect_stale(conn, now=T0 + timedelta(hours=2))["event_count"] == 2

    def test_positive_control_naive_detector_catches_real_version_bump(self, conn, evidence_files):
        """反证朴素实现不是全瞎：真版本号变化它抓得到，盲区只在「同版本号内容变更」。"""
        _bootstrap(conn, evidence_files)
        _bump(conn, version="1.4.1", sha="e" * 40, at_hours=1)
        hits = ci.naive_version_only_detect(conn)
        assert len(hits) == 1 and hits[0]["pinned_version"] == "1.4.0"

    def test_conflict_without_detection_leaves_evidence_valid(self, conn, evidence_files):
        """不跑检测器 → 证据仍 valid：证明失效标记不是时间自发的，必须有检测回路。"""
        _bootstrap(conn, evidence_files)
        _bump(conn, version="1.4.0", sha="9" * 40, at_hours=1)
        assert ci.audit_snapshot(conn)["evidence_status"] == {"valid": 2}


# ───────────────────────────── 回滚 ─────────────────────────────
class TestRollback:
    def test_rollback_switches_active_version_and_blocks_superseded(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        ci.pin_version(conn, "acme_ns:case_alpha", version_label="v002", git_sha="7" * 40,
                       deps=["acme_solver.core"],
                       evidence=[{"artifact_path": str(evidence_files["fit"]),
                                  "node_ref": "node-fit"}],
                       now=T0 + timedelta(hours=1))
        out = ci.rollback(conn, "acme_ns:case_alpha", to_version="v001",
                          now=T0 + timedelta(hours=2), reason="regression")
        assert out["active_version"] == "v001"
        assert out["tasks_created"] == 1
        case = ci.get_case(conn, "acme_ns:case_alpha")
        assert case["active_version"] == "v001" and case["status"] == "rolled_back"
        row = conn.execute(
            "SELECT status, status_reason FROM case_evidence WHERE pin_id = ?", (out["v002_pin_id"]
            if "v002_pin_id" in out else 2,)
        ).fetchone()
        assert row["status"] == "blocked" and "superseded_by_rollback" in row["status_reason"]

    def test_rollback_does_not_revive_stale_blocked_evidence(self, conn, evidence_files):
        """回滚不粉饰：因上游变更被 block 的证据保持 blocked，只能走重算。"""
        _bootstrap(conn, evidence_files)
        ci.pin_version(conn, "acme_ns:case_alpha", version_label="v002", git_sha="7" * 40,
                       deps=["acme_solver.core"],
                       evidence=[{"artifact_path": str(evidence_files["fit"]),
                                  "node_ref": "node-fit"}], now=T0 + timedelta(hours=1))
        _bump(conn, at_hours=2)
        ci.detect_stale(conn, now=T0 + timedelta(hours=3))
        ci.remediate_stale(conn, now=T0 + timedelta(hours=4))
        out = ci.rollback(conn, "acme_ns:case_alpha", to_version="v002",
                          now=T0 + timedelta(hours=5))
        assert out["target_pin_still_blocked_evidence"] == 1
        assert out["active_version"] == "v002"

    def test_rollback_to_unknown_version_raises(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)
        with pytest.raises(ci.UnknownVersion):
            ci.rollback(conn, "acme_ns:case_alpha", to_version="v999", now=T0)

    def test_rollback_restores_evidence_blocked_by_a_later_rollback(self, conn, evidence_files):
        _bootstrap(conn, evidence_files)  # v001 两条证据 valid
        ci.pin_version(conn, "acme_ns:case_alpha", version_label="v002", git_sha="7" * 40,
                       deps=["acme_solver.core"],
                       evidence=[{"artifact_path": str(evidence_files["fit"]),
                                  "node_ref": "node-fit"}], now=T0 + timedelta(hours=1))
        ci.rollback(conn, "acme_ns:case_alpha", to_version="v001", now=T0 + timedelta(hours=2))
        # v002 的证据被面板回滚 block；再从 v001 回滚到 v002 时它应恢复 valid
        ci.rollback(conn, "acme_ns:case_alpha", to_version="v002", now=T0 + timedelta(hours=3))
        statuses = [r["status"] for r in conn.execute(
            "SELECT status FROM case_evidence WHERE pin_id = 2").fetchall()]
        assert statuses == ["valid"]


# ───────────────────────────── 端到端 / 可复算 ─────────────────────────────
class TestAcceptanceDemo:
    def test_demo_all_criteria_pass_and_is_deterministic(self, tmp_path):
        # 同一工作目录 + 同一参考时钟 = 同一输入 → 输出必须逐字节相同
        first = ci.run_acceptance_demo(tmp_path / "run", now=T0)
        second = ci.run_acceptance_demo(tmp_path / "run", now=T0)
        assert first["all_passed"] is True
        assert first["criteria"] == {"C1": True, "C2": True, "C3": True,
                                     "C4": True, "C5": True, "rollback": True}
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_cli_exit_codes(self, tmp_path):
        ddl = tmp_path / "110.sql"
        out = tmp_path / "acc.json"
        rc = ci.main(["--demo", "--ddl-out", str(ddl), "--json-out", str(out),
                      "--workdir", str(tmp_path / "demo"), "--now", "2026-11-08T00:00:00Z"])
        assert rc == 0
        assert ddl.read_text(encoding="utf-8") == ci.generate_ddl("postgres")
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["all_passed"] is True
        assert payload["C3_stale_detection_24h"]["max_latency_hours"] <= 24.0

    def test_demo_persisted_db_is_auditable(self, tmp_path):
        """落盘审计：demo 用持久库跑完后，可直接用 SQL 复核判据数字（零手抄）。"""
        db = tmp_path / "audit.db"
        ci.run_acceptance_demo(tmp_path / "run", now=T0, conn=ci.connect(db))
        c = ci.connect(db)
        try:
            snap = ci.audit_snapshot(c)
            assert snap["cases"] == 1 and snap["pins"] == 2
            assert snap["evidence"] == 3 and snap["stale_events"] >= 2
            assert snap["queued_recompute_tasks"] >= 2
        finally:
            c.close()