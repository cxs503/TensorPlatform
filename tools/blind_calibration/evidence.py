#!/usr/bin/env python3
"""Run-manifest → 五元证据链回填器（E1–E5），带「回填 E1 封顶」纪律。

要解决的问题
============
历史 run 目录里通常只有散落工件，没有生产者写的 manifest。要让「预测 → 观测 →
结论」这条链**内容可追溯**（而不是只验形态），需要把每个节点的证据链逐槽判定，
并且**说清楚每一槽是谁给的可信度**：

===========  ==============================  ====================================
槽位          内容                             谁能把它置为通过
===========  ==============================  ====================================
``E1``       工件内容锚定：目录内每个文件的    **回填器自己可做**（机器复算）：
             sha256 与该文件在 git 历史中       与「交付提交」里的 blob 复算值比对，
             （最后改动它的提交）的 blob         检出交付后篡改/替换
             复算 sha256 一致
``E2``       run-manifest 契约：生产者写的     **必须有生产者 manifest**；回填器
             manifest 结构合法 + 摘要可算        自建的 manifest 一律不给分
``E3``       观测/结论引用：声明给出结论工件    **声明**给出引用，机器复算 sha 一致
             路径 + sha256
``E4``       可复现记录：声明给出提交号 +      机器验证提交在 git 中真实存在
             复现命令
``E5``       独立复核：声明给出复核席位 +      机器复算复核报告 sha；且复核席位
             复核报告路径 + sha256               必须 ≠ 生产席位（自批不给分）
===========  ==============================  ====================================

**回填 E1 封顶**的含义：回填器只有 E1 的授予权。E2–E5 只能由**生产者工件或外部声明**
提供证据、由机器复算引用是否成立；回填器绝不因为「链看起来齐」就把等级往上抬。
``grade`` = 从 E1 起连续通过的槽位数；``auto_only`` = 除 E1 外没有任何外部证据。

两个数的诚实区分（别混为一谈）
==============================
* ``slot_coverage``（完整率）：5 个槽位**都有机器可读状态**（通过 / 带原因的 withhold）
  的节点占比。判据要求 100%，含义是「没有静默的空缺」——不是「10 个节点都通过 E5」。
* ``grade_distribution``（内容等级）：真实节点的实际内容等级分布。按上面的纪律，
  只有生产者工件/外部声明的节点才可能 > E1；报告里必须两个数都写。

零领域语义：节点标识、目录、声明全部经配置/工件注入；本模块只做文件哈希、git
复算与结构化判定。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# 槽位
SLOT_E1 = "E1"
SLOT_E2 = "E2"
SLOT_E3 = "E3"
SLOT_E4 = "E4"
SLOT_E5 = "E5"
EVIDENCE_SLOTS: tuple[str, ...] = (SLOT_E1, SLOT_E2, SLOT_E3, SLOT_E4, SLOT_E5)

#: 回填器自身的授予上限（纪律常量，不可由配置上调）
BACKFILL_CAP = SLOT_E1

# 状态词表
STATUS_VERIFIED = "verified"    # 机器复算通过（E1）/ 生产者工件通过（E2）
STATUS_ATTESTED = "attested"    # 外部声明 + 机器复算引用通过（E3–E5）
STATUS_WITHHELD = "withheld"    # 证据不足，带机器可读 reason（fail-closed）
STATUS_MISSING = "missing"      # 输入层面缺失（目录不存在等）

# 结构原因词表（机器可读，禁止自由文本当原因）
REASON_RUN_DIR_MISSING = "run_dir_missing"
REASON_NO_ARTIFACTS = "no_artifacts_in_run_dir"
REASON_ARTIFACT_UNTRACKED = "artifact_not_in_git_history"
REASON_ARTIFACT_DRIFT = "artifact_content_drift_since_commit"
REASON_ARTIFACT_OK = "artifact_blob_matches_commit"
REASON_MANIFEST_PRESENT = "producer_manifest_valid"
REASON_MANIFEST_MISSING = "producer_manifest_missing"
REASON_MANIFEST_INVALID = "producer_manifest_invalid"
REASON_NO_DECLARATION = "no_declaration"
REASON_REF_MISSING = "declared_reference_missing"
REASON_REF_HASH_MISMATCH = "declared_reference_hash_mismatch"
REASON_COMMIT_MISSING = "declared_commit_not_in_git"
REASON_NOT_INDEPENDENT = "declared_reviewer_not_independent"
REASON_BAD_DECLARATION = "declaration_malformed"

MANIFEST_BASENAMES = ("run_manifest.json", "manifest.json", "RunManifest.json")


# --------------------------------------------------------------------------- #
# 基础：哈希 / git
# --------------------------------------------------------------------------- #
def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(repo: str | os.PathLike[str], *args: str,
         binary: bool = False) -> bytes | str | None:
    """只读 git 调用；失败返回 None（不抛栈：git 不可用要如实降级）。"""
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def last_commit_for_path(repo: str | os.PathLike[str], relpath: str) -> str | None:
    """最后改动该路径的提交号（交付提交），作为独立内容锚。"""
    out = _git(repo, "log", "-1", "--format=%H", "--", relpath)
    if not isinstance(out, str):
        return None
    sha = out.strip()
    return sha or None


def blob_sha256_at_commit(repo: str | os.PathLike[str], commit: str,
                          relpath: str) -> str | None:
    """取该提交里该路径的文件内容并复算 sha256（独立于工作树当前内容）。"""
    out = _git(repo, "cat-file", "blob", f"{commit}:{relpath}", binary=True)
    if not isinstance(out, (bytes, bytearray)):
        return None
    return sha256_bytes(bytes(out))


def commit_exists(repo: str | os.PathLike[str], commit: str) -> bool:
    if not commit or len(commit) < 7:
        return False
    out = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    return out is not None and isinstance(out, str)


# --------------------------------------------------------------------------- #
# E1：工件内容锚定
# --------------------------------------------------------------------------- #
def _iter_files(run_dir: Path, exclude_names: Iterable[str] = MANIFEST_BASENAMES
                ) -> list[Path]:
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(run_dir):
        for name in sorted(filenames):
            if name in set(exclude_names):
                continue
            files.append(Path(dirpath) / name)
    return sorted(files)


def anchor_artifacts(run_dir: str | os.PathLike[str], repo: str | os.PathLike[str]
                     ) -> list[dict[str, Any]]:
    """逐文件比对「当前内容」与「交付提交里的内容」的 sha256。"""
    rd = Path(run_dir)
    repo_p = Path(repo)
    rows: list[dict[str, Any]] = []
    for path in _iter_files(rd):
        try:
            rel_repo = Path(os.path.relpath(path, repo_p)).as_posix()
        except ValueError:
            rel_repo = path.as_posix()
        rel_run = path.relative_to(rd).as_posix()
        actual = sha256_file(path)
        commit = last_commit_for_path(repo_p, rel_repo)
        anchor = blob_sha256_at_commit(repo_p, commit, rel_repo) if commit else None
        if anchor is None:
            status, reason = STATUS_WITHHELD, REASON_ARTIFACT_UNTRACKED
        elif anchor == actual:
            status, reason = STATUS_VERIFIED, REASON_ARTIFACT_OK
        else:
            status, reason = STATUS_WITHHELD, REASON_ARTIFACT_DRIFT
        rows.append({
            "file": rel_run,
            "repo_path": rel_repo,
            "size_bytes": path.stat().st_size,
            "sha256": actual,
            "anchor_commit": commit,
            "anchor_sha256": anchor,
            "status": status,
            "reason": reason,
        })
    return rows


# --------------------------------------------------------------------------- #
# 槽位判定
# --------------------------------------------------------------------------- #
def _slot(status: str, reason: str, *, authority: str, detail: Any = None,
          awarded: bool = False) -> dict[str, Any]:
    return {"status": status, "reason": reason, "authority": authority,
            "detail": detail, "awarded": awarded}


def _declared_reference(repo: Path, decl: dict[str, Any], key_path: str = "reference",
                        key_sha: str = "sha256") -> dict[str, Any]:
    """复算声明里引用的工件：路径存在 + sha256 一致。"""
    if not isinstance(decl, dict):
        return _slot(STATUS_WITHHELD, REASON_BAD_DECLARATION, authority="declaration")
    ref = decl.get(key_path)
    want = decl.get(key_sha)
    if not ref or not want:
        return _slot(STATUS_WITHHELD, REASON_BAD_DECLARATION, authority="declaration",
                     detail={"need": [key_path, key_sha]})
    path = (repo / str(ref)) if not os.path.isabs(str(ref)) else Path(str(ref))
    if not path.is_file():
        return _slot(STATUS_WITHHELD, REASON_REF_MISSING, authority="declaration",
                     detail={"reference": str(ref)})
    actual = sha256_file(path)
    if actual != str(want):
        return _slot(STATUS_WITHHELD, REASON_REF_HASH_MISMATCH, authority="declaration",
                     detail={"reference": str(ref), "declared": want, "actual": actual})
    return _slot(STATUS_ATTESTED, "declared_reference_hash_matches", authority="declaration",
                 detail={"reference": str(ref), "sha256": actual,
                         "claim": decl.get("claim", "")}, awarded=True)


def resolve_evidence_chain(node: dict[str, Any], *,
                           repo: str | os.PathLike[str] = ".") -> dict[str, Any]:
    """单个节点的五元证据链判定（fail-closed，逐槽给状态与原因）。"""
    repo_p = Path(repo).resolve()
    node_id = str(node.get("node_id") or node.get("id") or "")
    run_dir = Path(node.get("run_dir") or "")
    if not run_dir.is_absolute():
        run_dir = repo_p / run_dir
    decl = node.get("declaration") or {}
    slots: dict[str, Any] = {}

    # ---- E1 工件内容锚定（回填器唯一有授予权的槽位）
    if not run_dir.is_dir():
        artifacts: list[dict[str, Any]] = []
        slots[SLOT_E1] = _slot(STATUS_MISSING, REASON_RUN_DIR_MISSING, authority="backfill",
                               detail={"run_dir": str(run_dir)})
        e1_awarded = False
    else:
        artifacts = anchor_artifacts(run_dir, repo_p)
        if not artifacts:
            slots[SLOT_E1] = _slot(STATUS_WITHHELD, REASON_NO_ARTIFACTS, authority="backfill",
                                   detail={"run_dir": str(run_dir)})
            e1_awarded = False
        else:
            ok = all(a["status"] == STATUS_VERIFIED for a in artifacts)
            slots[SLOT_E1] = _slot(
                STATUS_VERIFIED if ok else STATUS_WITHHELD,
                REASON_ARTIFACT_OK if ok else
                (REASON_ARTIFACT_DRIFT if any(a["reason"] == REASON_ARTIFACT_DRIFT
                                              for a in artifacts)
                 else REASON_ARTIFACT_UNTRACKED),
                authority="backfill",
                detail={"n_artifacts": len(artifacts),
                        "n_verified": sum(1 for a in artifacts
                                          if a["status"] == STATUS_VERIFIED),
                        "anchor": "git_blob_sha256_at_last_commit"},
                awarded=ok)
            e1_awarded = ok

    # ---- E2 生产者 manifest（结构与摘要）
    manifest_path = node.get("manifest_path")
    cand = None
    if manifest_path:
        p = Path(manifest_path)
        cand = p if p.is_absolute() else repo_p / p
    elif run_dir.is_dir():
        for name in MANIFEST_BASENAMES:
            if (run_dir / name).is_file():
                cand = run_dir / name
                break
    if cand is not None and cand.is_file():
        try:
            manifest = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            slots[SLOT_E2] = _slot(STATUS_WITHHELD, REASON_MANIFEST_INVALID,
                                   authority="producer",
                                   detail={"path": str(cand), "error": str(exc)})
        else:
            required = ("schema_version", "run_id", "status", "validation_status", "outputs")
            missing = [k for k in required if k not in manifest]
            from tools.abm.ledger import sha256_hex
            digest = sha256_hex(manifest)
            if missing:
                slots[SLOT_E2] = _slot(STATUS_WITHHELD, REASON_MANIFEST_INVALID,
                                       authority="producer",
                                       detail={"path": str(cand), "missing_keys": missing})
            else:
                slots[SLOT_E2] = _slot(STATUS_VERIFIED, REASON_MANIFEST_PRESENT,
                                       authority="producer",
                                       detail={"path": str(cand), "manifest_digest": digest,
                                               "n_outputs": len(manifest.get("outputs") or [])},
                                       awarded=True)
    else:
        slots[SLOT_E2] = _slot(STATUS_WITHHELD, REASON_MANIFEST_MISSING, authority="producer",
                               detail={"note": "回填器不自建 manifest 充数（回填 E1 封顶）"})

    # ---- E3 观测/结论引用
    slots[SLOT_E3] = (_declared_reference(repo_p, decl.get(SLOT_E3))
                      if decl.get(SLOT_E3) else
                      _slot(STATUS_WITHHELD, REASON_NO_DECLARATION, authority="declaration"))

    # ---- E4 可复现记录（提交号 + 命令）
    d4 = decl.get(SLOT_E4)
    if not isinstance(d4, dict):
        slots[SLOT_E4] = _slot(STATUS_WITHHELD, REASON_NO_DECLARATION, authority="declaration")
    else:
        commit = str(d4.get("commit") or "")
        command = str(d4.get("command") or "")
        if not commit or not command:
            slots[SLOT_E4] = _slot(STATUS_WITHHELD, REASON_BAD_DECLARATION,
                                   authority="declaration",
                                   detail={"need": ["commit", "command"]})
        elif not commit_exists(repo_p, commit):
            slots[SLOT_E4] = _slot(STATUS_WITHHELD, REASON_COMMIT_MISSING,
                                   authority="declaration", detail={"commit": commit})
        else:
            slots[SLOT_E4] = _slot(STATUS_ATTESTED, "declared_commit_exists",
                                   authority="declaration",
                                   detail={"commit": commit, "command": command},
                                   awarded=True)

    # ---- E5 独立复核（复核席位 ≠ 生产席位）
    d5 = decl.get(SLOT_E5)
    if not isinstance(d5, dict):
        slots[SLOT_E5] = _slot(STATUS_WITHHELD, REASON_NO_DECLARATION, authority="declaration")
    else:
        reviewer = str(d5.get("reviewer_role") or "")
        producer = str(d5.get("producer_role") or "")
        if reviewer and producer and reviewer == producer:
            slots[SLOT_E5] = _slot(STATUS_WITHHELD, REASON_NOT_INDEPENDENT,
                                   authority="declaration",
                                   detail={"reviewer_role": reviewer})
        else:
            slots[SLOT_E5] = _declared_reference(repo_p, d5)

    # ---- 等级：从 E1 起连续通过的槽位数（回填封顶）
    grade = 0
    for slot in EVIDENCE_SLOTS:
        if slots[slot]["status"] in (STATUS_VERIFIED, STATUS_ATTESTED) and slots[slot]["awarded"]:
            grade += 1
        else:
            break
    auto_only = not any(slots[s]["authority"] != "backfill" and slots[s]["awarded"]
                        for s in EVIDENCE_SLOTS)
    capped = grade > int(BACKFILL_CAP[1]) and auto_only
    if capped:
        grade = int(BACKFILL_CAP[1])
    chain_complete = all(s in slots and slots[s].get("status") for s in EVIDENCE_SLOTS)
    return {
        "node_id": node_id,
        "run_dir": str(run_dir),
        "label": str(node.get("label") or ""),
        "slots": slots,
        "grade": grade,
        "grade_label": f"E{grade}",
        "auto_only": auto_only,
        "backfill_cap": BACKFILL_CAP,
        "chain_complete": chain_complete,
        "n_slots_recorded": sum(1 for s in EVIDENCE_SLOTS if slots.get(s, {}).get("status")),
        "n_artifacts": len(artifacts),
        "n_artifacts_verified": sum(1 for a in artifacts
                                    if a["status"] == STATUS_VERIFIED),
        "artifacts": artifacts,
    }


# --------------------------------------------------------------------------- #
# 批量回填 + 审计表
# --------------------------------------------------------------------------- #
def backfill(nodes: Sequence[dict[str, Any]], *,
             repo: str | os.PathLike[str] = ".") -> dict[str, Any]:
    rows = [resolve_evidence_chain(n, repo=repo) for n in nodes]
    n = len(rows)
    total_slots = n * len(EVIDENCE_SLOTS)
    recorded = sum(r["n_slots_recorded"] for r in rows)
    grades: dict[str, int] = {}
    for r in rows:
        grades[r["grade_label"]] = grades.get(r["grade_label"], 0) + 1
    complete = sum(1 for r in rows if r["chain_complete"])
    art_total = sum(r["n_artifacts"] for r in rows)
    art_ok = sum(r["n_artifacts_verified"] for r in rows)
    return {
        "schema": "blind_calibration/evidence_backfill/v1",
        "n_nodes": n,
        "slots_per_node": len(EVIDENCE_SLOTS),
        "slots_recorded": recorded,
        "slots_expected": total_slots,
        "slot_coverage": (recorded / total_slots) if total_slots else 0.0,
        "chain_complete_rate": (complete / n) if n else 0.0,
        "grade_distribution": dict(sorted(grades.items())),
        "backfill_cap": BACKFILL_CAP,
        "artifacts_total": art_total,
        "artifacts_verified": art_ok,
        "artifact_verify_rate": (art_ok / art_total) if art_total else 0.0,
        "nodes": rows,
    }


def audit_table(result: dict[str, Any]) -> list[dict[str, Any]]:
    """把批量结果摊平成「一行一节点」的审计表（论文级报告直接引用）。"""
    rows: list[dict[str, Any]] = []
    for r in result.get("nodes", []):
        row: dict[str, Any] = {
            "node_id": r["node_id"],
            "label": r["label"],
            "grade": r["grade_label"],
            "auto_only": r["auto_only"],
            "n_artifacts": r["n_artifacts"],
            "n_artifacts_verified": r["n_artifacts_verified"],
        }
        for slot in EVIDENCE_SLOTS:
            s = r["slots"].get(slot, {})
            row[f"{slot}_status"] = s.get("status")
            row[f"{slot}_reason"] = s.get("reason")
        rows.append(row)
    return rows


def write_backfill(result: dict[str, Any], out_path: str | os.PathLike[str]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return str(out_path)