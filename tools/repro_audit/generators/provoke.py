"""repro_audit.generators.provoke —— 入库门效力实证（tamper → 门红 → 复原）。

用法::

    python3 -m tools.repro_audit.generators.provoke \\
        --artifact artifacts/lineage_test.json \\
        --json-path total_lineage_records --delta -1 \\
        --out artifacts/repro_audit/gate_provocation.json

流程（**可回滚、只碰一个文件**）：

1. 记录目标文件 sha256 与 git 状态（脏文件直接拒绝，避免误伤别人的改动）；
2. 按 ``--delta`` 手改工件里的一个头部数字（明细不动 = 模拟「头部被手改」）；
3. 跑 ``maturity.gate()``，把门输出原样记下来（fail-closed 必须为 exit 1）；
4. ``git checkout -- <artifact>`` 复原，并核对 sha256 与原始一致；
5. 再跑一次门，确认复原后重新转绿（证明红/绿切换只由被改的数字引起）。

产物：一份证据 JSON（改动前后 sha256、门的两份输出、复原校验位）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

try:
    from . import maturity
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))))
    from tools.repro_audit.generators import maturity  # type: ignore


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, timeout=60).stdout


def provoke(repo: str, artifact: str, json_path: str, delta: float,
            out_path: str, min_coverage: int = 24) -> dict:
    repo = os.path.abspath(repo)
    full = os.path.join(repo, artifact)
    dirty = _git(repo, "status", "--porcelain", "--", artifact).strip()
    if dirty:
        raise SystemExit("refusing to tamper a dirty file: %s" % dirty)

    sha_before = _sha256(full)
    with open(full, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if json_path not in doc:
        raise SystemExit("json path not found in artifact: %s" % json_path)
    original = doc[json_path]
    if not isinstance(original, (int, float)) or isinstance(original, bool):
        raise SystemExit("value at %s is not numeric" % json_path)
    doc[json_path] = original + delta
    with open(full, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    sha_tampered = _sha256(full)

    try:
        red = maturity.gate(repo, min_coverage=min_coverage)
    finally:
        _git(repo, "checkout", "--", artifact)
    sha_restored = _sha256(full)
    green = maturity.gate(repo, min_coverage=min_coverage)

    evidence = {
        "schema": "repro-audit/gate-provocation/v1",
        "artifact": artifact,
        "tamper": {"json_path": json_path, "original": original,
                   "tampered": original + delta},
        "sha256": {"before": sha_before, "tampered": sha_tampered,
                   "restored": sha_restored},
        "restored_exactly": sha_before == sha_restored,
        "gate_before_tamper_red": {
            "passed": red["passed"], "reason": red.get("reason"),
            "grades": red.get("grades"), "coverage": red.get("coverage"),
            "failures": red.get("failures", [])[:5],
        },
        "gate_after_restore_green": {
            "passed": green["passed"], "reason": green.get("reason"),
            "grades": green.get("grades"), "coverage": green.get("coverage"),
        },
        "fail_closed_demonstrated": (red["passed"] is False
                                    and green["passed"] is True
                                    and sha_before == sha_restored),
    }
    evidence["sha256"]["evidence"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True).encode("utf-8")).hexdigest()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    return evidence


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="入库门效力实证（tamper→门红→复原）")
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--json-path", required=True)
    ap.add_argument("--delta", type=float, default=-1.0)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="artifacts/repro_audit/gate_provocation.json")
    ap.add_argument("--min-coverage", type=int, default=24)
    args = ap.parse_args(argv)
    ev = provoke(args.repo, args.artifact, args.json_path, args.delta,
                 args.out, args.min_coverage)
    print(json.dumps({k: ev[k] for k in ("artifact", "restored_exactly",
                                        "fail_closed_demonstrated")},
                     indent=2))
    return 0 if ev["fail_closed_demonstrated"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())