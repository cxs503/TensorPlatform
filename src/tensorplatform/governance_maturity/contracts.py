"""治理成熟化：三契约源 + 场交换契约的**统一装载面**。

本模块只做两件事：
1. 从仓库里把契约文件读成结构化对象，并**给每个文件记 sha256**（审计链的锚点）；
2. 把"契约集合视图"暴露给度量审计（:mod:`.metrics_audit`）、场交换审计
   （:mod:`.field_audit`）与端到端治理链路（:mod:`.e2e`）。

为什么统一装载
--------------
N01（度量工具）/ N02（三契约源）/ N03（跨库场交换）此前各自读自己的文件；
"契约版本变了但消费者没跟上"只能靠人情发现。统一装载面把"用了哪些契约、
各是什么版本、内容指纹是什么"变成一次可枚举的调用，报告里的版本/指纹可被
第三方按 sha256 复核（零手抄）。

零领域语义：本模块只认识**文件名/契约名**这类机制信息，不认识任何学科量。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 三契约源（N02）：任务状态词表 / 事件流词表 / 运行登记词表（正典文件在 backend/contracts）
VOCAB_CONTRACTS: Dict[str, str] = {
    "mission_status_vocab": "backend/contracts/mission_status_vocab.v1.json",
    "session_event_kinds": "backend/contracts/session_event_kinds.v1.json",
    "research_run_status_vocab": "backend/contracts/research_run_status_vocab.v1.json",
    # 派生/辅助契约：状态机与跨仓状态映射，一并纳入指纹
    "mission_state_machine": "backend/contracts/mission_state_machine.v1.json",
    "mission_task_status_map": "backend/contracts/mission_task_status_map.v1.json",
}

# 度量工具事件面声明（N01 × N02 的联合体，节点新增）
METRICS_EMISSION_CONTRACT = "contracts/metrics_emission.v1.json"

# 场交换契约（N03）
COUPLING_SCHEMA = "contracts/coupling/schema/coupling-contract.v1.schema.json"
COUPLING_FRAMES = "contracts/coupling/frames.v1.json"
COUPLING_MAP_V1 = "contracts/coupling/coupling-map.v1.json"
COUPLING_MAP_V2 = "contracts/coupling/coupling-map.v2.json"
COUPLING_MIGRATIONS = "contracts/coupling/migrations.v1_to_v2.json"

DEFAULT_ROOT = Path(__file__).resolve().parents[3]


class ContractError(RuntimeError):
    """契约文件缺失/损坏/版本非法（fail-closed，绝不静默降级成空集）。"""


def repo_root() -> Path:
    return DEFAULT_ROOT


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"契约文件缺失: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # 损坏的契约不能被当成"没有契约"
        raise ContractError(f"契约文件不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"契约文件顶层必须是对象: {path}")
    return data


@dataclass(frozen=True)
class ContractRef:
    """一个契约文件的身份：名字/相对路径/版本/指纹。"""

    name: str
    rel_path: str
    version: Optional[int]
    sha256: str
    data: Dict[str, Any] = field(repr=False, compare=False, default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "path": self.rel_path, "version": self.version,
                "sha256": self.sha256}


def load_vocab_contracts(root: Optional[Path] = None) -> Dict[str, ContractRef]:
    """装载三契约源（含派生契约），返回按契约名索引的引用表。"""
    root = Path(root) if root else repo_root()
    out: Dict[str, ContractRef] = {}
    for name, rel in VOCAB_CONTRACTS.items():
        path = root / rel
        data = load_json(path)
        if data.get("contract") != name:
            raise ContractError(f"{rel}: contract={data.get('contract')!r} != {name!r}")
        version = data.get("version")
        if not isinstance(version, int):
            raise ContractError(f"{rel}: version 必须是整数，得到 {version!r}")
        out[name] = ContractRef(name, rel, version, sha256_file(path), data)
    return out


def load_metrics_emission(root: Optional[Path] = None) -> ContractRef:
    root = Path(root) if root else repo_root()
    path = root / METRICS_EMISSION_CONTRACT
    data = load_json(path)
    if data.get("contract") != "metrics_emission":
        raise ContractError(f"{METRICS_EMISSION_CONTRACT}: contract 字段不是 metrics_emission")
    version = data.get("version")
    if not isinstance(version, int):
        raise ContractError(f"{METRICS_EMISSION_CONTRACT}: version 必须是整数")
    if not isinstance(data.get("tools"), dict) or not data["tools"]:
        raise ContractError(f"{METRICS_EMISSION_CONTRACT}: tools 必须是非空对象")
    return ContractRef("metrics_emission", METRICS_EMISSION_CONTRACT, version,
                       sha256_file(path), data)


@dataclass(frozen=True)
class CouplingContract:
    """一个求解器耦合契约（含版本与指纹）。"""

    rel_path: str
    version: int
    contract_id: str
    solver: str
    ports: Tuple[Dict[str, Any], ...]
    data: Dict[str, Any] = field(repr=False, compare=False, default_factory=dict)


def load_coupling_contracts(root: Optional[Path] = None) -> List[CouplingContract]:
    """装载 ``contracts/coupling/v*/`` 下全部耦合契约（版本目录是有序事实）。"""
    root = Path(root) if root else repo_root()
    base = root / "contracts" / "coupling"
    out: List[CouplingContract] = []
    for path in sorted(base.glob("v*/*.json")):
        data = load_json(path)
        version = data.get("version")
        if not isinstance(version, int):
            raise ContractError(f"{path}: version 必须是整数")
        ports = data.get("ports")
        if not isinstance(ports, list) or not ports:
            raise ContractError(f"{path}: ports 必须是非空数组")
        out.append(CouplingContract(
            rel_path=str(path.relative_to(root)),
            version=version,
            contract_id=str(data.get("contract_id")),
            solver=str(data.get("solver")),
            ports=tuple(ports),
            data=data,
        ))
    if not out:
        raise ContractError("contracts/coupling/v*/ 下没有任何耦合契约")
    return out


def load_coupling_schema(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else repo_root()
    return load_json(root / COUPLING_SCHEMA)


def load_frames(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else repo_root()
    return load_json(root / COUPLING_FRAMES)


def load_migrations(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else repo_root()
    return load_json(root / COUPLING_MIGRATIONS)


def load_coupling_map(root: Optional[Path] = None, version: int = 2) -> Dict[str, Any]:
    root = Path(root) if root else repo_root()
    rel = COUPLING_MAP_V2 if version == 2 else COUPLING_MAP_V1
    return load_json(root / rel)


def latest_by_contract_id(contracts: Iterable[CouplingContract]) -> Dict[str, CouplingContract]:
    """同一 contract_id 多版本时取最高版本（版本化解析规则，显式且可测）。"""
    best: Dict[str, CouplingContract] = {}
    for c in contracts:
        cur = best.get(c.contract_id)
        if cur is None or c.version > cur.version:
            best[c.contract_id] = c
    return best


def contract_inventory(root: Optional[Path] = None) -> Dict[str, Any]:
    """契约清单 + 指纹（报告与 API 的公共入口）。"""
    root = Path(root) if root else repo_root()
    vocabs = load_vocab_contracts(root)
    emission = load_metrics_emission(root)
    coupling = load_coupling_contracts(root)
    return {
        "vocab_contracts": [v.as_dict() for v in vocabs.values()],
        "metrics_emission": emission.as_dict(),
        "coupling_contracts": [
            {"contract_id": c.contract_id, "version": c.version, "path": c.rel_path,
             "sha256": sha256_file(root / c.rel_path), "ports": len(c.ports)}
            for c in coupling
        ],
        "coupling_schema_sha256": sha256_file(root / COUPLING_SCHEMA),
        "frames_sha256": sha256_file(root / COUPLING_FRAMES),
        "migrations_sha256": sha256_file(root / COUPLING_MIGRATIONS),
    }