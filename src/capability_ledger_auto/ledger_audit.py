"""台账审计产物（ledger audit artifact）与升账流水线。

产物纪律
--------
* 产物文件内**不含墙钟时间戳**：同一仓库连续两次扫描字节一致（战役判据 AC4）。
* 文件内自带 ``content_digest``（对规范化内容求 sha256）、``git_commit``、
  ``registry_sha256``；旁车文件 ``ledger_audit.sha256`` 给出整文件 sha256（AC6）。
* 时间/耗时类易变量写入 ``run_metrics.json``，与审计产物分离，不参与摘要。

零领域语义：产物字段只来自注册表与扫描证据，不含任何域词条。
"""
from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404 - 读取 git 元数据
import time
from pathlib import Path
from typing import Any, Iterable

from .evidence_chain import EvidenceChain, build_evidence_chain
from .fingerprint import FINGERPRINT_SCHEMA, fingerprint_chain, record_digest
from .provenance import canonical_json, sha256_text
from .grader import DEFAULT_POLICY, GradeDeterminer, GradeResult
from .registry import CapabilityRegistry, LEVELS
from .scanner import DEFAULT_EXCLUDE_PREFIXES, PASSING_LEVELS, EvidenceScanner, sha256_file

SCHEMA_VERSION = "1.0"
DEFAULT_CORPUS = "tests/fixtures/capability_ledger/reports_corpus.json"
DEFAULT_ADJUDICATION_LEDGER = "artifacts/capability_ledger/capability_ledger.json"
DEFAULT_MANUAL_LEDGER = "artifacts/capability_ledger/capability_ledger_resolved.json"
AUDIT_DIR = "artifacts/capability_ledger_auto"


# ------------------------------------------------------------------ 工具

