"""repro_audit.generators.negcontrols —— 入库门的"负控三刀"（门禁效力实证）。

门禁最容易犯的错是"永远通过"：没有负控，`gate()` 返回 `passed=True` 只说明它
没崩。本模块在一个**合成小仓**（零领域语义：``demo``/``rows``）里跑四组实验：

====== ================================================================ ==========
实验   做法                                                             期望
====== ================================================================ ==========
base   生成器与工件一致                                               门**通过**
cut1   手抄数字：把工件头部值改成 5（明细仍是 3 行）                    门**拒**（coverage/grade）
cut2   生成器路径改到别处（模块不存在）                                 门**拒**（G0，回无数值通道）
cut3   seed 改硬编码随机：头部对得上，但每次运行附带随机明细             门**拒**（幂等不过）
====== ================================================================ ==========

三条负控各自打到契约的不同环节：cut1 打 C2（可重放），cut2 打 C1（可定位），
cut3 打 C2 的幂等分支。任一条"门没拒"即为门禁失效 —— 调用方必须把本模块的
结论当阻塞信号（fail-closed）。

用法::

    python3 -m tools.repro_audit.generators.negcontrols --out artifacts/...json
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import textwrap

from . import contract

__all__ = ["run_controls", "EXPECTED"]

#: 合成仓：工件 / 派生生成器 / 乱数生成器 / 通道清单 / 规格
ARTIFACT = "artifacts/demo/coverage.json"
GENERATOR = "tools/gen_demo.py"
GENERATOR_NONDET = "tools/gen_demo_nondet.py"
CHANNELS = "artifacts/demo/channels.json"
SPECS = "artifacts/demo/specs.json"
OUTPUT = "artifacts/demo/generated/coverage.json"

ARTIFACT_BODY = {
    "rows": [{"ok": True}, {"ok": True}, {"ok": False}],
    "total": 3,
    "passed": 2,
}

CHANNELS_BODY = {"channels": [
    {"channel_id": "demo#total", "artifact": ARTIFACT, "target": "total",
     "expr": "n.len('rows')", "kind": "tally", "rtol": 1e-9},
    {"channel_id": "demo#passed", "artifact": ARTIFACT, "target": "passed",
     "expr": "n.count_true('rows.ok')", "kind": "tally", "rtol": 1e-9},
]}

GENERATOR_SRC = '''"""合成派生生成器（负控夹具，零领域语义）。

