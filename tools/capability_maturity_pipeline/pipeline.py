"""成熟度自治管线：证据扫描 → 自动判定 → 台账更新 → 回滚 → 审计（p156w2-n07）。

把「人工升账」变成机器可判的闭环：

    ┌────────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐
    │ 证据扫描    │→ │ 矛盾/推翻检测 │→ │ 裁定（预声明阈值）│→ │ 台账决策+审计链 │
    │ artifacts/ │   │ SUPERSEDE    │   │ REAL/WITHHELD/ │   │ UPGRADE/      │
    │ runs/      │   │ refuted basis│   │ MOCK/…         │   │ DOWNGRADE/    │
    │ tensor_    │   └──────────────┘   └────────────────┘   │ ROLLBACK/     │
    │ reports/   │                                            │ REBASIS/HOLD  │
    └────────────┘                                            └──────────────┘

关键性质（全部可机器验证）：

1. **确定性**：同一输入 + 同一台账 + 同一时间戳 → ``core_sha256`` 完全一致。
2. **幂等**：同批证据重复跑，等级与基座都不变 → HOLD，不写链（链长不增长）。
3. **可回滚**：基座证据被后续证据推翻 → 自动 ROLLBACK，``rollback_of`` 指回
   确立旧等级的事件 seq；历史事件永不改写。
4. **可审计**：每条决策进哈希链，链头 ``sha256`` 即本次运行的可比对指纹。
5. **零领域语义**：能力 ID / 指标名 / 阈值全部来自数据或配置注入。

复用 ``tools.capability_ledger_maturity`` 的证据采集器与裁定引擎（不重复实现）。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..capability_ledger_maturity.adjudication_engine import (
    DEFAULT_THRESHOLDS, AdjudicationEngine, AdjudicationResult,
    AdjudicationThresholds,
)
from ..capability_ledger_maturity.evidence_collector import (
    EvidenceCollector, EvidenceItem,
)
from .chain import now_iso, sha256_of
from .contradiction import (
    DEFAULT_POLICY, ContradictionPolicy, detect_conflicts,
    detect_supersessions, oversized_groups,
)
from .ledger import Decision, MaturityLedger

ARTIFACT_SCHEMA = "capability-maturity-pipeline-run/1.0"

DEFAULT_ROOTS: Tuple[str, ...] = ("artifacts", "runs", "tensor_reports")

# 采集器无法从工件里读出 capability_id 时的占位值
UNATTRIBUTED: str = "unknown"
ATTRIBUTION_SCHEMA = "capability-attribution/1.0"

# 证据来源分类（按路径/文件名，不看领域词汇）
_BENCHMARK_HINTS = ("benchmark", "metrics", "validation", "bench", "sweep")
_CONTRACT_HINTS = ("contract", "schema", "interface")


def classify_path(path: Path) -> Optional[str]:
    """按路径与后缀决定解析策略；返回 None 表示不作为证据源。"""
    name = path.name.lower()
    parts = {p.lower() for p in path.parts}
    if path.suffix == ".py":
        return "pytest" if "tests" in parts or "test" in parts else None
    if path.suffix == ".md":
        return "report"
    if path.suffix == ".json":
        if "contracts" in parts and any(h in name for h in _CONTRACT_HINTS):
            return "contract"
        if any(h in name for h in _BENCHMARK_HINTS):
            return "benchmark"
        return "report"
    if path.suffix in (".yaml", ".yml") and "contracts" in parts:
        return "contract"
    return None


@dataclass(frozen=True)
class PipelineConfig:
    """管线配置（阈值与判据在运行前声明，写进审计链不可事后改判）。"""
    roots: Tuple[Path, ...] = tuple(Path(r) for r in DEFAULT_ROOTS)
    thresholds: AdjudicationThresholds = DEFAULT_THRESHOLDS
    policy: ContradictionPolicy = DEFAULT_POLICY
    claimed_levels: Dict[str, str] = field(default_factory=dict)
    # 归因规则：(相对路径 glob, capability_id)，按顺序首个命中生效。
    # 规则是**数据**（配置文件注入），代码里不出现任何具体能力名。
    attribution: Tuple[Tuple[str, str], ...] = ()
    max_file_bytes: int = 2_000_000
    recursive: bool = True
    max_conflict_group: int = 64
    # 证据来源类型白名单（可做「仅结构化证据」与「含文本报告」的对照）
    source_types: Tuple[str, ...] = ("pytest", "benchmark", "report", "contract")
    # 排除自身输出（避免管线读自己的台账/工件：自我引用会污染证据集）
    exclude_globs: Tuple[str, ...] = ("artifacts/capability_maturity/**",)

    def roots_sha256(self, worktree: Path) -> str:
        """扫描范围指纹（只含**相对**根路径，不含绝对工作树 → 可跨机器复现）。"""
        rel = []
        for r in self.roots:
            rp = Path(r)
            try:
                rel.append(str(rp.resolve().relative_to(Path(worktree).resolve())))
            except (ValueError, OSError):
                rel.append(str(rp))
        return sha256_of({
            "roots": sorted(rel),
            "max_file_bytes": self.max_file_bytes,
            "recursive": self.recursive,
            "attribution": [[g, c] for g, c in self.attribution],
            "max_conflict_group": self.max_conflict_group,
            "source_types": sorted(self.source_types),
            "exclude_globs": list(self.exclude_globs),
        })

    @staticmethod
    def load_attribution(path: Path) -> Tuple[Tuple[str, str], ...]:
        """读归因配置：``{"schema":..., "rules":[{"glob":..., "capability_id":...}]}``。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != ATTRIBUTION_SCHEMA:
            raise ValueError(f"unsupported attribution schema {data.get('schema')!r}")
        rules = []
        for rule in data.get("rules") or []:
            g, c = rule.get("glob"), rule.get("capability_id")
            if not g or not c:
                raise ValueError(f"malformed attribution rule: {rule!r}")
            rules.append((str(g), str(c)))
        return tuple(rules)


