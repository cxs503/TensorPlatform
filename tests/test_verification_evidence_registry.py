#!/usr/bin/env python3
"""验证证据登记册（tools/verification_evidence_registry.py）的判据测试。

设计：全部用 tmp_path 内联夹具建一个微型"仓库"，把抽取规则、指针解析、
判级优先级、门禁退出码逐条钉死。不依赖真实语料（真实语料上的数字见
artifacts/verification_evidence/registry.json）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import verification_evidence_registry as ver  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def write(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


@pytest.fixture()
def mini_repo(tmp_path):
    root = str(tmp_path)
    write(root, "tests/test_anchor.py",
          "def test_closed_form():\n    assert 1 == 1\n\n\nclass TestGroup:\n    def test_x(self):\n        pass\n")
    write(root, "backend/tests/test_moved.py", "def test_relocated():\n    pass\n")
    write(root, "src/solver.py", "def solve():\n    return 0\n")
    write(root, "artifacts/ev/anchor.json", "{\"ok\": true}\n")
    # A：真锚定
    write(root, "docs/verification/A_real.md", """# A
对照解析解，最大相对误差 0.5%，判据 PASS。
- 测试：`tests/test_anchor.py::test_closed_form`
- 工件：`artifacts/ev/anchor.json`
""")
    # B：只有自洽
    write(root, "docs/verification/B_self.md", """# B
双路线互校，偏差 1e-12，两条路线一致。
- 测试：`tests/test_anchor.py`
""")
    # C：无锚
    write(root, "docs/verification/C_unanchored.md", """# C
本模块已实现，验证待补。
""")
    # D：腐烂（指针 LOST）
    write(root, "docs/verification/D_rotted.md", """# D
对文献基准误差 0.2%，PASS。
- 测试：`tests/test_does_not_exist.py::test_gone`
""")
    # E：MOVED（测试迁仓，不算腐烂）
    write(root, "docs/verification/E_moved.md", """# E
对 ITTC 参考值误差 1.0%，通过。
- 测试：`tests/test_moved.py::test_relocated`
""")
    # F：文件在、函数名不在
    write(root, "docs/verification/F_nodename.md", """# F
