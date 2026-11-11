"""判据 F：零领域语义 —— 回填器/计分器不硬编码任何域词条，域数据经配置/工件注入。

三重证据
========
1. **词表扫描**：读取平台外置的域词表配置（``config/dispatch_readiness_domain_terms.json``），
   扫描本包全部源文件；命中即失败。词表读不到时**降级为恒等**（不假装通过也不假装失败），
   这一降级本身要在测试里显式处理。
2. **依赖面扫描**：本包不得 import 领域/平台后端模块（``backend`` 等），只允许 stdlib +
   ``tools.abm.ledger``（哈希链原语）。
3. **合成域实跑**：用 alpha/beta 合成节点跑完整链路（出题 → 承诺 → 观测 → 计分 → 回填），
   证明机制不依赖任何具体域内容。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.abm.ledger import TraceLedger
from tools.blind_calibration import episode, evidence, ledger as ledger_mod, metrics

PKG = Path(ROOT) / "tools" / "blind_calibration"
TERMS_CFG = Path(ROOT) / "config" / "dispatch_readiness_domain_terms.json"


# --------------------------------------------------------------------------- #
# 1) 词表扫描
# --------------------------------------------------------------------------- #
def _source_files() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py"))


def _vocab() -> dict | None:
    if not TERMS_CFG.is_file():
        return None
    return json.loads(TERMS_CFG.read_text(encoding="utf-8"))


def test_package_has_sources():
    files = _source_files()
    assert len(files) >= 7, [p.name for p in files]


def test_no_substring_domain_terms_in_package():
    vocab = _vocab()
    if vocab is None:
        pytest.skip(f"域词表缺失（{TERMS_CFG}）：按平台约定降级为恒等，不判定")
    hits = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for term in vocab.get("substring_terms", []):
            if term in text:
                hits.append((path.name, term))
    assert hits == [], f"发现硬编码域词条: {hits}"


def test_no_word_domain_terms_in_package():
    vocab = _vocab()
    if vocab is None:
        pytest.skip("域词表缺失：降级为恒等")
    hits = []
    for path in _source_files():
        lowered = path.read_text(encoding="utf-8").lower()
        for term in vocab.get("word_terms", []):
            if re.search(rf"\b{re.escape(str(term).lower())}\b", lowered):
                hits.append((path.name, term))
    assert hits == [], f"发现硬编码域词条: {hits}"


def test_vocab_degradation_is_explicit():
    """词表文件真的存在（否则上两条会 skip 而让人误以为通过）。"""
    assert TERMS_CFG.is_file(), "域词表应随平台提供；缺失会削弱本判据的证据力"
    vocab = _vocab()
    assert vocab["substring_terms"] and vocab["word_terms"]


# --------------------------------------------------------------------------- #
# 2) 依赖面
# --------------------------------------------------------------------------- #
FORBIDDEN_IMPORTS = ("backend", "from backend", "import ship", "tensorphysics")


def test_no_backend_or_domain_imports():
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_IMPORTS:
            for line in text.splitlines():
                s = line.strip()
                if s.startswith(("import ", "from ")) and token in s:
                    offenders.append((path.name, s))
    assert offenders == []


def test_only_platform_common_layer_dependency():
    """唯一允许的仓内依赖是通用哈希链原语（tools.abm.ledger）。"""
    # tools.pre_dispatch_prior = 平台级「派发前可行性先验」，其自身声明零领域语义
    # （只按 JSON 结构数数），本包只把它当作**外部预测数据源**读，不引入域语义。
    allowed = {"tools.abm.ledger", "tools.blind_calibration", "tools.pre_dispatch_prior"}
    used = set()
    for path in _source_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("from tools.") or s.startswith("import tools."):
                mod = s.split()[1]
                used.add(".".join(mod.split(".")[:3]))
    assert used <= allowed, f"出现非通用层依赖: {sorted(used - allowed)}"


def test_external_data_source_is_also_domain_free():
    """外部预测源（平台先验）自身也必须零域词条，否则「注入」会变成「夹带」。"""
    vocab = _vocab()
    if vocab is None:
        pytest.skip("域词表缺失：降级为恒等")
    src = Path(ROOT) / "tools" / "pre_dispatch_prior"
    hits = []
    for path in sorted(src.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for term in vocab.get("substring_terms", []):
            if term in text:
                hits.append((path.name, term))
        for term in vocab.get("word_terms", []):
            if re.search(rf"\b{re.escape(str(term).lower())}\b", lowered):
                hits.append((path.name, term))
    assert hits == [], f"外部数据源出现域词条: {hits}"


# --------------------------------------------------------------------------- #
# 3) 合成域实跑
# --------------------------------------------------------------------------- #
def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="f", GIT_AUTHOR_EMAIL="f@example.invalid",
               GIT_COMMITTER_NAME="f", GIT_COMMITTER_EMAIL="f@example.invalid")
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr


SYNTH_CONFIG = {
    "schema": "blind_calibration/nodes/v1",
    "declared_at": "2026-01-01T00:00:00Z",
    "declared_by": "fixture",
    "nodes": [{"node_id": "alpha", "run_dir": "runs/alpha"},
              {"node_id": "beta", "run_dir": "runs/beta"}],
    "questions": ["file_anchor", "utf8_text", "json_parse", "json_top_is_dict"],
    "declaration_rule": {
        "file_anchor": {"p": 0.97, "ground": "fixture clean tree"},
        "utf8_text": {"p_text": 0.93, "p_binary": 0.02,
                      "text_exts": [".md", ".json"], "ground": "fixture"},
        "json_parse": {"p": 0.9, "ground": "fixture"},
        "json_top_is_dict": {"p": 0.88, "ground": "fixture"},
    },
    "prior_knowledge_disclosure": ["fixture"],
}


@pytest.fixture()
def synth_repo(tmp_path):
    repo = tmp_path / "repo"
    for name in ("alpha", "beta"):
        d = repo / "runs" / name
        d.mkdir(parents=True)
        (d / "payload.json").write_text('{"metric_a": 1.0}\n', encoding="utf-8")
        (d / "notes.md").write_text("# notes\n", encoding="utf-8")
        (d / "blob.bin").write_bytes(b"\x00\xff\xfe")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=f", "-c", "user.email=f@example.invalid",
         "commit", "-q", "-m", "synthetic delivery")
    return repo


def test_end_to_end_on_synthetic_domain(synth_repo, tmp_path):
    program = episode.plan(SYNTH_CONFIG, repo=synth_repo)
    assert program["n_items"] > 0
    led = TraceLedger(str(tmp_path / "blind.jsonl"))
    assert episode.commit(led, program) == program["n_items"]
    assert episode.observe(led, program, repo=synth_repo) == program["n_items"]
    integ = episode.integrity(led)
    assert integ["ok"] is True and integ["n_items"] == program["n_items"]

    records = episode.forecasts(led)
    assert len(records) == program["n_items"]
    cal = ledger_mod.CalibrationLedger(str(tmp_path / "cal.jsonl"))
    cal.append_many(records)
    scored = cal.score()
    assert scored["n"] == len(records)
    assert scored["checks"]["murphy_identity"]["pass"] is True
    assert cal.verify()["chain_ok"] is True

    bf = evidence.backfill([{"node_id": "alpha", "run_dir": "runs/alpha"},
                            {"node_id": "beta", "run_dir": "runs/beta"}], repo=synth_repo)
    assert bf["slot_coverage"] == pytest.approx(1.0)
    assert bf["artifact_verify_rate"] == pytest.approx(1.0)
    assert set(bf["grade_distribution"]) == {"E1"}


def test_plan_does_not_read_file_contents(monkeypatch, synth_repo):
    """出题阶段必须只用配置规则：任何一次文件内容读取都应被拦下。"""
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text
    calls: list[str] = []

    def guard(self, *a, **kw):  # noqa: ANN001
        if str(self).startswith(str(synth_repo / "runs")):
            calls.append(str(self))
        return real_read_bytes(self, *a, **kw)

    def guard_text(self, *a, **kw):  # noqa: ANN001
        if str(self).startswith(str(synth_repo / "runs")):
            calls.append(str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_bytes", guard)
    monkeypatch.setattr(Path, "read_text", guard_text)
    program = episode.plan(SYNTH_CONFIG, repo=synth_repo)
    assert program["n_items"] > 0
    assert calls == [], f"出题阶段不应读取工件内容: {calls}"


def test_metrics_are_pure_and_domain_free():
    """同一输入两次调用结果逐位相同（无隐藏状态、无随机性）。"""
    p = [0.05, 0.85, 0.95, 0.15]
    y = [0, 1, 1, 0]
    a = metrics.score(p, y)
    b = metrics.score(p, y)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)