自包含：沙箱仓里没有平台包，所以这里带一个**最小**派生求值器（只认
``n.len(path)`` / ``n.count_true(path)`` 两种归约），契约裁决完全照旧。
"""
import json
import os
import re

INPUT_PATH = "%s"
OUTPUT_PATH = "%s"
CHANNELS_PATH = "%s"

_REDUCER = re.compile(r"n\.(len|count_true)\('([^']+)'\)")


def _rows(doc, path):
    node = doc
    for seg in path.split("."):
        if isinstance(node, list):
            node = [r[seg] for r in node if isinstance(r, dict) and seg in r]
        else:
            node = node[seg]
    return node


def _eval(expr, doc):
    kind, path = _REDUCER.match(expr.strip()).groups()
    vals = _rows(doc, path)
    if kind == "len":
        return len(vals)
    return sum(1 for v in vals if v is True)


def main():
    src = os.environ.get("REPRO_INPUT", INPUT_PATH)
    dst = os.environ.get("REPRO_OUTPUT", OUTPUT_PATH)
    doc = json.load(open(src))
    channels = json.load(open(CHANNELS_PATH))["channels"]
    payload = {"artifact": INPUT_PATH,
               "channels": {c["channel_id"]: _eval(c["expr"], doc) for c in channels}}
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    json.dump(payload, open(dst, "w"), sort_keys=True)


main()
''' % (ARTIFACT, OUTPUT, CHANNELS)

#: 可装配"随机明细"的生成器：头部值仍然正确，但附带一个每次不同的字段
GENERATOR_NONDET_SRC = GENERATOR_SRC.replace(
    "    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)\n",
    "    import random\n"
    "    payload[\"noise\"] = [random.random() for _ in range(3)]  # 无种子：每次不同\n"
    "    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)\n")

EXPECTED = {
    "base": {"passed": True, "reason_codes": []},
    "cut1_handcopy": {"passed": False, "reason_codes": ["coverage_below_threshold",
                                                        "channels_below_min_grade"]},
    "cut2_generator_moved": {"passed": False, "reason_codes": ["coverage_below_threshold"]},
    "cut3_hardcoded_seed": {"passed": False, "reason_codes": ["channels_below_min_grade"]},
}


def _write(path: str, body) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(body, str):
            fh.write(body)
        else:
            json.dump(body, fh, indent=2, sort_keys=True)


def _specs(module: str = GENERATOR) -> dict:
    return {
        "channel_manifest": CHANNELS,
        "generators": [{
            "id": "demo_derive",
            "artifact": ARTIFACT,
            "module": module,
            "kind": "derive",
            "output": OUTPUT,
            "argv": [module],
            "sys_path": ["."],
            "cwd": ".",
            "timeout": 120,
        }],
    }


def _build_repo(root: str, module_src: str = GENERATOR_SRC,
                module_path: str = GENERATOR) -> None:
    _write(os.path.join(root, ARTIFACT), ARTIFACT_BODY)
    _write(os.path.join(root, CHANNELS), CHANNELS_BODY)
    _write(os.path.join(root, module_path), textwrap.dedent(module_src))
    _write(os.path.join(root, SPECS), _specs(module_path))


def _run_gate(root: str, specs_rel: str = SPECS) -> dict:
    ledger = contract.build_ledger(root, os.path.join(root, specs_rel), ref=None,
                                   workdir=tempfile.mkdtemp(prefix="negctl_"))
    verdict = contract.gate(ledger, min_coverage=2, min_grade="G3")
    verdict["coverage"] = ledger["coverage"]
    verdict["grades"] = ledger["grades"]
    verdict["channels"] = {c["channel_id"]: {"grade": c["grade"], "reason": c["reason"]}
                           for c in ledger["channels"]}
    return verdict


def _reason_codes(verdict: dict) -> list[str]:
    return sorted({r.split(":")[0] for r in verdict.get("reasons", [])})


def run_controls(keep: bool = False) -> dict:
    """跑 base + 三刀负控，返回逐项"期望 vs 实测"。"""
    base_dir = tempfile.mkdtemp(prefix="negctl_base_")
    results: dict[str, dict] = {}

    # ① base：门必须**通过**（否则门是"永远拒"，同样没效）
    root = os.path.join(base_dir, "base")
    os.makedirs(root, exist_ok=True)
    _build_repo(root)
    verdict = _run_gate(root)
    results["base"] = {"gate_passed": verdict["passed"], "reason_codes": _reason_codes(verdict),
                       "coverage": verdict["coverage"], "grades": verdict["grades"]}

    # ② cut1 手抄数字：头部 total 改成 5，明细还是 3 行
    root = os.path.join(base_dir, "cut1")
    os.makedirs(root, exist_ok=True)
    _build_repo(root)
    tampered = dict(ARTIFACT_BODY, total=5)
    _write(os.path.join(root, ARTIFACT), tampered)
    verdict = _run_gate(root)
    results["cut1_handcopy"] = {"gate_passed": verdict["passed"],
                                "reason_codes": _reason_codes(verdict),
                                "channels": verdict["channels"],
                                "coverage": verdict["coverage"]}

    # ③ cut2 生成器路径改到别处
    root = os.path.join(base_dir, "cut2")
    os.makedirs(root, exist_ok=True)
    _build_repo(root)
    _write(os.path.join(root, SPECS), _specs("tools/elsewhere/gen_demo.py"))
    verdict = _run_gate(root)
    results["cut2_generator_moved"] = {"gate_passed": verdict["passed"],
                                       "reason_codes": _reason_codes(verdict),
                                       "channels": verdict["channels"],
                                       "coverage": verdict["coverage"]}

    # ④ cut3 seed 改硬编码随机：头部仍对，附随机明细 → 幂等不过
    root = os.path.join(base_dir, "cut3")
    os.makedirs(root, exist_ok=True)
    _build_repo(root, GENERATOR_NONDET_SRC, GENERATOR_NONDET)
    _write(os.path.join(root, SPECS), _specs(GENERATOR_NONDET))
    verdict = _run_gate(root)
    results["cut3_hardcoded_seed"] = {"gate_passed": verdict["passed"],
                                      "reason_codes": _reason_codes(verdict),
                                      "channels": verdict["channels"],
                                      "grades": verdict["grades"]}

    checks = []
    for name, expected in EXPECTED.items():
        got = results[name]
        ok = (got["gate_passed"] is expected["passed"]
              and all(code in got["reason_codes"] for code in expected["reason_codes"]))
        checks.append({"control": name, "expected_passed": expected["passed"],
                       "observed_passed": got["gate_passed"],
                       "expected_reasons": expected["reason_codes"],
                       "observed_reasons": got["reason_codes"], "ok": ok})
    if not keep:
        shutil.rmtree(base_dir, ignore_errors=True)
    return {"schema": "repro_audit.negcontrols/v1", "all_ok": all(c["ok"] for c in checks),
            "checks": checks, "observed": results}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI 壳
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    report = run_controls()
    body = json.dumps(report, indent=2, ensure_ascii=False)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        open(out, "w", encoding="utf-8").write(body + "\n")
    print(body)
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())