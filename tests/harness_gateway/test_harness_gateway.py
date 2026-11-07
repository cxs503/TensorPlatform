"""harness_gateway 门控仓测试（含红队负例）。

判据全部是机器可裁的：
    * 脱敏：镜像里搜不到明文口令，且脱敏后仍可编译；
    * 编译门：语法错 / 重复定义 / 明文凭据 → FAIL；
    * 金丝雀门：沙箱里真跑真代码路径，网络外联被拦，隔离不可信时**降级而非硬跑**；
    * 回滚门：git checkout + 字节级 sha256 逐文件比对；
    * 管线：正例 PASS，语法注入/运行期注入 → FAIL_ROLLED_BACK 且字节复原；
    * 诊断单：白名单 op 可执行，未知 op 判 NOT_EXECUTABLE（不假装成功）。
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from harness_gateway import mirror as mirror_mod
from harness_gateway import redact
from harness_gateway.diagnostics import DiagnosticExecutor
from harness_gateway.gates import canary_gate, compile_gate, rollback_gate
from harness_gateway.pipeline import HarnessDeployPipeline, PatchSpec, Replacement
from harness_gateway.spec import HarnessUnit

DAEMON_SRC = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    """demo daemon: 口令内联（历史坏法，脱敏门要抓），隔离可信（不写死 PATH）"""
    import subprocess

    PGENV = {"PGPASSWORD": "s3cr3t-Value-9x"}


    def q(sql):
        p = subprocess.run(["psql", "-U", "postgres", "-d", "tensorplatform", "-t", "-A", "-c", sql],
                           capture_output=True, text=True)
        return p.stdout.strip() if not p.returncode else ""


    def main():
        online = q("SELECT count(*) FROM t;")
        print("online=%s" % online)
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())
    '''
)

#: 隔离不可信的坏法：脚本把 PATH 写死 → PATH-shim 会被绕过（实测事故形态）
HAZARD_SRC = DAEMON_SRC.replace(
    'capture_output=True, text=True)',
    'capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **PGENV})',
)

WORKER_SRC = textwrap.dedent(
    """\
    def load_env(p):
        return {}


    def main():
        return 0
    """
)

NET_SRC = textwrap.dedent(
    """\
    import json
    import urllib.request


    def main():
        urllib.request.urlopen("http://127.0.0.1:9/ping", timeout=1)
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)

DAEMON_UNIT = HarnessUnit(
    name="demo_daemon",
    role="daemon",
    relpath="demo_daemon.py",
    canary="exec_dry_run",
    expect_symbols=("q", "main"),
    expect_exit=(0,),
)
WORKER_UNIT = HarnessUnit(
    name="demo_worker",
    role="worker",
    relpath="demo_worker.py",
    canary="symbol_probe",
    expect_symbols=("load_env", "main"),
)
NET_UNIT = HarnessUnit(
    name="net_daemon",
    role="daemon",
    relpath="net_daemon.py",
    canary="exec_dry_run",
    expect_symbols=("main",),
)
UNITS = (DAEMON_UNIT, WORKER_UNIT)


@pytest.fixture
def src_root(tmp_path: Path) -> Path:
    root = tmp_path / "live_harness"
    root.mkdir()
    (root / "demo_daemon.py").write_text(DAEMON_SRC, encoding="utf-8")
    (root / "demo_worker.py").write_text(WORKER_SRC, encoding="utf-8")
    (root / "net_daemon.py").write_text(NET_SRC, encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path: Path, src_root: Path) -> Path:
    """门控仓（git 工作树）+ 已入库镜像。"""
    repo = tmp_path / "gated_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "gate@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "gate"], cwd=repo, check=True)
    mirror_mod.mirror_harness(src_root, repo / "mirror", UNITS)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "mirror"], cwd=repo, check=True)
    return repo


def _pipeline(repo: Path, src_root: Path, units=UNITS, **kw) -> HarnessDeployPipeline:
    return HarnessDeployPipeline(
        repo_root=repo,
        mirror_root=repo / "mirror",
        src_root=src_root,
        units=units,
        artifacts_dir=repo / "artifacts",
        shim_exit=1,
        **kw,
    )


# ---------------------------------------------------------------- redact
def test_redaction_removes_credentials_and_keeps_syntax():
    text = (
        'PGPASSWORD = "s3cr3t-Value-9x"\n'
        'PGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", "s3cr3t-Value-9x")}\n'
        'TOKEN = "sk-abcdefghijklmnopqrstuvwxyz"\n'
    )
    out, report = redact.redact(text)
    assert "s3cr3t" not in out
    assert report.count >= 3
    assert redact.scan(out) == []
    compile(out, "<mirror>", "exec")  # 脱敏后仍是合法 python


