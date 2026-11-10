"""四契约知识沉淀管线（p351w1_n03）后端契约围栏。

判据（机器可裁，编号与战役节点 metadata 对齐）
--------------------------------------------
(a) 召回：独立标注集上四契约 recall ≥ 0.8，且虚构槽位 == 0；
(b) schema：入库条目 100% 含完整字段（``schema_complete_ratio == 1.0``）；
(c) 幂等：同一 mission 重复触发 → 条目数不增（第二遍 ``inserted == 0``）；
(d) 检索：``GET /api/v1/knowledge/search`` 命中正确条目、域过滤与 limit 生效；
(e) 质量门：无 evidence 的构造件 → 拒绝入库 + 落 rejected 日志；
(f) 时延：1000 条知识检索 ≤ 100ms（本文件用小规模 + CLI 压测工件覆盖）；
(g) 本文件 ≥18 项（含负控）；
(h) 零领域语义：改动路径过 ``tools/boundary/pr_gate_scan.py`` → 判红 0。

运行::

    cd backend && python -m pytest tests/test_knowledge_sediment.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api import knowledge_sediment as ks_api  # noqa: E402
from api.deps import require_auth  # noqa: E402
from services.knowledge_sediment import (  # noqa: E402
    CONTRACT_FIELDS,
    ENTRY_FIELDS,
    KnowledgeStore,
    content_sha256,
    entry_is_complete,
    extract_from_dir,
    has_evidence_signal,
    scan_root,
    section_field,
)
from services.knowledge_sediment.contracts import MIN_FIELD_CHARS, split_sections  # noqa: E402
from services.knowledge_sediment.pipeline import (  # noqa: E402
    load_labels,
    recall_against_reference,
)

ZERO_DOMAIN_PATHS = [
    "backend/services/knowledge_sediment/__init__.py",
    "backend/services/knowledge_sediment/contracts.py",
    "backend/services/knowledge_sediment/extractor.py",
    "backend/services/knowledge_sediment/store.py",
    "backend/services/knowledge_sediment/pipeline.py",
    "backend/api/knowledge_sediment.py",
    "backend/tests/test_knowledge_sediment.py",
    "tools/knowledge_sediment.py",
]

CH_HEAD = ["## 1. 案例介绍", "## 2. 方法", "## 3. 计算结果", "## 4. 结果图", "## 5. 注意点", "## 6. 误差判定"]
EN_HEAD = ["## 1. Case Introduction", "## 2. Method", "## 3. Results", "## 4. Figures", "## 5. Limitations"]


# --------------------------------------------------------------------- 夹具
def write_report(
    root: Path,
    mission: str,
    mid: int | str,
    markdown: str,
    *,
    summary: str = "任务摘要：把一件可验证的事做完。",
    declared: int | str | None = None,
    tags: list[str] | None = None,
    extra: dict | None = None,
) -> Path:
    d = root / mission
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "title": f"fixture {mission}",
        "summary": summary,
        "scenario_key": "fixture",
        "mission_id": declared if declared is not None else mid,
        "tags": tags or ["fixture", "alpha"],
        "markdown": markdown,
    }
    if extra:
        doc.update(extra)
    (d / "report.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return d


def ch_report(mid: int | str = 100) -> str:
    return "\n\n".join(
        [
            "# 合成报告",
            CH_HEAD[0] + "\n对象：一段合成的背景描述，用于验证模板切分。",
            CH_HEAD[1] + "\n步骤：先做甲，再做乙；工具链为 pytest + shell。",
            CH_HEAD[2] + "\n| 量 | 值 |\n|---|---|\n| 残差 | 1.2e-9 |\n| 样本数 | 42 |",
            CH_HEAD[3] + "\n本用例无图。",
            CH_HEAD[4] + "\n局限：只覆盖合成情形，不外推到其它尺度。",
            CH_HEAD[5] + "\n相对基准误差 0.7%，低于 3%。",
        ]
    )


def entry_payload(mid: str = "1", *, evidence: str | None = None, domain: str = "alpha") -> dict:
    contracts = {
        "problem": "合成问题：某量在给定条件下是否守恒。",
        "method": "合成方法：先离散、再积分、最后对比基准。",
        "evidence": evidence if evidence is not None else "残差 1.2e-9，误差 0.7%，pytest 全绿。",
        "limitations": "局限：只覆盖合成情形。",
    }
    sha = content_sha256(mid, contracts)
    return {
        "id": f"ke-{sha[:16]}",
        "mission_id": mid,
        "domain": domain,
        "tags": ["fixture", "alpha"],
        "sha256": sha,
        "related": [],
        "source": {"mission_dir": f"m{mid}"},
        **contracts,
    }


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "store")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("KM_SEDIMENT_STORE", str(tmp_path / "api_store"))
    app = FastAPI()
    app.include_router(ks_api.router, prefix="/api/v1")
    app.dependency_overrides[require_auth] = lambda: {"username": "pytest"}
    return TestClient(app)


# ------------------------------------------------- (a) 章节契约识别（模板面）
def test_section_field_recognises_numbered_and_circled_headers():
    assert section_field("1. 案例介绍（物理背景/几何/参数表）") == "problem"
    assert section_field("① 案例介绍") == "problem"
    assert section_field("2. Case Introduction") == "problem"
    assert section_field("② 怎么做的（方法/流程/关键设置/工具链）") == "method"
    assert section_field("3. 计算结果") == "evidence"
    assert section_field("⑥ 误差判定") == "evidence"
    assert section_field("5. 注意点（踩过的坑/局限/适用范围）") == "limitations"
    assert section_field("5. Limitations") == "limitations"


def test_section_field_negative_unknown_header_returns_none():
    # 负控：机制无关的标题不得被误判成任何契约
    for title in ("附录 A 符号表", "参考文献", "致谢", ""):
        assert section_field(title) is None


def test_split_sections_keeps_body_per_header():
    sections = split_sections(ch_report())
    titles = [s["title"] for s in sections]
    assert "1. 案例介绍" in titles
    body = next(s["body"] for s in sections if s["title"] == "3. 计算结果")
    assert "1.2e-9" in body


# ------------------------------------------------------- 证据判据（质量门）
def test_has_evidence_signal_accepts_numbers_and_markers():
    assert has_evidence_signal("相对误差 0.7%，低于 3%")
    assert has_evidence_signal("pytest 全绿，日志见 artifacts/run.log")


def test_has_evidence_signal_negative_qualitative_only():
    # 负控：只有形容词、没有可核对信号的段落不算证据
    assert not has_evidence_signal("效果良好，符合预期，没有明显问题")
    assert not has_evidence_signal("完了")


def test_content_sha256_stable_and_sensitive():
    a = {"problem": "同一段  ", "method": "方法", "evidence": "1", "limitations": "局限"}
    b = {"problem": "同一段", "method": "方法  ", "evidence": " 1 ", "limitations": "局限\n"}
    assert content_sha256("7", a) == content_sha256("7", b)      # 空白折叠 → 同摘要
    assert content_sha256("7", a) != content_sha256("8", a)      # 换 mission → 不同摘要
    assert content_sha256("7", a) != content_sha256("7", {**a, "evidence": "2"})


# --------------------------------------------------------- 抽取器（正/负控）
def test_extract_chinese_template_all_four(tmp_path: Path):
    d = write_report(tmp_path, "m101", 101, ch_report())
    e = extract_from_dir(d)
    assert e.accepted and e.reason is None
    assert e.filled == list(CONTRACT_FIELDS)
    assert "1.2e-9" in e.contracts["evidence"]
    assert e.mission_id == "101"
    assert e.sha256 and len(e.sha256) == 64


def test_extract_english_template_all_four(tmp_path: Path):
    md = "\n\n".join(
        [
            "# report",
            EN_HEAD[0] + "\nSynthetic background paragraph.",
            EN_HEAD[1] + "\nSteps one and two, verified by pytest.",
            EN_HEAD[2] + "\nresidual 3.4e-8 over 96 samples.",
            EN_HEAD[4] + "\nOnly synthetic scope is covered.",
        ]
    )
    e = extract_from_dir(write_report(tmp_path, "m102", 102, md))
    assert e.accepted
    assert e.filled == list(CONTRACT_FIELDS)


def test_extract_missing_limitations_is_rejected(tmp_path: Path):
    # 负控：缺一个契约 → 拒绝入库 + 原因码指向缺口
    md = "\n\n".join(
        [
            "# report",
            "## 1. 案例介绍\n合成背景：验证缺段时抽取器的拒绝路径。",
            "## 2. 方法\n先离散再积分，pytest 通过 12 项。",
            "## 3. 计算结果\n残差 1e-9，样本数 42。",
        ]
    )
    e = extract_from_dir(write_report(tmp_path, "m103", 103, md))
    assert not e.accepted
    assert e.reason == "incomplete_contracts"
    assert e.missing == ["limitations"]


def test_extract_without_evidence_signal_is_rejected(tmp_path: Path):
    # 判据 (e)：四段凑齐但 evidence 无任何可核对信号 → 拒绝
    md = "\n\n".join(
        [
            "# report",
            "## 1. 案例介绍\n合成背景描述：验证证据门的拒绝路径。",
            "## 2. 方法\n按既定步骤把流程走完并记录。",
            "## 3. 计算结果\n整体效果好，符合预期，没有明显问题。",
            "## 4. 注意点\n只覆盖合成情形，不外推到其它尺度。",
        ]
    )
    e = extract_from_dir(write_report(tmp_path, "m104", 104, md))
    assert not e.accepted
    assert e.reason == "no_evidence"


def test_extract_unattributable_report_is_rejected(tmp_path: Path):
    # 负控：源报文自述的 mission_id 与目录号不符（整仓检出带进来的别人的报告）
    d = write_report(tmp_path, "m105", 105, ch_report(105), declared=999)
    e = extract_from_dir(d)
    assert not e.accepted
    assert e.reason == "no_attributable_source"
    assert e.attributed is False


def test_extract_shared_gate_summary_not_used(tmp_path: Path):
    # 负控：全库共享的 gate 自述不能当本 mission 的契约来源
    d = tmp_path / "m106"
    d.mkdir()
    gate = d / "REPORT_GATE_SUMMARY.md"
    gate.write_text("## 实现清单\n- 改了甲文件；pytest 通过 12 项。", encoding="utf-8")
    from services.knowledge_sediment.extractor import _digest  # 内部工具，仅供夹具构造

    sha, _ = _digest(gate)
    e = extract_from_dir(d, shared_shas={sha})
    assert not e.accepted and e.reason == "no_attributable_source"
    # 对照：不把它当共享件时，gate 自述可以兜底出 method
    e2 = extract_from_dir(d, shared_shas=set())
    assert "method" in e2.filled


def test_extraction_entry_has_full_schema(tmp_path: Path):
    e = extract_from_dir(write_report(tmp_path, "m107", 107, ch_report(107)))
    entry = e.to_entry()
    assert set(ENTRY_FIELDS) <= set(entry)
    assert entry_is_complete(entry)
    assert entry["id"] == f"ke-{entry['sha256'][:16]}"


# ------------------------------------------------------------ 存储（判据 b/c/e）
def test_store_insert_persists_and_reloads(store: KnowledgeStore, tmp_path: Path):
    assert store.add(entry_payload("11")).status == "inserted"
    assert store.count() == 1
    again = KnowledgeStore(store.dir)
    assert again.count() == 1
    got = again.get(entry_payload("11")["id"])
    assert got and got["mission_id"] == "11"


def test_store_idempotent_same_content_does_not_grow(store: KnowledgeStore):
    # 判据 (c)：同一 mission 重复触发 → 条目数不增
    assert store.add(entry_payload("12")).status == "inserted"
    for _ in range(3):
        assert store.add(entry_payload("12")).status == "duplicate"
    assert store.count() == 1


def test_store_links_related_mission_on_duplicate_content(store: KnowledgeStore):
    first = entry_payload("13")
    assert store.add(first).status == "inserted"
    # 另一个 mission 产出同样的四契约（内容级重复）→ 不新增条目，只建关联
    res = store.add({**entry_payload("13"), "id": "other-id", "mission_id": "14"})
    assert res.status == "linked"
    assert store.count() == 1
    entry = store.get(first["id"])
    assert entry["related"] == ["14"]


def test_store_rejects_missing_field_and_logs(store: KnowledgeStore):
    # 判据 (e)：schema 不全 → 拒绝 + 落 rejected 日志
    bad = entry_payload("15")
    bad["limitations"] = ""
    assert store.add(bad).status == "rejected"
    assert store.count() == 0
    rows = store.rejected()
    assert rows and rows[0]["reason"] == "incomplete_schema"
    assert rows[0]["mission_id"] == "15"


def test_store_rejects_no_evidence_and_logs(store: KnowledgeStore):
    assert store.add(entry_payload("16", evidence="效果良好，符合预期")).status == "rejected"
    assert store.count() == 0
    assert any(r["reason"] == "no_evidence" for r in store.rejected())


def test_store_schema_complete_ratio_is_one(store: KnowledgeStore):
    # 判据 (b)：入库知识 100% 含完整 schema
    for i in range(5):
        store.add(entry_payload(str(20 + i)))
    stats = store.stats()
    assert stats["count"] == 5
    assert stats["schema_complete_ratio"] == 1.0
    assert all(entry_is_complete(e) for e in store.all_entries())


def test_store_bulk_add_flushes_once(store: KnowledgeStore):
    tally = store.add_many([entry_payload(str(30 + i)) for i in range(6)])
    assert tally["inserted"] == 6
    assert KnowledgeStore(store.dir).count() == 6


# --------------------------------------------------------------- 检索（判据 d/f）
def test_search_matches_problem_and_returns_fields(store: KnowledgeStore):
    store.add(entry_payload("41"))
    store.add(
        {
            **entry_payload("42"),
            "problem": "另一件事：把离散化收敛性测出来。",
            "sha256": "b" * 64,
            "id": "ke-" + "b" * 16,
        }
    )
    res = store.search(q="收敛性")
    assert res["total"] == 1
    hit = res["items"][0]
    assert hit["mission_id"] == "42"
    for field in CONTRACT_FIELDS + ("id", "domain", "tags", "sha256"):
        assert field in hit


def test_search_domain_filter(store: KnowledgeStore):
    store.add(entry_payload("43", domain="alpha"))
    store.add({**entry_payload("44"), "domain": "beta", "sha256": "c" * 64, "id": "ke-" + "c" * 16,
               "problem": "合成问题二：另一条待检索的知识。"})
    assert store.search(q="", domain="beta")["total"] == 1
    assert store.search(q="", domain="gamma")["total"] == 0


def test_search_limit_and_empty_query(store: KnowledgeStore):
    store.add_many([entry_payload(str(50 + i)) for i in range(6)])
    assert store.search(q="", limit=100)["count"] == 6
    assert store.search(q="", limit=2)["count"] == 2


def test_search_negative_no_hit(store: KnowledgeStore):
    # 负控：库里没有的东西必须查不到（不许模糊兜底返回全库）
    store.add(entry_payload("45"))
    assert store.search(q="zzz-not-present")["total"] == 0


def test_search_latency_small_batch(store: KnowledgeStore):
    # 判据 (f) 的小规模代理：1000 条压测见 artifacts/knowledge_sediment/bench_report.json
    store.add_many([entry_payload(str(1000 + i)) for i in range(120)])
    res = store.search(q="合成")
    assert res["took_ms"] < 100.0


# ------------------------------------------------------------- pipeline（判据 a/c）
def _build_corpus(tmp_path: Path) -> tuple[Path, dict]:
    """合成 10 mission 语料 + 独立标注（模拟真实语料的 4 种模板 + 2 类缺口）。"""
    root = tmp_path / "work"
    root.mkdir()
    labels: dict = {}
    for i in range(8):
        mid = 200 + i
        write_report(root, f"m{mid}", mid, ch_report(mid))
        labels[str(mid)] = {"present": list(CONTRACT_FIELDS)}
    # 两类缺口：缺 limitations / evidence 无信号
    mid = 210
    md = "\n\n".join(
        [
            "# r",
            "## 1. 案例介绍\n合成背景：验证召回标注的缺口样本。",
            "## 2. 方法\n先离散再积分，pytest 通过 12 项。",
            "## 3. 计算结果\n残差 1e-9，样本数 42。",
        ]
    )
    write_report(root, f"m{mid}", mid, md)
    labels[str(mid)] = {"present": ["problem", "method", "evidence"]}
    mid = 211
    md = "\n\n".join(
        [
            "# r",
            "## 1. 案例介绍\n合成背景：证据无信号的负控样本。",
            "## 2. 方法\n按既定步骤把流程走完并记录。",
            "## 3. 计算结果\n效果好。",
            "## 4. 注意点\n仅合成情形，不外推。",
        ]
    )
    write_report(root, f"m{mid}", mid, md)
    labels[str(mid)] = {"present": ["problem", "method", "limitations"]}
    # 污染件：一个自称别的 mission 的报告（整仓检出场景）
    write_report(root, "m212", 212, ch_report(999), declared=999)
    labels["212"] = {"present": []}
    return root, labels


def test_pipeline_recall_against_labels(tmp_path: Path):
    # 判据 (a)：机制层召回 ≥0.8 且虚构槽位 0（真实语料 recall 见 artifacts）
    root, labels = _build_corpus(tmp_path)
    summary = scan_root(root=root, store=KnowledgeStore(tmp_path / "s1"), dry_run=True)
    recall = recall_against_reference(summary["rows"], labels)
    assert recall["recall"] >= 0.8
    assert recall["fabricated_slots"] == 0


def test_pipeline_second_run_inserts_nothing(tmp_path: Path):
    # 判据 (c)：同一批 mission 跑两遍 → 第二遍 0 新增
    root, _ = _build_corpus(tmp_path)
    store = KnowledgeStore(tmp_path / "s2")
    first = scan_root(root=root, store=store, dry_run=False)
    assert first["tally"]["inserted"] == 8
    second = scan_root(root=root, store=store, dry_run=False)
    assert second["tally"]["inserted"] == 0
    assert store.count() == 8


def test_pipeline_attributes_shared_artifacts(tmp_path: Path):
    # 负控：同一份报告被复制到两个目录 → 归属门按自述 id 只认一个
    root = tmp_path / "work2"
    root.mkdir()
    src = write_report(root, "m301", 301, ch_report(301)) / "report.json"
    dst = root / "m302"
    dst.mkdir()
    (dst / "report.json").write_bytes(src.read_bytes())   # 逐字节复制（整仓检出的真实形态）
    summary = scan_root(root=root, store=KnowledgeStore(tmp_path / "s3"), dry_run=True)
    by_id = {r["mission_id"]: r for r in summary["rows"]}
    assert by_id["301"]["attributed"] is True
    assert by_id["302"]["attributed"] is False
    assert by_id["302"]["reason"] == "no_attributable_source"
    assert summary["contamination"]["shared_contents"] >= 1


def test_pipeline_reference_labels_file_is_loadable():
    labels = load_labels(ROOT / "artifacts" / "knowledge_sediment" / "reference_labels.json")
    assert len(labels) == 10
    for mid, row in labels.items():
        assert set(row["present"]) <= set(CONTRACT_FIELDS), mid


# ------------------------------------------------------------------ API（判据 d）
def test_api_search_returns_hits(client: TestClient):
    store = KnowledgeStore(os.environ["KM_SEDIMENT_STORE"])
    store.add(entry_payload("61"))
    res = client.get("/api/v1/knowledge/search", params={"q": "守恒"}).json()
    assert res["available"] is True
    assert res["count"] == 1
    assert res["items"][0]["mission_id"] == "61"
    assert "took_ms" in res


def test_api_search_domain_and_limit(client: TestClient):
    store = KnowledgeStore(os.environ["KM_SEDIMENT_STORE"])
    store.add_many([entry_payload(str(70 + i), domain="alpha") for i in range(3)])
    res = client.get("/api/v1/knowledge/search", params={"q": "", "domain": "alpha", "limit": 2}).json()
    assert res["count"] == 2 and res["total"] == 3
    assert client.get("/api/v1/knowledge/search", params={"q": "", "domain": "beta"}).json()["total"] == 0


def test_api_item_404_negative(client: TestClient):
    assert client.get("/api/v1/knowledge/items/ke-not-there").status_code == 404


def test_api_stats_and_rejected(client: TestClient):
    store = KnowledgeStore(os.environ["KM_SEDIMENT_STORE"])
    store.add(entry_payload("81"))
    store.add(entry_payload("82", evidence="效果良好"))
    stats = client.get("/api/v1/knowledge/stats").json()
    assert stats["count"] == 1 and stats["schema_complete_ratio"] == 1.0
    rej = client.get("/api/v1/knowledge/rejected").json()
    assert rej["count"] == 1 and rej["items"][0]["reason"] == "no_evidence"


def test_api_scan_dry_run_writes_nothing(client: TestClient, tmp_path: Path):
    root, _ = _build_corpus(tmp_path / "corpus")
    res = client.post("/api/v1/knowledge/scan", json={"root": str(root), "dry_run": True}).json()
    assert res["dry_run"] is True
    assert res["tally"]["would_insert"] >= 8
    assert KnowledgeStore(os.environ["KM_SEDIMENT_STORE"]).count() == 0


def test_api_scan_requires_root(client: TestClient, monkeypatch):
    monkeypatch.delenv("KM_SEDIMENT_WORK_ROOT", raising=False)
    assert client.post("/api/v1/knowledge/scan", json={"dry_run": True}).status_code == 400


# ------------------------------------------------------- 判据 (h)：零领域语义
def test_zero_domain_semantics_gate():
    existing = [p for p in ZERO_DOMAIN_PATHS if (ROOT / p).is_file()]
    assert len(existing) >= 6
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools/boundary/pr_gate_scan.py"), "paths", *existing],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert r.returncode in (0, 1), r.stderr[-800:]
    doc = json.loads(r.stdout)
    assert doc["verdict"] == "PASS", json.dumps(doc.get("findings", doc)[:5], ensure_ascii=False)


def test_contract_alias_table_has_no_domain_terms():
    """负控的反面：章节词表只描述报告写作契约（过程词），不含学科词条。

    若有人把某个学科词条塞进别名表，这里会红 —— 机制层不该认识任何学科。
    """
    from services.knowledge_sediment.contracts import SECTION_ALIASES

    lexicon = json.loads((ROOT / "config" / "domain_word_denylist.json").read_text(encoding="utf-8"))
    banned = [w.lower() for w in lexicon["ascii_words"]] + [w for w in lexicon["cjk_words"]]
    for field, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            low = alias.lower()
            for term in banned:
                assert term not in low, f"{field} 别名含域词 {term!r}: {alias}"


def test_min_field_chars_floor_documented():
    assert MIN_FIELD_CHARS >= 4  # 太短的「契约」等于没答，判空

# ------------------------------------------------------------ 接线守卫（组装面）
def test_router_is_wired_into_domain_aggregator():
    """机检：聚合路由表里确实 include 了沉淀检索路由（防「模块写完但没挂载」）。"""
    src = (BACKEND / "api" / "domain" / "tensor.py").read_text(encoding="utf-8")
    assert "knowledge_sediment_router" in src
    assert "router.include_router(knowledge_sediment_router)" in src


# ------------------------------------------------- 凭据掩码（知识库不得搬运口令）
def test_redact_secrets_masks_credential_shapes():
    from services.knowledge_sediment.contracts import REDACTION_MARK, redact_secrets

    cases = [
        "PGPASSWORD（s3cr3t-value-here）见代码",
        "token=abcdef123456789",
        "密码(abc12345)",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdef",
        "dsn=postgresql://user:pw123@db:5432/app",
        "令牌ghp_" + "a" * 30,
    ]
    for text in cases:
        masked, hits = redact_secrets(text)
        assert hits >= 1, text
        assert "abcdef123456789" not in masked
        assert "s3cr3t-value-here" not in masked
        assert "pw123" not in masked
        assert REDACTION_MARK in masked


def test_redact_secrets_keeps_measurements():
    # 负控：数值/误差/判据不能被误掩码（否则证据被吃）
    from services.knowledge_sediment.contracts import redact_secrets

    text = "残差 1.2e-9，误差 0.7%，样本数 42，判据 <3%，pytest 39 项全绿。"
    masked, hits = redact_secrets(text)
    assert hits == 0
    assert masked == text


def test_extract_records_redactions(tmp_path: Path):
    md = "\n\n".join(
        [
            "# report",
            "## 1. 案例介绍\n合成背景：验证凭据掩码记账。",
            "## 2. 方法\n按步骤执行，pytest 通过 12 项。",
            "## 3. 计算结果\n残差 1e-9，样本数 42。",
            "## 4. 注意点\n需要 PG_PASSWORD=supersecret123 才能连库，仅合成环境使用。",
        ]
    )
    e = extract_from_dir(write_report(tmp_path, "m108", 108, md))
    assert e.accepted
    assert e.redactions >= 1
    assert "supersecret123" not in e.contracts["limitations"]
    assert e.to_entry()["source"]["redactions"] == e.redactions


def test_store_masks_injected_secret_defensively(store: KnowledgeStore):
    # 纵深防御：绕过抽取器直插带口令的条目，贮藏层仍然掩码
    payload = entry_payload("17")
    payload["evidence"] = "连接成功；PGPASSWORD（leaked-value-42）；残差 1e-9"
    assert store.add(payload).status == "inserted"
    entry = store.all_entries()[0]
    assert "leaked-value-42" not in entry["evidence"]
    assert entry.get("redactions", 0) >= 1
