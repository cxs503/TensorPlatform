"""测试套件自身的健康闸门（元测试 / meta-lint）。

背景（本单实测到的三类「失效测试」——测试在库里但从未真正执行）：
  1. `tests/test_maneuvering_reference.py` 与 `tests/verif/test_damage_stability_verdict.py`
     消费 `artifacts/verif_maneuvering/`、`artifacts/verif/damage_stability/` 下的
     参考实现与判决工件，但工件从未入库 → 两个模块整包收集时 ImportError，
     45 条验证判据永久不执行（"绿"是因为压根没跑）。
  2. `tests/test_public_wall.py` 复用 `backend.database.config.engine`（进程内共享
     `:memory:`），与其它模块建出的同名异构表冲突 → collection ERROR；且夹具使用
     硬编码绝对时间戳断言 24h 窗口，日期一过恒假（时效炸弹）。
  3. `tests/test_maneuvering_reference.py` 与 `tests/verifier/test_maneuvering_reference.py`
     同名不同内容，prepend 导入模式下 "import file mismatch" → collection ERROR。

本文件把上述三类缺陷变成可机器裁决的回归门，防止再次退化为静默死测试。

运行：python3 -m pytest tests/test_suite_health.py -q
"""
from __future__ import annotations

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 只扫描已入库的测试模块（跳过 __pycache__ 与 conftest）
def _test_modules():
    out = []
    for dirpath, dirnames, filenames in os.walk(HERE):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            if fn.startswith("test_") and fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


MODULES = _test_modules()

_JOIN_RE = re.compile(r"os\.path\.join\((.*?)\)")
_LIT_RE = re.compile(r"[\"']([A-Za-z0-9_./-]+)[\"']")
_HARD_DATE_RE = re.compile(r"20\d\d-\d\d-\d\dT")
_WINDOW_RE = re.compile(r"(?:succeeded_24h|failed_24h|24h|hours=24|timedelta\(hours=24)")


def _artifact_dirs(path):
    """从模块源码提取引用的 artifacts 目录（去掉文件名段，只看目录）。"""
    src = open(path, encoding="utf-8").read()
    dirs = set()
    for call in _JOIN_RE.findall(src):
        segs = [x for x in _LIT_RE.findall(call)]
        flat = []
        for seg in segs:
            flat.extend([x for x in seg.split("/") if x])
        if "artifacts" not in flat:
            continue
        idx = flat.index("artifacts")
        tail = []
        for seg in flat[idx + 1:]:
            if "." in seg:          # 文件名段 → 目录前缀到此为止
                break
            tail.append(seg)
        if tail:
            dirs.add(os.path.join("artifacts", *tail))
    return dirs


# 历史欠账：以下 artifacts 目录由若干「证据驱动」测试模块引用，但从未入库。
# 这些模块在本仓当前状态是「静默死测试」（import/夹具缺失 → ERROR 或 skip），
# 列入本表只是**如实登记**，不是豁免：新增项必须修（本闸门会失败），
# 存量项由后续单逐条清偿（见 docs/audit/m1795_dead_test_recovery.md）。
KNOWN_MISSING_ARTIFACT_DIRS = {
    "artifacts/geometry_audit",
    "artifacts/gz_audit",
    "artifacts/m1515/seakeeping_audit",
    "artifacts/m1584",
    "artifacts/ref",
    "artifacts/ref/anchors",
    "artifacts/verifier/geometry/api_captures",
}


def test_artifact_dependencies_are_committed():
    """L1：测试引用的 artifacts 子目录必须真实存在于工作树。

    证据（参考实现 / 判决工件）不入库 = 验证测试永远 ERROR = 验证欠账被静默吞掉。
    """
    missing = []
    for path in MODULES:
        for rel in sorted(_artifact_dirs(path)):
            if rel in KNOWN_MISSING_ARTIFACT_DIRS:
                continue
            if not os.path.isdir(os.path.join(ROOT, rel)):
                missing.append((os.path.relpath(path, ROOT), rel))
    assert not missing, f"测试引用的 artifacts 目录不存在（证据未入库）：{missing}"


def test_known_debt_list_only_shrinks():
    """存量欠账只能减少不能新增：列入表内的目录一旦入库即必须移出。"""
    stale = [d for d in KNOWN_MISSING_ARTIFACT_DIRS
             if os.path.isdir(os.path.join(ROOT, d))]
    assert not stale, f"这些 artifacts 目录已入库，请从 KNOWN_MISSING_ARTIFACT_DIRS 移除：{stale}"


def test_no_duplicate_test_basenames():
    """L2：不同目录下不得有同名 test_*.py。

    pytest 默认 importmode=prepend 下同名模块触发 "import file mismatch"，
    整包收集直接 ERROR，套件整体不可运行。
    """
    seen: dict[str, list[str]] = {}
    for path in MODULES:
        seen.setdefault(os.path.basename(path), []).append(os.path.relpath(path, ROOT))
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dupes, f"重名测试模块会导致收集失败：{dupes}"


def test_no_hardcoded_date_in_24h_window_tests():
    """L3：断言 24h 窗口的模块不得使用硬编码绝对时间戳。

    否则夹具日期滑出窗口后断言恒假（时效炸弹），与代码正确性无关。
    """
    offenders = []
    for path in MODULES:
        src = open(path, encoding="utf-8").read()
        # 只约束自建 sqlite 夹具的模块：它们的种子数据就是被断言的窗口数据
        if "DATABASE_URL" not in src:
            continue
        if _WINDOW_RE.search(src) and _HARD_DATE_RE.search(src):
            offenders.append(os.path.relpath(path, ROOT))
    assert not offenders, f"24h 窗口断言里出现硬编码日期（时效炸弹）：{offenders}"


def test_shared_backend_engine_not_polluted_by_ad_hoc_ddl():
    """L4：使用 backend 共享 engine 的测试必须幂等建表（IF NOT EXISTS）。

    共享 `:memory:` engine 在同一 pytest 进程内跨模块可见，裸 CREATE TABLE
    会在整包收集时报 "table ... already exists"，把后来者打成 ERROR。
    """
    offenders = []
    for path in MODULES:
        if os.path.basename(path) == os.path.basename(__file__):
            continue  # 本文件自身含有 lint 模式字面量
        src = open(path, encoding="utf-8").read()
        uses_shared = "from database.config import engine" in src
        if not uses_shared:
            continue
        for stmt in re.findall(r"CREATE TABLE(?! IF NOT EXISTS)", src):
            offenders.append(os.path.relpath(path, ROOT))
            break
    assert not offenders, f"共享 engine 上的裸 CREATE TABLE（跨模块冲突）：{offenders}"


def test_verification_modules_are_runnable():
    """L5：关键验证模块必须可导入（收集阶段不 ERROR）。

    只做导入冒烟，不重复执行其判据（判据本身由各自模块的测试承担）。
    """
    import importlib.util

    targets = [
        os.path.join(HERE, "test_maneuvering_reference.py"),
        os.path.join(HERE, "verif", "test_damage_stability_verdict.py"),
        os.path.join(HERE, "verifier", "test_maneuvering_audit_contract.py"),
    ]
    for t in targets:
        assert os.path.exists(t), f"验证模块缺失：{t}"
        spec = importlib.util.spec_from_file_location(
            "smoke_" + os.path.basename(t)[:-3] + "_" + os.path.basename(os.path.dirname(t)),
            t,
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:  # pragma: no cover - 失败即代表模块失效
            pytest.fail(f"{os.path.relpath(t, ROOT)} 导入失败：{exc!r}")