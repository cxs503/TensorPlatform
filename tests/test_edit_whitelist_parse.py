"""[m16622] 编辑白名单机器门解析器回归测试（合成用例，无任何真实业务域词条）。

缺陷（开工前实测于 harness_gateway/mirror/tp_worker.py，基线 git blob = PRE_FIX_REF）：
    ``_parse_edit_whitelist`` 用 ``re.split(r"[,，、;；]", ...)`` 切前缀后仅做
    ``strip().lstrip("./")`` + ``normpath``。任务单实际写法几乎总是
    ``EDIT_WHITELIST: docs/, artifacts/。``——中文句号粘在最后一个前缀上，
    归一化后得到合法但永不匹配的前缀 ``artifacts/。`` ⇒ **最后一个白名单路径静默失效**。
    后果是「静默收紧」（无安全漏洞）：worker 白丢最后一个仓/目录的写权限，
    需花调用试错，还可能把产出落到白名单外被判违规。

本测试文件结构：
    1）POSITIVE_GOALS / NEGATIVE_CASES / DORMANT_GOALS —— 用例矩阵，
       测试与证据脚本共用同一份（单一事实源）。
    2）legacy_parse_edit_whitelist / legacy_wl_ok —— 修复前实现的逐字副本，
       用于把「修复前失败」钉成永久回归证人（D2 基线对照）。
    3）TestNewParser —— 修复后行为断言（D1/D3/D5）。
    4）TestBaselineWitness —— 断言旧实现确实在这些用例上失败（防止有人把用例改软）。
    5）TestMirrorIntegrity —— 仓内镜像与 MIRROR_MANIFEST.json 的 sha256 自洽（D4）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MIRROR_WORKER = REPO_ROOT / "harness_gateway" / "mirror" / "tp_worker.py"
MIRROR_MANIFEST = REPO_ROOT / "harness_gateway" / "mirror" / "MIRROR_MANIFEST.json"

# 修复前的 git blob（f40e2cb1a = m16622 开工时 HEAD），供证据脚本取真基线
PRE_FIX_REF = "f40e2cb1a"

# --------------------------------------------------------------------------
# 用例矩阵（合成路径：docs/ artifacts/ backend/ 均为通用目录名，不含域词条）
# --------------------------------------------------------------------------
# (用例 id, goal 文本, 期望 _parse_edit_whitelist 结果, 期望 _wl_ok("artifacts/x.json"))
POSITIVE_GOALS = [
    ("cjk_full_stop", "EDIT_WHITELIST: docs/, artifacts/。", ["docs", "artifacts"], True),
    ("ascii_period", "EDIT_WHITELIST: docs/, artifacts/.", ["docs", "artifacts"], True),
    ("fullstop_then_newline", "EDIT_WHITELIST: docs/, artifacts/。\n后续正文", ["docs", "artifacts"], True),
    ("fullwidth_comma", "EDIT_WHITELIST: docs/，artifacts/", ["docs", "artifacts"], True),
    ("no_slash_single", "EDIT_WHITELIST: docs", ["docs"], False),
    ("dot_slash_prefix", "EDIT_WHITELIST: ./docs/", ["docs"], False),
    ("semicolon_tail", "EDIT_WHITELIST: docs/; artifacts/；", ["docs", "artifacts"], True),
]

# (用例 id, goal 文本, 被拒路径, 说明)
NEGATIVE_CASES = [
    ("outside_dir", "EDIT_WHITELIST: docs/", "backend/x.py", "白名单外的普通目录"),
    ("dotdot_escape", "EDIT_WHITELIST: docs/", "docs/../backend/x.py", "normpath 后逃出白名单"),
    ("absolute_path", "EDIT_WHITELIST: docs/", "/etc/passwd", "绝对路径（越界）"),
    ("absolute_inside_name", "EDIT_WHITELIST: docs/", "/docs/x.md", "绝对路径即便同名也拒"),
    ("parent_escape", "EDIT_WHITELIST: docs/", "../x.py", "上一级目录"),
    ("prefix_sibling", "EDIT_WHITELIST: docs/", "docs_evil/x.py", "同前缀兄弟目录不得误放"),
]

# 休眠组（零行为变化）：无声明 / 只有标点 → None
DORMANT_GOALS = [
    ("no_declaration", "普通任务目标，没有白名单声明。"),
    ("empty_goal", ""),
    ("none_goal", None),
    ("only_punct", "EDIT_WHITELIST: 。"),
    ("only_puncts", "EDIT_WHITELIST: ，、；。"),
    ("only_whitespace", "EDIT_WHITELIST:     "),
]


# --------------------------------------------------------------------------
# 实现载入
# --------------------------------------------------------------------------
def _load_impl(path):
    spec = importlib.util.spec_from_file_location(f"tpw_under_test_{abs(hash(str(path))) % 10**8}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tpw():
    return _load_impl(MIRROR_WORKER)


# --------------------------------------------------------------------------
# 修复前实现的逐字副本（基线证人；勿改逻辑，否则基线对照失效）
# --------------------------------------------------------------------------
def legacy_parse_edit_whitelist(goal):
    m = re.search(r"EDIT_WHITELIST[：:]\s*([^\n]+)", goal or "")
    if not m:
        return None
    prefs = []
    for p in re.split(r"[,，、;；]", m.group(1)):
        p = p.strip().lstrip("./").strip()
        if p:
            prefs.append(os.path.normpath(p))
    return prefs or None


def legacy_wl_ok(path, prefs):
    p = os.path.normpath(str(path).strip().lstrip("./"))
    return any(p == q or p.startswith(q.rstrip("/") + "/") for q in prefs)


# --------------------------------------------------------------------------
# 1) 修复后行为（D1 / D3 / D5）
# --------------------------------------------------------------------------
class TestNewParser:
    @pytest.mark.parametrize("cid,goal,expect_prefs,expect_artifacts", POSITIVE_GOALS)
    def test_positive_parse(self, tpw, cid, goal, expect_prefs, expect_artifacts):
        got = tpw._parse_edit_whitelist(goal)
        assert got == expect_prefs, f"[{cid}] {goal!r} -> {got!r}，期望 {expect_prefs!r}"
        assert not any("。" in p or "，" in p or p.endswith(".") for p in got), \
            f"[{cid}] 前缀仍残留标点: {got!r}"

    @pytest.mark.parametrize("cid,goal,path,why", NEGATIVE_CASES)
    def test_negative_rejected(self, tpw, cid, goal, path, why):
        prefs = tpw._parse_edit_whitelist(goal)
        assert prefs is not None
        assert tpw._wl_ok(path, prefs) is False, f"[{cid}] {path!r} 应被拒（{why}）"

    def test_positive_artifacts_writable(self, tpw):
        """缺陷现场最小复现：修复后 artifacts/x.json 必须放行。"""
        prefs = tpw._parse_edit_whitelist("EDIT_WHITELIST: docs/, artifacts/。")
        assert prefs == ["docs", "artifacts"]
        assert tpw._wl_ok("artifacts/x.json", prefs) is True
        assert tpw._wl_ok("docs/master_plan.md", prefs) is True

    @pytest.mark.parametrize("cid,goal", DORMANT_GOALS)
    def test_dormant_zero_behaviour_change(self, tpw, cid, goal):
        """D5：未声明 / 只有标点 → None（休眠，零行为变化）。"""
        assert tpw._parse_edit_whitelist(goal) is None

    def test_no_loosening_via_wildcards(self, tpw):
        """不得放宽匹配：通配/越界 token 不得凭空放行。"""
        prefs = tpw._parse_edit_whitelist("EDIT_WHITELIST: docs/*。")
        assert prefs is not None
        assert not tpw._wl_ok("backend/x.py", prefs)
        prefs2 = tpw._parse_edit_whitelist("EDIT_WHITELIST: artifacts/../..。")
        assert not tpw._wl_ok("backend/x.py", prefs2 or [])
        assert not tpw._wl_ok("/etc/passwd", prefs2 or [])


# --------------------------------------------------------------------------
# 2) 基线证人（D2）：旧实现必须在上述用例上失败
# --------------------------------------------------------------------------
class TestBaselineWitness:
    def test_legacy_corrupts_last_prefix(self):
        got = legacy_parse_edit_whitelist("EDIT_WHITELIST: docs/, artifacts/。")
        assert got == ["docs", "artifacts/。"], f"旧实现行为变了？实得 {got!r}"

    def test_legacy_rejects_artifacts(self):
        prefs = legacy_parse_edit_whitelist("EDIT_WHITELIST: docs/, artifacts/。")
        assert legacy_wl_ok("artifacts/x.json", prefs) is False

    def test_legacy_passes_docs(self):
        """缺陷是「静默收紧」而非全拒：docs/ 仍放行 ⇒ 难被发现。"""
        prefs = legacy_parse_edit_whitelist("EDIT_WHITELIST: docs/, artifacts/。")
        assert legacy_wl_ok("docs/x.md", prefs) is True

    def test_legacy_still_rejects_escapes(self):
        """反例在旧实现上也全部拒绝 —— 修复不得引入回退。"""
        prefs = legacy_parse_edit_whitelist("EDIT_WHITELIST: docs/")
        for path in ("backend/x.py", "docs/../backend/x.py", "/etc/passwd"):
            assert legacy_wl_ok(path, prefs) is False


# --------------------------------------------------------------------------
# 3) 仓内镜像与清单自洽（D4）
# --------------------------------------------------------------------------
def _sha256_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class TestMirrorIntegrity:
    def test_manifest_mirror_sha_matches_disk(self):
        rec = json.loads(MIRROR_MANIFEST.read_text())
        unit = next(u for u in rec["units"] if u["name"] == "tp_worker")
        on_disk = _sha256_file(MIRROR_WORKER)
        assert unit["mirror_sha256"] == on_disk, (
            "MIRROR_MANIFEST.json 的 tp_worker.mirror_sha256 与磁盘不一致："
            f"manifest={unit['mirror_sha256'][:12]} disk={on_disk[:12]}")

    def test_manifest_mirror_bytes_matches_disk(self):
        rec = json.loads(MIRROR_MANIFEST.read_text())
        unit = next(u for u in rec["units"] if u["name"] == "tp_worker")
        assert unit["mirror_bytes"] == MIRROR_WORKER.stat().st_size

    def test_fix_present_in_mirror_only_once(self):
        """仓内只应有一份 canonical 实现（镜像）；若将来加第二份，本测试提醒同步。"""
        text = MIRROR_WORKER.read_text()
        assert text.count("def _parse_edit_whitelist(") == 1
        assert "_WL_TAIL_PUNCT" in text