对解析解误差 0.3%，PASS。
- 测试：`tests/test_anchor.py::test_renamed_away`
""")
    # G：叙事文档（不进分母）
    write(root, "docs/notes/plan.md", "# 计划\n下周看图纸。\n")
    return root


# ---------------------------------------------------------------------------
# 1) token 分类
# ---------------------------------------------------------------------------
def test_classify_accepts_repo_path():
    assert ver.classify_path_token("tests/test_a.py") == "tests/test_a.py"
    assert ver.classify_path_token("artifacts/x/y.json") == "artifacts/x/y.json"
    assert ver.classify_path_token("./tools/z.py") == "tools/z.py"


def test_classify_strips_nodeid_suffix():
    assert ver.classify_path_token("tests/test_a.py::TestK::test_m") == "tests/test_a.py"


def test_classify_rejects_non_paths():
    assert ver.classify_path_token("J=0.5") is None
    assert ver.classify_path_token("K_T/K_Q") is None          # 首段不在白名单
    assert ver.classify_path_token("tests/test_a") is None      # 无扩展名
    assert ver.classify_path_token("some file.py") is None       # 含空格


# ---------------------------------------------------------------------------
# 2) 指针抽取
# ---------------------------------------------------------------------------
def test_extract_pointers_dedupes_and_keeps_node():
    text = "见 `tests/test_a.py::test_b` 与 `tests/test_a.py::test_b` 以及 `src/x.py`"
    ptrs = ver.extract_pointers(text)
    assert len(ptrs) == 2
    assert ptrs[0]["node"] == "test_b"
    assert ptrs[1]["node"] is None


def test_extract_pointers_node_strips_class_path_and_parens():
    ptrs = ver.extract_pointers("`tests/t.py::TestG::test_m(1)`")
    assert ptrs[0]["node"] == "test_m"
    assert ptrs[0]["path"] == "tests/t.py"


# ---------------------------------------------------------------------------
# 3) 容差解析
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("groups,expect", [
    (("0.5", "%"), 0.5),
    (("12", "‰"), 1.2),
    (("3", None), 3.0),
    (("4.9e-05", None), 4.9e-05),
])
def test_parse_tolerance(groups, expect):
    assert ver.parse_tolerance(groups) == pytest.approx(expect)


def test_tolerance_regex_reads_chinese_claim():
    m = ver.TOL_RE.search("最大相对误差 2.4%，满足要求")
    assert ver.parse_tolerance((m.group(1), m.group(2))) == pytest.approx(2.4)


# ---------------------------------------------------------------------------
# 4) 指针解析
# ---------------------------------------------------------------------------
def test_resolve_exact(mini_repo):
    idx = ver.build_index(mini_repo)
    assert ver.resolve_pointer(mini_repo, "tests/test_anchor.py", idx) == ("EXACT", "tests/test_anchor.py")


def test_resolve_moved_unique(mini_repo):
    idx = ver.build_index(mini_repo)
    status, resolved = ver.resolve_pointer(mini_repo, "tests/test_moved.py", idx)
    assert status == "MOVED"
    assert resolved == "backend/tests/test_moved.py"


def test_resolve_lost(mini_repo):
    idx = ver.build_index(mini_repo)
    assert ver.resolve_pointer(mini_repo, "tests/test_nope.py", idx) == ("LOST", None)


def test_resolve_ambiguous(tmp_path):
    root = str(tmp_path)
    write(root, "tests/dup.py", "")
    write(root, "backend/tests/dup.py", "")
    idx = ver.build_index(root)
    assert ver.resolve_pointer(root, "tools/dup.py", idx) == ("AMBIGUOUS", None)


# ---------------------------------------------------------------------------
# 5) nodeid 校验
# ---------------------------------------------------------------------------
def test_check_nodeid_present(mini_repo):
    assert ver.check_nodeid(mini_repo, "tests/test_anchor.py", "test_closed_form") == "OK"
    assert ver.check_nodeid(mini_repo, "tests/test_anchor.py", "TestGroup") == "OK"


def test_check_nodeid_missing_and_unresolved(mini_repo):
    assert ver.check_nodeid(mini_repo, "tests/test_anchor.py", "test_renamed") == "MISSING"
    assert ver.check_nodeid(mini_repo, None, "test_closed_form") == "UNRESOLVED"


# ---------------------------------------------------------------------------
# 6) 判级
# ---------------------------------------------------------------------------
def test_grade_real_anchored(mini_repo):
    rec = ver.analyze_doc(mini_repo, "docs/verification/A_real.md", ver.build_index(mini_repo))
    assert rec["grade"] == ver.GRADE_REAL
    assert rec["tol_min_pct"] == pytest.approx(0.5)
    assert rec["existing_test_pointers"] == ["tests/test_anchor.py"]


def test_grade_self_consistent_only(mini_repo):
    rec = ver.analyze_doc(mini_repo, "docs/verification/B_self.md", ver.build_index(mini_repo))
    assert rec["grade"] == ver.GRADE_SELF_ONLY
    assert rec["ref_keywords"] == []


def test_grade_unanchored(mini_repo):
    rec = ver.analyze_doc(mini_repo, "docs/verification/C_unanchored.md", ver.build_index(mini_repo))
    assert rec["grade"] == ver.GRADE_UNANCHORED


def test_grade_rotted_beats_other_signals(mini_repo):
    rec = ver.analyze_doc(mini_repo, "docs/verification/D_rotted.md", ver.build_index(mini_repo))
    assert rec["grade"] == ver.GRADE_ROTTED
    assert rec["n_lost"] == 1


def test_moved_pointer_is_not_rot(mini_repo):
    rec = ver.analyze_doc(mini_repo, "docs/verification/E_moved.md", ver.build_index(mini_repo))
    assert rec["n_moved"] == 1
    assert rec["n_lost"] == 0
    assert rec["grade"] == ver.GRADE_REAL
    assert rec["existing_test_pointers"] == ["backend/tests/test_moved.py"]


def test_renamed_test_function_is_rot(mini_repo):
    """文件在、函数名不在：声称的测试不可复核 → 与 LOST 同级判 ROTTED。"""
    rec = ver.analyze_doc(mini_repo, "docs/verification/F_nodename.md", ver.build_index(mini_repo))
    assert rec["n_node_missing"] == 1
    assert rec["grade"] == ver.GRADE_ROTTED


def test_node_found_in_sibling_copy_is_moved(tmp_path):
    """nodeid 名在原路径不在、但在同名拷贝里在 → MOVED_ELSEWHERE，不算腐烂。"""
    root = str(tmp_path)
    write(root, "tests/test_dup.py", "def test_other():\n    pass\n")
    write(root, "backend/tests/test_dup.py", "def test_relocated():\n    pass\n")
    write(root, "docs/verification/G_node_moved.md", """# G
