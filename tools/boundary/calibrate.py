#!/usr/bin/env python3
"""边界门校准跑批：对本仓历史 PR + 合成机制用例复判，判决落 artifacts/。

判据（IN17-B1）
---------------
* 负例：已并入 PR 里"域语义回流平台代码"的那几个 → **MUST FAIL**；
* 正例：其余已并入 PR → **MUST PASS**；
* 机制：白名单路径伪造域代码 MUST FAIL、白名单观察数据 MUST PASS 等。

语料在 ``data/boundary/pr_gate_calibration_corpus.json``（词条/期望值都是数据，
不是代码）。合成用例只覆盖词条集合，路径域策略继承真实词表——这样测到的
就是上线用的真策略。git 类用例在提交缺失（CI 浅克隆）时记为 ``unavailable``
并从「全过」判据里剔除，而不是伪造结果。

用法
----
    python3 tools/boundary/calibrate.py --out artifacts/boundary/pr_gate_calibration.json
    python3 tools/boundary/calibrate.py --only pr-608 --format text

退出码：0=全部期望命中，1=有期望未命中，3=内部错误。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

if __package__ in (None, ""):  # 允许 python3 tools/boundary/calibrate.py 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.boundary import pr_gate_scan as gate  # type: ignore
else:  # pragma: no cover
    from . import pr_gate_scan as gate  # type: ignore

SCHEMA = "tp.boundary.pr_gate_calibration/v1"
CORPUS_REL = "data/boundary/pr_gate_calibration_corpus.json"
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_corpus(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"corpus schema mismatch: {data.get('schema')!r} != {SCHEMA!r}")
    return data


def build_synthetic_lexicon(base: gate.Lexicon, corpus: Dict[str, Any]) -> gate.Lexicon:
    """真实路径域策略 + 合成长词条 → 临时词表文件。"""

    base_raw = json.loads(Path(base.path).read_text(encoding="utf-8"))
    merged = dict(base_raw)
    merged["version"] = f"{base_raw.get('version', '?')}+synthetic-probe"
    for key, value in (corpus.get("synthetic_lexicon_overrides") or {}).items():
        merged[key] = value
    tmpdir = Path(tempfile.mkdtemp(prefix="tp_boundary_probe_"))
    path = tmpdir / "synthetic_lexicon.json"
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return gate.Lexicon.load(path)


def _git(*args: str, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def run_case(
    case: Dict[str, Any],
    real_lexicon: gate.Lexicon,
    synthetic_lexicon: gate.Lexicon,
    repo: Path,
) -> Dict[str, Any]:
    cid = case["id"]
    mode = case.get("mode", "synthetic")
    expect = case["expect"]
    record: Dict[str, Any] = {"id": cid, "mode": mode, "expect": expect, "note": case.get("note", "")}

    if mode == "git":
        merge = case["merge"]
        parent = _git("rev-parse", f"{merge}^1", repo=repo)
        head = _git("rev-parse", merge, repo=repo)
        if parent.returncode != 0 or head.returncode != 0:
            record.update(status="unavailable", reason="commit not present in this checkout")
            return record
        base_sha = parent.stdout.strip()
        head_sha = head.stdout.strip()
        diff_text = gate.git_diff(base_sha, head_sha, repo)
        result = gate.scan_diff(
            diff_text, real_lexicon, mode="git",
            target={"base": base_sha, "head": head_sha, "merge_commit": merge},
        )
        record["lexicon"] = "real"
    else:
        diff_text = "\n".join(case.get("diff_lines") or []) + "\n"
        result = gate.scan_diff(diff_text, synthetic_lexicon, mode="synthetic",
                                target={"case": cid})
        record["lexicon"] = "synthetic"

    report = result.to_json()
    ok = report["verdict"] == expect
    min_exempted = int(case.get("expect_exempted_min", 0))
    if min_exempted and report["totals"]["exempted"] < min_exempted:
        ok = False
        record["exempted_requirement"] = f">= {min_exempted}, got {report['totals']['exempted']}"

    record.update(
        status="matched" if ok else "mismatched",
        got=report["verdict"],
        exit_code=report["exit_code"],
        violations=report["totals"]["violations"],
        advisories=report["totals"]["advisories"],
        exempted=report["totals"]["exempted"],
        files_touched=report["totals"]["files_touched"],
        files_whitelisted=report["totals"]["files_whitelisted"],
        violation_samples=[
            {k: v[k] for k in ("file", "line", "term_id", "matched")}
            for v in report["violations"][:5]
        ],
    )
    return record


def run_corpus(
    corpus: Dict[str, Any],
    lexicon: gate.Lexicon,
    *,
    repo: Path = REPO_ROOT,
    only: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    synthetic = build_synthetic_lexicon(lexicon, corpus)
    cases = [c for c in corpus.get("cases", []) if not only or c["id"] in set(only)]
    records = [run_case(c, lexicon, synthetic, repo) for c in cases]
    scored = [r for r in records if r["status"] in ("matched", "mismatched")]
    mismatched = [r["id"] for r in scored if r["status"] == "mismatched"]
    unavailable = [r["id"] for r in records if r["status"] == "unavailable"]

    pr = {r["id"]: r for r in records}
    pr_must_fail = (corpus.get("criteria") or {}).get("pr_must_fail", [])
    pr_must_pass = (corpus.get("criteria") or {}).get("pr_must_pass", [])
    criteria = {
        "pr_must_fail_ok": all(
            pr.get(f"pr-{n}", {}).get("got") == "FAIL" for n in pr_must_fail
        ),
        "pr_must_pass_ok": all(
            pr.get(f"pr-{n}", {}).get("got") == "PASS" for n in pr_must_pass
        ),
        "pr_must_fail": pr_must_fail,
        "pr_must_pass": pr_must_pass,
    }
    return {
        "schema": "tp.boundary.pr_gate_calibration_result/v1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "lexicon": lexicon.describe(),
        "synthetic_lexicon_version": synthetic.version,
        "criteria": criteria,
        "summary": {
            "cases": len(records),
            "scored": len(scored),
            "matched": len(scored) - len(mismatched),
            "mismatched": mismatched,
            "unavailable": unavailable,
            "all_matched": not mismatched and not unavailable,
        },
        "cases": records,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="边界门校准跑批（历史 PR 复判 + 合成机制用例）")
    p.add_argument("--corpus", default=str(REPO_ROOT / CORPUS_REL))
    p.add_argument("--lexicon", default=None)
    p.add_argument("--repo", default=str(REPO_ROOT))
    p.add_argument("--out", default=None, help="校准结果 JSON 落盘路径")
    p.add_argument("--only", nargs="*", default=None)
    p.add_argument("--format", choices=("json", "text"), default="json")
    args = p.parse_args(argv)

    try:
        lexicon = gate.resolve_lexicon(args.lexicon, Path(args.repo))
        corpus = load_corpus(Path(args.corpus))
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    result = run_corpus(corpus, lexicon, repo=Path(args.repo), only=args.only)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    if args.format == "json":
        print(payload)
    else:
        for r in result["cases"]:
            print(f"{r['status']:<11} {r['id']:<48} expect={r['expect']:<4} "
                  f"got={r.get('got', '-'):<4} viol={r.get('violations', '-')}")
        print(json.dumps(result["summary"], ensure_ascii=False))
    ok = result["summary"]["all_matched"]
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())