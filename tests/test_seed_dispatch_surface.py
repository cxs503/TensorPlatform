"""surface 测试：报告/图、验收编排、CLI、日志拉取与参数面的分支覆盖。

全部用合成夹具（r00… / seed-000x，无物理域词）；联网入口（``urllib``）与子进程入口
（``subprocess.run``）一律打桩，测试不触网、不跑真实 pytest/coverage。
"""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import pytest

from tools.seed_dispatch import acceptance, cli
from tools.seed_dispatch import fixtures as fx
from tools.seed_dispatch import log as logmod
from tools.seed_dispatch.integration import audit_leakage, corpus_summary, rank_seeds, seed_candidates
from tools.seed_dispatch.log import build_records
from tools.seed_dispatch.params import ConfigError, SeedDispatchParams, load_params
from tools.seed_dispatch.policy import (
    CostModel,
    build_slates,
    fit_cost_model,
    matrix,
    predict,
    walk_forward,
)
from tools.seed_dispatch.report import make_figures, render_markdown, write_report

pytest.importorskip("matplotlib")


def _payload(n: int = 420, seed: int = 41) -> dict:
    raw = fx.make_raw(n, seed=seed)
    recs = build_records(raw)
    wf = walk_forward(recs, windows=3, min_train=140, epochs=150, random_repeats=5)
    bundle = fx.make_distill_bundle(mission_ids=[r.mission_id for r in recs[:5]])
    cands = seed_candidates(bundle, recs)
    ranking = rank_seeds(cands, recs, epochs=120)
    lk = audit_leakage(raw, recs, params={
        "windows": 3, "min_train": 140, "epochs": 120, "random_repeats": 3,
        "leakage_tol": 0.05, "leakage_sensitivity": 0.0, "seed": 3})
    return {
        "mission_id": 11930, "node": "synthetic", "generated_utc": "2026-01-01T00:00:00Z",
        "runtime_sec": 0.5, "params": SeedDispatchParams().to_dict(),
        "corpus": {**corpus_summary(recs), "snapshot": "synthetic", "snapshot_sha256": "0" * 64},
        "walk_forward": wf,
        "offpolicy": {"rounds": 5, "support": 0.4, "naive_value": 0.6, "ips_value": 0.7,
                      "snips_value": 0.62, "ess": 2.5, "mean_propensity": 0.2},
        "leakage": lk,
        "seed_ranking": {"model": ranking["model"], "cost_model": ranking["cost_model"],
                         "train_n": ranking.get("train_n"),
                         "score_sha256": ranking["score_sha256"],
                         "count": len(ranking["ranked"]), "top": ranking["ranked"][:3]},
        "ablation": [{"variant": "gain=novelty, alpha=1.0 (primary)", "precision_evoi": 0.8,
                      "precision_fifo": 0.75, "gain_pp": 5.0, "meets_gate": False, "records": 100}],
        "gates": {}, "distill": {"dir": "synthetic"},
    }


def test_make_figures_and_write_report(tmp_path: Path):
    payload = _payload()
    payload["gates"] = {"a_parse_coverage": {"ok": True}, "b_cluster_consistency": {"ok": True},
                        "c_novelty_gate": {"ok": True}, "d_ranking_utility": {"ok": False},
                        "e_leakage_audit": {"ok": True}, "f_tests_coverage": {"ok": True},
                        "g_zero_domain_semantics": {"ok": True}, "all_pass": False}
    figs, notes = make_figures(payload, tmp_path)
    assert not notes
    assert "fig1_precision_windows.png" in figs
    assert Path(figs["fig1_precision_windows.png"]).is_file()
    out = write_report(payload, tmp_path, figures=True)
    assert Path(out["markdown"]).is_file()
    text = Path(out["markdown"]).read_text(encoding="utf-8")
    assert "① 案例介绍" in text and "⑥ 误差判定" in text
    assert "TBD" not in text


