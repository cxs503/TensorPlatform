"""样本库入库（带三门准入）—— 门缺陷 B1 的修复入口。

设计：**判定与抽取分离**。抽取/建表逻辑复用现有 `tools/sample_db/backfill.py`
（不改它，保持单一事实源），但在调用 `backfill_file()` 之前先过三门准入；任一
门不过 → `AdmissionRejected`（结构化错误）+ 审计留痕，**一行都不 INSERT**。

    decision, inserted = ingest_artifact(conn, artifact, mission_resolver=..., audit_log=...)

CLI：
    python3 -m src.sample_db.ingest --db /tmp/samples.sqlite <artifact.json> \
        --mission-status 811=succeeded --audit /tmp/audit.jsonl
    退出码 0 = ADMITTED(已入库) ／ 3 = REJECTED ／ 5 = 工具故障

残留（需白名单扩展才能收口，见 docs/e2e1_b1/README.md）：
    `tools/sample_db/backfill.py::backfill_file()` 仍有旧的裸入口。把它的首两行
    改成 `from src.sample_db.ingest import ingest_file_guarded` 会形成回环，因此
    正确收口方式是让 backfill.py 在 INSERT 前调用
    `src.sample_db.admission.admit_sample(...)` 并对拒绝 raise。该文件在本单
    EDIT_WHITELIST（src/, tests/, docs/, outputs/）之外，故未改；用例
    `test_legacy_ungated_backfill_still_admits` 以 xfail(strict=True) 钉住这段
    缺口，一旦接线会 XPASS 报错，强制同步更新台账。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping

if __package__ in (None, ""):  # 允许直接 `python3 src/sample_db/ingest.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.sample_db.admission import (  # noqa: E402
    DEFAULT_AUDIT_LOG,
    EXIT_ADMITTED,
    EXIT_REJECTED,
    EXIT_TOOL_ERROR,
    AdmissionDecision,
    AdmissionRejected,
    ApiMissionResolver,
    MissionResolver,
    NullMissionResolver,
    StaticMissionResolver,
    _parse_mission_status_specs,
    admit_sample,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKFILL_PATH = REPO_ROOT / "tools" / "sample_db" / "backfill.py"


def load_backfill() -> ModuleType:
    """加载现有回填模块（只读复用其 schema/抽取/INSERT 逻辑）。"""
    spec = importlib.util.spec_from_file_location("sample_db_backfill", BACKFILL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {BACKFILL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(load_backfill().SCHEMA_SQL)
    conn.commit()


def ingest_artifact(
    conn: sqlite3.Connection,
    artifact_path: str | Path,
    *,
    mission_resolver: MissionResolver | None = None,
    audit_log: str | Path | None = DEFAULT_AUDIT_LOG,
    manifest_path: str | Path | None = None,
    run_dir: str | Path | None = None,
    mission_id: int | None = None,
) -> tuple[AdmissionDecision, list[dict[str, Any]]]:
    """三门准入通过后才入库。拒绝 → 抛 `AdmissionRejected`（含审计留痕事实）。"""
    decision = admit_sample(
        artifact_path,
        mission_resolver=mission_resolver,
        audit_log=audit_log,
        manifest_path=manifest_path,
        run_dir=run_dir,
        mission_id=mission_id,
    )
    if not decision.admitted:
        raise AdmissionRejected(decision)
    backfill = load_backfill()
    inserted = backfill.backfill_file(conn, str(Path(artifact_path).resolve()))
    conn.commit()
    return decision, inserted


def row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m src.sample_db.ingest",
        description="样本库入库（三门准入：RunManifest / mission 终态 / sha256 复算）",
    )
    parser.add_argument("artifact")
    parser.add_argument("--db", default=None, help="SQLite 库路径（默认 :memory:）")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--mission-id", type=int, default=None)
    parser.add_argument("--mission-status", action="append", default=[], metavar="MID=STATUS[:FINISHED]")
    parser.add_argument("--api", action="store_true", help="改用平台 API 解析 mission 终态")
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT_LOG))
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    if args.api:
        resolver: MissionResolver = ApiMissionResolver()
    elif args.mission_status:
        resolver = StaticMissionResolver(_parse_mission_status_specs(args.mission_status))
    else:
        resolver = NullMissionResolver()

    conn = sqlite3.connect(args.db or ":memory:")
    try:
        init_db(conn)
        try:
            decision, inserted = ingest_artifact(
                conn,
                args.artifact,
                mission_resolver=resolver,
                audit_log=args.audit,
                manifest_path=args.manifest,
                run_dir=args.run_dir,
                mission_id=args.mission_id,
            )
        except AdmissionRejected as exc:
            payload = exc.to_dict()
            payload["rows_inserted"] = row_count(conn)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            if args.json_out:
                Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.json_out).write_text(text + "\n", encoding="utf-8")
            print(text, file=sys.stderr)
            return EXIT_REJECTED
        payload = decision.to_dict()
        payload["rows_inserted"] = row_count(conn)
        payload["inserted"] = inserted
        payload["audit_log"] = args.audit
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.json_out:
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_out).write_text(text + "\n", encoding="utf-8")
        print(text)
        return EXIT_ADMITTED
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())