class EvidenceScanner:
    """受限根目录的证据扫描器（不越界、有大小上限、可复现）。"""

    def __init__(self, worktree: Path, config: PipelineConfig):
        self.worktree = Path(worktree).resolve()
        self.config = config
        self.collector = EvidenceCollector(worktree=self.worktree)

    def _iter_files(self, root: Path) -> Iterable[Path]:
        root = root if root.is_absolute() else self.worktree / root
        if not root.exists():
            return
        it = root.rglob("*") if self.config.recursive else root.glob("*")
        for p in sorted(it):
            if not p.is_file():
                continue
            try:
                if p.stat().st_size > self.config.max_file_bytes:
                    continue
            except OSError:
                continue
            yield p

    def _attribute(self, rel_path: str) -> Optional[str]:
        """按归因规则把路径映射到 capability_id（无命中返回 None）。"""
        for glob, cap in self.config.attribution:
            if fnmatch.fnmatch(rel_path, glob):
                return cap
        return None

    def scan(self) -> List[EvidenceItem]:
        """扫描所有配置根目录，返回去重后的证据项（单文件失败不中断整轮）。"""
        items: List[EvidenceItem] = []
        seen: Dict[str, EvidenceItem] = {}
        self.errors: List[Dict[str, str]] = []
        self.stats = {"files_scanned": 0, "files_with_evidence": 0,
                      "items_explicit_id": 0, "items_attributed_by_rule": 0,
                      "items_unattributed": 0, "attribution_rule_misses": 0}
        for root in self.config.roots:
            for path in self._iter_files(Path(root)):
                source_type = classify_path(path)
                if source_type is None or source_type not in self.config.source_types:
                    continue
                try:
                    rel_posix = path.resolve().relative_to(self.worktree).as_posix()
                except (ValueError, OSError):
                    rel_posix = str(path)
                if any(fnmatch.fnmatch(rel_posix, g)
                       for g in self.config.exclude_globs):
                    continue
                try:
                    parsed = self.collector.collect_from_path(path, source_type)
                except Exception as exc:  # noqa: BLE001 —— 自治管线不得被单文件拖死
                    self.errors.append({"path": str(path),
                                        "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if parsed:
                    self.stats["files_with_evidence"] += 1
                try:
                    rel = path.resolve().relative_to(self.worktree).as_posix()
                except (ValueError, OSError):
                    rel = str(path)
                for it in parsed:
                    key = it.sha256
                    if key in seen:
                        continue
                    if it.capability_id in ("", UNATTRIBUTED, None):
                        mapped = self._attribute(rel)
                        if mapped:
                            it = replace(it, capability_id=mapped)
                            self.stats["items_attributed_by_rule"] += 1
                        else:
                            self.stats["items_unattributed"] += 1
                            self.stats["attribution_rule_misses"] += 1
                    else:
                        self.stats["items_explicit_id"] += 1
                    seen[key] = it
                    items.append(it)
        for root in self.config.roots:
            for path in self._iter_files(Path(root)):
                st = classify_path(path)
                if st is not None and st in self.config.source_types:
                    self.stats["files_scanned"] += 1
        return items


def basis_from_state(state: Any,
                     collector_worktree: Optional[Path] = None) -> List[EvidenceItem]:
    """把台账里存下的证据基座还原成 EvidenceItem（用于推翻检测）。"""
    out: List[EvidenceItem] = []
    for b in getattr(state, "evidence_basis", []) or []:
        out.append(EvidenceItem(
            capability_id=b["capability_id"],
            source_type=b.get("source_type", "report"),
            source_path=b.get("source_path", ""),
            metric_name=b["metric_name"],
            metric_value=float(b.get("metric_value", 0.0)),
            reference_value=b.get("reference_value"),
            error_pct=b.get("error_pct"),
            tolerance_pct=b.get("tolerance_pct"),
            passed=bool(b.get("passed", False)),
            grade_hint=b.get("grade_hint", ""),
            sha256=b["sha256"],
            timestamp=b.get("timestamp", ""),
        ))
    return out


def compact_basis(items: Sequence[EvidenceItem]) -> List[Dict[str, Any]]:
    """证据项 → 台账可存档的紧凑基座（保留矛盾检测所需字段）。"""
    out = []
    for it in sorted(items, key=lambda x: x.sha256):
        out.append({
            "capability_id": it.capability_id,
            "metric_name": it.metric_name,
            "metric_value": it.metric_value,
            "reference_value": it.reference_value,
            "error_pct": it.error_pct,
            "source_type": it.source_type,
            "source_path": it.source_path,
            "sha256": it.sha256,
            "timestamp": it.timestamp,
        })
    return out


@dataclass
class PipelineRun:
    """一次管线运行的结果（可序列化为工件 JSON）。"""
    artifact: Dict[str, Any]
    decisions: List[Decision]
    ledger: MaturityLedger

    @property
    def core_sha256(self) -> str:
        return self.artifact["core_sha256"]


class MaturityPipeline:
    """成熟度自治管线。"""

    def __init__(self, worktree: Path, config: Optional[PipelineConfig] = None,
                 ledger: Optional[MaturityLedger] = None,
                 clock=now_iso):
        self.worktree = Path(worktree).resolve()
        self.config = config or PipelineConfig()
        self.clock = clock
        self.ledger = ledger or MaturityLedger(
            clock=clock,
            thresholds_sha256=self.config.thresholds.sha256(),
            contradiction_policy_sha256=self.config.policy.sha256(),
        )
        self.scanner = EvidenceScanner(self.worktree, self.config)
        self.engine = AdjudicationEngine(self.config.thresholds)

    # ── 主流程 ─────────────────────────────────────────────────────

    def run(self, *, persist: bool = True,
            ledger_path: Optional[Path] = None) -> PipelineRun:
        t0 = time.perf_counter()
        started = self.clock()

        items = self.scanner.scan()
        # 证据观测时间统一记为本轮扫描时刻：矛盾检测关心的是「先看到谁、后看到谁」，
        # 而文件自带的解析时刻可能缺失或不可比。观测时间确定化后，
        # 同输入 + 同注入时钟 → 指纹完全可复现。
        items = [replace(it, timestamp=started) for it in items]
        unattributed = [it for it in items if it.capability_id == UNATTRIBUTED]
        attributable = [it for it in items if it.capability_id != UNATTRIBUTED]
        by_cap: Dict[str, List[EvidenceItem]] = {}
        for it in attributable:
            by_cap.setdefault(it.capability_id, []).append(it)

        supersessions_all = []
        conflicts_all = detect_conflicts(attributable, self.config.policy,
                                        self.config.max_conflict_group)
        oversized = oversized_groups(attributable, self.config.max_conflict_group)
        decisions: List[Decision] = []
        adjudication_rows: List[Dict[str, Any]] = []

        for cap in sorted(by_cap):
            cap_items = sorted(by_cap[cap], key=lambda x: x.sha256)
            state = self.ledger.states.get(cap)
            prior = basis_from_state(state) if state else []
            sups = detect_supersessions(prior, cap_items, self.config.policy)
            supersessions_all.extend(sups)
            result: AdjudicationResult = self.engine.adjudicate(
                cap, cap_items, self.config.claimed_levels.get(cap))
            decision = self.ledger.apply_decision(
                result,
                incoming_refs=[i.sha256 for i in cap_items],
                supersessions=sups,
                basis=compact_basis(cap_items),
                thresholds_sha256=self.config.thresholds.sha256(),
            )
            if decision is not None:
                decisions.append(decision)
            row = result.to_dict()
            row["timestamp"] = started          # 观测时间统一为本轮扫描时刻
            adjudication_rows.append({
                **row,
                "superseded_refs": [s.refuted_sha256 for s in sups],
                "decision_action": decision.action if decision else "HOLD",
                "decision_event_seq": decision.event_seq if decision else None,
            })

        verification = self.ledger.verify()
        finished = self.clock()
        duration = time.perf_counter() - t0

        source_counts: Dict[str, int] = {}
        for it in items:
            source_counts[it.source_type] = source_counts.get(it.source_type, 0) + 1
        scan_errors = list(getattr(self.scanner, "errors", []))

        unattributed_paths = sorted({it.source_path for it in unattributed})
        core = {
            "evidence_counts": dict(sorted(source_counts.items())),
            "evidence_total": len(items),
            "evidence_attributable": len(attributable),
            "unattributed_evidence_count": len(unattributed),
            "unattributed_paths": unattributed_paths[:40],
            "attribution_stats": dict(sorted(
                getattr(self.scanner, "stats", {}).items())),
            "conflict_groups_oversized": [
                {"capability_id": c, "metric_name": m, "size": n}
                for c, m, n in oversized],
            "capabilities": adjudication_rows,
            "decisions": [d.to_dict() for d in decisions],
            "supersessions": [s.to_dict() for s in supersessions_all],
            "conflicts": [c.to_dict() for c in conflicts_all],
            "levels": self.ledger.levels(),
            "generation": self.ledger.generation,
            "chain_head_sha256": self.ledger.chain.head,
            "chain_length": len(self.ledger.chain),
            "thresholds_sha256": self.config.thresholds.sha256(),
            "policy_sha256": self.config.policy.sha256(),
        }
        artifact = {
            "schema": ARTIFACT_SCHEMA,
            "started_at": started,
            "finished_at": finished,
            "duration_s": round(duration, 6),
            "worktree": str(self.worktree),
            "roots": [str(r) for r in self.config.roots],
            "roots_sha256": self.config.roots_sha256(self.worktree),
            "verification": verification,
            "scan_error_count": len(scan_errors),
            "scan_errors": scan_errors[:50],
            "core_sha256": sha256_of(core),
            **core,
        }
        if persist and ledger_path is not None:
            artifact["ledger_file_sha256"] = self.ledger.save(Path(ledger_path))
            artifact["ledger_path"] = str(ledger_path)
        return PipelineRun(artifact=artifact, decisions=decisions,
                           ledger=self.ledger)

    # ── 摘要 ───────────────────────────────────────────────────────

    @staticmethod
    def summarize(run: PipelineRun) -> Dict[str, Any]:
        a = run.artifact
        actions: Dict[str, int] = {}
        for d in a["decisions"]:
            actions[d["action"]] = actions.get(d["action"], 0) + 1
        return {
            "evidence_total": a["evidence_total"],
            "evidence_counts": a["evidence_counts"],
            "capabilities": len(a["capabilities"]),
            "levels": a["levels"],
            "actions": dict(sorted(actions.items())),
            "supersessions": len(a["supersessions"]),
            "conflicts": len(a["conflicts"]),
            "generation": a["generation"],
            "chain_length": a["chain_length"],
            "chain_head_sha256": a["chain_head_sha256"],
            "chain_ok": a["verification"]["ok"],
            "core_sha256": a["core_sha256"],
            "duration_s": a["duration_s"],
        }