"""case_pool 扩容加载器的纯函数单测（不连库、域词条用合成数据，符合"测试夹具用合成域名"铁律）。

覆盖：
  1. validate()     必填字段/仓白名单/case_code 形式/tol_policy 是否量化
  2. dedup 四道闸   code 归一化冲突(硬闸)、名称 bigram 报警(软闸)、同仓引文族(软闸)、跨仓同 setup(允许)
  3. anchor_probe  精确覆盖锚三重约束：CASE-* + 同仓 + 锚名主体与 case_code 互含 + 未被既有行占用
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "ops", "case_pool", "load_pool_expand_r1.py")
spec = importlib.util.spec_from_file_location("pool_loader", MOD)
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


def _case(code, repo=None, name="算例甲", ref="Synthetic 2001 J. Test 1:1", tol="<5% | 指标: 合成量"):
    return {"case_code": code, "repo": repo or pl.REPOS[0], "name": name, "ref_source": ref,
            "tol_policy": tol, "dedup_key": code.lower(), "citation": "Synthetic, A. (2001). J. Test 1, 1-2.",
            "anchor_probe": []}


def test_validate_accepts_wellformed():
    cases = [_case("FAKE-ALPHA"), _case("FAKE-BETA", name="算例乙")]
    assert pl.validate(cases) == []


def test_validate_flags_missing_field_repo_and_tol():
    bad = [_case("FAKE-ALPHA", tol=""), _case("FAKE-BETA", repo="TensorNope")]
    errs = {}
    for e in pl.validate(bad):
        errs.setdefault(e["case_code"], []).append(e["error"])
    assert "missing_field:tol_policy" in errs["FAKE-ALPHA"]
    assert "tol_policy_not_quantified" in errs["FAKE-ALPHA"]
    assert any(x.startswith("repo_not_in_whitelist") for x in errs["FAKE-BETA"])


def test_validate_flags_duplicate_and_bad_code():
    dup = [_case("FAKE-ALPHA"), _case("FAKE-ALPHA", name="别的")]
    assert any(e["error"].startswith("duplicate_inside_batch") for e in pl.validate(dup))
    assert any(e["error"] == "case_code_format" for e in pl.validate([_case("bad code!")]))


def test_dedup_hard_block_on_same_code():
    pool = [{"case_code": "FAKE-ALPHA", "repo": pl.REPOS[0], "name": "老算例",
             "ref_source": "Other 1999", "status": "passed", "linked_anchor": None}]
    rep = pl.dedup_against_pool([_case("FAKE-ALPHA-2")][0:0] + [_case("FAKE-ALPHA")], pool)
    assert rep["hard_blocks"], rep


def test_dedup_soft_hit_on_similar_name_and_family():
    pool = [{"case_code": "OLD-GAMMA", "repo": pl.REPOS[0], "name": "算例甲 变体",
             "ref_source": "Synthetic 2001 合成基准", "status": "passed", "linked_anchor": None}]
    rep = pl.dedup_against_pool([_case("FAKE-NEW", name="算例甲 变体 二")], pool)
    whys = [w for h in rep["gate_hits"] for w in h["why"]]
    assert any(w.startswith("name_bigram_jaccard") for w in whys)
    assert any(w.startswith("same_repo_citation_family") for w in whys)
    assert rep["hard_blocks"] == []


def test_dedup_same_setup_cross_repo_allowed():
    a = _case("FAKE-ONE", repo=pl.REPOS[0])
    a["dedup_key"] = "same-setup"
    b = _case("FAKE-TWO", repo=pl.REPOS[1])
    b["dedup_key"] = "same-setup"
    out = pl.dedup_key_consistency([a, b], [])
    assert out["in_repo_duplicates"] == []
    assert out["cross_repo_same_setup"] == {"same-setup": 2}


def test_anchor_probe_requires_case_prefix_same_repo_and_overlap():
    ledger = [
        {"slug": "GAP-FAKE-ALPHA", "title": "通用缺口锚", "domain": pl.REPOS[0], "status": "open"},
        {"slug": "CASE-FAKE-ALPHA", "title": "算例池·甲", "domain": pl.REPOS[0], "status": "done"},
    ]
    c = _case("FAKE-ALPHA")
    c["anchor_probe"] = ["甲"]
    out = pl.anchor_probe([c], ledger, pool_rows=[])[0]
    assert [m["slug"] for m in out["exact_covering_anchor"]] == ["CASE-FAKE-ALPHA"]

    # 锚已被既有池行占用 -> 不得再填（否则该例被 case_feeder 跳过、供给线又断）
    out2 = pl.anchor_probe([c], ledger, pool_rows=[{"case_code": "FAKE-ALPHA", "repo": pl.REPOS[0]}] * 1)[0]
    assert out2["exact_covering_anchor"] == []
    assert out2["decision"] == "linked_anchor=NULL"


def test_anchor_probe_leaves_null_when_no_match():
    c = _case("FAKE-ZETA")
    c["anchor_probe"] = ["不存在"]
    out = pl.anchor_probe([c], [{"slug": "CASE-OTHER", "title": "别的", "domain": pl.REPOS[0],
                                 "status": "done"}], pool_rows=[])[0]
    assert out["exact_covering_anchor"] == []
    assert out["decision"] == "linked_anchor=NULL"


def test_seed_batch_shape():
    """真实 seed 只做形状/唯一性检查（域数据在 data 文件里，不在测试代码里硬编码域词条）。"""
    cases = pl.load_seeds()
    assert 30 <= len(cases) <= 50, len(cases)
    assert len({c["case_code"] for c in cases}) == len(cases)
    assert {c["repo"] for c in cases} <= set(pl.REPOS)
    assert len({c["repo"] for c in cases}) == 6
    assert pl.validate(cases) == []