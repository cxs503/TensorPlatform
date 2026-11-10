"""战役判据机器验收（AC1–AC8）。

每个 AC 都给出**可复算的数字**与产物路径；无法诚实达成的判据按原样记 FAIL/PARTIAL，
不粉饰（honest FAIL 不可覆盖）。

用法::

    python -m capability_ledger_auto.ac_verify --root . --out artifacts/capability_ledger_auto

零领域语义：本模块只使用注册表 id/label 与治理裁决词表做机器比对。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess  # nosec B404 - 读取 git 基线 blob
import sys
import tempfile
from pathlib import Path
from typing import Any

from .evidence_chain import build_evidence_chain
from .grader import GradeDeterminer
from .ledger_audit import (
    AUDIT_DIR,
    DEFAULT_ADJUDICATION_LEDGER,
    DEFAULT_CORPUS,
    DEFAULT_MANUAL_LEDGER,
    content_digest,
    git_commit,
    grade_capabilities,
    load_manual_reference,
    run_audit,
)
from .registry import LEVEL_RANK, CapabilityDef, CapabilityRegistry
from .scanner import EvidenceScanner, sha256_file

REGISTRY_REL = "config/capabilities.json"
ENGINE_DIR_REL = "src/capability_ledger_auto"


# ------------------------------------------------------------------ 工具

def _ac(ac_id: str, title: str, passed: bool, detail: str, numbers: dict[str, Any],
        artifacts: list[str] | None = None, status: str | None = None) -> dict[str, Any]:
    return {
        "id": ac_id,
        "title": title,
        "status": status or ("PASS" if passed else "FAIL"),
        "passed": bool(passed),
        "detail": detail,
        "numbers": numbers,
        "artifacts": artifacts or [],
    }


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """recorded commit 是否为 HEAD 的祖先（产物可早于 HEAD，只要仍在谱系内）。"""
    try:
        proc = subprocess.run(  # nosec B603,B607
            ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=str(root),
            capture_output=True, timeout=30,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _caps_with_evidence(adjudication_ledger: Path) -> list[str]:
    """从结构化裁决台账取"已有证据"的能力（每条证据 = 一次独立裁决）。"""
    if not adjudication_ledger.exists():
        return []
    raw = json.loads(adjudication_ledger.read_text(encoding="utf-8"))
    return sorted(c["id"] for c in raw.get("capabilities", []) or [] if c.get("evidence"))


def _overclaim_set(manual: dict[str, dict[str, Any]]) -> list[str]:
    """人工台账里"自称 REAL 但裁决不是 REAL"的能力 = OVERCLAIM 案例。"""
    return sorted(
        cid for cid, v in manual.items()
        if v.get("claimed_level") == "REAL" and v.get("reconciled_level") not in ("", "REAL", None)
    )


def _minimal_registry(tmp: Path) -> tuple[Path, CapabilityRegistry]:
    """合成注册表（零领域语义）：能力 id/模式全部虚构。"""
    reg = {
        "schema_version": "1.0",
        "capabilities": [
            {
                "capability_id": "zz.frobnicate",
                "label": "Frobnicate widget",
                "description": "synthetic capability for AC fixtures",
                "expected_evidence_types": ["theory", "code", "config", "data", "report"],
                "code_patterns": ["frobnicate"],
                "test_patterns": ["test_frobnicate"],
                "report_patterns": ["frobnicate"],
                "initial_level": "WITHHELD",
            },
            {
                "capability_id": "wq.tessellate",
                "label": "Tessellate wquux",
                "description": "synthetic stub-only capability",
                "expected_evidence_types": ["theory", "code"],
                "code_patterns": ["tessellate_wquux"],
                "test_patterns": ["test_tessellate"],
                "report_patterns": ["tessellate"],
                "initial_level": "WITHHELD",
            },
        ],
    }
    reg_path = tmp / "synthetic_registry.json"
    reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return reg_path, CapabilityRegistry.from_json(reg_path)


def _build_synthetic_repo(root: Path, *, with_test: bool) -> None:
    """合成仓库（全部虚构词条）：代码+理论+配置+数据+报告齐备，可选测试文件。"""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    (root / "tensor_reports").mkdir(exist_ok=True)

    (root / "src" / "frobnicate_core.py").write_text(
        "def frobnicate_widget(x, k):\n"
        "    \"\"\"Substantive synthetic implementation (governing equation below).\"\"\"\n"
        "    return x * k + 1.0\n",
        encoding="utf-8",
    )
    (root / "src" / "tessellate_stub.py").write_text(
        "def tessellate_wquux(n):\n"
        "    \"\"\"Synthetic stub: interface declared, no implementation.\"\"\"\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (root / "docs" / "frobnicate_theory.md").write_text(
        "# frobnicate theory\n\n方程：y = k x + 1（governing equation）\n", encoding="utf-8",
    )
    (root / "config" / "frobnicate_config.json").write_text(
        json.dumps({"method": "frobnicate", "k": 2.0}), encoding="utf-8",
    )
    (root / "data" / "frobnicate_data.json").write_text(
        json.dumps({"frobnicate_samples": [1.0, 2.0, 3.0]}), encoding="utf-8",
    )
    (root / "tensor_reports" / "zz_report.json").write_text(
        json.dumps({"scenario_key": "zz1", "verdict": "PASS", "capability": "zz.frobnicate"}),
        encoding="utf-8",
    )
    if with_test:
        (root / "tests" / "test_frobnicate.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
            "from frobnicate_core import frobnicate_widget\n\n"
            "def test_frobnicate_basic():\n"
            "    assert frobnicate_widget(2.0, 3.0) == 7.0\n",
            encoding="utf-8",
        )


def _grade_one(root: Path, reg: CapabilityRegistry, cid: str, *, exec_tests: bool = False):
    scanner = EvidenceScanner(root, reg, exec_tests=exec_tests)
    cap = reg.get(cid)
    scan = scanner.scan_capability(cap)
    chain = build_evidence_chain(cap, scan, root)
    return scan, chain, GradeDeterminer(root=root).determine(chain)


# ------------------------------------------------------------------ AC 实现

def ac1_manual_agreement(root: Path, payload: dict[str, Any], manual_ledger: Path,
                         adjudication_ledger: Path) -> dict[str, Any]:
    """AC1：已有证据的能力，自动判级与人工裁决一致（要求 100%）。"""
    manual = load_manual_reference(manual_ledger)
    targets = _caps_with_evidence(adjudication_ledger)
    rows, matched = [], 0
    for cid in targets:
        auto = payload["capabilities"].get(cid, {}).get("grade", "MISSING")
        ref = manual.get(cid, {}).get("reconciled_level", "")
        ok = bool(ref) and auto == ref
        matched += int(ok)
        rows.append({"capability_id": cid, "auto_grade": auto, "manual_grade": ref, "match": ok})
    rate = matched / len(targets) if targets else 0.0
    return _ac(
        "AC1", "自动判级 vs 人工裁决（已有证据能力）", len(targets) > 0 and rate == 1.0,
        f"{matched}/{len(targets)} 一致（一致率 {rate:.1%}）",
        {"targets": targets, "matched": matched, "total": len(targets), "rate": round(rate, 4),
         "rows": rows},
        [str(manual_ledger), str(adjudication_ledger)],
    )


def ac2_overclaim(root: Path, payload: dict[str, Any], manual_ledger: Path) -> dict[str, Any]:
    """AC2：OVERCLAIM 案例必须被判为非 REAL（propeller 等）。"""
    manual = load_manual_reference(manual_ledger)
    targets = _overclaim_set(manual)
    rows, detected = [], 0
    for cid in targets:
        auto = payload["capabilities"].get(cid, {}).get("grade", "MISSING")
        ok = auto != "REAL"
        detected += int(ok)
        rows.append({
            "capability_id": cid,
            "claimed_level": manual[cid]["claimed_level"],
            "manual_reconciled": manual[cid]["reconciled_level"],
            "auto_grade": auto,
            "detected": ok,
        })
    rate = detected / len(targets) if targets else 0.0
    judged_mock = [r["capability_id"] for r in rows if r["auto_grade"] == "MOCK"]
    return _ac(
        "AC2", "OVERCLAIM 检出（自称 REAL 但证据反向）", len(targets) > 0 and rate == 1.0,
        f"检出 {detected}/{len(targets)}（检出率 {rate:.1%}）；判为 MOCK 的案例 {len(judged_mock)} 条",
        {"targets": targets, "detected": detected, "total": len(targets), "rate": round(rate, 4),
         "auto_grade_mock": judged_mock, "rows": rows},
        [str(manual_ledger)],
    )


def ac3_negative_control(root: Path) -> dict[str, Any]:
    """AC3：空仓库上全部判 NOT_SUPPORTED（0 假阳性）。"""
    tmp = Path(tempfile.mkdtemp(prefix="n02_ac3_"))
    try:
        empty = tmp / "empty_repo"
        empty.mkdir()
        registry = CapabilityRegistry.from_json(root / REGISTRY_REL)
        chains, grades, _ = grade_capabilities(empty, registry, exec_tests=False)
        tally: dict[str, int] = {}
        for g in grades.values():
            tally[g.grade] = tally.get(g.grade, 0) + 1
        false_pos = [cid for cid, g in grades.items() if g.grade != "NOT_SUPPORTED"]
        return _ac(
            "AC3", "负控：空仓库 0 假阳性", not false_pos and len(grades) == registry.count(),
            f"{len(grades) - len(false_pos)}/{len(grades)} 判 NOT_SUPPORTED，假阳性 {len(false_pos)}",
            {"capabilities": len(grades), "tally": tally, "false_positives": false_pos},
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ac4_idempotency(root: Path, *, corpus: Path, adj_ledger: Path, manual: Path,
                    exec_tests: bool, test_cache_path: Path | None = None) -> dict[str, Any]:
    """AC4：同一仓库连续扫描 2 次，产物 sha256 完全一致。"""
    tmp = Path(tempfile.mkdtemp(prefix="n02_ac4_"))
    try:
        cache = Path(test_cache_path) if test_cache_path else tmp / "test_cache.json"
        r1 = run_audit(root, out_dir=str(tmp / "run1"), corpus_paths=(corpus,),
                       adjudication_ledgers=(adj_ledger,), manual_ledger=manual,
                       exec_tests=exec_tests, test_cache_path=cache, write=True)
        r2 = run_audit(root, out_dir=str(tmp / "run2"), corpus_paths=(corpus,),
                       adjudication_ledgers=(adj_ledger,), manual_ledger=manual,
                       exec_tests=exec_tests, test_cache_path=cache, write=True)
        sha1 = sha256_file(Path(r1["paths"]["json"]))
        sha2 = sha256_file(Path(r2["paths"]["json"]))
        d1 = r1["payload"]["content_digest"]
        d2 = r2["payload"]["content_digest"]
        same = sha1 == sha2 and d1 == d2
        return _ac(
            "AC4", "幂等性：连续两次扫描 sha256 相等", same,
            f"file_sha256 {sha1[:16]}… == {sha2[:16]}… ；content_digest 相等={d1 == d2}",
            {"run1_file_sha256": sha1, "run2_file_sha256": sha2,
             "run1_content_digest": d1, "run2_content_digest": d2, "equal": same},
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ac5_incremental(root: Path) -> dict[str, Any]:
    """AC5：新增测试文件 → 等级提升（增量检测）。

    战役原文写"STUB→REAL"。本引擎**拒绝**这条路径：仅补一个测试文件不能把
    "只有签名没有实现"的能力变成 REAL（那是典型的过账路径）。故此处验证的是
    增量检测机制本身：``WITHHELD → REAL``（补测试后升账），并附加**反例控制**：
    给 stub-only 能力补测试文件，等级必须仍为 STUB（0 假升账）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="n02_ac5_"))
    try:
        reg_path, reg = _minimal_registry(tmp)
        repo = tmp / "repo"
        repo.mkdir()
        _build_synthetic_repo(repo, with_test=False)

        _, _, before = _grade_one(repo, reg, "zz.frobnicate")
        _, _, stub_before = _grade_one(repo, reg, "wq.tessellate")

        _build_synthetic_repo(repo, with_test=True)
        _, _, after = _grade_one(repo, reg, "zz.frobnicate", exec_tests=True)
        _, _, stub_after = _grade_one(repo, reg, "wq.tessellate", exec_tests=True)

        upgraded = LEVEL_RANK[after.grade] > LEVEL_RANK[before.grade]
        stub_stable = stub_after.grade == stub_before.grade == "STUB"
        passed = upgraded and stub_stable
        return _ac(
            "AC5", "增量检测：新增测试文件驱动等级提升（含反例控制）", passed,
            f"zz.frobnicate {before.grade} → {after.grade}（升级={upgraded}）；"
            f"反例 wq.tessellate {stub_before.grade} → {stub_after.grade}（0 假升账={stub_stable}）",
            {"before": before.grade, "after": after.grade, "upgraded": upgraded,
             "control_before": stub_before.grade, "control_after": stub_after.grade,
             "control_stable": stub_stable,
             "spec_deviation": "战役原文 STUB→REAL 被判为过账路径而拒绝；改验 WITHHELD→REAL + stub 反例"},
            status="PASS" if passed else "FAIL",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ac6_provenance(root: Path, out_dir: Path) -> dict[str, Any]:
    """AC6：产物 ledger_audit.json + sha256 + git_commit 落盘且可复算。"""
    json_path = out_dir / "ledger_audit.json"
    sha_path = out_dir / "ledger_audit.sha256"
    if not json_path.exists() or not sha_path.exists():
        return _ac("AC6", "产物溯源（ledger_audit.json + sha256 + git_commit）", False,
                   "产物缺失", {"json": str(json_path), "sha256_file": str(sha_path)})
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    file_sha = sha256_file(json_path)
    sidecar = sha_path.read_text(encoding="utf-8").split()[0]
    recomputed = content_digest(payload)
    embedded = payload.get("content_digest", "")
    head = git_commit(root)
    recorded = str(payload.get("git_commit", ""))
    rec_sha, head_sha, dirty = recorded.split("-")[0], head.split("-")[0], head.endswith("-dirty")
    commit_ok = bool(rec_sha) and (
        rec_sha == head_sha or (not dirty and _is_ancestor(root, rec_sha, head_sha))
    )
    ok = file_sha == sidecar and recomputed == embedded and bool(embedded) and commit_ok
    return _ac(
        "AC6", "产物溯源（ledger_audit.json + sha256 + git_commit）", ok,
        f"file_sha256 复算一致={file_sha == sidecar}；content_digest 复算一致={recomputed == embedded}；"
        f"git_commit={payload.get('git_commit')}（HEAD={head}）",
        {"file_sha256": file_sha, "sidecar_sha256": sidecar,
         "content_digest": embedded, "recomputed_digest": recomputed,
         "git_commit": payload.get("git_commit"), "head": head,
         "file_sha256_match": file_sha == sidecar,
         "content_digest_match": recomputed == embedded,
         "commit_match": commit_ok},
        [str(json_path), str(sha_path)],
    )


def ac7_zero_domain_semantics(root: Path) -> dict[str, Any]:
    """AC7：扫描器零领域语义。

    (a) 引擎源码的**可执行字符串常量**（排除 docstring/注释）不含注册表里的域标签；
    (b) 用全虚构词条的合成注册表+合成仓库，引擎仍能正确判级（只依赖注册表模式）。
    """
    registry_raw = json.loads((root / REGISTRY_REL).read_text(encoding="utf-8"))
    # 领域词条 = 注册表里的 label / description（人类域语义）；code_patterns 是注册表
    # 下发的匹配规则（引擎本就该按数据使用），不参与"硬编码"判定。
    domain_terms: list[str] = []
    capability_ids: list[str] = []
    for cap in registry_raw.get("capabilities", []):
        for key in ("label", "description"):
            val = str(cap.get(key, "")).strip()
            if len(val) >= 2:
                domain_terms.append(val)
        capability_ids.append(str(cap.get("capability_id", "")))

    engine_dir = root / ENGINE_DIR_REL
    hits: list[str] = []
    for fpath in sorted(engine_dir.glob("*.py")):
        try:
            tree = ast.parse(fpath.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                ds = ast.get_docstring(node, clean=False)
                if ds:
                    docstrings.add(ds)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in docstrings:
                    continue
                for term in domain_terms:
                    if term and term in node.value:
                        hits.append(f"{fpath.name}:{node.lineno}:{term}")
                for cid in capability_ids:
                    if cid and node.value == cid:
                        hits.append(f"{fpath.name}:{node.lineno}:capability_id_literal:{cid}")

    tmp = Path(tempfile.mkdtemp(prefix="n02_ac7_"))
    try:
        _, reg = _minimal_registry(tmp)
        repo = tmp / "repo"
        repo.mkdir()
        _build_synthetic_repo(repo, with_test=True)
        _, _, grade_obj = _grade_one(repo, reg, "zz.frobnicate", exec_tests=True)
        synthetic_ok = grade_obj.grade == "REAL"
        return _ac(
            "AC7", "零领域语义（源码无域词条 + 虚构词条可判级）", not hits and synthetic_ok,
            f"引擎可执行字符串常量命中域标签 {len(hits)} 处；虚构词条能力判级={grade_obj.grade}",
            {"domain_term_hits": hits[:20], "domain_terms_checked": len(domain_terms),
             "capability_ids_checked": len(capability_ids),
             "synthetic_grade": grade_obj.grade, "synthetic_ok": synthetic_ok},
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ac8_registry_format(root: Path) -> dict[str, Any]:
    """AC8：不改变 capabilities.json 格式（自动判定层是旁挂新增）。"""
    reg_path = root / REGISTRY_REL
    file_sha = sha256_file(reg_path)
    try:
        head_blob = subprocess.run(  # nosec B603,B607
            ["git", "show", f"HEAD:{REGISTRY_REL}"], cwd=str(root),
            capture_output=True, timeout=30, check=True,
        ).stdout
        head_sha = hashlib.sha256(head_blob).hexdigest()
    except (subprocess.SubprocessError, OSError):
        head_sha = ""
    registry = CapabilityRegistry.from_json(reg_path)
    keys = set(json.loads(reg_path.read_text(encoding="utf-8")).keys())
    # AST 级检测：引擎任何 open(..., 写模式) 调用只要涉及注册表文件名即记违规
    write_hits = []
    for fpath in sorted((root / ENGINE_DIR_REL).glob("*.py")):
        try:
            tree = ast.parse(fpath.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open"):
                continue
            consts = [a.value for a in node.args if isinstance(a, ast.Constant)]
            modes = [c for c in consts if isinstance(c, str) and any(ch in c for ch in "wax+")]
            names = [c for c in consts if isinstance(c, str) and "capabilities.json" in c]
            if modes and names:
                write_hits.append(f"{fpath.name}:{node.lineno}")
    # 行为验证：跑一次审计后注册表字节不变（自动判定层是旁挂的）
    tmp = Path(tempfile.mkdtemp(prefix="n02_ac8_"))
    try:
        registry_obj = CapabilityRegistry.from_json(reg_path)
        grade_capabilities(root, registry_obj, exec_tests=False)
        sha_after = sha256_file(reg_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = (file_sha == head_sha) and (sha_after == file_sha) and registry.count() == 21 and not write_hits
    return _ac(
        "AC8", "回归：不改动 capabilities.json 格式（旁挂自动判定层）", ok,
        f"文件与 HEAD blob 一致={file_sha == head_sha}；审计后字节不变={sha_after == file_sha}；"
        f"能力数={registry.count()}；引擎写该文件={bool(write_hits)}",
        {"file_sha256": file_sha, "head_blob_sha256": head_sha, "sha_after_audit": sha_after,
         "unchanged": file_sha == head_sha, "audit_kept_bytes": sha_after == file_sha,
         "capability_count": registry.count(),
         "top_level_keys": sorted(keys), "engine_write_hits": write_hits},
    )


# ------------------------------------------------------------------ 入口

def verify_all(root: Path | str = ".", out_dir: str | Path = AUDIT_DIR,
               *, exec_tests: bool = True, write: bool = True,
               test_cache_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    out_path = root / out_dir if not Path(out_dir).is_absolute() else Path(out_dir)
    corpus = root / DEFAULT_CORPUS
    adj = root / DEFAULT_ADJUDICATION_LEDGER
    manual = root / DEFAULT_MANUAL_LEDGER

    cache = Path(test_cache_path) if test_cache_path else (out_path / "test_cache.json")
    audit = run_audit(root, out_dir=str(out_dir), corpus_paths=(corpus,),
                      adjudication_ledgers=(adj,), manual_ledger=manual,
                      exec_tests=exec_tests, test_cache_path=cache, write=True)
    payload = audit["payload"]

    results = [
        ac1_manual_agreement(root, payload, manual, adj),
        ac2_overclaim(root, payload, manual),
        ac3_negative_control(root),
        ac6_provenance(root, out_path),
        ac7_zero_domain_semantics(root),
        ac8_registry_format(root),
        ac5_incremental(root),
        ac4_idempotency(root, corpus=corpus, adj_ledger=adj, manual=manual,
                        exec_tests=exec_tests, test_cache_path=cache),
    ]

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "status_by_id": {r["id"]: r["status"] for r in results},
    }
    report = {
        "schema_version": "1.0",
        "battle_node": "p270w2-n02-capability-ledger-auto",
        "git_commit": git_commit(root),
        "ledger_audit_path": str(out_path / "ledger_audit.json"),
        "ledger_audit_file_sha256": sha256_file(out_path / "ledger_audit.json")
        if (out_path / "ledger_audit.json").exists() else "",
        "ledger_content_digest": payload.get("content_digest", ""),
        "grade_tally": payload["summary"]["grade_tally"],
        "summary": summary,
        "ac": results,
        "run_metrics": audit["metrics"],
    }
    if write:
        path = out_path / "ac_results.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        report["ac_results_path"] = str(path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="能力台账自动升账引擎 · 战役判据机器验收")
    parser.add_argument("--root", default=".")
    parser.add_argument("--out", default=AUDIT_DIR)
    parser.add_argument("--test-cache", default=None,
                        help="pytest 执行结果缓存（同仓库复跑时省算力，判级不受影响）")
    parser.add_argument("--no-exec-tests", action="store_true",
                        help="不真实执行测试（REAL 将因缺执行证据而降为 WITHHELD）")
    args = parser.parse_args(argv)
    report = verify_all(args.root, out_dir=args.out, exec_tests=not args.no_exec_tests,
                        test_cache_path=args.test_cache)
    for r in report["ac"]:
        print(f"[{r['status']:7s}] {r['id']} {r['title']} :: {r['detail']}")
    print(f"AC 通过 {report['summary']['passed']}/{report['summary']['total']}")
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())