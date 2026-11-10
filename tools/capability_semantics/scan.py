"""delivery 层扫描：把「仓内可复核交付物」抽取成 ``DeliveryArtifact``。

扫描器可注入（``scanner=``），默认复用仓内既有三源扫描内核
``tools.audit.capability_audit_scan.scan_worktree``（它已实现：一次遍历、
四类证据、命中行 + 文件 sha256、数值判据与失败词判决）。本模块只做**语义分层
所需的归一**：把扫描记录映射到 delivery / evidence 两层，并补端点计数。

零领域语义：关键词表来自注入的配置（``config/capabilities.json`` 的
``code_patterns``/``test_patterns``），代码里不出现任何能力 id 或域词条。
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .model import DeliveryArtifact, EvidenceRecord
from .sources import EndpointModule, SemanticsConfig, file_sha256

Scanner = Callable[[Path, Mapping[str, Any]], Mapping[str, Mapping[str, list[dict]]]]
_PROFILE_CACHE: dict[str, dict[str, Any]] = {}


def load_profiles(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    """从配置 JSON 读取每能力的扫描关键词 → 扫描器 profile。

    兼容两种形状：``{"capabilities": [{"capability_id", "code_patterns", ...}]}``
    与 ``{cid: {"keywords": [...]}}``。无文件 → 空表（扫描器用自身默认 profile）。
    """
    profiles: dict[str, dict[str, Any]] = {}
    for path in paths:
        if path is None or not Path(path).exists():
            continue
        key = str(path)
        if key in _PROFILE_CACHE:
            profiles.update(_PROFILE_CACHE[key])
            continue
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        built: dict[str, dict[str, Any]] = {}
        if isinstance(payload, dict) and isinstance(payload.get("capabilities"), list):
            for rec in payload["capabilities"]:
                if not isinstance(rec, dict):
                    continue
                cid = rec.get("capability_id") or rec.get("id")
                if not cid:
                    continue
                words: list[str] = []
                for field in ("code_patterns", "test_patterns", "report_patterns"):
                    for w in rec.get(field) or []:
                        if isinstance(w, str) and w and w not in words:
                            words.append(w)
                if not words:
                    continue
                built[str(cid)] = {
                    "domain": str(rec.get("domain", "")),
                    "keywords": tuple(words),
                }
        elif isinstance(payload, dict):
            for cid, rec in payload.items():
                if isinstance(rec, dict) and isinstance(rec.get("keywords"), list):
                    built[str(cid)] = dict(rec)
        _PROFILE_CACHE[key] = built
        profiles.update(built)
    return profiles


SCAN_KERNEL_NAME = "_tp_capability_audit_scan"


def load_scan_kernel():
    """按**文件路径**加载仓内三源扫描内核。

    之所以不用 ``import tools.audit.capability_audit_scan``：本仓存在
    ``backend/tools``，服务进程里 ``sys.path`` 先命中 backend 时 ``tools``
    会被影子包抢占（能力 API 正是跑在这种进程里）。按路径加载 ⇒ 与 sys.path 无关。
    """
    if SCAN_KERNEL_NAME in sys.modules:
        return sys.modules[SCAN_KERNEL_NAME]
    kernel = Path(__file__).resolve().parents[1] / "audit" / "capability_audit_scan.py"
    if not kernel.exists():
        raise ImportError(f"scan kernel not found: {kernel}")
    spec = importlib.util.spec_from_file_location(SCAN_KERNEL_NAME, kernel)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load scan kernel: {kernel}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[SCAN_KERNEL_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(SCAN_KERNEL_NAME, None)
        raise
    return mod


def default_scanner(root: Path, profiles: Mapping[str, Any]) -> Mapping[str, Mapping[str, list[dict]]]:
    """默认扫描器：惰性加载仓内三源扫描内核（避免 import 期硬依赖）。"""
    cas = load_scan_kernel()
    return cas.scan_worktree(root, profiles=dict(profiles) or None)


def _count_endpoints(text: str, deco: re.Pattern[str]) -> int:
    return len(deco.findall(text))


def _decorator_re(cfg: SemanticsConfig) -> re.Pattern[str]:
    pat = str(cfg.discovery.get("endpoint_decorator_regex") or "")
    try:
        return re.compile(pat) if pat else re.compile(r"@(?:router|app)\.(?:get|post|put|patch|delete)\(")
    except re.error:
        return re.compile(r"@(?:router|app)\.(?:get|post|put|patch|delete)\(")


def build_deliveries(
    root: Path,
    cfg: SemanticsConfig,
    capability_ids: Iterable[str],
    profiles: Mapping[str, Any] | None = None,
    scanner: Scanner | None = None,
    endpoint_modules: Sequence[EndpointModule] | None = None,
) -> tuple[dict[str, list[DeliveryArtifact]], dict[str, list[EvidenceRecord]], list[str]]:
    """扫描仓内交付物。

    Returns
    -------
    (deliveries_by_capability, evidences_by_capability, warnings)
        ``warnings`` 是扫描降级原因（如扫描器不可用），显式记入报告，
        不静默返回空证据（空证据 ≠ 无交付）。
    """
    ids = [str(i) for i in capability_ids]
    warnings: list[str] = []
    prof = dict(profiles or {})
    if not prof:
        warnings.append("empty_profile_table: 扫描关键词表为空，交付发现可能不全")
    scan = scanner or default_scanner
    try:
        raw = scan(root, prof)
    except Exception as exc:  # noqa: BLE001 - 扫描器故障必须显式降级
        warnings.append(f"scanner_failed: {type(exc).__name__}: {exc}")
        raw = {}

    exclude = [str(g) for g in (cfg.discovery.get("exclude_glob_patterns") or [])]

    def _excluded(path: str) -> bool:
        return any(fnmatch.fnmatch(path, pat) for pat in exclude)

    deco = _decorator_re(cfg)
    ep_count: dict[str, int] = {m.path: m.endpoint_count for m in (endpoint_modules or [])}
    text_cache: dict[str, str] = {}

    def _endpoints(path: str) -> int:
        if path in ep_count:
            return ep_count[path]
        if path not in text_cache:
            try:
                text_cache[path] = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text_cache[path] = ""
        return _count_endpoints(text_cache[path], deco)

    deliveries: dict[str, list[DeliveryArtifact]] = {cid: [] for cid in ids}
    evidences: dict[str, list[EvidenceRecord]] = {cid: [] for cid in ids}
    seen: set[tuple[str, str, str]] = set()

    for cid in ids:
        bucket = raw.get(cid) or {}
        for kind in ("code", "test", "verified", "doc"):
            for rec in bucket.get(kind) or []:
                if not isinstance(rec, dict):
                    continue
                path = str(rec.get("path") or "")
                if not path:
                    continue
                if _excluded(path):
                    continue  # 自指证据治理：本节点自己的产物/文档不算能力证据
                key = (cid, kind, path)
                if key in seen:
                    continue
                seen.add(key)
                sha = str(rec.get("sha256") or file_sha256(root / path))
                art = DeliveryArtifact(
                    capability_id=cid,
                    kind=kind,
                    path=path,
                    sha256=sha,
                    line=int(rec.get("line") or 0),
                    quote=str(rec.get("quote") or "")[:200],
                    endpoint_count=_endpoints(path) if kind in ("code", "verified") else 0,
                    numeric_asserts=int(rec.get("numeric_asserts") or 0),
                    stub_markers=tuple(rec.get("stub_markers") or ()),
                    mock_markers=tuple(rec.get("mock_markers") or ()),
                    numeric_pass=(bool(rec["pass"]) if "pass" in rec else None),
                    numeric_error_pct=(float(rec["min_error_pct"])
                                        if isinstance(rec.get("min_error_pct"), (int, float))
                                        else None),
                    subject_hit=bool(rec.get("subject_hit", True)),
                )
                deliveries[cid].append(art)
                if kind == "verified" and "pass" in rec:
                    evidences[cid].append(EvidenceRecord(
                        source="verified_artifact",
                        ref=path,
                        stance="supports_real" if rec.get("pass") else "neutral",
                        asserted_level="REAL" if rec.get("pass") else None,
                        note=(f"min_error_pct={rec.get('min_error_pct')} "
                              f"fail_quote={rec.get('fail_quote')} "
                              f"negative_verdict={rec.get('negative_verdict')}"),
                        sha256=sha,
                        path=path,
                    ))
        deliveries[cid].sort(key=lambda d: (d.kind, d.path, d.line))
        evidences[cid].sort(key=lambda e: (e.source, e.ref))
    if exclude:
        warnings.append("excluded_globs: " + "|".join(exclude))
    return deliveries, evidences, warnings


def orphan_endpoint_modules(
    capability_deliveries: Mapping[str, Sequence[DeliveryArtifact]],
    endpoint_modules: Sequence[EndpointModule],
) -> list[EndpointModule]:
    """端点模块中未被任何能力条目绑定者 = delivery 层孤立节点（账上无条目）。"""
    bound = {d.path for arts in capability_deliveries.values() for d in arts}
    return [m for m in endpoint_modules if m.path not in bound]


__all__ = [
    "Scanner",
    "load_profiles",
    "default_scanner",
    "build_deliveries",
    "orphan_endpoint_modules",
]