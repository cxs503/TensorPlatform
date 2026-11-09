#!/usr/bin/env python3
"""验收跑批：把机器可算判据 (1)~(4) + 三项支撑判据一次跑出来。

判据 → 产物映射（全部写进 ``artifacts/write_gate/acceptance.json``）
-------------------------------------------------------------------
1. 白名单越界 100% 拦截   → ``criteria.whitelist``（预注册 10 种越界写法）
2. 跨仓污染 100% 生效     → ``criteria.cross_repo``（5 个必检场景 + 2 扩展）
3. 凭证泄漏 100% 捕获     → ``criteria.secrets``（3 个必检泄漏面 + 3 扩展，且脱敏）
4. 误报率 ≤1%             → ``criteria.false_positive``（122 路径 + 39 内容样本）
5. 确定/幂等              → ``criteria.determinism``（三次重跑逐字节比对）
6. fail-closed            → ``criteria.fail_closed``（仓根不可读 / 策略非法）
7. 零领域语义             → ``criteria.semantics``（预注册词表 grep 包源码与策略数据）
8. pytest 用例数          → ``criteria.pytest``（子进程跑本模块测试取摘要行）
9. 性能                   → ``criteria.performance``（1000 路径判决耗时）

用法::

    python3 tools/write_gate/acceptance.py --out artifacts/write_gate --work /tmp/write_gate_accept
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.write_gate import gate, policy as policy_mod, secrets as secrets_mod  # noqa: E402
from tools.write_gate.model import ACCEPTANCE_SCHEMA, Change, WriteGateError  # noqa: E402
from tools.write_gate.samples import REQUIRED_SECRET_RULES, all_samples  # noqa: E402

POLICY_PATH = REPO_ROOT / "data" / "write_gate" / "policy.json"
SCENARIO_PATH = REPO_ROOT / "data" / "write_gate" / "scenarios.json"
CORPUS_PATH = REPO_ROOT / "data" / "write_gate" / "fp_corpus.json"

GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", HOME="/tmp")
GIT_ID = ["-c", "user.name=gate", "-c", "user.email=gate@example.invalid"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], capture_output=True,
                          text=True, env=GIT_ENV, check=False)
    if proc.returncode != 0:
        raise WriteGateError(f"git_failed:{proc.stderr[:200]}")
    return proc.stdout


def build_worktree(tmp: Path) -> Path:
    """验收夹具：带 ``.git`` 的工作树 + 嵌套仓 + 外部目录 + 逃逸符号链接。"""
    root = tmp / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
    (root / "vendor" / "subrepo" / ".git").mkdir(parents=True, exist_ok=True)
    (root / "vendor" / "subrepo" / "src").mkdir(parents=True, exist_ok=True)
    outside = tmp / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("outside\n", encoding="utf-8")
    link = root / "link"
    if not link.exists():
        os.symlink(outside, link)
    return root


def crit_whitelist(cfg, scenarios, root: Path) -> Dict[str, Any]:
    rows = []
    for item in scenarios["whitelist_escapes"]:
        verdict = gate.evaluate_changes([Change(item["path"])], cfg, root=root, active_repo="TensorPlatform")
        rows.append({"id": item["id"], "path": item["path"], "blocked": verdict.verdict == "FAIL",
                     "codes": sorted({v.code for v in verdict.violations}),
                     "categories": sorted({v.category for v in verdict.violations})})
    hit = sum(1 for r in rows if r["blocked"])
    total = len(rows)
    return {"required": total, "detected": hit, "rate": round(hit / total, 4),
            "pass": hit == total, "rows": rows}


def crit_cross_repo(cfg, scenarios, root: Path, tmp: Path) -> Dict[str, Any]:
    rows = []
    for item in scenarios["cross_repo"]:
        if item["kind"] == "absolute":
            path = str(tmp / "other_repo" / "backend" / "x.py")
        else:
            path = item["path"]
        verdict = gate.evaluate_changes([Change(path, repo=item.get("repo"))], cfg,
                                        root=root, active_repo="TensorPlatform")
        codes = sorted({v.code for v in verdict.violations})
        rows.append({"id": item["id"], "expect": item["expect_code"], "path": path,
                     "codes": codes, "hit": item["expect_code"] in codes,
                     "required": item["id"] in scenarios["required_cross_repo"]})
    required_rows = [r for r in rows if r["required"]]
    hit = sum(1 for r in required_rows if r["hit"])
    total = len(required_rows)
    extra_hit = sum(1 for r in rows if r["hit"] and not r["required"])
    return {"required": total, "detected": hit, "rate": round(hit / total, 4), "pass": hit == total,
            "extra_scenarios": len(rows) - total, "extra_detected": extra_hit, "rows": rows}


def crit_secrets(cfg, root: Path) -> Dict[str, Any]:
    rows = []
    for rule_id, text in all_samples():
        verdict = gate.evaluate_changes([Change("tools/write_gate/sample.py", content=text)],
                                        cfg, root=root)
        hit_rules = sorted({v.rule_id for v in verdict.violations if v.category == "secret"})
        blob = json.dumps(verdict.to_json(), ensure_ascii=False)
        core = text.split("=")[-1].strip().strip('"') if "=" in text else text
        redacted = core[:12] not in blob
        rows.append({"rule_id": rule_id, "detected": rule_id in hit_rules, "hit_rules": hit_rules,
                     "redacted": redacted, "required": rule_id in REQUIRED_SECRET_RULES})
    required_rows = [r for r in rows if r["required"]]
    hit = sum(1 for r in required_rows if r["detected"])
    total = len(required_rows)
    redaction_ok = all(r["redacted"] for r in rows if r["detected"])
    return {"required": total, "detected": hit, "rate": round(hit / total, 4),
            "pass": hit == total and redaction_ok,
            "extra_patterns": len(rows) - total,
            "extra_detected": sum(1 for r in rows if r["detected"] and not r["required"]),
            "redaction_ok": redaction_ok, "rows": rows}


def crit_false_positive(cfg, corpus, root: Path) -> Dict[str, Any]:
    samples = blocked = 0
    blocked_items: List[Dict[str, Any]] = []
    for path in corpus["normal_paths"]:
        samples += 1
        verdict = gate.evaluate_changes([Change(path)], cfg, root=root)
        if verdict.verdict != "PASS":
            blocked += 1
            blocked_items.append({"kind": "path", "sample": path,
                                  "codes": sorted({v.code for v in verdict.violations})})
    for line in corpus["normal_lines"]:
        samples += 1
        verdict = gate.evaluate_changes([Change("docs/write_gate/fp_sample.md", content=line)],
                                        cfg, root=root)
        if verdict.verdict != "PASS":
            blocked += 1
            blocked_items.append({"kind": "content", "sample": line[:60],
                                  "codes": sorted({v.code for v in verdict.violations})})
    rate = blocked / samples if samples else 1.0
    return {"samples": samples, "false_positives": blocked, "rate": round(rate, 4),
            "budget": 0.01, "pass": rate <= 0.01, "blocked_items": blocked_items,
            "corpus_sha256": _sha256(CORPUS_PATH)}


def crit_determinism(cfg, root: Path) -> Dict[str, Any]:
    changes = [Change("tools/write_gate/gate.py"),
               Change("tools/write_gate/sample.py", content="API_KEY = \"" + "Qz7" * 6 + "\""),
               Change(".git/config")]
    blobs = [json.dumps(gate.evaluate_changes(changes, cfg, root=root).to_json(),
                        ensure_ascii=False, sort_keys=True) for _ in range(3)]
    return {"runs": 3, "identical": blobs[0] == blobs[1] == blobs[2],
            "digest": json.loads(blobs[0])["digest"],
            "pass": blobs[0] == blobs[1] == blobs[2]}


def crit_fail_closed(cfg, tmp: Path) -> Dict[str, Any]:
    missing = gate.evaluate_changes([Change("tools/a.py")], cfg, root=tmp / "does_not_exist")
    probes = [{"probe": "missing_root", "verdict": missing.verdict,
               "decision": missing.decision,
               "codes": sorted({v.code for v in missing.violations}),
               "closed": missing.verdict == "FAIL" and missing.decision == "unknown"}]
    bad_policy = []
    for overrides in ({"whitelist": {"allow_globs": []}}, {"nope": {}},
                      {"whitelist": {"treat_deny_as": "bogus"}}):
        try:
            policy_mod.load_policy(POLICY_PATH, overrides=overrides)
            bad_policy.append(False)
        except WriteGateError:
            bad_policy.append(True)
    probes.append({"probe": "bad_policy_rejected", "detail": bad_policy, "closed": all(bad_policy)})
    try:
        policy_mod.load_policy(tmp / "missing_policy.json")
        probes.append({"probe": "missing_policy", "closed": False})
    except WriteGateError:
        probes.append({"probe": "missing_policy", "closed": True})
    return {"probes": probes, "pass": all(p["closed"] for p in probes)}


def crit_semantics() -> Dict[str, Any]:
    denylist = json.loads((REPO_ROOT / "tools" / "tenancy_gate" / "domain_denylist.json")
                          .read_text(encoding="utf-8"))
    targets = sorted(HERE.glob("*.py")) + sorted((REPO_ROOT / "data" / "write_gate").glob("*.json"))
    hits: List[str] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for term in denylist["terms"]:
            pattern = (r"(?<![0-9A-Za-z])" + re.escape(term) + r"(?![0-9A-Za-z])"
                       if term.isascii() else re.escape(term))
            if re.search(pattern, text, flags=re.IGNORECASE if term.isascii() else 0):
                hits.append(f"{path.name}:{term}")
    return {"targets": [str(p.relative_to(REPO_ROOT)) for p in targets], "hits": hits,
            "pass": hits == []}


def crit_pytest(timeout: int = 900) -> Dict[str, Any]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/test_write_gate.py", "-q"],
                          cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)))
    tail = (proc.stdout or "") + (proc.stderr or "")
    line = ""
    for candidate in reversed(tail.strip().splitlines()):
        if re.search(r"\d+ (passed|failed)", candidate):
            line = candidate.strip()
            break
    match = re.search(r"(\d+) passed", line)
    failed = re.search(r"(\d+) failed", line)
    return {"returncode": proc.returncode, "summary": line,
            "passed": int(match.group(1)) if match else 0,
            "failed": int(failed.group(1)) if failed else 0,
            "pass": proc.returncode == 0 and match is not None}


def crit_performance(cfg, root: Path, n: int = 1000) -> Dict[str, Any]:
    paths = [f"tools/write_gate/mod_{i % 50}/file_{i}.py" for i in range(n)]
    start = time.perf_counter()
    verdict = gate.evaluate_changes([Change(p) for p in paths], cfg, root=root)
    elapsed = time.perf_counter() - start
    return {"changes": n, "seconds": round(elapsed, 4),
            "microseconds_per_change": round(1e6 * elapsed / n, 1),
            "blocked": verdict.blocked, "pass": verdict.verdict == "PASS" and elapsed < 30.0}


def run(out_dir: Path, work_dir: Path) -> Dict[str, Any]:
    scenarios = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    cfg_default = policy_mod.load_policy(POLICY_PATH)
    cfg_wl = policy_mod.load_policy(POLICY_PATH,
                                    overrides={"whitelist": {"allow_globs": scenarios["edit_whitelist"]}})

    work_dir.mkdir(parents=True, exist_ok=True)
    fixture_parent = Path(tempfile.mkdtemp(prefix="write_gate_accept_", dir=str(work_dir)))
    root = build_worktree(fixture_parent)

    allow_verdict = gate.evaluate_changes([Change(p) for p in scenarios["allow_samples"]],
                                         cfg_wl, root=root, active_repo="TensorPlatform")

    criteria = {
        "whitelist": crit_whitelist(cfg_wl, scenarios, root),
        "cross_repo": crit_cross_repo(cfg_default, scenarios, root, fixture_parent),
        "secrets": crit_secrets(cfg_default, REPO_ROOT),
        "false_positive": crit_false_positive(cfg_default, corpus, REPO_ROOT),
        "determinism": crit_determinism(cfg_wl, root),
        "fail_closed": crit_fail_closed(cfg_default, fixture_parent),
        "semantics": crit_semantics(),
        "pytest": crit_pytest(),
        "performance": crit_performance(cfg_default, REPO_ROOT),
        "allow_samples": {"samples": len(scenarios["allow_samples"]),
                          "passed": sum(1 for f in allow_verdict.files if f["status"] == "allow"),
                          "pass": allow_verdict.verdict == "PASS"},
    }
    verdict = "PASS" if all(c.get("pass") for c in criteria.values()) else "FAIL"

    payload = {
        "schema": ACCEPTANCE_SCHEMA,
        "verdict": verdict,
        "criteria": criteria,
        "policy": {"path": str(POLICY_PATH.relative_to(REPO_ROOT)),
                   "sha256": _sha256(POLICY_PATH),
                   "config_digest": gate.evaluate_changes([], cfg_default, root=REPO_ROOT)
                   .to_json()["policy"]["sha256"]},
        "scenarios": {"path": str(SCENARIO_PATH.relative_to(REPO_ROOT)),
                      "sha256": _sha256(SCENARIO_PATH)},
        "code": {p.name: _sha256(p) for p in sorted(HERE.glob("*.py"))},
        "environment": {"python": sys.version.split()[0], "platform": sys.platform},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "acceptance.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)
                                             + "\n", encoding="utf-8")
    sample = gate.evaluate_changes(
        [Change("tools/write_gate/gate.py"),
         Change("backend/api/leak.py"),
         Change(".git/config"),
         Change("tools/write_gate/sample.py", content="API_KEY = \"" + "Qz7" * 6 + "\"")],
        cfg_wl, root=REPO_ROOT, active_repo="TensorPlatform", policy_path=str(POLICY_PATH))
    (out_dir / "verdict_sample.json").write_text(
        json.dumps(sample.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="三合一写入边界门验收跑批")
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "write_gate"))
    ap.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "write_gate_accept"))
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)
    work = Path(args.work)
    if work.exists() and not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    payload = run(Path(args.out), work)
    print(json.dumps({"verdict": payload["verdict"],
                      **{k: {kk: vv for kk, vv in v.items() if kk in ("required", "detected",
                                                                      "rate", "pass", "samples",
                                                                      "false_positives", "summary",
                                                                      "seconds", "hits")}
                         for k, v in payload["criteria"].items()}},
                     ensure_ascii=False, indent=2))
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())