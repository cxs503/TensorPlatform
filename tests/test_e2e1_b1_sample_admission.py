"""E2E-1 修复 · 门缺陷 B1：样本库准入三门回归（先红后绿）。

红（修复前）：`tools/sample_db/backfill.py::backfill_file()` 无门 ——
    `test_legacy_ungated_backfill_still_admits` 以 `xfail(strict=True)` 钉住，
    现在一定红；接线后 XPASS → strict 报错，强制同步台账与判决表。
绿（修复后）：`src.sample_db.ingest.ingest_artifact()` 三门全过才 INSERT ——
    B 类 11 个负用例全部 REJECTED + 结构化错误码 + 审计留痕，1 个正对照 ADMITTED。

判据（可失败）：
    * 任一坏产物被准入（samples 表出现其行）→ FAIL
    * 拒绝未留痕（审计行数 < 判定次数 / 缺 code）→ FAIL
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(TESTS_DIR / "e2e1_b1"))

from build_cases import build_all  # noqa: E402
from src.sample_db.admission import (  # noqa: E402
    AdmissionRejected,
    ApiMissionResolver,
    StaticMissionResolver,
    admit_sample,
)
from src.sample_db.ingest import ingest_artifact, init_db, load_backfill, row_count  # noqa: E402
from src.sample_db.inspect import inspect_run_products  # noqa: E402

EXIT_ADMITTED = 0
EXIT_REJECTED = 3


# --------------------------------------------------------------------------- 夹具
@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e1_b1_cases")
    return build_all(root)


@pytest.fixture()
def audit_log(tmp_path):
    return tmp_path / "audit" / "sample_db_admission.jsonl"


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "samples.sqlite")
    init_db(conn)
    yield conn
    conn.close()


def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resolver(case):
    return StaticMissionResolver(case["mission_status"], source="pytest-static")


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


# --------------------------------------------------------------------------- 正例
def test_control_valid_product_is_admitted(cases, db, audit_log):
    case = next(c for c in cases if c["name"] == "C0_control_valid")
    decision, inserted = ingest_artifact(
        db,
        case["artifact"],
        mission_resolver=_resolver(case),
        audit_log=audit_log,
        manifest_path=case["manifest"],
        run_dir=case["run_dir"],
    )
    assert decision.admitted is True, decision.to_dict()
    assert [g.status for g in decision.gates] == ["pass", "pass", "pass"]
    assert inserted and len(inserted) == 1
    assert row_count(db) == 1
    verified = decision.verified_outputs
    assert verified and len(verified[0]["sha256"]) == 64
    record = _read_audit(audit_log)[-1]
    assert record["decision"] == "admitted" and record["code"] is None
    assert record["gates"] == {"manifest": "pass", "mission": "pass", "checksum": "pass"}


# --------------------------------------------------------------------------- 负例
@pytest.mark.parametrize(
    "case_name",
    [
        "B1_repro_m810_unverified_ingest",
        "B2_manifest_not_object",
        "B3_contract_missing_checksum",
        "B4_checksum_mismatch",
        "B5_unverified_manifest",
        "B6_mission_failed",
        "B7_mission_unknown",
        "B8_artifact_escape",
        "B9_size_mismatch",
        "B10_artifact_missing_file",
        "B11_no_outputs",
    ],
)
def test_bad_products_are_rejected_with_structured_error(case_name, cases, db, audit_log):
    case = next(c for c in cases if c["name"] == case_name)
    assert case["expect"] == "REJECTED"  # 判据先于实现
    with pytest.raises(AdmissionRejected) as excinfo:
        ingest_artifact(
            db,
            case["artifact"],
            mission_resolver=_resolver(case),
            audit_log=audit_log,
            manifest_path=case["manifest"],
            run_dir=case["run_dir"],
        )
    payload = excinfo.value.to_dict()
    assert payload["admitted"] is False
    assert payload["code"] == case["expect_code"], payload
    assert payload["code"].startswith("E_")
    assert payload["audit_written"] is True, "拒绝必须留痕"
    # 结构化错误必须指名坏在哪一门，且失败门与构造攻击面一致
    failed = [g for g in payload["gates"] if g["status"] == "fail"]
    assert len(failed) == 1, payload["gates"]
    assert failed[0]["gate"] == case["gate"], failed
    # 一行都不许进 samples 表
    assert row_count(db) == 0


def test_rejections_all_leave_audit_trail(cases, tmp_path):
    """拒绝无留痕 = FAIL：11 个负例 + 1 个正例 = 12 条判定，审计必须恰好 12 条。"""
    audit_log = tmp_path / "audit.jsonl"
    db_path = tmp_path / "samples.sqlite"
    conn = sqlite3.connect(db_path)
    init_db(conn)
    for case in cases:
        for path in (case["artifact"], case["run_dir"]):
            assert Path(path).exists(), f"case fixture missing: {path}"
        with pytest.raises(AdmissionRejected) if case["expect"] == "REJECTED" else _nullcontext():
            decision, _ = ingest_artifact(
                conn,
                case["artifact"],
                mission_resolver=_resolver(case),
                audit_log=audit_log,
                manifest_path=case["manifest"],
                run_dir=case["run_dir"],
            )
    rows = _read_audit(audit_log)
    assert len(rows) == len(cases), f"审计行数 {len(rows)} != 判定次数 {len(cases)}"
    for row in rows:
        assert row["event"] == "sample_db_admission"
        assert row["decision"] in ("admitted", "rejected")
        assert row["artifact"] and row["artifact_sha256"]
        assert set(row["gates"]) == {"manifest", "mission", "checksum"}
        if row["decision"] == "rejected":
            assert row["code"] and row["code"].startswith("E_")
            assert row["message"]
        assert row["ts"]
    admitted = [r for r in rows if r["decision"] == "admitted"]
    assert [r["artifact"].split("/")[-2] for r in admitted] == ["C0_control_valid"]
    assert row_count(conn) == 1
    conn.close()


class _nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_m810_payload_hash_is_recorded_in_audit(cases, tmp_path):
    """m810 攻击载荷（伪造 verdict=PASS 的裸 JSON）必须在审计里留下产物指纹。"""
    audit_log = tmp_path / "audit.jsonl"
    case = next(c for c in cases if c["name"] == "B1_repro_m810_unverified_ingest")
    decision = admit_sample(
        case["artifact"],
        mission_resolver=_resolver(case),
        audit_log=audit_log,
        manifest_path=case["manifest"],
        run_dir=case["run_dir"],
    )
    assert decision.admitted is False
    assert decision.code == "E_MANIFEST_MISSING"
    row = _read_audit(audit_log)[0]
    assert row["code"] == "E_MANIFEST_MISSING"
    assert row["artifact_sha256"] == decision.artifact_sha256
    assert len(row["artifact_sha256"]) == 64


def test_running_mission_is_rejected(cases, db, audit_log):
    """非终态（running）也必须拒：mission 终态门不是「非 failed 即可」。"""
    case = next(c for c in cases if c["name"] == "C0_control_valid")
    decision = admit_sample(
        case["artifact"],
        mission_resolver=StaticMissionResolver({811: "running"}),
        audit_log=audit_log,
        manifest_path=case["manifest"],
        run_dir=case["run_dir"],
    )
    assert decision.admitted is False
    assert decision.code == "E_MISSION_STATE"
    assert "not even terminal" in decision.message


def test_null_resolver_fails_closed(cases, audit_log):
    case = next(c for c in cases if c["name"] == "C0_control_valid")
    decision = admit_sample(
        case["artifact"],
        mission_resolver=None,  # 默认 NullMissionResolver
        audit_log=audit_log,
        manifest_path=case["manifest"],
        run_dir=case["run_dir"],
    )
    assert decision.admitted is False
    assert decision.code == "E_MISSION_UNRESOLVED"


# --------------------------------------------------------------------------- CLI 真实入口
def test_cli_exit_codes(cases, tmp_path):
    bad = next(c for c in cases if c["name"] == "B1_repro_m810_unverified_ingest")
    good = next(c for c in cases if c["name"] == "C0_control_valid")
    audit = tmp_path / "cli_audit.jsonl"

    rejected = _run_cli(
        [
            "-m",
            "src.sample_db.ingest",
            str(bad["artifact"]),
            "--db",
            str(tmp_path / "cli_bad.sqlite"),
            "--run-dir",
            str(bad["run_dir"]),
            "--mission-status",
            "811=succeeded",
            "--audit",
            str(audit),
        ]
    )
    assert rejected.returncode == EXIT_REJECTED, rejected.stderr[-2000:]
    assert "E_MANIFEST_MISSING" in rejected.stderr

    admitted = _run_cli(
        [
            "-m",
            "src.sample_db.ingest",
            str(good["artifact"]),
            "--db",
            str(tmp_path / "cli_good.sqlite"),
            "--run-dir",
            str(good["run_dir"]),
            "--mission-status",
            "811=succeeded",
            "--audit",
            str(audit),
        ]
    )
    assert admitted.returncode == EXIT_ADMITTED, admitted.stderr[-2000:]
    payload = json.loads(admitted.stdout)
    assert payload["admitted"] is True and payload["rows_inserted"] == 1

    rows = _read_audit(audit)
    assert [r["decision"] for r in rows] == ["rejected", "admitted"]


def test_inspect_mode_does_not_short_circuit(cases):
    """体检模式：门1 失败也要把门2/门3 各自评一遍，才能回答「缺什么」。"""
    case = next(c for c in cases if c["name"] == "B1_repro_m810_unverified_ingest")
    report = inspect_run_products(
        case["artifact"],
        run_dir=case["run_dir"],
        mission_id=811,
        mission_resolver=StaticMissionResolver({811: "succeeded"}),
    )
    assert report["verdict"] == "NOT_ADMISSIBLE"
    assert report["gate_view"] == {"manifest": "fail", "mission": "pass", "checksum": "not_evaluable"}
    assert any("RunManifest" in item for item in report["missing"])
    assert report["inspection_mode"] == "read_only_no_audit"


# --------------------------------------------------------------------------- 缺口钉死
@pytest.mark.xfail(
    strict=True,
    reason=(
        "缺口：tools/sample_db/backfill.py 不在本单 EDIT_WHITELIST（src/, tests/, docs/, outputs/），"
        "旧的裸入口 backfill_file() 仍可接受未验证产物；本单提供 src/sample_db/ingest.py 作为"
        "带门入口。接线后此用例 XPASS → 必须删掉 xfail 并同步 DEFECTS/VERDICTS 台账。"
    ),
)
def test_legacy_ungated_backfill_still_admits(cases, tmp_path):
    """若这条变绿，说明 tools/ 的裸入口已被接线（缺口收口），台账要更新。"""
    case = next(c for c in cases if c["name"] == "B1_repro_m810_unverified_ingest")
    backfill = load_backfill()
    conn = sqlite3.connect(tmp_path / "legacy.sqlite")
    try:
        conn.executescript(backfill.SCHEMA_SQL)
        backfill.backfill_file(conn, str(case["artifact"]))
        conn.commit()
        rows = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    finally:
        conn.close()
    assert rows == 0, f"裸入口放行了 {rows} 行未验证样本（门缺陷仍然在 tools/ 一侧）"


# --------------------------------------------------------------------------- 真实 mission 状态源
needs_api = pytest.mark.skipif(
    not os.environ.get("TENSOR_API_BASE"),
    reason="TENSOR_API_BASE 未设置：跳过线上 mission 终态解析验证",
)


@needs_api
def test_real_api_resolver_reads_m811_terminal_success():
    """真值源联调：mission 811（Ghia Re=1000 硬门达成）在平台上终态 succeeded。"""
    state = ApiMissionResolver().resolve(811)
    assert state["status"] == "succeeded"
    assert state["finished_status"] == "succeeded"