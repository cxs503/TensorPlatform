"""判据 D：run-manifest → E1–E5 五元证据链回填（完整率 100%，回填 E1 封顶）。

用自建的临时 git 仓库做夹具（合成域名 alpha/beta，零域词条）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.blind_calibration import evidence as E


# --------------------------------------------------------------------------- #
# 夹具：临时 git 仓库 + 一个「已交付节点」目录
# --------------------------------------------------------------------------- #
def _git(repo, *args):
    env = dict(os.environ,
               GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "runs" / "alpha").mkdir(parents=True)
    (r / "runs" / "alpha" / "metrics.json").write_text('{"score": 1.5}\n', encoding="utf-8")
    (r / "runs" / "alpha" / "notes.md").write_text("# alpha\n\nsynthetic case\n", encoding="utf-8")
    (r / "docs").mkdir()
    (r / "docs" / "alpha_verdict.md").write_text("# verdict: accepted\n", encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.name=fixture", "-c", "user.email=f@x.invalid",
         "commit", "-q", "-m", "deliver alpha")
    return r


def _declaration(repo):
    verdict = repo / "docs" / "alpha_verdict.md"
    head = _git(repo, "rev-parse", "HEAD")
    return {
        "E3": {"reference": "docs/alpha_verdict.md",
               "sha256": E.sha256_file(verdict), "claim": "verdict accepted"},
        "E4": {"commit": head, "command": "python3 -m synthetic.runner alpha"},
        "E5": {"reviewer_role": "verifier", "producer_role": "lead",
               "reference": "docs/alpha_verdict.md",
               "sha256": E.sha256_file(verdict)},
    }


def _add_manifest(repo, run_id: str = "alpha"):
    """写入一份生产者 manifest 并提交（E2 是 E3+ 的前置：链必须连续）。"""
    run_dir = repo / "runs" / run_id
    outputs = []
    for f in sorted(p for p in run_dir.rglob("*") if p.is_file() and p.name != "run_manifest.json"):
        outputs.append({"uri": f.relative_to(run_dir).as_posix(),
                        "media_type": "application/json" if f.suffix == ".json" else "text/plain",
                        "checksum_sha256": E.sha256_file(f),
                        "size_bytes": f.stat().st_size})
    manifest = {"schema_version": 1, "run_id": run_id, "status": "succeeded",
                "validation_status": "passed", "outputs": outputs}
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=fixture", "-c", "user.email=f@x.invalid",
         "commit", "-q", "-m", f"manifest {run_id}")


def _node(repo, **kw):
    node = {"node_id": "alpha", "run_dir": "runs/alpha", "label": "synthetic"}
    node.update(kw)
    return node


# --------------------------------------------------------------------------- #
# 槽位判定
# --------------------------------------------------------------------------- #
def test_e1_verified_by_git_anchor_alone(repo):
    res = E.resolve_evidence_chain(_node(repo), repo=repo)
    assert res["slots"]["E1"]["status"] == E.STATUS_VERIFIED
    assert res["slots"]["E1"]["reason"] == E.REASON_ARTIFACT_OK
    assert res["n_artifacts"] == 2 and res["n_artifacts_verified"] == 2
    # 没有生产者 manifest、没有声明 → E2–E5 一律 withhold，且给出机器可读原因
    assert res["slots"]["E2"]["reason"] == E.REASON_MANIFEST_MISSING
    for slot in ("E3", "E4", "E5"):
        assert res["slots"][slot]["status"] == E.STATUS_WITHHELD
        assert res["slots"][slot]["reason"] == E.REASON_NO_DECLARATION


def test_backfill_cap_keeps_auto_only_node_at_e1(repo):
    res = E.resolve_evidence_chain(_node(repo), repo=repo)
    assert E.BACKFILL_CAP == "E1"
    assert res["auto_only"] is True
    assert res["grade_label"] == "E1", "回填器自己只能给到 E1"


def test_chain_completeness_counts_withheld_as_recorded(repo):
    """完整率 = 每槽都有机器可读状态（不是「都通过」）。"""
    res = E.resolve_evidence_chain(_node(repo), repo=repo)
    assert res["chain_complete"] is True
    assert res["n_slots_recorded"] == len(E.EVIDENCE_SLOTS) == 5


def test_missing_run_dir_still_records_all_slots(repo):
    res = E.resolve_evidence_chain({"node_id": "ghost", "run_dir": "runs/ghost"}, repo=repo)
    assert res["slots"]["E1"]["status"] == E.STATUS_MISSING
    assert res["slots"]["E1"]["reason"] == E.REASON_RUN_DIR_MISSING
    assert res["chain_complete"] is True
    assert res["grade_label"] == "E0"


def test_artifact_drift_is_detected_and_fails_closed(repo):
    (repo / "runs" / "alpha" / "metrics.json").write_text('{"score": 9.9}\n', encoding="utf-8")
    res = E.resolve_evidence_chain(_node(repo), repo=repo)
    assert res["slots"]["E1"]["status"] == E.STATUS_WITHHELD
    assert res["slots"]["E1"]["reason"] == E.REASON_ARTIFACT_DRIFT
    assert res["n_artifacts_verified"] == 1
    assert res["grade_label"] == "E0", "E1 不过 → 后续槽位不得被授予"


def test_untracked_artifact_is_withheld_not_trusted(repo):
    (repo / "runs" / "alpha" / "extra.bin").write_bytes(b"\x00\x01")
    res = E.resolve_evidence_chain(_node(repo), repo=repo)
    assert res["slots"]["E1"]["status"] == E.STATUS_WITHHELD
    assert res["slots"]["E1"]["reason"] == E.REASON_ARTIFACT_UNTRACKED


# --------------------------------------------------------------------------- #
# E2：生产者 manifest（回填器不自建 manifest 充数）
# --------------------------------------------------------------------------- #
def test_producer_manifest_raises_grade_to_e2(repo):
    manifest = {
        "schema_version": 1, "run_id": "alpha", "status": "succeeded",
        "validation_status": "passed",
        "outputs": [{"uri": "metrics.json", "media_type": "application/json",
                     "checksum_sha256": E.sha256_file(repo / "runs/alpha/metrics.json"),
                     "size_bytes": (repo / "runs/alpha/metrics.json").stat().st_size}],
    }
    (repo / "runs" / "alpha" / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=fixture", "-c", "user.email=f@x.invalid",
         "commit", "-q", "-m", "add manifest")
    res = E.resolve_evidence_chain(_node(repo), repo=repo)
    assert res["slots"]["E2"]["status"] == E.STATUS_VERIFIED
    assert res["slots"]["E2"]["reason"] == E.REASON_MANIFEST_PRESENT
    assert res["grade_label"] == "E2"
    assert res["auto_only"] is False


def test_invalid_producer_manifest_is_withheld(repo):
    (repo / "runs" / "alpha" / "run_manifest.json").write_text('{"schema_version": 1}',
                                                              encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=fixture", "-c", "user.email=f@x.invalid",
         "commit", "-q", "-m", "bad manifest")
    res = E.resolve_evidence_chain(_node(repo), repo=repo)
    assert res["slots"]["E2"]["status"] == E.STATUS_WITHHELD
    assert res["slots"]["E2"]["reason"] == E.REASON_MANIFEST_INVALID


# --------------------------------------------------------------------------- #
# E3–E5：外部声明 + 机器复算引用
# --------------------------------------------------------------------------- #
def test_full_declaration_reaches_e5(repo):
    """E1..E5 连续：生产者 manifest（E2）是 E3–E5 的前置，链不可跳级。"""
    _add_manifest(repo)
    res = E.resolve_evidence_chain(_node(repo, declaration=_declaration(repo)), repo=repo)
    for slot in ("E3", "E4", "E5"):
        assert res["slots"][slot]["status"] == E.STATUS_ATTESTED, res["slots"][slot]
    assert res["grade_label"] == "E5"
    assert res["auto_only"] is False


def test_declared_reference_hash_mismatch_is_caught(repo):
    decl = _declaration(repo)
    decl["E3"]["sha256"] = "0" * 64
    res = E.resolve_evidence_chain(_node(repo, declaration=decl), repo=repo)
    assert res["slots"]["E3"]["status"] == E.STATUS_WITHHELD
    assert res["slots"]["E3"]["reason"] == E.REASON_REF_HASH_MISMATCH
    assert res["grade_label"] == "E1", "E3 断链 → 不得跳到 E4/E5"


def test_declared_reference_missing_file(repo):
    decl = _declaration(repo)
    decl["E3"]["reference"] = "docs/not_there.md"
    res = E.resolve_evidence_chain(_node(repo, declaration=decl), repo=repo)
    assert res["slots"]["E3"]["reason"] == E.REASON_REF_MISSING


def test_declared_commit_must_exist(repo):
    decl = _declaration(repo)
    decl["E4"]["commit"] = "deadbeef" * 5
    res = E.resolve_evidence_chain(_node(repo, declaration=decl), repo=repo)
    assert res["slots"]["E4"]["status"] == E.STATUS_WITHHELD
    assert res["slots"]["E4"]["reason"] == E.REASON_COMMIT_MISSING


def test_self_review_does_not_count_as_independent(repo):
    _add_manifest(repo)
    decl = _declaration(repo)
    decl["E5"]["reviewer_role"] = decl["E5"]["producer_role"]
    res = E.resolve_evidence_chain(_node(repo, declaration=decl), repo=repo)
    assert res["slots"]["E5"]["status"] == E.STATUS_WITHHELD
    assert res["slots"]["E5"]["reason"] == E.REASON_NOT_INDEPENDENT
    assert res["grade_label"] == "E4", "自批被拦下后等级止于 E4"


def test_malformed_declaration_is_withheld(repo):
    res = E.resolve_evidence_chain(_node(repo, declaration={"E3": {"claim": "no ref"}}),
                                  repo=repo)
    assert res["slots"]["E3"]["reason"] == E.REASON_BAD_DECLARATION


# --------------------------------------------------------------------------- #
# 批量 + 审计表 + 「回填 E1 封顶」不变量
# --------------------------------------------------------------------------- #
def test_batch_coverage_and_grade_distribution(repo, tmp_path):
    (repo / "runs" / "beta").mkdir()
    (repo / "runs" / "beta" / "out.txt").write_text("beta\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=fixture", "-c", "user.email=f@x.invalid",
         "commit", "-q", "-m", "deliver beta")
    nodes = [{"node_id": "alpha", "run_dir": "runs/alpha"},
             {"node_id": "beta", "run_dir": "runs/beta"},
             {"node_id": "gamma", "run_dir": "runs/gamma"}]
    res = E.backfill(nodes, repo=repo)
    assert res["n_nodes"] == 3
    assert res["slot_coverage"] == pytest.approx(1.0)
    assert res["chain_complete_rate"] == pytest.approx(1.0)
    assert res["grade_distribution"] == {"E0": 1, "E1": 2}
    assert res["backfill_cap"] == "E1"
    table = E.audit_table(res)
    assert len(table) == 3
    assert {"grade", "auto_only", "E1_status", "E5_reason"} <= set(table[0])
    out = E.write_backfill(res, tmp_path / "bf.json")
    assert json.loads(open(out, encoding="utf-8").read())["n_nodes"] == 3


def test_auto_only_never_exceeds_cap(repo):
    """不变量：没有任何外部证据的节点，等级不得超过 E1。"""
    for decl in ({}, {"E2": {}}):
        res = E.resolve_evidence_chain(_node(repo, declaration=decl), repo=repo)
        if res["auto_only"]:
            assert res["grade"] <= int(E.BACKFILL_CAP[1])