def test_redaction_is_idempotent():
    out1, _ = redact.redact(DAEMON_SRC)
    out2, report2 = redact.redact(out1)
    assert out2 == out1
    assert report2.count == 0


# ---------------------------------------------------------------- mirror
def test_mirror_manifest_hashes_and_no_secret(repo, src_root):
    manifest = json.loads((repo / "mirror" / "MIRROR_MANIFEST.json").read_text())
    assert manifest["unit_count"] == len(UNITS)
    assert manifest["redaction_total"] >= 1
    mirrored = (repo / "mirror" / "demo_daemon.py").read_text()
    assert "s3cr3t" not in mirrored
    unit = next(u for u in manifest["units"] if u["name"] == "demo_daemon")
    assert unit["source_sha256"] != unit["mirror_sha256"]
    assert mirror_mod.verify_mirror(src_root, repo / "mirror", UNITS) == []


def test_verify_detects_tamper_and_source_drift(repo, src_root):
    (repo / "mirror" / "demo_worker.py").write_text("def hacked():\n    return 1\n", encoding="utf-8")
    drifts = mirror_mod.verify_mirror(src_root, repo / "mirror", UNITS)
    assert [d.kind for d in drifts] == ["mirror_tamper"]

    subprocess.run(["git", "checkout", "--", "mirror/demo_worker.py"], cwd=repo, check=True)
    (src_root / "demo_worker.py").write_text("def load_env(p):\n    return {'x': 1}\n", encoding="utf-8")
    drifts = mirror_mod.verify_mirror(src_root, repo / "mirror", UNITS)
    assert [d.kind for d in drifts] == ["source_drift"]


def test_mirror_sync_does_not_clobber_staged_patch(repo, src_root):
    """已过门未部署的补丁不能被一次镜像同步静默抹掉。"""
    patched = (repo / "mirror" / "demo_daemon.py").read_text().replace(
        'PGENV = {"PGPASSWORD": "<REDACTED:pgpassword_literal>"}',
        'import os\n\nPGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", "")}',
    )
    (repo / "mirror" / "demo_daemon.py").write_text(patched, encoding="utf-8")
    manifest = mirror_mod.mirror_harness(src_root, repo / "mirror", UNITS)
    assert manifest.to_dict()["staged_patch_units"] == ["demo_daemon"]
    assert "os.environ.get" in (repo / "mirror" / "demo_daemon.py").read_text()
    # 显式 force 才允许覆盖（= 放弃该补丁）
    mirror_mod.mirror_harness(src_root, repo / "mirror", UNITS, force=True)
    assert "s3cr3t" not in (repo / "mirror" / "demo_daemon.py").read_text()
    assert "<REDACTED:pgpassword_literal>" in (repo / "mirror" / "demo_daemon.py").read_text()


