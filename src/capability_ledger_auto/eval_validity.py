"""判级器自身的效度评估（eval validity）：带标注的合成算例 + 混淆矩阵。

为什么需要：证据分级器（grader）本身也是被测对象。修复之前，仓库里只有
"某个启发式有没有命中" 的单元测试，没有任何**带真值标注**的算例集，因此
"已知 STUB 应判 STUB / 已知 REAL 应判 REAL" 这类判据无法机器复核——历史上
一次"文件级 STUB 过触发"修复把 STUB 召回率直接压到 0（真仓库扫描 tally 里
STUB=0），却没有度量能发现它。

本模块构造**合成算例仓库**（铁律零领域语义：能力 id 与模块名全部是
``fx.*`` / ``fxalpha*`` 形状，不含任何域词条），每级若干复本，
真值由构造过程决定，然后跑真实扫描器 + 真实 pytest 子进程 + 判级器，
输出混淆矩阵与逐级 recall / precision / F1。

变体（variant）用于把"结构性缺陷"变成可度量的假阴性：

``standard``     干净实现（src/ 下）
``backend_root`` 同一份证据放在 ``backend/modules/`` 下 —— 度量代码扫描根
                 是否只认 ``src/``（平台真实代码大量在 backend/）
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .evidence_chain import build_evidence_chain
from .fingerprint import build_record
from .grader import GradeDeterminer
from .registry import CapabilityRegistry
from .scanner import DEFAULT_CODE_ROOTS, EvidenceScanner

LEVELS = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
CLI_EXIT_OK = 0

# 合成命名（零领域语义：这些名字在真实仓库里不存在，也不可能被域词表污染）
_PREFIX = {
    "REAL": "fxalpha",
    "MOCK": "fxmock",
    "STUB": "fxstub",
    "WITHHELD": "fxheld",
    "NOT_SUPPORTED": "fxnone",
}


@dataclass(frozen=True)
class FixtureCase:
    capability_id: str
    truth: str
    pattern: str
    variant: str = "standard"

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "truth": self.truth,
            "pattern": self.pattern,
            "variant": self.variant,
        }


def fixture_cases(replicas: int = 3, include_variants: bool = True) -> list[FixtureCase]:
    """生成带真值的算例清单（真值 = 构造时植入的证据形态）。"""
    cases: list[FixtureCase] = []
    for level, prefix in _PREFIX.items():
        for i in range(1, replicas + 1):
            cases.append(FixtureCase(f"fx.{level.lower()}.{i}", level, f"{prefix}{i}"))
    if include_variants:
        for i in range(1, replicas + 1):
            # 变体必须用互不相同的合成模式名：与 standard 案例共用模式会互相
            # 串证据（同名模块/测试文件），度量结果就不是单变量对照了。
            cases.append(FixtureCase(
                f"fx.real_backend.{i}", "REAL", f"fxalphab{i}", variant="backend_root",
            ))
            cases.append(FixtureCase(
                f"fx.mock_backend.{i}", "MOCK", f"fxmockb{i}", variant="backend_root",
            ))
    return cases


# ---------------------------------------------------------------- 夹具落盘
def _module_body(pattern: str, level: str) -> str:
    """按真值形态生成模块源码。"""
    fn = f"{pattern}_compute"
    if level == "STUB":
        return (
            '"""Synthetic fixture module (planted STUB)."""\n\n\n'
            f"def {fn}(x):\n"
            '    """Interface only."""\n'
            "    raise NotImplementedError\n"
        )
    if level == "MOCK":
        return (
            '"""Synthetic fixture module (planted MOCK)."""\n\n'
            "import random\n\n\n"
            f"def {fn}(x):\n"
            '    """Returns a fabricated value instead of computing."""\n'
            "    return x + random.uniform(0.0, 1.0)\n"
        )
    return (
        '"""Synthetic fixture module (planted substantive implementation)."""\n\n\n'
        f"def {fn}(x):\n"
        '    """Deterministic affine transform."""\n'
        "    return 2.0 * x + 1.0\n"
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _plant(repo: Path, case: FixtureCase) -> None:
    """按 variant 把一套证据写进合成仓库。"""
    pat = case.pattern
    if case.variant == "backend_root":
        code_rel = Path("backend") / "modules" / f"{pat}.py"
        sys_path_hint = 'ROOT / "backend" / "modules"'
    else:
        code_rel = Path("src") / f"{pat}.py"
        sys_path_hint = 'ROOT / "src"' 

    if case.truth == "NOT_SUPPORTED":
        return  # 什么都不放：无代码/测试/报告/五元痕迹

    _write(repo / code_rel, _module_body(pat, case.truth))

    # 理论证据（docs/ 下，含推导标记词）
    _write(repo / "docs" / f"{pat}.md",
           f"# {pat}\n\nScaling equation / algorithm derivation for {pat}.\n")

    # 配置与数据证据
    _write(repo / "config" / f"{pat}.json", json.dumps({"capability": pat}))
    _write(repo / "data" / f"{pat}.json", json.dumps({"capability": pat, "rows": 3}))

    if case.truth == "STUB":
        return  # 只有接口声明：无测试、无报告

    if case.truth == "REAL":
        _write(repo / "tests" / f"test_{pat}.py", f'''"""Synthetic fixture tests for {pat}."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str({sys_path_hint}))
