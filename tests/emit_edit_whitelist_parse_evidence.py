"""m16622 证据生成器：编辑白名单解析器「修复前 vs 修复后」同一用例矩阵对照。

用法（仓库根目录执行）::

    python3 tests/emit_edit_whitelist_parse_evidence.py \
        --out-dir docs/evidence/m16622_edit_whitelist_parse

做法（可复现、非手抄）：
  * before = ``git show <PRE_FIX_REF>:harness_gateway/mirror/tp_worker.py`` 导出到临时目录后 import；
  * after  = 当前工作树 ``harness_gateway/mirror/tp_worker.py``；
  * 用例矩阵直接 import ``tests/test_edit_whitelist_parse.py``（单一事实源，避免两套用例漂移）；
  * 输出 before.json / after.json / matrix.json + 终端对照表（含变更计数）。
退出码：0 = 结构符合预期（修复前至少 1 条正例失败、反例全拒；修复后全通过）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MIRROR_REL = "harness_gateway/mirror/tp_worker.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_cases():
    return _load(REPO_ROOT / "tests" / "test_edit_whitelist_parse.py", "wl_cases")


def _run_matrix(mod, cases):
    rows = []
    for cid, goal, expect_prefs, expect_art in cases.POSITIVE_GOALS:
        prefs = mod._parse_edit_whitelist(goal)
        ok_art = None if prefs is None else mod._wl_ok("artifacts/x.json", prefs)
        passed = (prefs == expect_prefs) and (ok_art == expect_art)
        rows.append({
            "group": "positive", "case": cid, "goal": goal.replace("\n", "\\n"),
            "prefs": prefs, "expect_prefs": expect_prefs,
            "write_artifacts_x_json": ok_art, "expect": expect_art, "pass": passed,
        })
    for cid, goal, path, why in cases.NEGATIVE_CASES:
        prefs = mod._parse_edit_whitelist(goal)
        allowed = None if prefs is None else mod._wl_ok(path, prefs)
        passed = allowed is False
        rows.append({
            "group": "negative", "case": cid, "goal": goal.replace("\n", "\\n"), "path": path,
            "why": why, "prefs": prefs, "allowed": allowed, "expect": False, "pass": passed,
        })
    for cid, goal in cases.DORMANT_GOALS:
        prefs = mod._parse_edit_whitelist(goal)
        rows.append({
            "group": "dormant", "case": cid,
            "goal": None if goal is None else goal.replace("\n", "\\n"),
            "prefs": prefs, "expect": None, "pass": prefs is None,
        })
    return rows


def _summary(rows):
    out = {}
    for r in rows:
        out.setdefault(r["group"], {"total": 0, "pass": 0})
        out[r["group"]]["total"] += 1
        out[r["group"]]["pass"] += 1 if r["pass"] else 0
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--before-ref", default=None,
                    help="修复前 git ref，缺省取 tests 模块里的 PRE_FIX_REF")
    ap.add_argument("--out-dir", default="docs/evidence/m16622_edit_whitelist_parse")
    args = ap.parse_args(argv)

    cases = _load_cases()
    before_ref = args.before_ref or cases.PRE_FIX_REF
    out_dir = (REPO_ROOT / args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    blob = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{before_ref}:{MIRROR_REL}"],
                          capture_output=True, text=True)
    if blob.returncode != 0:
        print("FAIL: 无法从 git 取修复前 blob：", blob.stderr.strip(), file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as td:
        before_path = pathlib.Path(td) / "tp_worker_before.py"
        before_path.write_text(blob.stdout)
        before = _load(before_path, "tpw_before")
        assert hasattr(before, "_parse_edit_whitelist"), "修复前 blob 缺 _parse_edit_whitelist"
        before_rows = _run_matrix(before, cases)
    after = _load(REPO_ROOT / MIRROR_REL, "tpw_after")
    after_rows = _run_matrix(after, cases)

    payload = {
        "mission": 16622,
        "before_ref": before_ref,
        "before_blob_sha256": __import__("hashlib").sha256(blob.stdout.encode()).hexdigest(),
        "after_file": MIRROR_REL,
        "after_sha256": __import__("hashlib").sha256(
            (REPO_ROOT / MIRROR_REL).read_bytes()).hexdigest(),
        "before": {"summary": _summary(before_rows), "rows": before_rows},
        "after": {"summary": _summary(after_rows), "rows": after_rows},
    }
    (out_dir / "before.json").write_text(json.dumps(
        {"summary": payload["before"]["summary"], "rows": before_rows},
        ensure_ascii=False, indent=2) + "\n")
    (out_dir / "after.json").write_text(json.dumps(
        {"summary": payload["after"]["summary"], "rows": after_rows},
        ensure_ascii=False, indent=2) + "\n")
    (out_dir / "matrix.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    print(f"before_ref={before_ref}  before_sha256={payload['before_blob_sha256'][:12]}  "
          f"after_sha256={payload['after_sha256'][:12]}")
    print(f"{'group':9} {'case':24} {'before':6} {'after':6}  detail")
    for b, a in zip(before_rows, after_rows):
        assert b["case"] == a["case"]
        det = f"prefs before={b['prefs']} after={a['prefs']}"
        print(f"{b['group']:9} {b['case']:24} {str(b['pass']):6} {str(a['pass']):6}  {det}")
    print("summary before:", payload["before"]["summary"])
    print("summary after :", payload["after"]["summary"])
    wrote = ", ".join(str(out_dir / n) for n in ("before.json", "after.json", "matrix.json"))
    print("written:", wrote)

    flipped = [b["case"] for b, a in zip(before_rows, after_rows) if (not b["pass"]) and a["pass"]]
    before_neg_ok = all(r["pass"] for r in before_rows if r["group"] == "negative")
    after_all_ok = all(r["pass"] for r in after_rows)
    print(f"flipped(before-fail -> after-pass): {len(flipped)} {flipped}")
    print(f"before_negatives_all_rejected={before_neg_ok}  after_all_pass={after_all_ok}")
    return 0 if (flipped and before_neg_ok and after_all_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())