"""run_case 机制测试（合成域夹具，零域词）。

被测：``tools/distill/run_case.py`` 的四件事

1. ``verify_archive`` —— **事后独立复核**已落盘归档：只信 SHA256SUMS + 磁盘，
   并与 manifest 双向对账。变异体判负（缺文件 / 篡改 / **多余文件** /
   sums↔manifest 不一致 / manifest 条目被改 / sums 缺行）；
2. ``normalize_bundle`` —— bundle 里相对路径按 bundle 所在目录解析，解析不到逐条报出；
   （原蒸馏链路对缺失证据是 ``if p.exists()`` 静默跳过 → 假绿，这里必须报出来）
3. ``run_case`` 端到端 —— 四阶段 ok、`case_run.json` 记账、失败可定位；
4. ``determinism`` —— 同一 bundle 两遍运行，账本逐字节相同；跨 workdir 只有
   ``path_embedding_outputs`` 声明的三件不同（已披露的路径敏感性，不是随机漂移）。

为何用合成域
------------
平台铁律：backend/tools/tests 内不得出现具体物理域词条（判例 #608）。夹具是
``synthetic/alpha|beta|gamma`` 三个合成节点，只验证"对任意案例成立"的机制性质；
真实战役数据经 ``artifacts/**/case.json`` 工件注入。域词零命中由
``tools/audit/domain_leak_scan.py`` 机器裁决（本文件 ``TestDomainNeutrality``）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "distill"))

import campaign_distill as cd  # noqa: E402
import run_case as rc  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "distill_case"
CASE = FIXTURE / "case.json"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _archive(tmp_path: Path, tag: str = "a") -> Path:
    """用 campaign_distill 真归档器造一个干净归档（两件工件）。"""
    src = tmp_path / f"src_{tag}"
    src.mkdir()
    files = []
    for name in ("alpha_impl.py", "beta_impl.py"):
        p = src / name
        p.write_text(f"VALUE = {tag!r}\n", encoding="utf-8")
        files.append({"path": str(p), "kind": "impl"})
    dest = tmp_path / f"verified_{tag}"
    cd.archive_artifacts(files, dest, dest / "manifest.json")
    return dest


def _rules(result: dict) -> set[str]:
    return {p["rule"] for p in result["problems"]}


@pytest.fixture()
def case_json(tmp_path: Path) -> Path:
    """拷一份夹具案例到 tmp（避免测试写进 repo 的 fixtures 目录）。"""
    shutil.copytree(FIXTURE, tmp_path / "case")
    return tmp_path / "case" / "case.json"


# --------------------------------------------------------------------------- #
# 1. 独立归档复核：干净归档必须过，六类变异必须报
# --------------------------------------------------------------------------- #
def test_clean_archive_passes(tmp_path: Path) -> None:
    res = rc.verify_archive(_archive(tmp_path))
    assert res["ok"] is True
    assert res["problems"] == []
    assert res["checked"] == res["entries"] == 2


def test_missing_file_detected(tmp_path: Path) -> None:
    dest = _archive(tmp_path)
    next(p for p in dest.iterdir() if p.suffix == ".py").unlink()
    res = rc.verify_archive(dest)
    assert res["ok"] is False
    assert "file_missing" in _rules(res)


def test_tampered_file_detected(tmp_path: Path) -> None:
    dest = _archive(tmp_path)
    target = next(p for p in dest.iterdir() if p.suffix == ".py")
    target.write_text("VALUE = 'tampered'\n", encoding="utf-8")
    res = rc.verify_archive(dest)
    assert res["ok"] is False
    assert "sha256_mismatch" in _rules(res)


def test_unlisted_extra_file_detected(tmp_path: Path) -> None:
    """`sha256sum -c` 检不出多余文件；本复核必须检出（防"塞件进归档"）。"""
    dest = _archive(tmp_path)
    (dest / "injected.py").write_text("VALUE = 0\n", encoding="utf-8")
    res = rc.verify_archive(dest)
    assert res["ok"] is False
    assert "unlisted_file" in _rules(res)


def test_sums_vs_manifest_disagreement_detected(tmp_path: Path) -> None:
    """只改 SHA256SUMS 不改文件：既要报内容不符，也要报 sums↔manifest 不一致。"""
    dest = _archive(tmp_path)
    sums = (dest / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    bad = "0" * 64 + "  " + sums[0].split("  ", 1)[1]
    (dest / "SHA256SUMS").write_text("\n".join([bad] + sums[1:]) + "\n", encoding="utf-8")
    res = rc.verify_archive(dest)
    assert res["ok"] is False
    assert {"sha256_mismatch", "sums_manifest_disagree"} <= _rules(res)


def test_manifest_entry_edit_detected(tmp_path: Path) -> None:
    """改 manifest 条目（kind）而文件不动：entries_sha256 摘要必须对不上。"""
    dest = _archive(tmp_path)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["kind"] = "forged"
    (dest / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    res = rc.verify_archive(dest)
    assert res["ok"] is False
    assert "manifest_digest_mismatch" in _rules(res)


def test_sums_missing_and_malformed_detected(tmp_path: Path) -> None:
    dest = _archive(tmp_path)
    (dest / "SHA256SUMS").write_text("deadbeef  alpha_impl.py\n", encoding="utf-8")
    res = rc.verify_archive(dest)
    assert res["ok"] is False
    assert "sums_malformed" in _rules(res)

    (dest / "SHA256SUMS").unlink()
    res2 = rc.verify_archive(dest)
    assert res2["ok"] is False
    assert "sums_missing" in _rules(res2)


def test_verify_only_cli_exit_codes(tmp_path: Path) -> None:
    dest = _archive(tmp_path)
    assert rc.main(["--verify-only", str(dest), "--quiet"]) == 0
    next(p for p in dest.iterdir() if p.suffix == ".py").write_text("VALUE = 'x'\n", encoding="utf-8")
    assert rc.main(["--verify-only", str(dest), "--quiet"]) == 1


# --------------------------------------------------------------------------- #
# 2. bundle 规范化：静默跳过是假绿，必须报出
# --------------------------------------------------------------------------- #
def test_bundle_relative_paths_resolved_and_missing_reported(tmp_path: Path, case_json: Path) -> None:
    case = rc.load_case(case_json, str(tmp_path / "wd"))
    path, problems = rc.normalize_bundle(case)
    assert problems == []
    data = json.loads(path.read_text(encoding="utf-8"))
    for node in data["nodes"]:
        for f in node["evidence"]["files"]:
            assert Path(f["path"]).is_file() and Path(f["path"]).is_absolute()

    # 删掉一件证据 → 必须逐条报 evidence_unresolvable（而不是被归档器静默跳过）
    (tmp_path / "case" / "worktree" / "alpha_impl.py").unlink()
    case2 = rc.load_case(case_json, str(tmp_path / "wd2"))
    _, problems2 = rc.normalize_bundle(case2)
    assert [p["rule"] for p in problems2] == ["evidence_unresolvable"]
    assert problems2[0]["target"] == "worktree/alpha_impl.py"


def test_case_contract_error_exit_code(tmp_path: Path) -> None:
    bad = tmp_path / "case.json"
    bad.write_text(json.dumps({"id": "no-bundle"}), encoding="utf-8")
    assert rc.main(["--case", str(bad), "--workdir", str(tmp_path / "wd"), "--quiet"]) == 2
    missing = tmp_path / "case2.json"
    missing.write_text(json.dumps({"bundle": "nope.json"}), encoding="utf-8")
    assert rc.main(["--case", str(missing), "--quiet"]) == 2


# --------------------------------------------------------------------------- #
# 3. 端到端：四阶段 + 记账
# --------------------------------------------------------------------------- #
def test_run_case_end_to_end(case_json: Path, tmp_path: Path) -> None:
    wd = tmp_path / "run"
    code = rc.main(["--case", str(case_json), "--workdir", str(wd), "--quiet"])
    assert code == 0

    record = json.loads((wd / "case_run.json").read_text(encoding="utf-8"))
    assert record["ok"] is True and record["failed_stages"] == []
    assert [r["name"] for r in record["stages"]] == list(rc.STAGE_ORDER)
    assert record["case"]["snapshot_utc"] == "2026-01-01T12:00:00Z"
    assert record["case"]["evidence_unresolvable"] == 0

    # 阶段产物必须真在盘上，且记录里的 sha256 与磁盘一致（记录不可空转）
    for stage in record["stages"]:
        assert stage["outputs"], f"{stage['name']} 未记录任何产物"
        for out in stage["outputs"]:
            p = wd / out["path"]
            assert p.is_file(), f"记录声称的产物不存在: {out['path']}"
            assert rc.sha256_file(p) == out["sha256"], f"记录 sha256 与磁盘不符: {out['path']}"

    figs = sorted((wd / "figures").glob("*.png"))
    assert len(figs) == 3, figs
    assert (wd / "report.md").is_file()
    assert rc.verify_archive(wd / "verified")["ok"] is True


def test_failed_stage_is_recorded_not_swallowed(case_json: Path, tmp_path: Path) -> None:
    """证据文件缺失 → distill 阶段必须红，整体退出 1，且记录落盘可定位。"""
    (tmp_path / "case" / "worktree" / "alpha_impl.py").unlink()
    wd = tmp_path / "run_bad"
    code = rc.main(["--case", str(case_json), "--workdir", str(wd), "--quiet"])
    assert code == 1
    record = json.loads((wd / "case_run.json").read_text(encoding="utf-8"))
    assert record["ok"] is False
    assert "distill" in record["failed_stages"]
    distill = next(r for r in record["stages"] if r["name"] == "distill")
    assert "evidence_unresolvable" in _rules(distill)


def test_dirty_workdir_refused_then_cleaned(case_json: Path, tmp_path: Path) -> None:
    """脏 workdir 必须拒绝复用（陈旧工件会混进归档），--clean 后才放行。"""
    wd = tmp_path / "run_dirty"
    assert rc.main(["--case", str(case_json), "--workdir", str(wd), "--quiet"]) == 0
    # 再跑一次、不给开关 → 契约错误（2），而不是把上次产物当本次结果
    assert rc.main(["--case", str(case_json), "--workdir", str(wd), "--quiet"]) == 2
    # 显式清理后放行
    assert rc.main(["--case", str(case_json), "--workdir", str(wd), "--clean", "--quiet"]) == 0
    record = json.loads((wd / "case_run.json").read_text(encoding="utf-8"))
    assert record["workdir"]["stale_before_run"] > 0 and record["workdir"]["cleaned"] > 0


def test_stale_archive_file_would_be_caught(case_json: Path, tmp_path: Path) -> None:
    """证据：脏归档真会被独立复核抓到（所以"拒绝复用"不是洁癖）。

    在归档里留一件上一版的工件，--force 复用 → 独立复核报 unlisted_file、整体退出 1；
    **蒸馏自身的 C5 自检仍绿**（只遍历 manifest 自列条目），--clean 重跑后恢复全绿。
    """
    wd = tmp_path / "run_stale"
    assert rc.main(["--case", str(case_json), "--workdir", str(wd), "--quiet"]) == 0
    (wd / "verified" / "stale_from_prev_bundle.py").write_text("VALUE = 0\n", encoding="utf-8")
    assert rc.main(["--case", str(case_json), "--workdir", str(wd), "--force", "--quiet"]) == 1
    record = json.loads((wd / "case_run.json").read_text(encoding="utf-8"))
    verify = next(r for r in record["stages"] if r["name"] == "verify_archive")
    assert "unlisted_file" in _rules(verify)
    assert record["failed_stages"] == ["verify_archive"], record["failed_stages"]
    # 关键对照：蒸馏自身的 C5 自检（verify_manifest 只遍历 manifest 自列条目）**照样绿**，
    # 只有独立复核抓得到陈旧件——这正是新增阶段的存在理由，不是重复实现。
    summary = json.loads((wd / "out" / "distill_summary.json").read_text(encoding="utf-8"))
    c5 = next(c for c in summary["self_checks"] if c["id"].startswith("C5"))
    assert c5["ok"] is True

    assert rc.main(["--case", str(case_json), "--workdir", str(wd), "--clean", "--quiet"]) == 0
    assert not (wd / "verified" / "stale_from_prev_bundle.py").exists()


def test_stage_subset_only_runs_requested(case_json: Path, tmp_path: Path) -> None:
    wd = tmp_path / "run_subset"
    code = rc.main(["--case", str(case_json), "--workdir", str(wd), "--stage", "distill", "--quiet"])
    assert code == 0
    record = json.loads((wd / "case_run.json").read_text(encoding="utf-8"))
    assert [r["name"] for r in record["stages"]] == ["distill"]
    assert not (wd / "figures").exists()


def test_bad_bundle_contract_is_recorded_stage_failure(case_json: Path, tmp_path: Path) -> None:
    """bundle 契约坏了：distill 阶段须记 ``distill_exit``/退出码 2，不得整条链路抛栈。

    对照基线（加契约层之前）：``campaign_distill`` 对缺 ``node_key`` 直接 ``KeyError``
    抛栈，案例入口只能以 ``stage_exception`` 兜住——退出码 2（数据坏）与 1（内容不合格）
    在记录里分不开，下游无法机判该重采数据还是重写报告。
    """
    bundle_path = tmp_path / "case" / "bundle.json"
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload["nodes"][0].pop("node_key")
    bundle_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    wd = tmp_path / "run_badbundle"
    code = rc.main(["--case", str(case_json), "--workdir", str(wd), "--quiet"])
    assert code == 1
    record = json.loads((wd / "case_run.json").read_text(encoding="utf-8"))
    distill = next(r for r in record["stages"] if r["name"] == "distill")
    assert distill["ok"] is False
    assert distill["detail"]["exit_code"] == 2
    rules = _rules(distill)
    assert "distill_exit" in rules
    assert "stage_exception" not in rules, "契约问题不该再走异常兜底路径"
    # 契约不过 → 一件账本都不该有（半成品比没有更危险）
    assert not (wd / "out" / "distill_summary.json").exists()


# --------------------------------------------------------------------------- #
# 4. 确定性：内容稳定 vs 已披露的路径敏感性
# --------------------------------------------------------------------------- #
def _run_into(case_json: Path, wd: Path) -> dict:
    assert rc.main(["--case", str(case_json), "--workdir", str(wd), "--quiet"]) == 0
    return json.loads((wd / "case_run.json").read_text(encoding="utf-8"))


def test_determinism_stage_compares_content_not_paths(case_json: Path, tmp_path: Path) -> None:
    wd = tmp_path / "run_det"
    record = _run_into(case_json, wd)
    det = next(r for r in record["stages"] if r["name"] == "determinism")
    assert det["ok"] is True and det["problems"] == []
    assert det["detail"]["compared"] == list(rc.DETERMINISM_TARGETS)


def test_cross_workdir_drift_limited_to_declared_paths(case_json: Path, tmp_path: Path) -> None:
    """两个不同 workdir 各跑一次：只有 path_embedding_outputs 声明的产物可不同。

    这条断言是"已披露的路径敏感性"与"真漂移"的分界线：若将来 silent 地引入随机/时间戳，
    漂移会出现在 content_stable_outputs 里，本测试立刻变红。
    """
    wd_a, wd_b = tmp_path / "run_a", tmp_path / "run_b"
    rec_a, rec_b = _run_into(case_json, wd_a), _run_into(case_json, wd_b)
    assert rec_a["path_embedding_outputs"] == rec_b["path_embedding_outputs"]

    drifted = []
    for rel in rec_a["content_stable_outputs"]:
        assert (wd_a / rel).is_file() and (wd_b / rel).is_file(), rel
        if (wd_a / rel).read_bytes() != (wd_b / rel).read_bytes():
            drifted.append(rel)
    assert drifted == [], f"内容稳定产物跨 workdir 漂移: {drifted}"

    # 声明为路径内嵌的产物确实就是唯一不同的那些（声明与实际一致，不许漏报）。
    # determinism 阶段复跑的产物（_repeat/…）是首跑产物的副本，比对时按原名归一。
    differing = {
        _tail(rel) for rel in _all_outputs(rec_a) if (wd_a / rel).read_bytes() != (wd_b / rel).read_bytes()
    }
    declared = set(rec_a["path_embedding_outputs"])
    assert differing <= declared, differing - declared

    # 阶段结论与问题清单必须一致（记录本身不携带环境相关的东西）
    assert [r["ok"] for r in rec_a["stages"]] == [r["ok"] for r in rec_b["stages"]]


def _tail(rel: str) -> str:
    return rel[len("_repeat/"):] if rel.startswith("_repeat/") else rel


def _all_outputs(record: dict) -> list[str]:
    rels = []
    for stage in record["stages"]:
        rels.extend(o["path"] for o in stage["outputs"])
    return sorted(set(rels))


def test_compare_runs_flags_real_drift(tmp_path: Path) -> None:
    """真漂移（同一 workdir 内两遍内容不同）必须被 compare_runs 抓到。"""
    workdir, repeat = tmp_path / "w", tmp_path / "w" / "_repeat"
    for root in (workdir, repeat):
        (root / "out").mkdir(parents=True, exist_ok=True)
        for rel in rc.DETERMINISM_TARGETS:
            (root / rel).write_text("same\n", encoding="utf-8")
    assert rc.compare_runs(workdir, repeat, roots=[workdir, repeat]) == []

    (repeat / "out" / "gaps.json").write_text("drifted\n", encoding="utf-8")
    (repeat / "report.md").unlink()
    problems = rc.compare_runs(workdir, repeat, roots=[workdir, repeat])
    rules = {(p["rule"], p["target"]) for p in problems}
    assert ("nondeterministic", "out/gaps.json") in rules
    assert ("repeat_missing", "report.md") in rules


# --------------------------------------------------------------------------- #
# 5. 域中性
# --------------------------------------------------------------------------- #
class TestDomainNeutrality:
    def test_zero_domain_hits(self, tmp_path: Path) -> None:
        paths = [
            "tools/distill/run_case.py",
            "tests/test_distill_run_case.py",
            "tests/fixtures/distill_case",
        ]
        json_out = tmp_path / "scan.json"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools/audit/domain_leak_scan.py"),
             "--paths", *paths, "--json", str(json_out)],
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads(json_out.read_text(encoding="utf-8"))
        assert report["hits"] == [], report["hits"]
        # 反空转：扫描器对不存在路径静默跳过，必须证明每个路径都真被扫到
        assert report["files_missing"] == [], report["files_missing"]
        assert len(report["files_scanned"]) >= len(paths), report["files_scanned"]