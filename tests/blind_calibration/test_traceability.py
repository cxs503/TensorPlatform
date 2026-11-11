"""判据 E：内容可追溯 —— 10 个已交付节点的工件 SHA256 与「交付提交里的 blob」对账。

两层验证
========
1. **真实节点**（本仓 10 个已交付节点）：回填器逐文件复算的 sha256 必须与
   「最后改动该文件的提交」里的 blob sha256 一致（独立锚，不依赖本次运行）。
2. **篡改检测**：把真实字节搬进临时仓库后改一个字节，锚定必须断——证明对账不是
   「自己算一遍再跟自己比」的空转。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.blind_calibration import evidence as E

CONFIG = Path(ROOT) / "config" / "blind_calibration_nodes.v1.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="f", GIT_AUTHOR_EMAIL="f@example.invalid",
               GIT_COMMITTER_NAME="f", GIT_COMMITTER_EMAIL="f@example.invalid")
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# --------------------------------------------------------------------------- #
# 1) 真实节点的可追溯性
# --------------------------------------------------------------------------- #
def test_config_declares_ten_delivered_nodes():
    cfg = _config()
    nodes = cfg["nodes"]
    assert len(nodes) == 10
    assert len({n["node_id"] for n in nodes}) == 10
    for n in nodes:
        assert (Path(ROOT) / n["run_dir"]).is_dir(), f"节点目录不存在: {n['run_dir']}"


def test_all_artifacts_of_ten_nodes_are_sha256_anchored():
    cfg = _config()
    result = E.backfill([{"node_id": n["node_id"], "run_dir": n["run_dir"]} for n in cfg["nodes"]],
                        repo=ROOT)
    assert result["n_nodes"] == 10
    assert result["artifact_verify_rate"] == pytest.approx(1.0), \
        f"未锚定的工件: {[ (n['node_id'], a['file'], a['reason']) for n in result['nodes'] for a in n['artifacts'] if a['status'] != E.STATUS_VERIFIED ]}"
    assert result["artifacts_total"] > 0
    for node in result["nodes"]:
        assert node["slots"]["E1"]["status"] == E.STATUS_VERIFIED
        for art in node["artifacts"]:
            assert art["anchor_commit"] and len(art["anchor_commit"]) == 40
            assert art["sha256"] == art["anchor_sha256"]


def test_sha256_recomputed_independently_matches_record():
    """测试自己用 hashlib 再算一遍，防止「记录值来自别处」的假一致。"""
    cfg = _config()
    result = E.backfill([{"node_id": n["node_id"], "run_dir": n["run_dir"]} for n in cfg["nodes"]],
                        repo=ROOT)
    for node in result["nodes"]:
        for art in node["artifacts"]:
            path = Path(ROOT) / art["repo_path"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert art["sha256"] == digest
            assert art["size_bytes"] == path.stat().st_size


def test_anchor_is_git_blob_not_working_tree_copy():
    """锚必须有独立来源：git 里同一路径的 blob 复算值 == 记录值。"""
    cfg = _config()
    node = cfg["nodes"][0]
    arts = E.anchor_artifacts(Path(ROOT) / node["run_dir"], Path(ROOT))
    assert arts
    for art in arts[:5]:
        blob = subprocess.run(["git", "-C", ROOT, "cat-file", "blob",
                               f"{art['anchor_commit']}:{art['repo_path']}"],
                              capture_output=True, timeout=120)
        assert blob.returncode == 0
        assert hashlib.sha256(blob.stdout).hexdigest() == art["sha256"]


# --------------------------------------------------------------------------- #
# 2) 篡改检测（用真实字节做夹具）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def tamper_repo(tmp_path):
    src = Path(ROOT) / "artifacts" / "m1386" / "results.json"
    repo = tmp_path / "repo"
    (repo / "runs" / "alpha").mkdir(parents=True)
    (repo / "runs" / "alpha" / "results.json").write_bytes(src.read_bytes())
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=f", "-c", "user.email=f@example.invalid",
         "commit", "-q", "-m", "deliver")
    return repo


def test_tampered_byte_breaks_the_anchor(tamper_repo):
    node = {"node_id": "alpha", "run_dir": "runs/alpha"}
    ok = E.resolve_evidence_chain(node, repo=tamper_repo)
    assert ok["slots"]["E1"]["status"] == E.STATUS_VERIFIED

    path = tamper_repo / "runs" / "alpha" / "results.json"
    data = bytearray(path.read_bytes())
    data[-1] = (data[-1] + 1) % 256          # 改一个字节
    path.write_bytes(bytes(data))

    bad = E.resolve_evidence_chain(node, repo=tamper_repo)
    assert bad["slots"]["E1"]["status"] == E.STATUS_WITHHELD
    assert bad["slots"]["E1"]["reason"] == E.REASON_ARTIFACT_DRIFT
    assert bad["grade_label"] == "E0"


def test_added_file_without_commit_is_withheld(tamper_repo):
    (tamper_repo / "runs" / "alpha" / "late.json").write_text("{}", encoding="utf-8")
    res = E.resolve_evidence_chain({"node_id": "alpha", "run_dir": "runs/alpha"},
                                   repo=tamper_repo)
    assert res["slots"]["E1"]["status"] == E.STATUS_WITHHELD
    assert res["n_artifacts"] == 2 and res["n_artifacts_verified"] == 1


def test_traceability_audit_table_lists_every_node():
    cfg = _config()
    result = E.backfill([{"node_id": n["node_id"], "run_dir": n["run_dir"]} for n in cfg["nodes"]],
                        repo=ROOT)
    table = E.audit_table(result)
    assert [row["node_id"] for row in table] == [n["node_id"] for n in cfg["nodes"]]
    assert all(row["E1_status"] == E.STATUS_VERIFIED for row in table)