# ---------------------------------------------------------------- gate 1
def test_compile_gate_passes_clean_and_catches_syntax_error(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("VALUE = 1\n\n\ndef f():\n    return VALUE\n", encoding="utf-8")
    assert compile_gate.run_compile_gate([good]).ok

    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n    return 1\n", encoding="utf-8")
    res = compile_gate.run_compile_gate([bad])
    assert not res.ok and "compile error" in res.detail


def test_compile_gate_catches_duplicate_defs(tmp_path):
    dup = tmp_path / "dup.py"
    dup.write_text("def f():\n    return 1\n\n\ndef f():\n    return 2\n", encoding="utf-8")
    res = compile_gate.run_compile_gate([dup])
    assert not res.ok
    assert res.evidence["files"][0]["duplicate_defs"] == ["f"]


def test_compile_gate_catches_plaintext_credential(tmp_path):
    leak = tmp_path / "leak.py"
    leak.write_text('PGPASSWORD = "s3cr3t-Value-9x"\n', encoding="utf-8")
    res = compile_gate.run_compile_gate([leak])
    assert not res.ok and "credential" in res.detail


# ---------------------------------------------------------------- gate 2
HERMETIC_DAEMON_SRC = textwrap.dedent(
    """\
    import subprocess


    def q(sql):
        p = subprocess.run(["psql", "-U", "postgres", "-t", "-A", "-c", sql],
                           capture_output=True, text=True)
        return p.stdout.strip() if not p.returncode else ""


    def main():
        print("online=%s" % q("SELECT count(*) FROM t;"))
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)


def test_canary_runs_real_code_path_against_shims(tmp_path):
    """隔离可信时：真跑一遍真代码路径，psql 被 shim 掉，零生产副作用。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "demo_daemon.py").write_text(HERMETIC_DAEMON_SRC, encoding="utf-8")
    res = canary_gate.run_canary_gate(src, (DAEMON_UNIT,), sandbox_root=tmp_path / "sb")
    unit = res.evidence["units"][0]
    assert res.ok, res.detail
    assert unit["mode"] == "exec_dry_run"
    assert unit["shim_calls"] >= 1  # 真代码路径被打到，而不是空壳
    assert unit["degraded"] is False
    # shim 已记录到 SELECT，但没有任何写语句被尝试
    assert unit["destructive_statements_attempted"] == []


def test_canary_blocks_outbound_network(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "net_daemon.py").write_text(NET_SRC, encoding="utf-8")
    res = canary_gate.run_canary_gate(src, (NET_UNIT,), sandbox_root=tmp_path / "sb2")
    assert not res.ok
    unit = res.evidence["units"][0]
    assert unit["traceback"] and "network blocked" in unit["stderr_tail"]


def test_canary_degrades_when_isolation_untrustworthy(tmp_path):
    """脚本写死 PATH → PATH-shim 会被绕过 → 必须降级为符号探针，绝不执行。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "demo_daemon.py").write_text(DAEMON_SRC, encoding="utf-8")
    sentinel = tmp_path / "EXECUTED"
    hazard = HAZARD_SRC.replace('print("online=%s" % online)',
                                f'open(r"{sentinel}", "w").write("ran")')
    (src / "demo_daemon.py").write_text(hazard, encoding="utf-8")
    res = canary_gate.run_canary_gate(src, (DAEMON_UNIT,), sandbox_root=tmp_path / "sb3")
    unit = res.evidence["units"][0]
    assert unit["degraded"] is True
    assert unit["mode"] == "symbol_probe"
    assert not sentinel.exists(), "隔离不可信的单元被执行了"


def test_canary_fails_on_timeout(tmp_path):
    hang_src = "import time\n\n\ndef main():\n    time.sleep(30)\n\n\nmain()\n"
    src = tmp_path / "src"
    src.mkdir()
    (src / "hang.py").write_text(hang_src, encoding="utf-8")
    unit = HarnessUnit(name="hang", role="daemon", relpath="hang.py", canary="exec_dry_run",
                       expect_symbols=("main",), timeout_s=1)
    res = canary_gate.run_canary_gate(src, (unit,), sandbox_root=tmp_path / "sb4")
    assert not res.ok and res.evidence["units"][0]["fail_reason"] == "timeout"


def test_sandbox_selfcheck_is_fail_closed(tmp_path):
    sb = canary_gate.build_sandbox(tmp_path / "sb5")
    assert canary_gate.verify_sandbox(sb)["ok"]
    # 故意把 shim 目录搬到不存在的地方 → 自检必须红
    sb.bin_dir = tmp_path / "nowhere"
    assert canary_gate.verify_sandbox(sb)["ok"] is False


# ---------------------------------------------------------------- gate 3
def test_rollback_restores_bytes_via_git(repo):
    rels = ["mirror/" + u.mirror_relpath for u in UNITS]
    ckpt = rollback_gate.checkpoint(repo, rels, run_dir=repo / "artifacts" / "ck")
    target = repo / "mirror" / "demo_worker.py"
    before = target.read_bytes()
    target.write_text("BROKEN(\n", encoding="utf-8")
    res = rollback_gate.rollback(repo, ckpt)
    assert res.ok, res.detail
    assert target.read_bytes() == before
    assert all(f["match"] for f in res.evidence["files"])


def test_rollback_drill_round_trip(tmp_path):
    res = rollback_gate.drill(tmp_path)
    assert res.ok
    ev = res.evidence
    assert ev["injection_changed_bytes"] and ev["restore_restored_bytes"]


# ---------------------------------------------------------------- pipeline
def _real_patch() -> PatchSpec:
    return PatchSpec(
        label="harden_demo_daemon",
        rationale="拆明文口令 + 拆死 PATH + psql 超时上限",
        replacements=[
            Replacement(
                file="demo_daemon.py",
                old='PGENV = {"PGPASSWORD": "<REDACTED:pgpassword_literal>"}',
                new='import os\n\nPGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", "")}',
            )
        ],
    )


def test_pipeline_happy_path_applies_patch_and_reports(repo, src_root):
    pipe = _pipeline(repo, src_root)
    patch = _real_patch()
    # 注入脱敏占位符后的真实 mirror 内容作为补丁锚点
    patch.replacements[0] = Replacement(
        file="demo_daemon.py",
        old='import subprocess\n',
        new='import os\nimport subprocess\n\nPGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", "")}\n',
    )
    report = pipe.run(patch)
    assert report["verdict"] == "PASS", report.get("failure_detail")
    assert report["caught_by"] is None
    assert [s["gate"] for s in report["stages"]][:4] == [
        "preflight_mirror_verify",
        "git_rollback_drill",
        "checkpoint",
        "apply_patch",
    ]
    mirrored = (repo / "mirror" / "demo_daemon.py").read_text()
    assert "os.environ.get" in mirrored
    assert Path(report["artifacts"]["patch_diff"]).exists()
    stage_names = {s["gate"]: s["status"] for s in report["stages"]}
    assert stage_names["py_compile"] == "PASS" and stage_names["canary_single_process"] == "PASS"


@pytest.mark.parametrize(
    "malicious,expected_gate",
    [
        ('def broken(:', 'py_compile'),                # 语法错 → 编译门
        ('raise RuntimeError("injected")', 'canary_single_process'),  # 运行期崩 → 金丝雀门
    ],
)
def test_pipeline_red_team_negative_control_rolls_back(repo, src_root, malicious, expected_gate):
    pipe = _pipeline(repo, src_root)
    target = repo / "mirror" / "demo_daemon.py"
    before = target.read_bytes()
    patch = PatchSpec(
        label="red_team_injection",
        replacements=[Replacement(file="demo_daemon.py", old='print("online=%s" % online)', new=malicious)],
    )
    report = pipe.run(patch, negative_control="injected-fault")
    assert report["verdict"] == "FAIL_ROLLED_BACK", report
    assert report["caught_by"] == expected_gate
    assert target.read_bytes() == before, "回滚门必须字节级复原"
    rb = report["rollback"]
    assert rb is not None and all(f["match"] for f in rb["evidence"]["files"])
    assert report["post_state"]["demo_daemon.py"] == mirror_mod.sha256_bytes(before)


def test_pipeline_blocks_on_source_drift_repo_is_untouched(repo, src_root):
    (src_root / "demo_worker.py").write_text("def load_env(p):\n    return {'y': 2}\n", encoding="utf-8")
    pipe = _pipeline(repo, src_root)
    before = (repo / "mirror" / "demo_worker.py").read_bytes()
    report = pipe.run(_real_patch())
    assert report["verdict"] == "BLOCKED_DRIFT"
    assert (repo / "mirror" / "demo_worker.py").read_bytes() == before


def test_pipeline_rejects_patch_outside_whitelist(repo, src_root):
    pipe = _pipeline(repo, src_root)
    patch = PatchSpec(label="out_of_scope", replacements=[Replacement(file="not_gated.py", old="a", new="b")])
    with pytest.raises(KeyError):
        pipe.run(patch)


def test_pipeline_patch_anchor_mismatch_rolls_back(repo, src_root):
    pipe = _pipeline(repo, src_root)
    target = repo / "mirror" / "demo_daemon.py"
    before = target.read_bytes()
    patch = PatchSpec(label="bad_anchor", replacements=[Replacement(file="demo_daemon.py", old="NOT_THERE", new="x")])
    report = pipe.run(patch)
    assert report["verdict"] == "FAIL_ROLLED_BACK"
    assert report["caught_by"] == "apply_patch"
    assert target.read_bytes() == before


def test_deploy_plan_blocks_units_carrying_redaction_placeholder(repo, src_root):
    """镜像里的凭据是占位符 → 不可直投生产（否则把口令换成占位符），必须 action=blocked_redaction。"""
    pipe = _pipeline(repo, src_root)
    plan = pipe.deploy_plan(src_root)
    assert plan["actions"] == {"noop": 1, "blocked_redaction": 1}
    blocked = next(u for u in plan["units"] if u["action"] == "blocked_redaction")
    assert blocked["carries_redaction_placeholder"] is True
    out = pipe.apply_deploy(src_root, dry_run=True)
    assert out["writes"] == []  # 没有任何"待写"
    assert "s3cr3t" in (src_root / "demo_daemon.py").read_text()  # 生产文件没被动


def test_preflight_allows_staged_deploy_ahead_of_live(repo, src_root):
    """镜像领先生产（补丁已过门、部署还没做）= AHEAD，允许继续，而不是当成漂移拦死。"""
    pipe = _pipeline(repo, src_root)
    patch = PatchSpec(
        label="extract_credential",
        replacements=[
            Replacement(
                file="demo_daemon.py",
                old='PGENV = {"PGPASSWORD": "<REDACTED:pgpassword_literal>"}',
                new='import os\n\nPGENV = {"PGPASSWORD": os.environ.get("TP_PG_PASSWORD", "")}',
            )
        ],
    )
    report = pipe.run(patch)
    assert report["verdict"] == "PASS", report.get("failure_detail")
    # 补丁提交后再跑一次（门体自检）：此时镜像领先生产 → AHEAD，不拦
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "patched"], cwd=repo, check=True)
    again = pipe.run(None)
    assert again["verdict"] == "PASS", again.get("failure_detail")
    pre = again["stages"][0]
    assert pre["evidence"]["units_ahead_of_live_pending_deploy"] == ["demo_daemon"]
    assert pre["evidence"]["units_in_sync_with_live"] == ["demo_worker"]
    # 补丁后镜像已无占位符 → 该单元可部署
    plan = pipe.deploy_plan(src_root)
    assert next(u for u in plan["units"] if u["unit"] == "demo_daemon")["action"] == "update"


def test_preflight_blocks_mirror_tamper(repo, src_root):
    pipe = _pipeline(repo, src_root)
    (repo / "mirror" / "demo_worker.py").write_text("def load_env(p):\n    return {}\n\n\ndef x():\n    return 1\n",
                                                    encoding="utf-8")
    report = pipe.run(_real_patch())
    assert report["verdict"] == "BLOCKED_MIRROR_TAMPER"


# ---------------------------------------------------------------- repo hygiene
def test_dsn_script_has_no_plaintext_credential():
    """回归：scripts/mission_failure_analysis.py 曾把生产库口令写进 DB_URL 缺省值。"""
    path = Path(__file__).resolve().parents[2] / "scripts" / "mission_failure_analysis.py"
    if not path.exists():
        pytest.skip("script not present")
    findings = redact.scan(path.read_text(encoding="utf-8"))
    assert findings == [], f"plaintext credential in {path}: {[f.pattern for f in findings]}"


# ---------------------------------------------------------------- diagnostics
def test_diagnostic_order_is_executable_and_rejects_unknown_op(repo, src_root):
    order = {
        "schema": "coordinator-diagnostic-order/1",
        "order_id": "TEST-D1",
        "issued_by": "coordinator",
        "items": [
            {"id": "d1", "op": "mirror_verify", "expect": {"drifts": 0}},
            {"id": "d2", "op": "gate_compile"},
            {"id": "d3", "op": "restart_magic_daemon"},  # 无门控执行器 → 必须拒绝
            {"id": "d4", "op": "deploy_dry_run", "params": {"target_root": str(src_root)}},
        ],
    }
    ex = DiagnosticExecutor(_pipeline(repo, src_root), run_dir=repo / "artifacts" / "diag")
    rep = ex.execute(order)
    by_id = {i["id"]: i for i in rep["items"]}
    assert by_id["d1"]["status"] == "PASS"
    assert by_id["d2"]["status"] == "PASS"
    assert by_id["d3"]["status"] == "NOT_EXECUTABLE"
    assert by_id["d4"]["status"] == "PASS"
    assert rep["verdict"] == "PARTIAL"
    assert Path(rep["artifacts"]["execution_report"]).exists()


def test_diagnostic_order_stops_on_failure(repo, src_root):
    order = {
        "schema": "coordinator-diagnostic-order/1",
        "order_id": "TEST-D2",
        "items": [
            {"id": "a", "op": "patch_apply", "params": {"patch": {
                "label": "boom",
                "replacements": [{"file": "demo_daemon.py", "old": "print(\"online=%s\" % online)",
                                  "new": "def broken(:"}],
            }}, "on_failure": "stop"},
            {"id": "b", "op": "mirror_verify"},
        ],
    }
    ex = DiagnosticExecutor(_pipeline(repo, src_root), run_dir=repo / "artifacts" / "diag2")
    rep = ex.execute(order)
    by_id = {i["id"]: i for i in rep["items"]}
    assert by_id["a"]["status"] == "FAIL"
    assert by_id["b"]["status"] == "SKIPPED"
    assert rep["verdict"] == "EXECUTED_FAIL"