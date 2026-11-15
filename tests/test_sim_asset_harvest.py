"""仿真成果捕获守护的机器判据（FEAT-SIM-DATAHOUSE 子件1）。

被推翻/守住的命题（每条断言对应一条，全部跑在 tmp 沙箱里，不碰真实
/root/sim-assets、不连数据库）：

- P1 「产出」判定：git tracked 的仓库自带文件**不能**被当成成果收走；否则
  1920 棵 worktree 会各收一遍同样的 187 个 benchmark CSV（15GB 噪声）。
- P2 规格阈值：<1KB 的文件不收（>1KB 才收）。
- P3 有界：文件数/总字节超上限必须落 state=partial 并在 capture_reason 写明
  截断数量——不许静默丢。
- P4 event_id 幂等：同 mission 同内容重复捕获只落一行（第二次 status=duplicate
  且不再复制、不再登记）。
- P5 路径越界：清单里出现 ``../`` 必须抛错，不许写到 /root/sim-assets 之外。
- P6 降级可查：源 worktree 不在 → state=missed + capture_reason 含
  worktree_absent，且 disk_path 为 NULL；此前的存量快照要指回来
  （params.already_captured_event_id）。
- P7 每轮计数如实：present+captured / absent+missed / present+无产出 三类
  不许互相顶替。
- P8 JSONB 字面量：中文/引号/反斜杠/``$j`` 都不能破坏 dollar-quoting。
- P9 入口配置抽参：json/yaml/ini 的标量参数要进 params.run_config（物理参数是
  资产的语义价值所在，光存文件不够）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARVESTER = REPO / "scripts" / "sim_asset_harvest.py"


def _load_harvester():
    spec = importlib.util.spec_from_file_location("sim_asset_harvest", HARVESTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _load_harvester()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把模块级的 SIM_ROOT/STAGING/状态文件全部改指 tmp。"""
    sim_root = tmp_path / "sim-assets"
    sim_root.mkdir()
    monkeypatch.setattr(H, "SIM_ROOT", sim_root)
    monkeypatch.setattr(H, "STAGING", sim_root / ".staging")
    monkeypatch.setattr(H, "STATE_PATH", sim_root / "harvest_state.json")
    monkeypatch.setattr(H, "LOG_PATH", sim_root / "harvest.log")
    monkeypatch.setattr(H, "LOCK_PATH", sim_root / ".harvest.lock")
    return sim_root


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True)


def _make_worktree(root: Path, *, tracked_csv: bool = True,
                   untracked: dict[str, int] | None = None) -> Path:
    wt = root / "m900001"
    wt.mkdir()
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@example.com")
    _git(wt, "config", "user.name", "t")
    if tracked_csv:
        bench = wt / "benchmarks"
        bench.mkdir()
        (bench / "reference.csv").write_text("x,y\n" + "\n".join(f"{i},{i * 2}" for i in range(400)))
        _git(wt, "add", "benchmarks/reference.csv")
        _git(wt, "commit", "-qm", "repo-provided reference data")
    for rel, size in (untracked or {}).items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * size)
    return wt


# ── P1/P2 产出判定与阈值 ────────────────────────────────────────────────
def test_discover_excludes_tracked_repo_files(tmp_path):
    wt = _make_worktree(tmp_path, untracked={"runs/case1/psi.npz": 4096})
    disc = H.discover(wt)
    paths = [it["path"] for it in disc["items"]]
    assert paths == ["runs/case1/psi.npz"], paths
    assert disc["skip"]["tracked"] == 1
    assert disc["items"][0]["kind"] == "field"


def test_discover_small_files_are_skipped(tmp_path):
    wt = _make_worktree(tmp_path, untracked={"runs/a/tiny.npz": 100,
                                             "runs/a/ok.npz": 2048})
    disc = H.discover(wt)
    assert [it["path"] for it in disc["items"]] == ["runs/a/ok.npz"]
    assert disc["skip"]["small"] == 1


def test_discover_kind_classification(tmp_path):
    wt = _make_worktree(tmp_path, untracked={
        "r/fields/u.vtk": 2048, "r/curves/cl.csv": 2048,
        "r/geom/hull.stl": 2048, "r/inputs/case_config.json": 2048,
        "r/notes/readme.txt": 2048,
    })
    disc = H.discover(wt)
    kinds = {it["path"]: it["kind"] for it in disc["items"]}
    assert kinds == {"r/fields/u.vtk": "field", "r/curves/cl.csv": "curve",
                     "r/geom/hull.stl": "geometry",
                     "r/inputs/case_config.json": "config"}
    # 场文件排在最前（先保信息量大的证据）
    assert disc["items"][0]["kind"] == "field"


