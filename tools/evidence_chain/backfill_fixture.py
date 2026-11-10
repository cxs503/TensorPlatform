#!/usr/bin/env python3
"""合成历史语料夹具（backfill_wiring 压力/红队用）。

零领域语义：全部合成域名（``SyntheticRepo`` / ``claim-bf-*`` / ``agent-bf``），
不含任何学科词，可安全放在平台仓。

它批量造「历史 run」（工件 + manifest）与「历史评价」（``outcome_evaluations`` 行），
并按预设比例混入五类残缺形态，用来量测回填器的三分类counting 与红队拦截率：

===============  =============================================================
kind             形态
===============  =============================================================
``intact``       工件完整 + manifest 与独立登记锚一致 → 应授完整链
``missing_manifest``  manifest 被删（工件还在）→ 缺件封顶
``missing_artifact``  manifest 声明的 data 工件被删 → 缺件封顶
``tampered``     工件被改一个字节（manifest 未更新）→ 校验失败
``self_signed``  工件完整但**无独立登记锚**（自签）→ 校验失败
``redteam_no_sha``        manifest 缺 ``checksum_sha256`` → 校验失败
``redteam_wrong_digest``  登记后改 manifest（摘要与锚不符）→ 校验失败
``redteam_fake_anchor``   评价伪造 ``data`` 摘要引用 → 校验失败
``redteam_cross_run``     评价指向别的 run 的目录（跨 run 混用）→ 校验失败
===============  =============================================================
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .evidence_chain import (
    canonical_json,
    manifest_digest,
    media_type_for,
    sha256_file,
    validate_manifest,
    load_vocab,
)

#: 默认配比（合计 240）：红队 4 例 + 自签 15 例覆盖「自签 manifest」红队面
DEFAULT_PLAN: dict[str, int] = {
    "intact": 146,
    "missing_manifest": 30,
    "missing_artifact": 25,
    "tampered": 20,
    "self_signed": 15,
    "redteam_no_sha": 1,
    "redteam_wrong_digest": 1,
    "redteam_fake_anchor": 1,
    "redteam_cross_run": 1,
}

#: 期望的三分类归属（夹具自带口径，测试与报表都用它当基准）
EXPECTED_CLASS = {
    "intact": "full_chain_granted",
    "missing_manifest": "capped_missing",
    "missing_artifact": "capped_missing",
    "tampered": "verify_failed",
    "self_signed": "verify_failed",
    "redteam_no_sha": "verify_failed",
    "redteam_wrong_digest": "verify_failed",
    "redteam_fake_anchor": "verify_failed",
    "redteam_cross_run": "verify_failed",
}

GRADES = ("S2", "S3", "S4")
E_LEVELS = ("E2", "E3")


@dataclass
class Corpus:
    root: Path
    run_root: Path
    registry_path: Path
    evaluations: list[dict[str, Any]] = field(default_factory=list)
    kinds: dict[str, str] = field(default_factory=dict)   # evaluation_id → kind
    run_dirs: dict[str, Path] = field(default_factory=dict)  # evaluation_id → run dir
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def attestations(self) -> dict[str, str]:
        from .backfill_wiring import load_attestations

        return load_attestations(self.registry_path)

    def expected_class_counts(self) -> dict[str, int]:
        out = {"full_chain_granted": 0, "capped_missing": 0, "verify_failed": 0}
        for kind in self.kinds.values():
            out[EXPECTED_CLASS[kind]] += 1
        return out


def _payload(run_id: str, index: int) -> bytes:
    return (canonical_json({"run_id": run_id, "index": index, "values": [index, index * 2]}) + "\n").encode()


def _manifest_for(run_dir: Path, run_id: str, index: int, revision: str, config_sha: str) -> dict[str, Any]:
    result = run_dir / "result.json"
    config = run_dir / "config.json"
    outputs = []
    for path, role in ((result, "data"), (config, "config")):
        outputs.append({
            "uri": path.name,
            "media_type": media_type_for(path),
            "checksum_sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "role": role,
        })
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "succeeded",
        "validation_status": "passed",
        "producer": {"name": "SyntheticRepo/runner", "version": "0.1.0", "revision": revision},
        "configuration_sha256": config_sha,
        "metrics": {"index": index},
        "outputs": outputs,
    }


def _write_manifest(run_dir: Path, manifest: Mapping[str, Any]) -> Path:
    path = run_dir / "run_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _evaluation(ev_id: str, run_id: str, index: int, data_ref: str, *, declared_run_id: str | None = None) -> dict[str, Any]:
    return {
        "evaluation_id": ev_id,
        "artifact_id": f"artifact-bf-{index:04d}",
        "mission_id": 900000 + index,
        "contributor": "agent-bf",
        "grade": GRADES[index % len(GRADES)],
        "e_level": E_LEVELS[index % len(E_LEVELS)],
        "grade_reason": "合成夹具：用于量测回填器三分类与红队拦截率。",
        "evidence_refs": {
            "conclusion_ref": f"claim-bf-{index:04d}",
            "theory": "doi:10.5555/synthetic.backfill.0001",
            "data": data_ref,
            "report": str(1000 + index),
        },
        "status": "confirmed",
        "reason_codes": [],
        "repo": "SyntheticRepo",
        "organization_id": 1,
        "created_at": "2026-11-01T00:00:00+00:00",
        "decided_at": "2026-11-01T00:00:00+00:00",
        "run_id": declared_run_id or run_id,
    }


def build_corpus(root: Path | str, plan: Mapping[str, int] | None = None) -> Corpus:
    """造「历史 run + 历史评价」语料。返回 Corpus（含期望分类口径）。"""
    plan = dict(plan or DEFAULT_PLAN)
    root = Path(root)
    run_root = root / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(root=root, run_root=run_root, registry_path=root / "registry.local.jsonl")
    registry: list[dict[str, Any]] = []

    kinds: list[str] = []
    for kind, count in plan.items():
        kinds.extend([kind] * count)

    vocab = load_vocab()
    index = 0
    for kind in kinds:
        index += 1
        run_id = f"bf-run-{index:04d}"
        run_dir = run_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result = run_dir / "result.json"
        result.write_bytes(_payload(run_id, index))
        config = run_dir / "config.json"
        config.write_text(canonical_json({"run_id": run_id, "params": {"index": index}}) + "\n", encoding="utf-8")
        revision = f"{index:07x}"
        manifest = _manifest_for(run_dir, run_id, index, revision, sha256_file(config))
        problems = validate_manifest(manifest, vocab)
        assert not problems, problems
        _write_manifest(run_dir, manifest)

        ev_id = f"eval-bf-{index:04d}"
        data_ref = "result.json"
        declared_run_id = run_id
        run_dir_for_eval = run_dir

        if kind == "missing_manifest":
            (run_dir / "run_manifest.json").unlink()
        elif kind == "missing_artifact":
            result.unlink()
        elif kind == "tampered":
            raw = bytearray(result.read_bytes())
            raw[-2] = raw[-2] ^ 0x01  # 篡改一个字节
            result.write_bytes(bytes(raw))
        elif kind == "redteam_no_sha":
            for ref in manifest["outputs"]:
                if ref["uri"] == "result.json":
                    ref.pop("checksum_sha256", None)
            _write_manifest(run_dir, manifest)
        elif kind == "redteam_fake_anchor":
            data_ref = "sha256:" + "0" * 64
        elif kind == "redteam_cross_run":
            # 目录是本 run，里面却放着别的 run 的 manifest → 跨 run 混用
            manifest["run_id"] = f"bf-run-{index + 5000:04d}"
            _write_manifest(run_dir, manifest)
        elif kind == "redteam_wrong_digest":
            pass  # 先按原样登记，登记后再改 manifest（见下）

        # 独立登记锚：先写 manifest → 算摘要 → 登记（自签类故意不登记）
        manifest_path = run_dir / "run_manifest.json"
        digest = None
        if manifest_path.is_file():
            digest = manifest_digest(json.loads(manifest_path.read_text(encoding="utf-8")))
        if kind != "self_signed" and digest:
            registry.append({"run_id": run_id, "manifest_sha256": digest})
        if kind == "redteam_wrong_digest":
            edited = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            edited["metrics"] = {"index": index, "edited_after_registration": True}
            _write_manifest(run_dir, edited)

        corpus.kinds[ev_id] = kind
        corpus.run_dirs[ev_id] = run_dir_for_eval
        corpus.evaluations.append(_evaluation(ev_id, run_id, index, data_ref, declared_run_id=declared_run_id))

    corpus.registry_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in registry), encoding="utf-8"
    )
    corpus.counts = {kind: kinds.count(kind) for kind in sorted(set(kinds))}
    return corpus


def ensure_evaluations_table(conn) -> None:
    """建 ``outcome_evaluations``（走工具仓既有 DDL，不新造表结构）。"""
    from sqlalchemy import text

    from .. import outcome_evaluations as oe

    for stmt in [s.strip() for s in oe.DDL.split(";") if s.strip()]:
        conn.execute(text(stmt))
    conn.commit()


EVAL_COLUMNS = (
    "evaluation_id", "artifact_id", "mission_id", "contributor", "grade", "e_level",
    "grade_reason", "evidence_refs", "status", "reason_codes", "repo",
    "organization_id", "created_at", "decided_at",
)


def seed_evaluations(conn, evaluations: list[Mapping[str, Any]]) -> int:
    """把合成评价写进 ``outcome_evaluations``（``evidence_refs`` 存 JSON 文本）。"""
    from sqlalchemy import text

    ensure_evaluations_table(conn)
    placeholders = ", ".join(f":{c}" for c in EVAL_COLUMNS)
    sql = text(
        f"INSERT INTO outcome_evaluations ({', '.join(EVAL_COLUMNS)}) VALUES ({placeholders})"
    )
    for row in evaluations:
        payload = {c: row.get(c) for c in EVAL_COLUMNS}
        payload["evidence_refs"] = json.dumps(row.get("evidence_refs") or {}, ensure_ascii=False)
        payload["reason_codes"] = json.dumps(row.get("reason_codes") or [], ensure_ascii=False)
        conn.execute(sql, payload)
    conn.commit()
    return len(evaluations)


def run_dir_resolver(corpus: Corpus):
    """评价 → run 目录解析（夹具自带映射；真实环境由调用方注入自己的布局知识）。"""
    return lambda evaluation: corpus.run_dirs.get(str(evaluation.get("evaluation_id")))