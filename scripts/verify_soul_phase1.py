#!/usr/bin/env python3
"""Tensor Soul Runtime 第一阶段验收取证脚本（RFC #72 / 契约 v0.1）。

真实执行三组测试（tests/factory 全量、Soul 专项、非 factory 回归），
把命令、退出码、用例名与耗时抄进 soul_phase1_validation.json。
数字全部来自本次真实执行，不做任何估算。

用法：/srv/tensor/shared/venvs/tensor_eco/bin/python scripts/verify_soul_phase1.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
OUT = ROOT / "soul_phase1_validation.json"

# 六场景 ↔ 测试用例映射（判据来自契约 "Acceptance tests" 1-6）
SCENARIOS = [
    ("1 跨 session 连续性",
     "tests.factory.test_soul_runtime.test_agent_identity_and_unfinished_task_survive_independent_sessions"),
    ("2 进程重启恢复",
     "tests.factory.test_soul_runtime.test_latest_checkpoint_restores_after_process_restart"),
    ("3 模型切换连续",
     "tests.factory.test_soul_runtime.test_model_binding_switch_preserves_identity_and_durable_state"),
    ("4 observe 幂等去重",
     "tests.factory.test_soul_runtime.test_observe_is_idempotent_and_detects_conflicting_writes"),
    ("5 租户(组织)隔离",
     "tests.factory.test_soul_runtime.test_tenant_isolation_for_identity_session_and_memory"),
    ("6 未验证信息隔离",
     "tests.factory.test_soul_runtime.test_unverified_model_output_cannot_reach_shared_knowledge"),
]


def run(label: str, args: list[str]) -> dict:
    xml = Path(tempfile.mkdtemp(prefix="soul-junit-")) / f"{label}.xml"
    cmd = [PYTHON, "-m", "pytest", *args, "-q", "-p", "no:warnings",
           f"--junitxml={xml}"]
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    duration = round(time.time() - started, 2)
    cases: list[dict] = []
    if xml.exists():
        for case in ET.parse(xml).getroot().iter("testcase"):
            cases.append({"id": f"{case.get('classname')}.{case.get('name')}",
                          "time": float(case.get("time") or 0),
                          "outcome": "failed" if list(case) and
                          case[0].tag in ("failure", "error") else "passed"})
    return {"label": label, "command": " ".join(cmd[:-1]),
            "exit_code": proc.returncode, "duration_seconds": duration,
            "tail": (proc.stdout.strip().splitlines() or [""])[-1],
            "cases": cases,
            "counts": {"passed": sum(c["outcome"] == "passed" for c in cases),
                       "failed": sum(c["outcome"] == "failed" for c in cases),
                       "total": len(cases)}}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True).stdout.strip()


def main() -> int:
    runs = [run("factory", ["tests/factory"]),
            run("soul", ["tests/factory/test_soul_runtime.py",
                         "tests/factory/test_soul_migrations.py"]),
            run("non_factory", ["tests", "--ignore=tests/factory"])]
    by_id = {case["id"]: case for run_ in runs for case in run_["cases"]}
    scenarios = []
    for name, case_id in SCENARIOS:
        case = by_id.get(case_id)
        scenarios.append({"scenario": name, "test": case_id,
                          "present": case is not None,
                          "outcome": case["outcome"] if case else "NOT RUN",
                          "seconds": case["time"] if case else None})

    checks_passed = (sum(r["exit_code"] == 0 for r in runs)
                     + sum(s["outcome"] == "passed" for s in scenarios))
    total = len(runs) + len(scenarios)
    evidence = {
        "issue": "#72",
        "title": "Tensor Soul Runtime 第一阶段 MVP（契约 v0.1）",
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "head_commit": git("rev-parse", "HEAD"),
        "commits": git("log", "--format=%H %s", "6a3f63d..HEAD").splitlines(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "python_binary": PYTHON,
        "environment_note": ("pgserver/fastapi 依赖装在本地 .pydeps（PYTHONPATH 注入），"
                             "CI 用 requirements-factory-dev.txt；测试库由 pgserver "
                             "每次起临时 PostgreSQL，不连生产库"),
        "checks_passed": checks_passed,
        "checks_total": total,
        "runs": runs,
        "acceptance_scenarios": scenarios,
        "not_run": [
            "神经记忆编码器实验（契约 Optional experiment；须待 A/B 基线后）",
            "闭源模型 hidden state 修改（契约明示 out of scope）",
            "生产部署验证：本阶段只做单元/集成测试，未部署到线上集群",
            "前端 factory 控制台未改动，故 console workflow 未跑",
        ],
    }
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"checks_passed": checks_passed, "checks_total": total,
                      "runs": [{r["label"]: r["counts"],
                                "exit": r["exit_code"]} for r in runs]},
                     ensure_ascii=False))
    return 0 if checks_passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())