对解析解误差 0.4%，PASS。
- 测试：`tests/test_dup.py::test_relocated`
""")
    rec = ver.analyze_doc(root, "docs/verification/G_node_moved.md", ver.build_index(root))
    assert rec["n_node_missing"] == 0
    assert rec["n_lost"] == 0
    assert rec["pointers"][0]["node_status"] == "OK_ELSEWHERE"
    assert rec["pointers"][0]["resolved"] == "backend/tests/test_dup.py"
    assert rec["grade"] == ver.GRADE_REAL


# ---------------------------------------------------------------------------
# 7) 汇总 / 确定性 / 门禁
# ---------------------------------------------------------------------------
def test_build_registry_counts(mini_repo):
    reg = ver.build_registry(mini_repo, "docs/**/*.md")
    assert reg["corpus"]["evidence_docs"] == 6
    assert reg["corpus"]["non_evidence_skipped"] == 1   # docs/notes/plan.md
    g = reg["grade_counts"]
    assert g[ver.GRADE_REAL] == 2      # A, E
    assert g[ver.GRADE_SELF_ONLY] == 1  # B
    assert g[ver.GRADE_UNANCHORED] == 1  # C
    assert g[ver.GRADE_ROTTED] == 2    # D(LOST), F(nodeid 名缺失)
    assert reg["pointers"]["lost"] == 1
    assert reg["pointers"]["moved"] == 1
    assert reg["pointers"]["node_missing"] == 1
    assert reg["kpi"]["denominator_evidence_docs"] == 6


def test_kpi_rate_matches_counts(mini_repo):
    reg = ver.build_registry(mini_repo, "docs/**/*.md")
    k = reg["kpi"]
    assert k["rate"] == pytest.approx(round(k["numerator_real_anchored"] / 6, 4))


def test_registry_is_deterministic(mini_repo):
    a = ver.build_registry(mini_repo, "docs/**/*.md")
    b = ver.build_registry(mini_repo, "docs/**/*.md")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert "stamp" not in a


def test_main_check_gate_fails_and_passes(mini_repo, capsys):
    code_fail = ver.main(["--root", mini_repo, "--out", "artifacts/ev_out", "--check", "--max-rot", "1"])
    assert code_fail == 1
    code_pass = ver.main(["--root", mini_repo, "--out", "artifacts/ev_out", "--check", "--max-rot", "2"])
    assert code_pass == 0


def test_main_writes_artifacts(mini_repo):
    ver.main(["--root", mini_repo, "--out", "artifacts/ev_out"])
    out = os.path.join(mini_repo, "artifacts/ev_out")
    assert os.path.exists(os.path.join(out, "registry.json"))
    md = open(os.path.join(out, "registry.md"), encoding="utf-8").read()
    assert "验证证据登记册" in md
    assert "LOST 指针" in md
    reg = json.load(open(os.path.join(out, "registry.json"), encoding="utf-8"))
    assert reg["schema_version"] == "1.0"
    assert reg["rot"]["lost"][0]["path"] == "tests/test_does_not_exist.py"


def test_render_md_has_all_grades(mini_repo):
    md = ver.render_md(ver.build_registry(mini_repo, "docs/**/*.md"))
    for g in ver.GRADE_ORDER:
        assert g in md

# ---------------------------------------------------------------------------
# 8) 指针三分类：花括号缩写 / glob / OFFBRANCH
# ---------------------------------------------------------------------------
def test_shorthand_token_is_excluded_from_denominator(mini_repo):
    write(mini_repo, "docs/verification/H_brace.md", """# H