# ── P3 有界（触限记 partial） ─────────────────────────────────────────
def test_discover_caps_files_and_flags_truncation(tmp_path):
    files = {f"runs/c{i}/f.npz": 2048 for i in range(6)}
    wt = _make_worktree(tmp_path, untracked=files)
    disc = H.discover(wt, max_files=3, max_bytes=10 ** 9)
    assert len(disc["items"]) == 3
    assert disc["truncated"] is True
    assert disc["skip"]["capped_files"] == 3


def test_capture_marks_partial_when_truncated(tmp_path, sandbox, monkeypatch):
    inserted = []
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: (inserted.append(ev), 1)[1])
    wt = _make_worktree(tmp_path, untracked={f"runs/c{i}/f.npz": 2048 for i in range(5)})
    disc = H.discover(wt, max_files=2)
    mission = {"id": 900001, "repo": "TensorLBM", "task_key": "", "goal": "g",
               "finance": None, "report_id": "", "model": "m", "status": "succeeded",
               "finished_at": "", "created_at": "", "budget": "{}"}
    res = H.capture(wt, mission, disc, cfg={})
    assert res["status"] == "captured" and res["state"] == "partial"
    ev = inserted[0]
    assert ev["state"] == "partial"
    assert "truncated" in (ev["capture_reason"] or "")
    assert ev["file_count"] == 2


# ── P4 幂等 ──────────────────────────────────────────────────────────
def test_capture_is_idempotent_by_content_digest(tmp_path, sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: (calls.append(ev["event_id"]), 7)[1])
    wt = _make_worktree(tmp_path, untracked={"runs/c/psi.npz": 4096})
    mission = {"id": 900001, "repo": "TensorLBM", "task_key": "case-a", "goal": "g",
               "report_id": "", "model": "m", "status": "succeeded",
               "finished_at": "", "created_at": "", "budget": "{}"}
    first = H.capture(wt, mission, H.discover(wt), cfg={})
    second = H.capture(wt, mission, H.discover(wt), cfg={})
    assert first["status"] == "captured"
    assert second["status"] == "duplicate"
    assert first["event_id"] == second["event_id"]
    # 只登记一次（第二次 ON CONFLICT DO NOTHING 仍是同 event_id 的尝试）
    assert set(calls) == {first["event_id"]}
    # 落盘目录只有一处，且内容 sha256 与清单一致
    files = list((sandbox / first["event_id"]).rglob("*.npz"))
    assert len(files) == 1
    stored = json.loads(json.dumps(first))  # 触发可序列化检查
    assert stored["event_id"] == first["event_id"]


def test_capture_content_change_makes_new_event(tmp_path, sandbox, monkeypatch):
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: 1)
    wt = _make_worktree(tmp_path, untracked={"runs/c/psi.npz": 4096})
    mission = {"id": 900001, "repo": "TensorLBM", "task_key": "", "goal": "g",
               "report_id": "", "model": "m", "status": "succeeded",
               "finished_at": "", "created_at": "", "budget": "{}"}
    first = H.capture(wt, mission, H.discover(wt), cfg={})
    (wt / "runs/c/psi.npz").write_bytes(b"\1" * 8192)
    second = H.capture(wt, mission, H.discover(wt), cfg={})
    assert first["event_id"] != second["event_id"]
    assert second["status"] == "captured"


def test_dry_run_writes_nothing(tmp_path, sandbox, monkeypatch):
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: pytest.fail("dry-run 不许入库"))
    wt = _make_worktree(tmp_path, untracked={"runs/c/psi.npz": 4096})
    mission = {"id": 900001, "repo": "r", "task_key": "", "goal": "g", "report_id": "",
               "model": "", "status": "succeeded", "finished_at": "", "created_at": "",
               "budget": "{}"}
    res = H.capture(wt, mission, H.discover(wt), cfg={}, dry_run=True)
    assert res["status"] == "dry_run"
    assert list(sandbox.iterdir()) == []


