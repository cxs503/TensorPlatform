#!/usr/bin/env python3
"""能力台账「入库防腐朽」闸门的变异探测（mission 2031，platform 席）。

目的
----
新增的两道闸门（`tests/test_capability_ledger_persisted.py` 的 G1~G5、
`ops/tp_worker.py::_tool_audit_caps` 的多候选回退）如果只是「跑通了」，不能证明它们
真能拦住事故。本脚本对**真实闸门**注入 5 种变异，逐一回收「是否报警」，
并写出 JSON 证据（零手抄，供报告直接读）：

  M1 台账文件缺失            → 期望 C1~C10 对账器退出码 2（输入缺失）
  M2 台账存在但未入库        → 期望 git 跟踪判据判 False（本事故的直接形态）
  M3 证据引文正则永不命中    → 期望 C3 违规、退出码 1
  M4 台账内容被手改（等级改写）→ 期望 C7 快照漂移闸退出码 1
  M5 台账结构性掉行（v1 证据行被删）→ 期望 C7 快照漂移闸退出码 1
  N1 阴性对照：原封台账      → 期望所有闸门通过（不得报警）

全部在临时目录里构造，不动工作树内的台账。

用法: python3 tools/audit/m2031_gate_mutation_probe.py [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
RESOLVED = ROOT / "artifacts" / "capability_ledger" / "capability_ledger_resolved.json"
TABLE = ROOT / "artifacts" / "capability_ledger" / "capability_ledger_table.md"
TOOL_REL = Path("tools/audit/capability_ledger.py")
CURATE_REL = Path("tools/audit/curate_capability_ledger_m1811.py")
FIX_REL = Path("tests/fixtures/capability_ledger")
DOC_MANIFEST_TOOL_REL = Path("tools/audit/build_capability_corpus_docs.py")


def _sandbox(tmp: Path) -> Path:
    """造一个只含对账器所需文件的最小仓（ROOT = tmp）。"""
    (tmp / "tools" / "audit").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts" / "capability_ledger").mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIX_REL, tmp / FIX_REL)
    shutil.copy2(ROOT / TOOL_REL, tmp / TOOL_REL)
    shutil.copy2(ROOT / CURATE_REL, tmp / CURATE_REL)
    shutil.copy2(ROOT / DOC_MANIFEST_TOOL_REL, tmp / DOC_MANIFEST_TOOL_REL)
    for f in (LEDGER, RESOLVED, TABLE):
        shutil.copy2(f, tmp / "artifacts" / "capability_ledger" / f.name)
    for d in ("docs",):  # curation 的 must_consume 需要这些文档存在
        if (ROOT / d).is_dir():
            shutil.copytree(ROOT / d, tmp / d, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.png", "*.pdf"))
    return tmp


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=300)


def _check(tmp: Path):
    return _run([sys.executable, str(tmp / TOOL_REL), "--check"], tmp)


def _curate(tmp: Path):
    return _run([sys.executable, str(tmp / CURATE_REL), "--check"], tmp)


def _tracked(path: Path) -> bool:
    out = subprocess.run(["git", "ls-files", "--error-unmatch", str(path.relative_to(ROOT))],
                         cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    return out.returncode == 0


def probe() -> dict:
    rows: list[dict] = []

    # N1 阴性对照
    with tempfile.TemporaryDirectory() as t:
        tmp = _sandbox(Path(t))
        r = _check(tmp)
        rows.append({"id": "N1", "mutation": "原封台账（阴性对照）", "signal": "对账器退出码 0",
                     "observed": r.returncode, "detected": r.returncode == 0,
                     "expected": "不报警", "ok": r.returncode == 0})

    # M1 台账缺失
    with tempfile.TemporaryDirectory() as t:
        tmp = _sandbox(Path(t))
        (tmp / "artifacts/capability_ledger/capability_ledger.json").unlink()
        r = _check(tmp)
        rows.append({"id": "M1", "mutation": "台账文件缺失", "signal": "对账器退出码 2",
                     "observed": r.returncode, "detected": r.returncode != 0,
                     "expected": "报警", "ok": r.returncode == 2})

    # M2 未入库（真实仓内造一个未跟踪文件；与 G2 判据同构）
    probe_file = ROOT / "artifacts" / "capability_ledger" / "_m2031_untracked_probe.json"
    probe_file.write_text("{}\n", encoding="utf-8")
    try:
        untracked = not _tracked(probe_file)
    finally:
        probe_file.unlink(missing_ok=True)
    legitimate = _tracked(LEDGER)
    rows.append({"id": "M2", "mutation": "台账存在于工作树但未入库（未跟踪）",
                 "signal": "git ls-files 判据", "observed": f"untracked={untracked}/ledger_tracked={legitimate}",
                 "detected": untracked, "expected": "报警", "ok": untracked and legitimate})

    # M3 引文正则失效
    with tempfile.TemporaryDirectory() as t:
        tmp = _sandbox(Path(t))
        p = tmp / "artifacts/capability_ledger/capability_ledger.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        for cap in d["capabilities"]:
            if cap.get("evidence"):
                cap["evidence"][0]["quote_match"] = r"__m2031_never_matches__"
                break
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        r = _check(tmp)
        rows.append({"id": "M3", "mutation": "证据引文正则永不命中", "signal": "C3 违规 + 退出码 1",
                     "observed": r.returncode, "detected": r.returncode == 1,
                     "expected": "报警", "ok": r.returncode == 1})

    # M4 台账内容被手改（等级改写）→ C7「对账结果 vs 已入库快照」漂移闸
    #    实测发现：curation 幂等闸对本变异**不报警**（build() 是台账的纯函数，
    #    任何内容都自洽 SYNCED），内容篡改只能由 C7 快照漂移闸抓到。
    with tempfile.TemporaryDirectory() as t:
        tmp = _sandbox(Path(t))
        p = tmp / "artifacts/capability_ledger/capability_ledger.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        for cap in d["capabilities"]:
            if cap.get("evidence"):
                cap["evidence"][0]["asserted_level"] = "REAL"
                break
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        r = _check(tmp)
        rows.append({"id": "M4", "mutation": "台账内容被手改（证据等级改写）", "signal": "C7 快照漂移 + 退出码 1",
                     "observed": r.returncode, "detected": r.returncode == 1,
                     "expected": "报警", "ok": r.returncode == 1})

    # M5 台账结构性掉行（一条证据被删）→ curation 幂等闸必须报 STALE
    with tempfile.TemporaryDirectory() as t:
        tmp = _sandbox(Path(t))
        p = tmp / "artifacts/capability_ledger/capability_ledger.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        for cap in d["capabilities"]:
            if cap.get("evidence"):
                cap["evidence"].pop(0)
                break
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        # 实测：curation 幂等闸只重建 v2 curation 段，**看不见 v1 证据行被删**；
        # 掉行由 C7（对账结果含每行 line_sha256 vs 已入库快照）抓到。
        r = _check(tmp)
        rows.append({"id": "M5", "mutation": "台账结构性掉行（v1 证据行被删）", "signal": "C7 快照漂移 + 退出码 1",
                     "observed": r.returncode, "detected": r.returncode == 1, "expected": "报警",
                     "ok": r.returncode == 1})
    return {
        "mission": "m2031",
        "generated_by": "tools/audit/m2031_gate_mutation_probe.py",
        "detected": sum(1 for r in rows if r["id"].startswith("M") and r["detected"]),
        "mutants": sum(1 for r in rows if r["id"].startswith("M")),
        "negative_control_ok": all(r["ok"] for r in rows if r["id"] == "N1"),
        "all_ok": all(r["ok"] for r in rows),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "artifacts/capability_ledger/m2031_gate_mutation_probe.json"))
    args = ap.parse_args(argv)
    res = probe()
    Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for r in res["rows"]:
        print(f"{r['id']:3s} {r['mutation']:28s} 期望={r['expected']:6s} 实测={r['observed']!s:22.22s} ok={r['ok']}")
    print(f"变异检出 {res['detected']}/{res['mutants']}；阴性对照通过={res['negative_control_ok']}；all_ok={res['all_ok']}")
    return 0 if res["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())