对照解析解误差 0.6%，PASS。工件：`artifacts/figs/fig1..fig3.png` 与 `artifacts/m/{a.py,b.json}`
- 测试：`tests/test_anchor.py::test_closed_form`
""")
    rec = ver.analyze_doc(mini_repo, "docs/verification/H_brace.md", ver.build_index(mini_repo))
    assert rec["n_shorthand"] == 2
    assert rec["n_lost"] == 0
    assert rec["grade"] == ver.GRADE_REAL


def test_glob_pointer_resolves_when_matches(mini_repo):
    idx = ver.build_index(mini_repo)
    status, hit = ver.resolve_pointer(mini_repo, "artifacts/ev/*.json", idx)
    assert status == "EXACT" and hit == "artifacts/ev/anchor.json"


def test_empty_glob_is_lost(mini_repo):
    idx = ver.build_index(mini_repo)
    assert ver.resolve_pointer(mini_repo, "artifacts/nope/*.json", idx)[0] == "LOST"


def test_history_turns_lost_into_offbranch(mini_repo):
    idx = ver.build_index(mini_repo)
    assert ver.resolve_pointer(mini_repo, "artifacts/gone/x.json", idx)[0] == "LOST"
    assert ver.resolve_pointer(mini_repo, "artifacts/gone/x.json", idx,
                               {"artifacts/gone/x.json"})[0] == "OFFBRANCH"


def test_offbranch_doc_graded_offbranch_only(mini_repo):
    write(mini_repo, "docs/verification/I_offbranch.md", """# I
对 ITTC 参考值误差 1.2%，PASS。
- 测试：`tests/test_anchor.py::test_closed_form`
- 工件：`artifacts/otherbranch/ledger.json`
""")
    idx = ver.build_index(mini_repo)
    rec = ver.analyze_doc(mini_repo, "docs/verification/I_offbranch.md", idx,
                          {"artifacts/otherbranch/ledger.json"})
    assert rec["n_offbranch"] == 1
    assert rec["n_lost"] == 0
    assert rec["grade"] == ver.GRADE_OFFBRANCH


def test_offbranch_does_not_enter_kpi_numerator(mini_repo):
    write(mini_repo, "docs/verification/I_offbranch.md", """# I
对 ITTC 参考值误差 1.2%，PASS。
- 测试：`tests/test_anchor.py::test_closed_form`
- 工件：`artifacts/otherbranch/ledger.json`
""")
    reg = ver.build_registry(mini_repo, "docs/**/*.md", {"artifacts/otherbranch/ledger.json"})
    assert reg["grade_counts"][ver.GRADE_OFFBRANCH] == 1
    assert reg["kpi"]["numerator_real_anchored"] == 2


def test_load_git_history_none_outside_repo(tmp_path):
    assert ver.load_git_history(str(tmp_path), timeout=20) is None


# ---------------------------------------------------------------------------
# 9) 假阳性抑制：提案上下文 / 模块级常量 / 通配 nodeid / 散文 nodeid
# ---------------------------------------------------------------------------
def test_proposed_context_not_counted_as_rot(tmp_path):
    root = str(tmp_path)
    write(root, "docs/proposals/plan.md", """# 提案
本单拟新增 `tests/test_future.py`，并计划输出 `artifacts/future/ledger.json`。

## 已交付（本单实测）
对照解析解误差 0.5%，PASS。`tests/test_exists.py::test_ok` 已存在。
""")
    write(root, "tests/test_exists.py", "def test_ok():\n    pass\n")
    rec = ver.analyze_doc(root, "docs/proposals/plan.md", ver.build_index(root))
    assert rec["n_proposed"] == 2
    assert rec["n_lost"] == 0
    assert rec["grade"] == ver.GRADE_REAL


def test_module_level_constant_node_is_ok(mini_repo):
    write(mini_repo, "tools/registry_mod.py", "ARM_REGISTRY = {'a': 1}\n")
    assert ver.check_nodeid(mini_repo, "tools/registry_mod.py", "ARM_REGISTRY") == "OK"


def test_glob_nodeid_matches_prefix_family(mini_repo):
    assert ver.check_nodeid(mini_repo, "tests/test_anchor.py", "test_closed*") == "OK"
    assert ver.check_nodeid(mini_repo, "tests/test_anchor.py", "test_absent_*") == "MISSING"


def test_prose_nodeid_is_not_a_defect(mini_repo):
    write(mini_repo, "docs/verification/J_prose.md", """# J
对解析解误差 0.8%，PASS。
- 接口：`tests/test_anchor.py::test_closed_form + src/solver.py`
""")
    rec = ver.analyze_doc(mini_repo, "docs/verification/J_prose.md", ver.build_index(mini_repo))
    assert rec["n_node_missing"] == 0
    assert rec["grade"] == ver.GRADE_REAL