# ── P5 路径越界 ──────────────────────────────────────────────────────
def test_safe_join_rejects_traversal(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    with pytest.raises(ValueError):
        H._safe_join(root, "../../etc/passwd")


# ── P6 降级：worktree 已清 → missed ──────────────────────────────────
def test_record_missed_marks_degradation_and_links_prior_snapshot(sandbox, monkeypatch):
    captured = []
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: (captured.append(ev), 3)[1])
    mission = {"id": 12345, "repo": "TensorLBM", "task_key": "case-x", "goal": "g",
               "report_id": "", "model": "", "status": "succeeded",
               "finished_at": "2026-11-15T00:00:00Z", "created_at": "", "budget": "{}"}
    cands = [Path("/srv/tensor/agent/work/m12345")]
    res = H.record_missed(mission, cfg={}, candidates=cands,
                          prior_event="m12345-deadbeef0000")
    assert res["status"] == "missed"
    ev = captured[0]
    assert ev["state"] == "missed"
    assert ev["disk_path"] is None
    assert "worktree_absent" in ev["capture_reason"]
    assert ev["params"]["already_captured_event_id"] == "m12345-deadbeef0000"
    assert ev["event_id"] == "m12345-missed"


def test_record_missed_without_prior_snapshot_says_lost(sandbox, monkeypatch):
    captured = []
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: (captured.append(ev), 3)[1])
    mission = {"id": 999, "repo": "r", "task_key": "", "goal": "g", "report_id": "",
               "model": "", "status": "succeeded", "finished_at": "", "created_at": "",
               "budget": "{}"}
    H.record_missed(mission, cfg={}, candidates=[Path("/nope/m999")], prior_event=None)
    ev = captured[0]
    assert ev["params"]["already_captured_event_id"] is None
    assert "丢失" in ev["capture_reason"]


# ── P7 run_cycle 计数 ────────────────────────────────────────────────
def test_run_cycle_counts_captured_missed_and_empty(tmp_path, sandbox, monkeypatch):
    work_root = tmp_path / "work"
    work_root.mkdir()
    # m101: 有产出；m102: worktree 在但没产出；m103: worktree 不在（未见过→不计 missed）
    _make_worktree(work_root, untracked={"runs/c/psi.npz": 4096}).rename(work_root / "m101")
    empty = work_root / "m102"
    empty.mkdir()
    (empty / "notes.md").write_text("docs only")
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: 1)
    monkeypatch.setattr(H, "prior_capture_event", lambda mid, db=None: None)
    rows = [{"id": 101, "repo": "TensorLBM", "status": "succeeded", "finished_at": "",
             "created_at": "", "task_key": "", "report_id": "", "goal": "", "model": "",
             "budget": "{}"},
            {"id": 102, "repo": "TensorLBM", "status": "succeeded", "finished_at": "",
             "created_at": "", "task_key": "", "report_id": "", "goal": "", "model": "",
             "budget": "{}"},
            {"id": 103, "repo": "TensorLBM", "status": "succeeded", "finished_at": "",
             "created_at": "", "task_key": "", "report_id": "", "goal": "", "model": "",
             "budget": "{}"}]
    monkeypatch.setattr(H, "fetch_missions", lambda **kw: rows)
    st = H.run_cycle(window=10, work_root=work_root, persist_state=False)
    assert st["captured"] == 1, st
    assert st["no_output"] == 1, st
    assert st["absent_unseen"] == 1, st
    assert st["missed"] == 0, st
    assert st["errors"] == 0, st


def test_run_cycle_records_missed_for_previously_seen_worktree(tmp_path, sandbox, monkeypatch):
    work_root = tmp_path / "work"
    work_root.mkdir()
    _make_worktree(work_root, untracked={"runs/c/psi.npz": 4096}).rename(work_root / "m201")
    mission = {"id": 201, "repo": "TensorLBM", "status": "succeeded", "finished_at": "",
               "created_at": "", "task_key": "", "report_id": "", "goal": "", "model": "",
               "budget": "{}"}
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: 1)
    monkeypatch.setattr(H, "prior_capture_event", lambda mid, db=None: "m201-abc123456789")
    monkeypatch.setattr(H, "fetch_missions", lambda **kw: [mission])
    st1 = H.run_cycle(window=10, work_root=work_root, persist_state=True)
    assert st1["captured"] == 1

    # 红队：手动删掉 worktree（模拟 pruner 回收），下一轮必须落 missed
    import shutil as _sh
    _sh.rmtree(work_root / "m201")
    st2 = H.run_cycle(window=10, work_root=work_root, persist_state=True)
    assert st2["missed"] == 1, st2
    assert st2["captured"] == 0


