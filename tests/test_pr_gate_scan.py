"""IN17-B1 平台 PR 边界门单测（不需要数据库、不需要网络）。

覆盖两层：

* **机制层** —— 合成词表（zorbium / blorf- / 龘，无领域语义）+ 合成 diff，
  验证词边界、连字符复合名豁免、白名单放行、白名单路径伪造代码判红、
  只判加行、CJK 子串、JSON 审计链、CLI 退出码；
* **校准层** —— 对本仓已并入 PR 复判（负例 MUST 红 / 正例 MUST 绿），
  语料与期望值来自 ``data/boundary/pr_gate_calibration_corpus.json``。
  该用例在浅克隆（历史提交缺失）时 skip，不伪造结论。

纪律自检：本文件不得出现任何真实域词条（用例 12 由机器校验）。
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.boundary import calibrate as cal  # noqa: E402
from tools.boundary import pr_gate_scan as gate  # noqa: E402

CORPUS_PATH = REPO / cal.CORPUS_REL
GATE_CLI = REPO / "tools" / "boundary" / "pr_gate_scan.py"


# ─────────────────────────────── 夹具 ───────────────────────────────
@pytest.fixture(scope="module")
def real_lexicon() -> gate.Lexicon:
    return gate.resolve_lexicon(None, REPO)


@pytest.fixture(scope="module")
def corpus() -> dict:
    return cal.load_corpus(CORPUS_PATH)


@pytest.fixture(scope="module")
def probe_lexicon(real_lexicon, corpus) -> gate.Lexicon:
    """真实路径域策略 + 合成长词条。"""

    return cal.build_synthetic_lexicon(real_lexicon, corpus)


def _diff(path: str, *added: str, start: int = 1) -> str:
    lines = [
        f"diff --git a/{path} b/{path}",
        f"+++ b/{path}",
        f"@@ -0,0 +{start},{len(added)} @@",
    ]
    lines.extend(f"+{line}" for line in added)
    return "\n".join(lines) + "\n"


def _case(corpus: dict, case_id: str) -> dict:
    for case in corpus["cases"]:
        if case["id"] == case_id:
            return case
    raise AssertionError(f"case not in corpus: {case_id}")


# ─────────────────────────── 机制层（1-8） ───────────────────────────
def test_code_path_hit_fails_with_file_and_line(probe_lexicon):
    """1. 代码路径命中 → FAIL，且 JSON 判决带文件名与行号。"""

    result = gate.scan_diff(_diff("tools/probe/mod.py", "# probe", 'VALUE = "zorbium"'),
                            probe_lexicon)
    report = result.to_json()
    assert report["verdict"] == "FAIL"
    assert report["exit_code"] == 2
    v = report["violations"][0]
    assert (v["file"], v["line"], v["term_id"]) == ("tools/probe/mod.py", 2, "probe_word")
    assert v["matched"].lower() == "zorbium"
    assert report["files"][0]["classification"] == "blocked"


def test_forged_code_in_whitelist_path_is_red(probe_lexicon):
    """2. 白名单路径（docs/reports/）里伪造域代码 → 仍 MUST 红。"""

    case = _case(cal.load_corpus(CORPUS_PATH), "synthetic-forged-code-in-whitelist-fails")
    result = gate.scan_diff("\n".join(case["diff_lines"]) + "\n", probe_lexicon)
    report = result.to_json()
    assert report["verdict"] == "FAIL"
    assert report["violations"][0]["file"].startswith("docs/reports/")
    assert report["files"][0]["classification"] == "blocked_whitelist_code"


def test_observation_data_whitelisted_passes(probe_lexicon):
    """3. 同一词条落在白名单观察数据里 → PASS。"""

    result = gate.scan_diff(_diff("docs/reports/probe_obs.md", "probe: zorbium at 14 kn"),
                            probe_lexicon)
    report = result.to_json()
    assert report["verdict"] == "PASS"
    assert report["totals"]["files_whitelisted"] == 1
    assert report["totals"]["lines_scanned"] == 0


def test_word_boundary_avoids_false_positive(probe_lexicon):
    """4. 词边界：zorbiums / xzorbium 不算命中。"""

    report = gate.scan_diff(
        _diff("tools/probe/boundary.py", 'NAMES = ["zorbiums", "xzorbium"]'), probe_lexicon
    ).to_json()
    assert report["verdict"] == "PASS"
    # 反向对照：同文件里真的词根必须被抓到，证明上一条不是"扫不到"
    hit = gate.scan_diff(_diff("tools/probe/boundary.py", 'NAME = "zorbium"'),
                         probe_lexicon).to_json()
    assert hit["verdict"] == "FAIL"


def test_removed_lines_not_scanned(probe_lexicon):
    """5. 只判加行：删掉域词属治理成果，不判红。"""

    diff = "\n".join([
        "diff --git a/tools/probe/cleanup.py b/tools/probe/cleanup.py",
        "--- a/tools/probe/cleanup.py",
        "+++ b/tools/probe/cleanup.py",
        "@@ -1,1 +1,1 @@",
        '-# VALUE = "zorbium"',
        '+# VALUE = "ok"',
    ]) + "\n"
    report = gate.scan_diff(diff, probe_lexicon).to_json()
    assert report["verdict"] == "PASS"
    assert report["totals"]["lines_scanned"] == 1


def test_dash_prefix_exempt_compound_leaves_audit_trail(probe_lexicon):
    """6. 专有名词整复合名豁免，但命中进 exempted 留痕（可审计）。"""

    report = gate.scan_diff(_diff("tools/probe/refs.py", 'REPO = "blorf-platform"'),
                            probe_lexicon).to_json()
    assert report["verdict"] == "PASS"
    assert report["totals"]["exempted"] == 1
    assert report["exempted"][0]["compound"] == "blorf-platform"
    assert report["exempted"][0]["exempt_reason"] == "exempt_compound"


def test_dash_prefix_plain_compound_fails(probe_lexicon):
    """7. 非豁免的连字符复合名 → FAIL（豁免不是路径级放行）。"""

    report = gate.scan_diff(_diff("tools/probe/refs2.py", 'CACHE = "blorf-cache"'),
                            probe_lexicon).to_json()
    assert report["verdict"] == "FAIL"
    assert report["violations"][0]["kind"] == "dash_prefix"


def test_cjk_substring_matched(probe_lexicon):
    """8. 无词边界语言按子串判。"""

    report = gate.scan_diff(_diff("backend/probe/cjk.py", 'LABEL = "龘龘"'),
                            probe_lexicon).to_json()
    assert report["verdict"] == "FAIL"
    assert {v["term_id"] for v in report["violations"]} == {"probe_cjk"}


# ──────────────────────── 审计链 / 路径策略（9-11） ────────────────────────
def test_json_verdict_schema_and_lexicon_sha(real_lexicon):
    """9. 判决 JSON 带 schema、词表路径与 sha256（审计链），且分类规则符合 SPEC。"""

    report = gate.scan_diff(_diff("tests/probe/empty.py", "x = 1"), real_lexicon).to_json()
    assert report["schema"] == gate.SCHEMA
    assert report["lexicon"]["path"].endswith(gate.DEFAULT_LEXICON_REL)
    assert len(report["lexicon"]["sha256"]) == 64
    assert report["lexicon"]["term_count"] > 0

    assert real_lexicon.classify("backend/api/x.py") == "blocked"
    assert real_lexicon.classify("tools/x/y.py") == "blocked"
    assert real_lexicon.classify("tests/test_x.py") == "blocked"
    assert real_lexicon.classify("docs/reports/x.md") == "observation"
    assert real_lexicon.classify("docs/papers/x.json") == "observation"
    assert real_lexicon.classify("docs/reports/x.py") == "blocked_whitelist_code"
    assert real_lexicon.classify("docs/architecture/x.md") == "out_of_scope"
    assert real_lexicon.classify("probe_script.py") == "blocked"      # 仓根代码散件
    assert real_lexicon.classify("CHANGELOG.md") == "observation"     # 仓根文档
    assert real_lexicon.classify(gate.DEFAULT_LEXICON_REL) == "self_exempt"


def test_tool_and_test_sources_carry_no_real_term(real_lexicon):
    """10. 零领域语义自检：工具/测试源码里不得出现词表里的任何阻断词条。"""

    sources = [GATE_CLI, REPO / "tools/boundary/calibrate.py", pathlib.Path(__file__),
               REPO / "tools/boundary/__init__.py"]
    offenders = []
    for src in sources:
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
            for hit in gate.scan_line(line, real_lexicon):
                if hit.blocking and not hit.exempt_reason:
                    offenders.append((src.name, lineno, hit.term_id, hit.matched))
    assert offenders == [], f"domain vocabulary leaked into tool/test code: {offenders}"


def test_cli_diff_stdin_exit_codes(tmp_path, probe_lexicon):
    """11. CLI 端到端：diff 走 stdin，PASS=0 / FAIL=2，--json 落盘可回读。"""

    lex_path = tmp_path / "probe_lexicon.json"
    lex_path.write_text(cal.Path(probe_lexicon.path).read_text(encoding="utf-8"),
                        encoding="utf-8")
    out = tmp_path / "verdict.json"

    fail = subprocess.run(
        [sys.executable, str(GATE_CLI), "diff", "-", "--lexicon", str(lex_path),
         "--json", str(out)],
        input=_diff("backend/probe/x.py", 'VALUE = "zorbium"'),
        capture_output=True, text=True,
    )
    assert fail.returncode == 2, fail.stderr
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["verdict"] == "FAIL"

    ok = subprocess.run(
        [sys.executable, str(GATE_CLI), "diff", "-", "--lexicon", str(lex_path)],
        input=_diff("docs/reports/x.md", "zorbium"), capture_output=True, text=True,
    )
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["verdict"] == "PASS"


# ─────────────────────────── 校准层（12-13） ───────────────────────────
def test_calibration_corpus_all_matched(real_lexicon, corpus):
    """12. 校准语料全过：负例 PR MUST 红、正例 PR MUST 绿、合成机制用例全中。"""

    result = cal.run_corpus(corpus, real_lexicon, repo=REPO)
    if result["summary"]["unavailable"]:
        pytest.skip(f"history unavailable in this checkout: {result['summary']['unavailable']}")

    by_id = {c["id"]: c for c in result["cases"]}
    for pr in result["criteria"]["pr_must_fail"]:
        assert by_id[f"pr-{pr}"]["got"] == "FAIL", by_id[f"pr-{pr}"]
    for pr in result["criteria"]["pr_must_pass"]:
        assert by_id[f"pr-{pr}"]["got"] == "PASS", by_id[f"pr-{pr}"]
    assert result["criteria"]["pr_must_fail_ok"] is True
    assert result["criteria"]["pr_must_pass_ok"] is True
    assert result["summary"]["mismatched"] == []


def test_calibration_artifact_is_fresh_and_consistent(real_lexicon):
    """13. 落库校准工件与当前词表/代码一致（词表改了没重跑 → 红）。"""

    artifact = REPO / "artifacts" / "boundary" / "pr_gate_calibration.json"
    assert artifact.exists(), "run: python3 tools/boundary/calibrate.py --out <artifact>"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["schema"] == "tp.boundary.pr_gate_calibration_result/v1"
    assert data["lexicon"]["sha256"] == real_lexicon.sha256, "lexicon changed; re-run calibrate"
    assert data["summary"]["mismatched"] == []
    assert data["criteria"]["pr_must_fail_ok"] is True
    assert data["criteria"]["pr_must_pass_ok"] is True