sys.path.insert(0, str(ROOT / "src"))

from {pat} import {pat}_compute  # noqa: E402


def test_affine_value():
    assert {pat}_compute(2.0) == 5.0


def test_affine_identity():
    assert {pat}_compute(0.0) == 1.0
''')
        _write(repo / "tensor_reports" / f"{pat}.json", json.dumps({
            "scenario_key": case.capability_id,
            "verdict": "PASS",
            "created_at": "2026-01-01T00:00:00+00:00",
            "capability": pat,
        }))
    elif case.truth == "MOCK":
        _write(repo / "tensor_reports" / f"{pat}.json", json.dumps({
            "scenario_key": case.capability_id,
            "verdict": "MOCK",
            "created_at": "2026-01-01T00:00:00+00:00",
            "capability": pat,
        }))
    elif case.truth == "WITHHELD":
        pass  # 有实现、有配置数据，但无测试、无报告 → 证据不足


def build_fixture_repo(
    dest: Path | str, replicas: int = 3, include_variants: bool = True,
) -> tuple[Path, Path, list[FixtureCase]]:
    """构造合成算例仓库；返回 ``(repo_root, registry_path, cases)``。"""
    repo = Path(dest)
    if repo.exists():
        shutil.rmtree(repo)
    (repo / "src").mkdir(parents=True)
    cases = fixture_cases(replicas=replicas, include_variants=include_variants)

    caps: list[dict[str, Any]] = []
    for case in cases:
        pat = case.pattern
        caps.append({
            "capability_id": case.capability_id,
            "label": case.capability_id,
            "description": "synthetic labelled fixture (no domain semantics)",
            "expected_evidence_types": ["theory", "code", "config", "data", "report"],
            "code_patterns": [pat],
            "test_patterns": [f"test_{pat}"],
            "report_patterns": [pat],
            "initial_level": "WITHHELD",
        })
        _plant(repo, case)

    registry_path = repo / "registry.json"
    _write(registry_path, json.dumps(
        {"schema_version": "1.0", "title": "synthetic fixture registry",
         "description": "labelled fixtures for grader eval validity",
         "capabilities": caps},
        indent=2, sort_keys=True,
    ))
    return repo, registry_path, cases


# ---------------------------------------------------------------- 度量
def _safe_div(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def confusion_matrix(pairs: Iterable[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """``truth -> predicted -> count``（缺格补 0）。"""
    matrix = {t: {p: 0 for p in LEVELS} for t in LEVELS}
    for truth, pred in pairs:
        matrix.setdefault(truth, {p: 0 for p in LEVELS})
        matrix[truth][pred] = matrix[truth].get(pred, 0) + 1
    return matrix


def per_level_metrics(matrix: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    """逐级 recall / precision / F1。"""
    out: dict[str, dict[str, float]] = {}
    for level in LEVELS:
        tp = matrix.get(level, {}).get(level, 0)
        support = sum(matrix.get(level, {}).values())
        predicted = sum(matrix.get(t, {}).get(level, 0) for t in matrix)
        out[level] = {
            "support": support,
            "predicted": predicted,
            "tp": tp,
            "recall": _safe_div(tp, support),
            "precision": _safe_div(tp, predicted),
            "f1": _safe_div(2 * tp, support + predicted),
        }
    return out


def evaluate(
    repo_root: Path | str,
    registry_path: Path | str,
    cases: list[FixtureCase],
    exec_tests: bool = True,
    exec_timeout_s: int = 120,
    exec_budget_s: int = 900,
    code_roots: Iterable[str] | None = None,
) -> dict[str, Any]:
    """在合成算例仓库上跑真实扫描 + 判级，返回混淆矩阵与逐级指标。

    ``code_roots`` 用于**复现修复前的对照基线**（例如 ``("src",)`` 即修复前
    的扫描根），同一套算例上前后对比才能算"改进"而不是"变了"。
    """
    repo = Path(repo_root)
    registry = CapabilityRegistry.from_json(registry_path)
    scanner = EvidenceScanner(
        repo, registry, exec_tests=exec_tests,
        exec_timeout_s=exec_timeout_s, exec_budget_s=exec_budget_s,
        code_roots=code_roots,
    )
    grader = GradeDeterminer(repo)
    registry_sha = _file_sha(repo / registry_path)
    truth = {c.capability_id: c for c in cases}

    rows: list[dict[str, Any]] = []
    for cap in registry.capabilities.values():
        scan = scanner.scan_capability(cap)
        chain = build_evidence_chain(cap, scan, repo, exclude_paths=[str(registry_path)])
        result = grader.determine(chain)
        record = build_record(chain, result, registry_sha256=registry_sha)
        case = truth.get(cap.capability_id)
        rows.append({
            **record,
            "truth": case.truth if case else "",
            "variant": case.variant if case else "",
            "match": bool(case and result.grade == case.truth),
            "reasoning": list(result.reasoning),
            "code_count": scan.code_count,
            "stub_only_code_count": scan.stub_only_code_count,
            "test_evidence_source": scan.test_evidence_source,
            "fingerprint_valid": True,
        })

    pairs = [(r["truth"], r["grade"]) for r in rows]
    matrix = confusion_matrix(pairs)
    metrics = per_level_metrics(matrix)
    n = len(rows)
    correct = sum(1 for r in rows if r["match"])

    by_variant: dict[str, dict[str, int]] = {}
    for r in rows:
        v = by_variant.setdefault(r["variant"], {"total": 0, "correct": 0})
        v["total"] += 1
        v["correct"] += 1 if r["match"] else 0

    return {
        "schema": "eval-validity/v1",
        "repo": str(repo),
        "registry_sha256": registry_sha,
        "exec_tests": exec_tests,
        "code_roots": list(DEFAULT_CODE_ROOTS if code_roots is None else code_roots),
        "replicas": sorted({c.capability_id.rsplit('.', 1)[-1] for c in cases}),
        "case_count": n,
        "accuracy": _safe_div(correct, n),
        "macro_recall": round(
            sum(m["recall"] for m in metrics.values()) / len(LEVELS), 4,
        ),
        "confusion_matrix": matrix,
        "per_level": metrics,
        "by_variant": by_variant,
        "mismatches": [
            {"capability_id": r["capability_id"], "truth": r["truth"], "predicted": r["grade"],
             "variant": r["variant"], "reasoning": r["reasoning"][:3]}
            for r in rows if not r["match"]
        ],
        "rows": rows,
    }


def _file_sha(path: Path) -> str:
    from .scanner import sha256_file
    try:
        return sha256_file(Path(path))
    except OSError:
        return ""


def summarize(result: dict[str, Any]) -> str:
    """人读摘要（CLI 用）。"""
    lines = [
        f"cases={result['case_count']} accuracy={result['accuracy']:.3f} "
        f"macro_recall={result['macro_recall']:.3f}",
    ]
    for level in LEVELS:
        m = result["per_level"][level]
        lines.append(
            f"  {level:14s} support={m['support']:2d} recall={m['recall']:.2f} "
            f"precision={m['precision']:.2f} f1={m['f1']:.2f}"
        )
    for v, s in sorted(result["by_variant"].items()):
        lines.append(f"  variant {v:14s} {s['correct']}/{s['total']} correct")
    return "\n".join(lines)


def write_artifact(result: dict[str, Any], out_dir: Path | str,
                   name: str = "eval_validity.json") -> dict[str, str]:
    """落盘度量产物 + sha256 旁车（内容确定性：不含墙钟时间）。"""
    from .scanner import sha256_file

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in result.items() if k != "repo"}
    target = out / name
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    digest = sha256_file(target)
    (out / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
    return {"json": str(target), "sha256": digest,
            "sidecar": str(out / f"{name}.sha256")}


__all__ = [
    "LEVELS",
    "FixtureCase",
    "fixture_cases",
    "build_fixture_repo",
    "evaluate",
    "confusion_matrix",
    "per_level_metrics",
    "summarize",
    "write_artifact",
]