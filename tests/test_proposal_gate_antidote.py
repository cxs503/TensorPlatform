"""反作弊三问 + 零领域语义自检（代码即判据）。

反作弊判据
----------
(a) 同一提案重复提交 → ``duplicate``，``dup_of`` 指向首次，**不新增行**；
(b) 同义改写变体（实测相似度 > 0.85 的轻改写 / 落在复核带的改写族）**必须命中**：
    轻改写直接自动拒，重改写进复核带待复核——都不许静默入池；
(c) 措辞完全不同但主题不同的合法提案**不得误判**（precision 门）。

零领域语义自检
--------------
域词黑名单从 ``config/proposal_gate.json`` → ``domain_vocab_file`` 注入，
扫描本节点代码/测试/配置；配置里的仓库白名单属「数据」而非「散文」，
扫描前按名单逐项遮蔽，避免把仓库名误当域词。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from proposal_gate_support import (  # noqa: E402
    PARAPHRASE_VARIANTS,
    distinct_payload,
    synth_config,
    v2_payload,
)
from tools.proposal_gate.config import GateConfig  # noqa: E402
from tools.proposal_gate.gate import ProposalGate  # noqa: E402
from tools.proposal_gate.ledger import LedgerStore  # noqa: E402
from tools.proposal_gate.textsim import similarity  # noqa: E402

SCAN_TARGETS = (
    "tools/proposal_gate/*.py",
    "backend/api/proposal_gate.py",
    "tests/proposal_gate_support.py",
    "tests/test_proposal_gate_*.py",
    "config/proposal_gate.json",
)


@pytest.fixture()
def cfg():
    return synth_config()


@pytest.fixture()
def gate(tmp_path, cfg):
    store = LedgerStore.for_sqlite(str(tmp_path / "ledger.sqlite"), cfg)
    g = ProposalGate(cfg, store, quarantine_dir=tmp_path / "q", events_file=tmp_path / "e.jsonl")
    yield g
    store.dispose()


# ── (a) 重复提交 ────────────────────────────────────────────────────────


def test_antidote_a_resubmission_points_to_first_and_adds_no_row(gate):
    first = gate.evaluate(v2_payload(), mission_id=10001)
    for _ in range(3):
        again = gate.evaluate(v2_payload(), mission_id=10001)
        assert again.verdict == "duplicate"
        assert again.dup_of == first.sha256
        assert again.created is False
    rows = gate.ledger.rows(mission_id=10001, limit=100)
    assert len(rows) == 1, [r.as_dict() for r in rows]  # 唯一键真的挡住了
    assert rows[0].verdict == "accepted"  # 首次判定不被覆写


# ── (b) 同义改写必须命中 ────────────────────────────────────────────────


def test_antidote_b_light_rewrite_is_auto_rejected(gate):
    first = gate.evaluate(v2_payload(), mission_id=10101)
    rewrites = [
        v2_payload(objective="为 foo 通道构建产物契约、去重器与覆盖率账本三道机器门"),
        v2_payload(objective="为 foo 通道建立产物契约、去重器与覆盖率台账三道机器门"),
        v2_payload(criteria=("重复率 < 0.15", "播种覆盖比例 ≥ 0.80", "误判率 ≤ 0.05")),
    ]
    measured = []
    for i, payload in enumerate(rewrites):
        res = gate.evaluate(payload, mission_id=10110 + i)
        measured.append((res.verdict, round(res.score, 4)))
        assert res.score > 0.85, res.as_dict()
        assert res.verdict == "duplicate", res.as_dict()
        assert res.dup_of == first.sha256
    assert all(v == "duplicate" for v, _ in measured), measured


def test_antidote_b_heavy_paraphrase_is_never_silently_admitted(gate):
    gate.evaluate(v2_payload(), mission_id=10201)
    accepted = []
    scores = []
    for i, variant in enumerate(PARAPHRASE_VARIANTS):
        res = gate.evaluate(v2_payload(title=variant, objective=variant), mission_id=10210 + i)
        scores.append(round(res.score, 4))
        if res.verdict == "accepted":
            accepted.append(res.as_dict())
        assert res.verdict in ("duplicate", "near_duplicate"), res.as_dict()
        assert res.state in ("dedup_rejected", "quarantined")
    assert accepted == [], accepted
    # 复核带下界之上全部命中（实测最小分值写在断言里，可复算）
    assert min(scores) >= float(gate.cfg.need("dedup.review_threshold")), scores


# ── (c) precision 门：不得误判 ──────────────────────────────────────────


def test_antidote_c_distinct_topics_are_accepted(gate):
    base = gate.evaluate(v2_payload(), mission_id=10300)
    assert base.verdict == "accepted"
    review = float(gate.cfg.need("dedup.review_threshold"))
    scores = []
    for i in range(3):
        res = gate.evaluate(distinct_payload(i), mission_id=10310 + i)
        scores.append(round(res.score, 4))
        assert res.verdict == "accepted", res.as_dict()
    assert max(scores) < review, scores


def test_antidote_c_margin_is_quantified(gate):
    """把「余量」写成数字：主题不同提案的最高分要低于复核带下界 ≥0.30。"""
    base_doc = v2_payload()
    from tools.proposal_gate.contract import validate_proposal

    base_blob = validate_proposal(base_doc, gate.cfg).doc.blob()
    top = 0.0
    for i in range(3):
        p = distinct_payload(i)
        blob = validate_proposal(p, gate.cfg).doc.blob()
        top = max(top, similarity(base_blob, blob, gate.cfg))
    review = float(gate.cfg.need("dedup.review_threshold"))
    assert review - top >= 0.30, (top, review)


# ── 零领域语义自检 ──────────────────────────────────────────────────────


def _mask_repo_names(text: str, mask_words: list[str]) -> str:
    """遮蔽仓库名（数据）：仓库名不是散文，且其中一个仓名内部含有域词子串。"""
    for repo in mask_words:
        text = text.replace(repo, "REPO")
    return text


def _scan(files: list[Path], ascii_words: list[str], cjk_words: list[str], mask_words: list[str]):
    hits: list[str] = []
    ascii_re = re.compile(
        "|".join(rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])" for w in ascii_words),
        re.IGNORECASE,
    )
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        text = _mask_repo_names(raw, mask_words)
        low = text.lower()
        for match in ascii_re.finditer(text):
            hits.append(f"{path}:ascii:{match.group(0)}")
        for word in cjk_words:
            if word and word in low:
                hits.append(f"{path}:cjk:{word}")
    return hits


def test_zero_domain_semantics_scan(cfg):
    ascii_words, cjk_words = cfg.domain_vocab()
    assert ascii_words, "域词黑名单必须由配置注入（空表说明注入链断了）"
    files: list[Path] = []
    for pattern in SCAN_TARGETS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    assert len(files) >= 8, [str(f) for f in files]
    real_cfg = GateConfig.load(REPO_ROOT / "config" / "proposal_gate.json")
    hits = _scan(files, ascii_words, cjk_words, list(real_cfg.repo_whitelist))
    assert not hits, hits


# 判定路径模块：这里的阈值必须全部来自配置（字面量=违规）
DECISION_MODULES = (
    "config.py",
    "contract.py",
    "index.py",
    "gate.py",
    "ledger.py",
    "textsim.py",
    "sources.py",
)
# 分析/渲染模块（标定扫描、出图、验收清单）：允许出现分析网格数值，
# 但它们采用的**工作点**必须从配置读（下面单独断言）
ANALYSIS_MODULES = ("calibrate.py", "figures.py", "acceptance.py")


def test_thresholds_live_only_in_config(cfg):
    """判定路径里不得出现阈值字面量：配置里的每个阈值都不能出现在实现源码里。"""
    values = [
        cfg.need("dedup.dup_threshold"),
        cfg.need("dedup.review_threshold"),
        cfg.need("dedup.twin_dependency_overlap"),
        cfg.need("dedup.coverage_match_threshold"),
        cfg.get("dedup.min_length_ratio"),
        cfg.get("dedup.containment_weight"),
    ]
    needles = {str(v) for v in values if v is not None and str(v) not in ("0", "1", "1.0")}
    assert needles, values
    offenders: list[str] = []
    for name in DECISION_MODULES:
        path = REPO_ROOT / "tools" / "proposal_gate" / name
        body = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in body:
                offenders.append(f"{name}:{needle}")
    assert not offenders, offenders


def test_analysis_modules_read_operating_points_from_config():
    """分析/渲染模块的工作点也必须来自配置（不许把 0.85/0.55 抄进脚本）。"""
    base = REPO_ROOT / "tools" / "proposal_gate"
    calibrate = (base / "calibrate.py").read_text(encoding="utf-8")
    assert 'cfg.need("dedup.dup_threshold")' in calibrate
    assert 'cfg.need("dedup.review_threshold")' in calibrate
    acceptance = (base / "acceptance.py").read_text(encoding="utf-8")
    assert 'cfg.need("dedup.dup_threshold")' in acceptance
    assert 'cfg.need("dedup.twin_dependency_overlap")' in acceptance
    # 出图模块不读配置，只消费标定工件里的工作点（config → calibration → figure 单源）
    figures = (base / "figures.py").read_text(encoding="utf-8")
    assert 'operating_points' in figures
    assert '"dup"' in figures and '"review"' in figures


def test_index_holds_no_wordlist(cfg):
    """索引只存文本与结构化键：不得出现域词表/白名单属性（白名单只在 config 层）。"""
    from tools.proposal_gate.index import ProposalIndex

    attrs = set(vars(ProposalIndex(cfg)))
    assert attrs == {"cfg", "entries"}, attrs


def test_domain_vocab_is_data_not_code(cfg):
    """域词黑名单来自文件（配置注入），代码里取到的是数据。"""
    ascii_words, cjk_words = cfg.domain_vocab()
    assert isinstance(ascii_words, list) and isinstance(cjk_words, list)
    data = json.loads(cfg.resolve_path(str(cfg.need("domain_vocab_file"))).read_text(encoding="utf-8"))
    assert data["ascii_words"] == ascii_words