def git_commit(root: Path | str = ".") -> str:
    """当前 commit sha（含 ``-dirty`` 后缀标记工作树未提交状态）。"""
    try:
        sha = subprocess.run(  # nosec B603,B607
            ["git", "rev-parse", "HEAD"], cwd=str(root),
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.strip()
        dirty = subprocess.run(  # nosec B603,B607
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=str(root),
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def canonical_json(obj: Any) -> str:
    """规范化 JSON（排序键、无多余空格、UTF-8 原样）：用于稳定摘要。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_digest(payload: dict[str, Any], exclude_keys: Iterable[str] = ("content_digest",)) -> str:
    """对产物的确定性内容求 sha256（剔除摘要字段自身与易变字段）。"""
    volatile = set(exclude_keys) | {"generated_at", "run_metrics"}
    body = {k: v for k, v in payload.items() if k not in volatile}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def load_manual_reference(path: Path | str) -> dict[str, dict[str, Any]]:
    """加载人工裁决台账（resolved 格式）：{capability_id: {claimed, reconciled}}。

    返回空字典表示"无人工基准"（调用方必须如实标注，不得伪造比对结果）。
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    caps = raw.get("capabilities")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(caps, list):
        for c in caps:
            cid = c.get("id") or c.get("capability_id")
            if cid:
                out[cid] = {
                    "claimed_level": c.get("claimed_level", ""),
                    "reconciled_level": c.get("reconciled_level", ""),
                }
    elif isinstance(caps, dict):
        for cid, c in caps.items():
            out[cid] = {
                "claimed_level": c.get("claimed_level", ""),
                "reconciled_level": c.get("reconciled_level", ""),
            }
    return out


# ------------------------------------------------------------------ 流水线

def grade_capabilities(
    root: Path,
    registry: CapabilityRegistry,
    *,
    exec_tests: bool = False,
    corpus_paths: Iterable[str | Path] = (),
    adjudication_ledgers: Iterable[str | Path] = (),
    exclude_prefixes: Iterable[str] = DEFAULT_EXCLUDE_PREFIXES,
    exec_candidates: Iterable[str] | None = None,
    max_exec_files: int = 3,
    exec_timeout_s: int = 30,
    exec_budget_s: int = 420,
    test_cache: dict[str, dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    code_roots: Iterable[str] | None = None,
) -> tuple[dict[str, EvidenceChain], dict[str, GradeResult], dict[str, Any]]:
    """扫描 + 建链 + 判级；返回 (chains, grades, scanner_ledger)。"""
    root = Path(root)
    scanner = EvidenceScanner(
        root, registry,
        exec_tests=exec_tests,
        max_exec_files=max_exec_files,
        exec_timeout_s=exec_timeout_s,
        exec_budget_s=exec_budget_s,
        corpus_paths=corpus_paths,
        adjudication_ledgers=adjudication_ledgers,
        exclude_prefixes=exclude_prefixes,
        test_cache=test_cache,
        exec_only=exec_candidates,
        code_roots=code_roots,
    )
    scans = scanner.scan_all()
    exclude_for_chain = tuple(exclude_prefixes) + (registry.source_path,)
    chains = {
        cid: build_evidence_chain(registry.get(cid), scan, root, exclude_paths=exclude_for_chain)
        for cid, scan in scans.items()
    }
    determiner = GradeDeterminer(root=root, policy=policy)
    grades = determiner.determine_all(chains)
    ledger = {
        "exec_ledger": scanner.exec_ledger,
        "exec_budget_left_s": scanner._budget_left,
        "test_cache_size": len(scanner.test_cache),
    }
    return chains, grades, ledger


def select_exec_candidates(chains: dict[str, EvidenceChain]) -> list[str]:
    """只对"可能升到 REAL"的能力执行测试。

    判据：五元齐全 + 报告最新裁决为 PASS/REAL + 存在实质实现。
    其余能力执行测试也无法改变其等级，故不花算力（省时间且不改变判定）。
    """
    out = []
    for cid, chain in chains.items():
        scan = chain.scan_result
        if chain.five_tuple.present_count >= 5 \
                and scan.adjudicated_level in PASSING_LEVELS \
                and scan.substantive_code_count > 0:
            out.append(cid)
    return sorted(out)


def build_payload(
    root: Path,
    registry: CapabilityRegistry,
    chains: dict[str, EvidenceChain],
    grades: dict[str, GradeResult],
    manual_reference: dict[str, dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
    test_cache: dict[str, dict[str, Any]] | None = None,
    sources: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """构建确定性审计产物（无时间戳）。"""
    root = Path(root)
    registry_sha = sha256_file(Path(registry.source_path)) if Path(registry.source_path).exists() else ""
    # 产物里存**相对路径**：绝对路径会让 content_digest 变成机器相关，跨机器无法复算
    registry_src = str(registry.source_path)
    try:
        registry_src = str(Path(registry_src).resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        pass
    capabilities: dict[str, Any] = {}
    for cid in sorted(grades):
        g = grades[cid]
        chain = chains[cid]
        chain_dict = chain.to_dict()
        manual = manual_reference.get(cid, {})
        fp = fingerprint_chain(chain, registry_sha256=registry_sha)
        capabilities[cid] = {
            "grade": g.grade,
            "confidence": round(g.confidence, 4),
            "reasoning": list(g.reasoning),
            "evidence_summary": g.evidence_summary,
            "five_tuple": chain_dict["five_tuple"],
            "chain_quality": chain_dict["chain_quality"],
            "manual_reference": manual,
            "manual_match": (manual.get("reconciled_level") == g.grade) if manual.get("reconciled_level") else None,
            "test_cache_keys": sorted(
                t.file_path for t in chain.scan_result.test_evidence if t.source == "executed"
            ),
            # 证据指纹：sha256 溯源链根 + 叶子规模 + 被有界截断的条数
            "evidence_fingerprint": {
                "schema": FINGERPRINT_SCHEMA,
                "root": fp.root,
                "leaf_count": fp.leaf_count,
                "truncated_by_kind": dict(fp.truncated_by_kind),
                "registry_sha256": registry_sha,
            },
            "record_digest": record_digest({
                "capability_id": cid,
                "registry_sha256": registry_sha,
                "grade": g.grade,
                "confidence": round(g.confidence, 4),
                "evidence_root": fp.root,
                "leaf_count": fp.leaf_count,
            }),
        }

    # 整个台账的证据状态摘要：任何一条能力的证据或判级变化都会改这个值
    roots_digest = sha256_text(canonical_json(
        [[cid, capabilities[cid]["evidence_fingerprint"]["root"]] for cid in capabilities]
    ))

    tally = {lv: 0 for lv in LEVELS}
    for cid, cap in capabilities.items():
        tally[cap["grade"]] = tally.get(cap["grade"], 0) + 1

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evidence_state_digest": roots_digest,
        "engine": "capability_ledger_auto",
        "repo": "TensorPlatform",
        "git_commit": git_commit(root),
        "evidence_sources": {
            "corpus": sorted((sources or {}).get("corpus", [])),
            "adjudication_ledgers": sorted((sources or {}).get("adjudication_ledgers", [])),
            "manual_reference": (sources or {}).get("manual_reference", ""),
        },
        "registry": {
            "source_path": registry_src,
            "sha256": registry_sha,
            "capability_count": registry.count(),
        },
        "policy": dict(policy or DEFAULT_POLICY),
        "summary": {
            "total_capabilities": len(capabilities),
            "grade_tally": tally,
            "executed_test_files": sum(
                1 for c in capabilities.values() for _ in c["test_cache_keys"]
            ),
        },
        "capabilities": capabilities,
    }
    payload["content_digest"] = content_digest(payload)
    return payload


def write_artifact(out_dir: Path | str, payload: dict[str, Any]) -> dict[str, str]:
    """落盘 ledger_audit.json + sha256 旁车 + markdown 表；返回路径字典。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ledger_audit.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    file_sha = sha256_file(json_path)
    sha_path = out_dir / "ledger_audit.sha256"
    sha_path.write_text(
        f"{file_sha}  ledger_audit.json\n"
        f"# content_digest {payload.get('content_digest', '')}\n"
        f"# git_commit {payload.get('git_commit', '')}\n"
        f"# registry_sha256 {payload.get('registry', {}).get('sha256', '')}\n",
        encoding="utf-8",
    )
    md_path = out_dir / "ledger_auto_table.md"
    write_markdown(md_path, payload)
    return {"json": str(json_path), "sha256": str(sha_path), "markdown": str(md_path),
            "file_sha256": file_sha}


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# 能力台账自动升账（ledger auto-upgrade）",
        "",
        f"- git_commit: `{payload.get('git_commit')}`",
        f"- registry: `{payload['registry']['source_path']}` sha256=`{payload['registry']['sha256'][:16]}…`",
        f"- content_digest: `{payload.get('content_digest')}`",
        "",
        "| 能力ID | 自动等级 | 人工裁决 | 一致 | 测试证据 | 报告最新裁决 |",
        "|---|---|---|---|---|---|",
    ]
    for cid, cap in payload["capabilities"].items():
        es = cap["evidence_summary"]
        src = es.get("test_evidence_source") or "none"
        lines.append(
            f"| {cid} | **{cap['grade']}** | {cap['manual_reference'].get('reconciled_level') or 'n/a'} | "
            f"{'✅' if cap['manual_match'] else ('—' if cap['manual_match'] is None else '❌')} | "
            f"{src} {es.get('test_pass_rate', 0):.2f} | {es.get('adjudicated_level') or 'none'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(
    root: Path | str = ".",
    registry_path: str = "config/capabilities.json",
    out_dir: str | Path = AUDIT_DIR,
    *,
    corpus_paths: Iterable[str | Path] = (DEFAULT_CORPUS,),
    adjudication_ledgers: Iterable[str | Path] = (DEFAULT_ADJUDICATION_LEDGER,),
    manual_ledger: str | Path = DEFAULT_MANUAL_LEDGER,
    exec_tests: bool = True,
    policy: dict[str, Any] | None = None,
    test_cache_path: str | Path | None = None,
    write: bool = True,
    code_roots: Iterable[str] | None = None,
) -> dict[str, Any]:
    """完整审计：静态扫描 → 候选能力真实执行 → 判级 → 落盘。"""
    root = Path(root)
    registry = CapabilityRegistry.from_json(root / registry_path if not Path(registry_path).is_absolute() else registry_path)
    corpora = [Path(c) if Path(c).is_absolute() else root / c for c in corpus_paths]
    corpora = [c for c in corpora if c.exists()]
    adj_ledgers = [Path(c) if Path(c).is_absolute() else root / c for c in adjudication_ledgers]
    adj_ledgers = [c for c in adj_ledgers if c.exists()]

    cache: dict[str, dict[str, Any]] = {}
    if test_cache_path:
        cache_path = Path(test_cache_path) if Path(test_cache_path).is_absolute() else root / test_cache_path
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cache = {}
    else:
        cache_path = None

    t0 = time.time()
    base_chains, base_grades, base_ledger = grade_capabilities(
        root, registry, exec_tests=False, corpus_paths=corpora,
        adjudication_ledgers=adj_ledgers, test_cache=cache, policy=policy,
        code_roots=code_roots,
    )
    static_elapsed = time.time() - t0

    candidates = select_exec_candidates(base_chains) if exec_tests else []
    exec_elapsed = 0.0
    exec_ledger: dict[str, Any] = {}
    if candidates:
        t1 = time.time()
        exec_chains, exec_grades, exec_ledger = grade_capabilities(
            root, registry, exec_tests=True, corpus_paths=corpora,
            adjudication_ledgers=adj_ledgers, exec_candidates=candidates,
            test_cache=cache, policy=policy, code_roots=code_roots,
        )
        exec_elapsed = time.time() - t1
        for cid in candidates:
            base_chains[cid] = exec_chains[cid]
            base_grades[cid] = exec_grades[cid]

    payload = build_payload(
        root, registry, base_chains, base_grades,
        load_manual_reference(root / manual_ledger if not Path(manual_ledger).is_absolute() else manual_ledger),
        policy=policy, test_cache=cache,
        sources={
            "corpus": [str(c.relative_to(root)) if c.is_relative_to(root) else str(c) for c in corpora],
            "adjudication_ledgers": [str(c.relative_to(root)) if c.is_relative_to(root) else str(c) for c in adj_ledgers],
            "manual_reference": str(manual_ledger),
        },
    )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")

    result: dict[str, Any] = {"payload": payload, "paths": {}, "metrics": {
        "static_scan_s": round(static_elapsed, 2),
        "exec_s": round(exec_elapsed, 2),
        "exec_candidates": candidates,
        "static_ledger": base_ledger,
        "exec_ledger": exec_ledger,
    }}
    if write:
        result["paths"] = write_artifact(root / out_dir, payload)
        metrics_path = root / out_dir / "run_metrics.json"
        metrics_path.write_text(
            json.dumps(result["metrics"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result["paths"]["metrics"] = str(metrics_path)
    return result