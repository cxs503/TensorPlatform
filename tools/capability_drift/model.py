"""能力注册表模型：能力 → 验证案例 → 判据(gates) → 基线(baseline) → 豁免白名单。

注册表目录布局（全部 JSON，可由任意领域仓注入，本仓只给合成示例）::

    capdrift/
      registry.json    # 能力、案例（pytest node id）、触发路径、blame 定位点
      gates.json       # 每能力每指标的相对误差阈值（可调）
      baseline.json    # 基线数字 + 更新审计（update_baseline 写入）
      whitelist.json   # 已知退化的显式豁免（须 owner 签名审批）

**铁律**：本模块不认识任何物理量名，metric 只是字符串键。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

DEFAULT_REL_TOL = 0.05  # 战役判据 A6 的默认阈值：相对误差 5%


class RegistryError(ValueError):
    """注册表结构非法。"""


# --------------------------------------------------------------------------- #
# 结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Case:
    """一条验证案例：pytest node id（相对仓库根）+ 它负责记录的数字名。"""

    id: str
    node: str
    metrics: Tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return self.node.split("::", 1)[0]


@dataclass(frozen=True)
class Gate:
    metric: str
    rel_tol: float = DEFAULT_REL_TOL
    abs_floor: float = 1e-12


@dataclass(frozen=True)
class BlamePattern:
    """退化定位点：``path`` 中匹配 ``pattern`` 的行，其 blame 提交即嫌疑 commit。"""

    path: str
    pattern: str


@dataclass(frozen=True)
class Capability:
    id: str
    solver: str
    cases: Tuple[Case, ...]
    metrics: Tuple[str, ...]
    gates: Tuple[Gate, ...]
    blame: Tuple[BlamePattern, ...] = ()
    paths: Tuple[str, ...] = ()

    def gate(self, metric: str) -> Optional[Gate]:
        for g in self.gates:
            if g.metric == metric:
                return g
        return None

    def trigger_paths(self) -> Tuple[str, ...]:
        """所有能影响本能力的路径（触发判定用）。"""
        seen: List[str] = []
        for p in self.paths + tuple(c.path for c in self.cases) + tuple(b.path for b in self.blame):
            if p and p not in seen:
                seen.append(p)
        return tuple(seen)


@dataclass(frozen=True)
class Waiver:
    """已知退化的显式豁免：未签名/过期/超限的豁免一律不生效（fail-closed）。"""

    capability: str
    metric: Optional[str]
    max_rel_err: Optional[float]
    reason: str
    approver: str
    approved_at: str
    expires_at: Optional[str] = None
    signature: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Bundle:
    """一次加载得到的完整注册表（带文件指纹，便于审计链）。"""

    root: Path
    repo_root: Path
    capabilities: Tuple[Capability, ...]
    baseline: Mapping[str, Mapping[str, float]]
    whitelist: Tuple[Waiver, ...]
    file_sha256: Mapping[str, str]
    baseline_meta: Mapping[str, Any] = field(default_factory=dict)

    def capability(self, cap_id: str) -> Optional[Capability]:
        for c in self.capabilities:
            if c.id == cap_id:
                return c
        return None

    @property
    def ids(self) -> Tuple[str, ...]:
        return tuple(c.id for c in self.capabilities)


# --------------------------------------------------------------------------- #
# 读写工具
# --------------------------------------------------------------------------- #
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def file_sha256_map(root: Path, names: Sequence[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in names:
        p = Path(root) / name
        if p.exists():
            out[name] = sha256_file(p)
    return out


def _read_json(path: Path, *, required: bool) -> Dict[str, Any]:
    if not path.exists():
        if required:
            raise RegistryError(f"missing registry file: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 结构错误路径
        raise RegistryError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError(f"{path} must contain a JSON object")
    return data


REGISTRY_FILES = ("registry.json", "gates.json", "baseline.json", "whitelist.json")


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _parse_capability(raw: Mapping[str, Any]) -> Capability:
    cap_id = str(raw.get("id") or "").strip()
    if not cap_id:
        raise RegistryError("capability without id")
    solver = str(raw.get("solver") or "").strip() or "unknown"
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise RegistryError(f"{cap_id}: at least one verification case required")
    cases: List[Case] = []
    for rc in raw_cases:
        if not isinstance(rc, dict) or not rc.get("node"):
            raise RegistryError(f"{cap_id}: case needs 'node'")
        cases.append(
            Case(
                id=str(rc.get("id") or rc["node"]),
                node=str(rc["node"]),
                metrics=tuple(str(m) for m in rc.get("metrics", ())),
            )
        )
    metrics = [str(m) for m in raw.get("metrics", ())]
    for c in cases:
        for m in c.metrics:
            if m not in metrics:
                metrics.append(m)
    if not metrics:
        raise RegistryError(f"{cap_id}: no metrics declared")
    blame = tuple(
        BlamePattern(path=str(b["path"]), pattern=str(b["pattern"]))
        for b in raw.get("blame", ())
        if isinstance(b, dict) and b.get("path") and b.get("pattern")
    )
    paths = tuple(str(p) for p in raw.get("paths", ()) if p)
    return Capability(
        id=cap_id,
        solver=solver,
        cases=tuple(cases),
        metrics=tuple(dict.fromkeys(metrics)),
        gates=(),  # gates 从 gates.json 注入
        blame=blame,
        paths=paths,
    )


def _attach_gates(caps: Iterable[Capability], gates_raw: Mapping[str, Any]) -> Tuple[Capability, ...]:
    defaults = gates_raw.get("defaults") or {}
    default_rel = float(defaults.get("rel_tol", DEFAULT_REL_TOL))
    default_floor = float(defaults.get("abs_floor", 1e-12))
    per_cap = gates_raw.get("capabilities") or {}
    out: List[Capability] = []
    for cap in caps:
        spec = per_cap.get(cap.id) or {}
        gates: List[Gate] = []
        for metric in cap.metrics:
            g = spec.get(metric) or {}
            gates.append(
                Gate(
                    metric=metric,
                    rel_tol=float(g.get("rel_tol", default_rel)),
                    abs_floor=float(g.get("abs_floor", default_floor)),
                )
            )
        for metric in spec:
            if metric not in cap.metrics:
                raise RegistryError(f"{cap.id}: gate for undeclared metric {metric!r}")
        for g in gates:
            if g.rel_tol <= 0:
                raise RegistryError(f"{cap.id}.{g.metric}: rel_tol must be > 0")
        out.append(
            Capability(
                id=cap.id,
                solver=cap.solver,
                cases=cap.cases,
                metrics=cap.metrics,
                gates=tuple(gates),
                blame=cap.blame,
                paths=cap.paths,
            )
        )
    return tuple(out)


def _parse_whitelist(raw: Mapping[str, Any]) -> Tuple[Waiver, ...]:
    items = raw.get("waivers") or []
    out: List[Waiver] = []
    for w in items:
        if not isinstance(w, dict) or not w.get("capability"):
            continue
        out.append(
            Waiver(
                capability=str(w["capability"]),
                metric=(str(w["metric"]) if w.get("metric") else None),
                max_rel_err=(float(w["max_rel_err"]) if w.get("max_rel_err") is not None else None),
                reason=str(w.get("reason") or ""),
                approver=str(w.get("approver") or ""),
                approved_at=str(w.get("approved_at") or ""),
                expires_at=(str(w["expires_at"]) if w.get("expires_at") else None),
                signature=str(w.get("signature") or ""),
                raw=w,
            )
        )
    return tuple(out)


def load_bundle(registry_dir: str | Path, repo_root: Optional[str | Path] = None) -> Bundle:
    """加载注册表目录；``repo_root`` 缺省时取 registry.json 的 ``repo_root`` 字段（相对注册表目录）。"""
    root = Path(registry_dir).resolve()
    reg_raw = _read_json(root / "registry.json", required=True)
    gates_raw = _read_json(root / "gates.json", required=False)
    baseline_raw = _read_json(root / "baseline.json", required=False)
    white_raw = _read_json(root / "whitelist.json", required=False)

    caps_raw = reg_raw.get("capabilities")
    if not isinstance(caps_raw, list) or not caps_raw:
        raise RegistryError("registry.json: capabilities must be a non-empty list")
    caps = [_parse_capability(c) for c in caps_raw]
    ids = [c.id for c in caps]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise RegistryError(f"duplicate capability ids: {sorted(dup)}")
    caps = list(_attach_gates(caps, gates_raw))

    baseline: Dict[str, Dict[str, float]] = {}
    for cap_id, values in (baseline_raw.get("values") or {}).items():
        if not isinstance(values, dict):
            raise RegistryError(f"baseline[{cap_id}] must be an object")
        baseline[str(cap_id)] = {str(k): float(v) for k, v in values.items()}

    if repo_root is not None:
        repo = Path(repo_root).resolve()
    else:
        rel = reg_raw.get("repo_root") or ".."
        repo = (root / str(rel)).resolve()

    return Bundle(
        root=root,
        repo_root=repo,
        capabilities=tuple(caps),
        baseline=baseline,
        whitelist=_parse_whitelist(white_raw),
        file_sha256=file_sha256_map(root, REGISTRY_FILES),
        baseline_meta={k: v for k, v in baseline_raw.items() if k != "values"},
    )


# --------------------------------------------------------------------------- #
# 变更 → 受影响能力（触发机制）
# --------------------------------------------------------------------------- #
def path_matches(candidate: str, pattern: str) -> bool:
    """路径匹配：精确、目录前缀（以 '/' 结尾）、或 glob（含 '*'/'?'）。"""
    candidate = candidate.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if not pattern:
        return False
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(candidate, pattern)
    if pattern.endswith("/"):
        return candidate.startswith(pattern)
    return candidate == pattern


def affected_capabilities(
    capabilities: Sequence[Capability], changed_files: Sequence[str]
) -> Tuple[str, ...]:
    """变更文件 → 受影响能力 id（保持注册表顺序；空=无影响，故不触发重跑）。"""
    hit: List[str] = []
    for cap in capabilities:
        patterns = cap.trigger_paths()
        if any(path_matches(f, p) for f in changed_files for p in patterns):
            hit.append(cap.id)
    return tuple(hit)


def capabilities_for_solver(capabilities: Sequence[Capability], solver: str) -> Tuple[str, ...]:
    return tuple(c.id for c in capabilities if c.solver == solver)