def test_report_without_figures_notes(tmp_path: Path):
    payload = _payload(300, seed=43)
    payload["gates"] = {k: {"ok": True} for k in
                        ("a_parse_coverage", "b_cluster_consistency", "c_novelty_gate",
                         "d_ranking_utility", "e_leakage_audit", "f_tests_coverage",
                         "g_zero_domain_semantics")}
    out = write_report(payload, tmp_path, figures=False)
    assert out["figures"] == []


def test_render_markdown_flags_honest_fail():
    payload = _payload(300, seed=47)
    payload["gates"] = {k: {"ok": True} for k in
                        ("a_parse_coverage", "b_cluster_consistency", "c_novelty_gate",
                         "d_ranking_utility", "e_leakage_audit", "f_tests_coverage",
                         "g_zero_domain_semantics")}
    md = render_markdown(payload, {}, [])
    assert "全部判据通过" in md


def _synthetic_inputs(tmp_path: Path, n: int = 420) -> tuple[str, str]:
    raw = fx.make_raw(n, seed=53)
    snap = tmp_path / "snapshot.json.gz"
    with gzip.open(snap, "wt", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False)
    distill = fx.dump_bundle(fx.make_distill_bundle(mission_ids=[r["id"] for r in raw[:5]]),
                             str(tmp_path / "distill"))
    return str(snap), distill


def test_acceptance_run_end_to_end_synthetic(tmp_path: Path):
    snap, distill = _synthetic_inputs(tmp_path)
    params = load_params({"windows": 3, "min_train": 140, "epochs": 120, "random_repeats": 3})
    payload = acceptance.run(snapshot=snap, distill_dir=distill, out=str(tmp_path / "out"),
                             params=params, with_checks=False, with_figures=False)
    assert payload["gates"]["a_parse_coverage"]["ok"] is False  # 合成蒸馏 run 无 coverage
    assert payload["ablation"]
    assert (tmp_path / "out" / "eval_report.json").is_file()
    assert (tmp_path / "out" / "MANIFEST.json").is_file()
    assert (tmp_path / "out" / "seeds_ranked.json").is_file()


def test_check_helpers_degrade_gracefully(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(acceptance.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "boom", "err"))
    py = acceptance.check_pytest()
    assert py["pass"] is False and py["returncode"] == 1
    cov = acceptance.check_coverage()
    assert cov["ok"] is False
    dom = acceptance.check_domain_scan()
    assert dom["ok"] is False


def test_check_cluster_truth_missing_fixture(tmp_path: Path):
    out = acceptance.check_cluster_truth(fixture_dir=str(tmp_path / "nope"),
                                         out_dir=str(tmp_path / "o"))
    assert out["ok"] is False and "missing" in out["reason"]


def test_cli_fetch_and_evaluate(monkeypatch, tmp_path: Path):
    raw = fx.make_raw(300, seed=59)
    monkeypatch.setattr(logmod, "fetch_missions", lambda *a, **k: raw)
    info = cli.fetch(str(tmp_path / "snap.json"))
    assert info["records"] == len(raw)
    assert (tmp_path / "snap.json").is_file()
    kept = json.loads((tmp_path / "snap.json").read_text(encoding="utf-8"))
    assert set(kept[0]) >= {"id", "repo", "goal", "created_at", "status", "result"}

    distill = fx.dump_bundle(fx.make_distill_bundle(mission_ids=[r["id"] for r in raw[:5]]),
                             str(tmp_path / "distill"))
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"windows": 3, "min_train": 140, "epochs": 120,
                               "random_repeats": 3}), encoding="utf-8")
    rc = cli.main(["evaluate", "--snapshot", str(tmp_path / "snap.json"),
                   "--distill", distill, "--out", str(tmp_path / "out"),
                   "--config", str(cfg), "--no-checks", "--no-figures"])
    assert rc == 1  # 判据不全 → 非零退出（诚实 FAIL）
    assert (tmp_path / "out" / "eval_report.json").is_file()


def test_cli_rejects_bad_config_and_missing_input(tmp_path: Path):
    cfg = tmp_path / "bad.json"
    cfg.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    assert cli.main(["evaluate", "--config", str(cfg)]) == 2
    assert cli.main(["evaluate", "--snapshot", str(tmp_path / "missing.json"),
                     "--distill", str(tmp_path / "none"), "--no-checks"]) == 2