# ── P8 JSONB 字面量安全 ──────────────────────────────────────────────
@pytest.mark.parametrize("payload", [
    {"k": "中文 · 场文件"},
    {"k": "quote ' and \" double"},
    {"k": "back\\slash $jabc$ tag"},
    {"k": "multi\nline\ttab"},
    {"k": "semicolon; DROP TABLE simulation_assets; --"},
])
def test_json_literal_roundtrip(payload):
    """JSONB 字面量必须 base64 过关：SQL 语法层看不到任何内容字节。

    回归 m20058 实锤：dollar-quoting 版本在真实清单上落库报
    `invalid input syntax for type json`，资产白抢收一次。
    """
    import base64 as _b64
    lit = H._json_literal(payload)
    assert lit.startswith("convert_from(decode('") and lit.endswith("', 'base64'), 'UTF8')::jsonb")
    payload_b64 = lit.split("'")[1]
    assert set(payload_b64) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
    assert json.loads(_b64.b64decode(payload_b64).decode("utf-8")) == payload


# ── P9 入口配置抽参 ──────────────────────────────────────────────────
def test_flatten_config_extracts_scalars(tmp_path):
    cfg = tmp_path / "case_config.json"
    cfg.write_text(json.dumps({"Re": 100000, "grid": [64, 64], "name": "cylinder",
                               "nested": {"omega": 1.9, "flag": True}}))
    flat = H._flatten_config(cfg)
    assert flat["Re"] == 100000 and flat["name"] == "cylinder"
    assert flat["nested.omega"] == 1.9 and flat["nested.flag"] is True
    assert "grid" not in flat          # 列表不是标量，不进参数表


def test_flatten_config_reads_ini(tmp_path):
    cfg = tmp_path / "run.ini"
    cfg.write_text("[solver]\nnu = 0.02\nsteps=1000\n")
    flat = H._flatten_config(cfg)
    assert flat == {"solver.nu": "0.02", "solver.steps": "1000"}


def test_capture_records_run_config_params(tmp_path, sandbox, monkeypatch):
    inserted = []
    monkeypatch.setattr(H, "insert_asset", lambda ev, db=None: (inserted.append(ev), 1)[1])
    wt = _make_worktree(tmp_path, untracked={"runs/c/psi.npz": 4096})
    (wt / "runs/c/case_config.json").write_text(
        json.dumps({"Re": 3900, "u_in": 0.05, "notes": "x" * 2000}))   # >1KB 才收
    mission = {"id": 900001, "repo": "TensorLBM", "task_key": "", "goal": "g",
               "report_id": "", "model": "m", "status": "succeeded",
               "finished_at": "", "created_at": "", "budget": '{"max_tokens": 10}'}
    H.capture(wt, mission, H.discover(wt), cfg={})
    run_cfg = inserted[0]["params"]["run_config"]
    assert run_cfg["case_config.json:Re"] == 3900
    assert inserted[0]["params"]["mission"]["budget"]["max_tokens"] == 10


# ── 其他小判据 ───────────────────────────────────────────────────────
def test_worktree_candidates_order_and_alias():
    cands = H.worktree_candidates(7, "TensorLBM", Path("/srv/tensor/agent/work"))
    assert cands[0] == Path("/srv/tensor/agent/work/m7")
    assert Path("/srv/tensor/workspace/TensorLBM/work/m7") in cands
    ship = H.worktree_candidates(8, "ship-performance-platform", Path("/srv/tensor/agent/work"))
    assert any("ship-performance-platform-incoming" in str(c) for c in ship)


def test_case_key_prefers_task_key_then_single_dir():
    items = [{"path": "xval/A/psi.npz", "size": 1, "kind": "field"}]
    assert H.case_key_for({"id": 5, "task_key": "TK"}, items) == "TK"
    assert H.case_key_for({"id": 5, "task_key": ""}, items) == "xval"
    many = items + [{"path": "other/B/psi.npz", "size": 1, "kind": "field"}]
    assert H.case_key_for({"id": 5, "task_key": ""}, many) == "m5"


def test_copy_hash_matches_source(tmp_path):
    src = tmp_path / "a.npz"
    src.write_bytes(os.urandom(5000))
    dst = tmp_path / "out" / "a.npz"
    digest = H._copy_hash(src, dst)
    assert digest == hashlib.sha256(src.read_bytes()).hexdigest()
    assert dst.read_bytes() == src.read_bytes()