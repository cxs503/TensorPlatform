#!/usr/bin/env python3
"""锚点查重门的变异红队（M-R17-01 的反向证明）。

「测试全绿」不等于「测试有牙」。本脚本对 ``anchor_dedup_gate.py`` 施加若干
**语义变异**（把判定改成恒 allow / 忽略在飞单 / 关掉归一化），跑同一批测试，
要求每个变异至少让 2 个用例转红；然后用 sha256 证明源文件已原样恢复。

用法::

    python3 tools/mission_governance/anchor_dedup_mutation_check.py \
        --json-out artifacts/r17/anchor_dedup_mutation_report.json

退出码：0=每个变异都至少 2 例转红且文件已复原；1=有变异「未杀死」（测试没牙）
或复原失败。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "tools" / "mission_governance" / "anchor_dedup_gate.py"
TESTS = [
    "tests/test_anchor_dedup_gate.py",
    "tests/test_anchor_precheck_endpoint.py",
]
MIN_KILLED = 2

#: name -> (说明, [(原文, 变异后) ...])；原文必须字面命中，否证则变异失败。
MUTATIONS: dict[str, dict[str, Any]] = {
    "M1_reject_becomes_allow": {
        "what": "把 gate 里所有 reject 改成 allow（查重门形同虚设）",
        "pairs": [('result["decision"] = "reject"', 'result["decision"] = "allow"')],
    },
    "M2_in_flight_ignored": {
        "what": "让 gate 完全忽略在飞候选池（同锚点在飞不再拦）",
        "pairs": [("for mission in in_flight or ():", "for mission in ():")],
    },
    "M3_no_normalization": {
        "what": "关掉锚点归一化（大小写/空白/连字符变体不再等价）",
        "pairs": [
            ('    text = re.sub(r"\\s+", "", text)\n    return text.lower()',
             '    return text'),
        ],
    },
    "M4_delivered_window_ignored": {
        "what": "让「24h 内已交付」永远算作窗口外（交付过也能再派）",
        "pairs": [("in_window = True if when is None else when >= cutoff",
                   "in_window = False")],
    },
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_tests() -> tuple[int, int, list[str]]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TESTS, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(REPO), capture_output=True, text=True, timeout=900,
    )
    out = proc.stdout + proc.stderr
    failed = re.findall(r"^(?:FAILED)\s+(\S+)", out, re.M)
    failed += re.findall(r"^(\S+::\S+)\s+FAILED", out, re.M)
    m = re.search(r"(\d+) failed", out)
    n_failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) passed", out)
    n_passed = int(m.group(1)) if m else 0
    return n_failed, n_passed, sorted(set(failed))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="查重门变异红队")
    ap.add_argument("--json-out", type=Path,
                    default=REPO / "artifacts" / "r17" / "anchor_dedup_mutation_report.json")
    args = ap.parse_args(argv)

    original = TARGET.read_text(encoding="utf-8")
    sha_before = _sha(TARGET)
    base_failed, base_passed, base_names = _run_tests()

    report: dict[str, Any] = {
        "tool": "tools/mission_governance/anchor_dedup_mutation_check.py",
        "target": "tools/mission_governance/anchor_dedup_gate.py",
        "tests": TESTS,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "min_killed_required": MIN_KILLED,
        "baseline": {"failed": base_failed, "passed": base_passed,
                     "sha256": sha_before},
        "mutations": [],
        "restored": None,
    }
    ok = base_failed == 0
    if not ok:
        print(f"[FAIL] 基线就是红的（{base_failed} failed），变异结论无意义：{base_names[:5]}")
        return 1

    try:
        for name, spec in MUTATIONS.items():
            mutated = original
            applied = 0
            for before, after in spec["pairs"]:
                if before in mutated:
                    mutated = mutated.replace(before, after)
                    applied += 1
            if applied == 0:
                report["mutations"].append({
                    "name": name, "what": spec["what"], "status": "mutation_not_applicable",
                    "killed": 0, "failed_tests": [],
                    "note": "原文未命中：源码已变更，需同步变异对",
                })
                print(f"[WARN] {name}: 变异未生效（源码漂移）")
                continue
            TARGET.write_text(mutated, encoding="utf-8")
            n_failed, n_passed, names = _run_tests()
            killed = n_failed
            status = "killed" if killed >= MIN_KILLED else "survived"
            report["mutations"].append({
                "name": name, "what": spec["what"], "status": status,
                "killed": killed, "passed": n_passed, "failed_tests": names,
                "mutated_sha256": _sha(TARGET),
            })
            print(f"[{'OK' if status == 'killed' else 'FAIL'}] {name}: {killed} 例转红 "
                  f"（要求 ≥{MIN_KILLED}）— {spec['what']}")
            for t in names[:6]:
                print(f"        ✗ {t}")
            TARGET.write_text(original, encoding="utf-8")
    finally:
        TARGET.write_text(original, encoding="utf-8")
        sha_after = _sha(TARGET)
        restored = sha_after == sha_before
        report["restored"] = {"sha256_before": sha_before, "sha256_after": sha_after,
                              "identical": restored}
        # 复原后再跑一遍：复原后的树必须仍然全绿（否则本脚本会污染工作树）
        after_failed, after_passed, _ = _run_tests()
        report["after_restore"] = {"failed": after_failed, "passed": after_passed}
        print(f"[{'OK' if restored else 'FAIL'}] 源文件复原：sha256 {'一致' if restored else '不一致'}"
              f"；复原后测试 {after_passed} passed / {after_failed} failed")

    killed_all = all(m["status"] == "killed" for m in report["mutations"]) and report["mutations"]
    report["verdict"] = "pass" if (killed_all and report["restored"]["identical"]
                                   and report["after_restore"]["failed"] == 0) else "fail"
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nverdict={report['verdict']}  工件：{args.json_out}")
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())