def test_fetch_missions_via_stub(monkeypatch):
    pages = {0: {"items": fx.make_raw(3, seed=61), "total": 3},
             3: {"items": [], "total": 3}}

    class Resp:
        def __init__(self, payload):
            self._p = payload

        def read(self):
            return json.dumps(self._p).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = []

    def fake_urlopen(req, timeout=0):
        url = getattr(req, "full_url", req)
        calls.append(url)
        off = int(str(url).split("offset=")[1])
        return Resp(pages[off])

    monkeypatch.setattr(logmod.urllib.request, "urlopen", fake_urlopen)
    rows = logmod.fetch_missions("http://example.invalid/api/v1", "tok", page=3)
    assert len(rows) == 3 and calls


def test_login_without_credentials(monkeypatch):
    monkeypatch.delenv("TENSOR_AGENT_USER", raising=False)
    monkeypatch.delenv("TENSOR_AGENT_PASS", raising=False)
    with pytest.raises(RuntimeError):
        logmod._login("http://example.invalid")  # noqa: SLF001


def test_build_records_handles_missing_fields():
    rows = [
        {"id": 1, "repo": "r00", "created_at": "2026-01-01T00:00:00+00:00"},  # 无 goal/result
        {"id": 2, "repo": "r00", "created_at": "2026-01-01T01:00:00+00:00",
         "goal": "alpha", "budget": "not-a-dict", "result": [], "status": "queued"},
        {"id": 3, "repo": "", "created_at": "2026-01-01T02:00:00+00:00"},     # 脏行 → 丢弃
    ]
    recs = build_records(rows)
    assert len(recs) == 2
    assert recs[1].label is None                     # queued → 删失
    assert recs[0].features["f_log_budget"] == 0.0


def test_load_snapshot_accepts_items_wrapper(tmp_path: Path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"items": fx.make_raw(4, seed=67)}), encoding="utf-8")
    assert len(logmod.load_snapshot(path)) == 4
    with pytest.raises(ValueError):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps("nope"), encoding="utf-8")
        logmod.load_snapshot(bad)


def test_policy_edge_cases():
    assert matrix([]).shape == (0, len(__import__("tools.seed_dispatch.policy",
                                                  fromlist=["FEATURES"]).FEATURES))
    assert predict(_dummy_model(), matrix([])).shape == (0,)
    cm = fit_cost_model([])
    assert cm.predict(__import__("numpy").zeros((0, 2))).shape == (0,)
    recs = build_records(fx.make_raw(60, seed=71))
    slates = build_slates(recs, [0.0] * len(recs), hours=96, min_slate=999)
    assert slates == []


def _dummy_model():
    import numpy as np

    from tools.seed_dispatch.policy import FEATURES, LogisticModel

    return LogisticModel(mean=np.zeros(len(FEATURES)), std=np.ones(len(FEATURES)),
                         coef=np.zeros(len(FEATURES)), intercept=0.0, n_train=0, train_loss=0.0)


def test_cost_model_dataclass_roundtrip():
    import numpy as np

    cm = CostModel(intercept=1.0, coef=np.asarray([0.1, 0.2]), fallback=100.0)
    out = cm.to_dict()
    assert out["features"] == ["f_log_budget", "f_log_len"]
    assert cm.predict(np.asarray([[1.0, 1.0]]))[0] > 0


def test_params_load_from_file(tmp_path: Path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"windows": 4, "leakage_tol": 0.03}), encoding="utf-8")
    p = load_params(path=cfg)
    assert p.windows == 4 and p.leakage_tol == 0.03
    with pytest.raises(ConfigError):
        load_params(path=tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"top_frac": 0.0}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_params(path=bad)
    with pytest.raises(ConfigError):
        load_params({"explore_eps": 1.5})
    with pytest.raises(ConfigError):
        load_params({"cjk_ngram": 9})
    with pytest.raises(ConfigError):
        load_params({"hash_bits": 2})
    with pytest.raises(ConfigError):
        load_params({"gain_floor": 0.0})
    with pytest.raises(ConfigError):
        load_params({"min_train": 5})