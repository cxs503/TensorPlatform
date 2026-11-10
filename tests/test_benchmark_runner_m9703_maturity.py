"""P167W2-N08 成熟化波（M9703）：墙钟护栏 / 证据链回验 / 覆盖度 fail-closed / 零域守卫。

本文件只测**成熟化增量**，与 ``test_benchmark_runner_p167w2_n08.py``（四件套本体）
分离。每条用例都针对一个具体的、先前存在的失守路径，并且都要求"改前红、改后绿"
（报告里给出改前实测值）：

1. 墙钟护栏：``stall`` 巨量挂住的 SUT 在 0.5 s 预声明预算下被裁 FAIL，
   引擎**按时返回**（改前：runner 永久挂住，门链无裁决）；
2. suite 预算：预算用尽后剩余算例记 ``__budget__`` error（不静默跳过、不当通过）；
3. 证据链回验：干净 run → ok；改一个字节 / 删一个文件 / 多塞一个文件 /
   局部改写算例 JSON → 必须判不可信（改前：引擎落盘后从不回验，哈希只是装饰）；
4. 覆盖度 fail-closed：基线 5 算例、本次只剩 1 算例 → 判回归、退出码 2
   （改前实测 status="noted"、退出码 0）；
5. 零领域语义守卫：引擎包扫描 0 命中，且守卫本身有判别力（注入词条必报），
   缺词表 fail-closed。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_runner import artifact as art          # noqa: E402
from tools.benchmark_runner import config as cfgmod       # noqa: E402
from tools.benchmark_runner import domain_guard           # noqa: E402
from tools.benchmark_runner import regression as reg      # noqa: E402
from tools.benchmark_runner import runner as run          # noqa: E402
from tools.benchmark_runner import timeout as tmo         # noqa: E402
from tools.benchmark_runner.model import ConfigError       # noqa: E402

CONFIGS = ROOT / "tools" / "benchmark_runner" / "configs"


# ── 1. 配置：墙钟预算必须预声明且有限 ──────────────────────────────────────
def _min_cfg(**over):
    cfg = {"suite": "s", "suite_criteria": [{"metric": "n_cases", "op": ">=", "value": 1}],
           "cases": [{"id": "a", "workload": "m:f", "criteria": []}]}
    cfg.update(over)
    return cfg


def test_config_timeout_must_be_finite_and_positive():
    assert cfgmod.validate_config(_min_cfg())["timeout_s"] == cfgmod.DEFAULT_TIMEOUT_S
    for bad in (0, -1.0, float("inf"), True, "60"):
        with pytest.raises(ConfigError):
            cfgmod.validate_config(_min_cfg(timeout_s=bad))
    # suite 级 None 是"忘了声明"，不是"不设限"：无人值守必须有上界
    with pytest.raises(ConfigError):
        cfgmod.validate_config(_min_cfg(timeout_s=None))


def test_config_case_timeout_overrides_suite_default():
    cfg = _min_cfg(timeout_s=30.0)
    cfg["cases"] = [{"id": "a", "workload": "m:f", "criteria": [], "timeout_s": 2.5},
                    {"id": "b", "workload": "m:f", "criteria": []}]
    norm = cfgmod.validate_config(cfg)
    assert [c["timeout_s"] for c in norm["cases"]] == [2.5, 30.0]
    with pytest.raises(ConfigError):
        cfgmod.validate_config({**cfg, "cases": [{**cfg["cases"][0], "timeout_s": 0}]})


# ── 2. 墙钟护栏本体（单调用层） ────────────────────────────────────────────
def test_call_with_timeout_raises_and_preserves_fast_path():
    from tools.benchmark_runner.synthetic import stall
    started = time.monotonic()
    with pytest.raises(tmo.CaseTimeout) as exc:
        tmo.call_with_timeout(stall, {"seconds": 5.0}, 0.2, label="probe")
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"timeout not enforced promptly: {elapsed:.2f}s"
    assert "probe" in str(exc.value) and "hang" in str(exc.value)
    # 正常调用不受影响（护栏不得污染快路径）
    assert tmo.call_with_timeout(stall, {"seconds": 0.0}, 1.0)["value"] == 0.0
    # timeout=None → 不设限（仅调试逃逸阀）
    assert tmo.call_with_timeout(lambda x: {"value": x}, {"x": 3}, None)["value"] == 3


# ── 3. 端到端：挂死的 SUT 被裁 FAIL，引擎按时返回 ──────────────────────────
def test_stalled_case_is_failed_not_hung(tmp_path):
    started = time.monotonic()
    summary = run.run_config(CONFIGS / "fx_timeout_negative.json", out_root=tmp_path / "art",
                             run_id="to-neg")
    elapsed = time.monotonic() - started
    assert elapsed < 15.0, f"unattended runner did not return promptly: {elapsed:.1f}s"
    assert summary["exit_code"] == run.EXIT_GATE_FAIL
    by_id = {c["id"]: c for c in summary["cases"]}
    stalled = by_id["fx_stall_beyond_timeout"]
    assert stalled["status"] == "error" and not stalled["passed"]
    assert "__timeout__" in stalled["failed_checks"]
    assert "CaseTimeout" in stalled["error"]
    # 关键：一个算例被裁掉后，后续正常算例仍然要跑（不许连锁静默跳过）
    assert by_id["fx_healthy_after_timeout"]["passed"] is True
    assert summary["suite_metrics"]["n_timeout"] == 1
    assert summary["suite_metrics"]["n_cases"] == 2
    # 计时证据：被裁算例的墙钟 ≥ 预声明 0.5 s，且单独落 timings.json（可审计）
    timings = summary["timings"]
    assert timings["fx_stall_beyond_timeout"]["timing_s"] >= 0.5
    assert pathlib.Path(summary["paths"]["timings"]).is_file()
    manifest = json.loads(pathlib.Path(summary["paths"]["manifest"]).read_text())
    assert "timings.json" in {f["path"] for f in manifest["files"]}
    # 计时不进裁决载荷（否则逐位可复现性会被机器抖动破坏）
    result = json.loads(pathlib.Path(summary["paths"]["result"]).read_text())
    assert "timing_s" not in json.dumps(result)


def test_suite_budget_exhaustion_is_recorded_not_skipped(tmp_path):
    summary = run.run_config(CONFIGS / "fx_budget_negative.json", out_root=tmp_path / "art",
                             run_id="budget-neg")
    assert summary["exit_code"] == run.EXIT_GATE_FAIL
    by_id = {c["id"]: c for c in summary["cases"]}
    starved = by_id["fx_budget_starved"]
    assert starved["status"] == "error" and "__budget__" in starved["failed_checks"]
    assert "SuiteBudgetExceeded" in starved["error"]
    assert summary["suite_metrics"]["n_skipped_budget"] == 1
    # 预算被写进工件（可审计），不是运行期暗箱
    result = json.loads(pathlib.Path(summary["paths"]["result"]).read_text())
    assert result["config"]["budget_s"] == 1.0


# ── 4. 证据链回验：干净 → ok；任一篡改 → 不可信 ────────────────────────────
def _clean_run(tmp_path):
    summary = run.run_config(CONFIGS / "fx_integration.json", out_root=tmp_path / "art",
                             run_id="integrity")
    return summary, pathlib.Path(summary["paths"]["out_dir"])


def test_integrity_clean_run_is_ok(tmp_path):
    summary, out_dir = _clean_run(tmp_path)
    assert summary["exit_code"] == run.EXIT_OK
    integrity = summary["integrity"]
    assert integrity["ok"] is True, integrity
    assert integrity["checked"] >= 4 and integrity["manifest_digest_ok"] is True
    again = art.verify_manifest(out_dir)            # 幂等：回验不改变工件
    assert again["ok"] is True and again["manifest_sha256"] == integrity["manifest_sha256"]
    assert art.main(["--verify", str(out_dir)]) == 0


def test_integrity_detects_tampered_artifact(tmp_path):
    _, out_dir = _clean_run(tmp_path)
    target = sorted((out_dir / "cases").glob("*.json"))[0]
    payload = json.loads(target.read_text())
    payload["metrics"]["rel_error"] = 0.0           # 把误差"改成"0：经典粉饰手法
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    verdict = art.verify_manifest(out_dir)
    assert verdict["ok"] is False
    assert any("sha256 mismatch" in m["reason"] for m in verdict["mismatch"])
    assert art.main(["--verify", str(out_dir)]) == 1


def test_integrity_detects_missing_and_extra_files(tmp_path):
    _, out_dir = _clean_run(tmp_path)
    result = out_dir / "result.json"
    keep = result.read_bytes()
    result.unlink()
    assert art.verify_manifest(out_dir)["missing"] == ["result.json"]
    result.write_bytes(keep)
    (out_dir / "cases" / "injected.json").write_text("{}")   # 未登记的多余工件
    verdict = art.verify_manifest(out_dir)
    assert verdict["ok"] is False and "cases/injected.json" in verdict["extra"]


def test_integrity_detects_case_self_hash_mismatch(tmp_path):
    """改写算例 JSON 后**重建 manifest**：清单自洽，但算例自带哈希对不上。"""
    _, out_dir = _clean_run(tmp_path)
    target = sorted((out_dir / "cases").glob("*.json"))[0]
    payload = json.loads(target.read_text())
    payload["title"] = payload["title"] + " (edited)"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    art.write_manifest(out_dir, {"suite": "x", "run_id": "y", "passed": True},
                       {"engine_version": "test", "config_sha256": None, "git_sha": None,
                        "generated_at": "t"})
    verdict = art.verify_manifest(out_dir)
    assert verdict["manifest_digest_ok"] is True
    assert verdict["case_self_hash_errors"], verdict
    assert verdict["ok"] is False


def test_runner_returns_exit_5_when_evidence_chain_broken(tmp_path, monkeypatch):
    broken = {"ok": False, "reason": "synthetic tamper", "checked": 0}
    monkeypatch.setattr(run.art, "verify_manifest", lambda *_a, **_k: dict(broken))
    summary = run.run_config(CONFIGS / "fx_integration.json", out_root=tmp_path / "art",
                             run_id="broken")
    assert summary["exit_code"] == run.EXIT_INTEGRITY == 5
    assert summary["integrity"]["ok"] is False


def test_no_write_mode_does_not_claim_verified(tmp_path):
    summary = run.run_config(CONFIGS / "fx_integration.json", out_root=tmp_path / "art",
                             run_id="nowrite", write=False)
    assert summary["exit_code"] == run.EXIT_OK
    # "没验过" ≠ "验过且通过"：verify_success 必须是 False
    assert run.verify_success(summary["integrity"]) is False


# ── 5. 覆盖度 fail-closed：算例消失 = 回归 ─────────────────────────────────
def _suite(cases):
    return {"suite": "fx", "run_id": "r1", "cases": cases}


def _case(cid, **metrics):
    return {"case_id": cid, "status": "pass", "passed": True, "metrics": metrics}


def test_coverage_loss_is_a_regression():
    base = reg.build_baseline(_suite([_case(f"c{i}", rel_error=1e-9) for i in range(5)]), {}, "t0")
    shrunk = _suite([_case("c0", rel_error=1e-9)])          # 5 → 1 算例
    report = reg.compare(shrunk, base, drift_threshold=0.03)
    assert report["counts"]["missing"] == 4
    assert report["status"] == "regression"
    assert reg.has_regression(report) is True
    assert {a["severity"] for a in report["alerts"] if a["kind"] == "case_missing"} == {"critical"}
    # 对照：算例**新增**仍是 info（不是回归），不给"多写算例"发罚单
    grown = _suite([_case("c0", rel_error=1e-9), _case("extra", rel_error=1e-9)])
    r2 = reg.compare(grown, reg.build_baseline(_suite([_case("c0", rel_error=1e-9)]), {}, "t0"))
    assert r2["status"] == "noted" and reg.has_regression(r2) is False


def test_coverage_loss_drives_exit_code_2(tmp_path):
    """端到端：门全过但算例少了 → 退出码 2（改前实测为 0，静默失守）。"""
    full = cfgmod.load_config(CONFIGS / "fx_integration.json")
    assert len(full["cases"]) >= 3
    base_path = tmp_path / "base.json"
    run.run_config(CONFIGS / "fx_integration.json", out_root=tmp_path / "art",
                   baseline_path=base_path, update_baseline=True, run_id="base")
    # 模拟"悄悄缩水"：只用 1 个算例，且把 suite 级判据放宽到它能过
    # （这正是真实事故形态——门被改松 + 覆盖面变小，两者一起才静默失守）
    truncated = dict(full)
    truncated["cases"] = full["cases"][:1]
    truncated["suite_criteria"] = [{"metric": "pass_ratio", "op": ">=", "value": 1.0}]
    tcfg = tmp_path / "truncated.json"
    tcfg.write_text(json.dumps(truncated, ensure_ascii=False, default=str))
    summary = run.run_config(tcfg, out_root=tmp_path / "art2",
                             baseline_path=base_path, run_id="trunc")
    assert summary["passed"] is True                        # 门本身是绿的……
    assert summary["regression"]["counts"]["missing"] == len(full["cases"]) - 1
    assert summary["exit_code"] == run.EXIT_REGRESSION      # ……但证据面缩水，机器必须红


# ── 6. 零领域语义守卫 ─────────────────────────────────────────────────────
def test_domain_guard_engine_package_is_clean():
    verdict = domain_guard.scan()
    assert verdict["ok"] is True, verdict["hits"][:5]
    assert verdict["scanned_files"] >= 10
    assert domain_guard.main(["--json"]) == 0


def test_domain_guard_detects_injected_term(tmp_path):
    lexicon = json.loads((ROOT / "tools" / "evidence_domain_lexicon.json").read_text())
    term = lexicon["ascii_terms"][0]
    injected = tmp_path / "pkg"
    injected.mkdir()
    # 词条不写死在源码里：从词表读出来再注入，避免本测试文件自己变成域词源
    (injected / "mod.py").write_text(f"def f():\n    return '{term}'\n")
    verdict = domain_guard.scan([str(injected)])
    assert verdict["ok"] is False and verdict["n_hits"] >= 1
    assert any(h["term"] == term for h in verdict["hits"])
    assert domain_guard.main([str(injected)]) == 1


def test_domain_guard_missing_lexicon_is_fail_closed(tmp_path):
    verdict = domain_guard.scan(lexicon=tmp_path / "nope.json")
    assert verdict["ok"] is False and verdict["status"] == "no_lexicon"