"""议会宪政化核心：成文宪法约束 + 预登记判据 + 多模型族独立表决 + 红线呈 owner 终裁。

设计纪律（与仓宪章一致）：
- **零领域语义**：本模块只实现"裁决程序"，不含任何学科公式/术语。宪法条款、
  红线词表、争议案例全部外置为 ``data/governance/*.json``。
- **判据先于票决**：争议项必须先预登记机器可裁判据（``Preregistration``），
  冻结后不可改；票只能投在冻结之后（``PreregistrationError``）。
- **多模型族独立表决**：一族一票（``Conclave`` 按 ``family_id`` 去重），
  争议项（非一致）需 ≥2/3 超级多数，一致性项需 >1/2 普通多数；
  达不到门槛 → 升级（不得自决）。
- **红线不可自决**：争议文本命中红线类别（战略变更/安全/不可逆/治理规则）
  → 只产出呈 owner 的决策包（``owner_packet``），``self_decision=False``。
- **审计链**：每次裁决追加到 hash 链账本（``Ledger``），可独立校验防篡改。

对外 schema：``tp.council.charter/v1``、``tp.council.adjudication/v1``、
``tp.council.ledger_entry/v1``。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_CHARTER = "tp.council.charter/v1"
SCHEMA_ADJUDICATION = "tp.council.adjudication/v1"
SCHEMA_LEDGER = "tp.council.ledger_entry/v1"

# 裁决词表（封闭集：任何不在词表内的判决一律拒收，避免"自造结论"）
VERDICTS: tuple[str, ...] = (
    "ADOPT",              # 采纳争议一方主张
    "REJECT",             # 驳回争议主张
    "DOWNGRADE_TO_MOCK",  # 证据等级降级（不得按已验证登记）
    "HONEST_FAIL",        # 如实判失败，不得宣称达标
    "ESCALATE_TO_OWNER",  # 呈 owner 终裁（红线/票数不足）
)

# owner 红线四类（2026-11-02 宪章口径）：只能打包呈 owner，不得自决
RED_LINE_KINDS: tuple[str, ...] = (
    "strategic_change",  # 战略级变更（北极星/产品定位/大方向）
    "safety",            # 安全
    "irreversible",      # 不可逆动作
    "governance_rule",   # 治理规则本身
)

OPS: dict[str, Any] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class ConstitutionError(RuntimeError):
    """宪政程序被违反（判据未预登记、票在冻结前投出、词表外判决等）。"""


class PreregistrationError(ConstitutionError):
    """预登记判据缺失/被改/时序倒置。"""


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def canonical_bytes(obj: Any) -> bytes:
    """规范化 JSON 字节（排序键、无多余空格）——哈希与审计链的唯一序列化口径。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str) -> datetime:
    """解析 ISO8601 时间；naive 时间按 UTC 处理。"""
    dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# 一、成文宪法（BP / 可控宪章 / 质量门 v1 / 北极星 的机器可读投影）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Clause:
    rule_id: str
    kind: str              # principle | procedure | evidence | red_line 四类之一
    text: str              # 宪法原文（用于溯源比对，不得改写）
    source_ref: str        # 对应 manifest.sources 的 source_id
    precedence: int = 100  # 越小越优先（红线恒最优先）

    @property
    def is_red_line(self) -> bool:
        return self.kind in RED_LINE_KINDS or self.kind == "red_line"


@dataclass(frozen=True)
class Source:
    source_id: str
    title: str
    role: str               # bp | charter | quality_gate | north_star | repo_boundary
    paths: tuple[str, ...]
    sha256: str | None = None
    external: bool = False  # True = 仓外 owner 脚手架文件（缺失时如实标记未验证）


class Charter:
    """成文宪法：条款 + 来源 + 优先级 + 溯源校验。

    宪法是**约束**而非建议：``red_line_clauses()`` 与 ``precedence_sorted()``
    决定裁决顺序，任何票决都不能覆盖红线条款。
    """

    def __init__(self, manifest: Mapping[str, Any]):
        if manifest.get("schema") != SCHEMA_CHARTER:
            raise ConstitutionError(f"charter schema 必须是 {SCHEMA_CHARTER}")
        self.manifest = dict(manifest)
        self.version = manifest.get("version", "v0")
        self.sources: tuple[Source, ...] = tuple(
            Source(
                source_id=s["source_id"],
                title=s["title"],
                role=s["role"],
                paths=tuple(s.get("paths", [])),
                sha256=s.get("sha256"),
                external=bool(s.get("external", False)),
            )
            for s in manifest["sources"]
        )
        self.clauses: tuple[Clause, ...] = tuple(
            Clause(
                rule_id=c["rule_id"],
                kind=c["kind"],
                text=c["text"],
                source_ref=c["source_ref"],
                precedence=int(c.get("precedence", 100)),
            )
            for c in manifest["clauses"]
        )
        ids = [s.source_id for s in self.sources]
        if len(set(ids)) != len(ids):
            raise ConstitutionError("source_id 重复")
        rule_ids = [c.rule_id for c in self.clauses]
        if len(set(rule_ids)) != len(rule_ids):
            raise ConstitutionError("rule_id 重复")
        known = set(ids)
        for c in self.clauses:
            if c.source_ref not in known:
                raise ConstitutionError(f"条款 {c.rule_id} 引用未知来源 {c.source_ref}")
            if not c.text.strip():
                raise ConstitutionError(f"条款 {c.rule_id} 原文为空")
        self._by_id = {c.rule_id: c for c in self.clauses}

    @property
    def digest(self) -> str:
        """宪法指纹：来源声明 sha256 + 全部条款 → 任何一句改动都会变。"""
        return sha256_obj(
            {
                "schema": SCHEMA_CHARTER,
                "version": self.version,
                "sources": [s.__dict__ for s in self.sources],
                "clauses": [c.__dict__ for c in self.clauses],
            }
        )

    def clause(self, rule_id: str) -> Clause:
        return self._by_id[rule_id]

    def clauses_of(self, kind: str | None = None) -> tuple[Clause, ...]:
        if kind is None:
            return self.clauses
        return tuple(c for c in self.clauses if c.kind == kind)

    def red_line_clauses(self) -> tuple[Clause, ...]:
        return tuple(c for c in self.clauses if c.is_red_line)

    def precedence_sorted(self) -> tuple[Clause, ...]:
        """红线条款永远排在最前，其余按 precedence 升序、rule_id 稳定排序。"""
        return tuple(sorted(self.clauses, key=lambda c: (0 if c.is_red_line else 1, c.precedence, c.rule_id)))

    def cite(self, rule_ids: Iterable[str]) -> list[dict[str, Any]]:
        out = []
        for rid in rule_ids:
            c = self._by_id[rid]
            out.append({"rule_id": c.rule_id, "kind": c.kind, "text": c.text, "source_ref": c.source_ref})
        return out

    def verify_provenance(self, base_dirs: Sequence[str | Path]) -> dict[str, Any]:
        """逐来源校验：文件是否存在、sha256 是否与声明一致、条款原文是否为文件子串。

        这是防"手抄/编造宪法条款"的机器门：条款 text 必须是来源文件里的原文。
        """
        bases = [Path(b) for b in base_dirs]
        report: dict[str, Any] = {
            "schema": "tp.council.charter_provenance/v1",
            "charter_digest": self.digest,
            "sources": [],
            "clauses_checked": 0,
            "clauses_verified": 0,
            "failures": [],
        }
        for src in self.sources:
            found_path: Path | None = None
            for p in src.paths:
                cand = Path(p)
                if cand.is_absolute():
                    if cand.exists():
                        found_path = cand
                        break
                else:
                    for b in bases:
                        if (b / p).exists():
                            found_path = b / p
                            break
                if found_path:
                    break
            entry: dict[str, Any] = {
                "source_id": src.source_id,
                "title": src.title,
                "role": src.role,
                "external": src.external,
                "path": str(found_path) if found_path else None,
                "status": "missing",
                "sha256_declared": src.sha256,
                "sha256_actual": None,
                "quotes_checked": 0,
                "quotes_missing": [],
            }
            if found_path is None:
                if src.external:
                    entry["status"] = "unverified_external"  # 仓外 owner 脚手架，如实标记
                else:
                    report["failures"].append(f"来源缺失: {src.source_id}")
                report["sources"].append(entry)
                continue
            content = _norm_ws(found_path.read_text(encoding="utf-8", errors="replace"))
            entry["sha256_actual"] = sha256_file(found_path)
            for c in [c for c in self.clauses if c.source_ref == src.source_id]:
                entry["quotes_checked"] += 1
                report["clauses_checked"] += 1
                if _norm_ws(c.text) in content:
                    report["clauses_verified"] += 1
                else:
                    entry["quotes_missing"].append(c.rule_id)
                    report["failures"].append(f"条款原文与来源不符: {c.rule_id}")
            if entry["quotes_missing"]:
                entry["status"] = "quote_mismatch"
            elif src.sha256 and entry["sha256_actual"] != src.sha256:
                entry["status"] = "sha256_drift"  # 来源演进：审计须复核，但不判伪造
            else:
                entry["status"] = "verified"
            report["sources"].append(entry)
        report["ok"] = not report["failures"]
        return report


def load_charter(path: str | Path) -> Charter:
    return Charter(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# 二、预登记判据（冻结后不可改、票只能在其后投）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    statement: str
    eval: Mapping[str, Any]     # {"metric": str, "op": str, "value": number}
    mandatory: bool = True
    # 判据**被违反**（predicate 不成立）时事先声明的判决；None = 该判据不产生判决
    on_fail_verdict: str | None = None

    def __post_init__(self) -> None:
        ev = dict(self.eval)
        if set(ev) - {"metric", "op", "value"}:
            raise PreregistrationError(f"{self.criterion_id}: eval 只允许 metric/op/value")
        if ev.get("op") not in OPS:
            raise PreregistrationError(f"{self.criterion_id}: 非法算子 {ev.get('op')!r}")
        if not str(ev.get("metric", "")).strip():
            raise PreregistrationError(f"{self.criterion_id}: metric 缺失")
        if self.on_fail_verdict is not None and self.on_fail_verdict not in VERDICTS:
            raise PreregistrationError(f"{self.criterion_id}: 词表外判决 {self.on_fail_verdict}")
        if not self.statement.strip():
            raise PreregistrationError(f"{self.criterion_id}: statement 为空")


class Preregistration:
    """争议项判据的冻结登记。冻结 = 不可变快照 + 内容哈希（写入裁决记录）。

    冻结后对属性赋值一律抛 ``PreregistrationError``——"事后改判据"在对象层就不成立。
    """

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_locked", False):
            raise PreregistrationError(f"预登记已冻结，不可修改（拦下对 {name} 的赋值）")
        object.__setattr__(self, name, value)

    def __init__(self, criteria: Sequence[Criterion], frozen_by: str, frozen_at: str, rationale: str = ""):
        if not criteria:
            raise PreregistrationError("争议项必须先预登记 ≥1 条机器可裁判据")
        self.criteria: tuple[Criterion, ...] = tuple(criteria)
        ids = [c.criterion_id for c in self.criteria]
        if len(set(ids)) != len(ids):
            raise PreregistrationError("criterion_id 重复")
        self.frozen_by = frozen_by
        self.frozen_at = frozen_at
        self.rationale = rationale
        parse_ts(frozen_at)
        self.freeze_hash = sha256_obj(self.snapshot())
        object.__setattr__(self, "_locked", True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "tp.council.preregistration/v1",
            "frozen_by": self.frozen_by,
            "frozen_at": self.frozen_at,
            "rationale": self.rationale,
            "criteria": [
                {
                    "criterion_id": c.criterion_id,
                    "statement": c.statement,
                    "eval": dict(c.eval),
                    "mandatory": c.mandatory,
                    "on_fail_verdict": c.on_fail_verdict,
                }
                for c in self.criteria
            ],
        }

    def assert_frozen_before(self, ballots: Sequence["Ballot"]) -> None:
        """时序不变量：任何一票早于冻结时刻 → 判据可能是事后编的（拒收）。"""
        for b in ballots:
            if not b.cast_at:
                raise PreregistrationError(f"票 {b.family_id}/{b.model_id} 缺时间戳，无法证明独立于判据")
            if parse_ts(b.cast_at) < parse_ts(self.frozen_at):
                raise PreregistrationError(
                    f"票 {b.family_id} 投于 {b.cast_at} 早于判据冻结 {self.frozen_at}（判据须先于票决）"
                )

    def evaluate(self, facts: Mapping[str, Any]) -> list[dict[str, Any]]:
        results = []
        for c in self.criteria:
            metric = c.eval["metric"]
            if metric not in facts:
                raise PreregistrationError(f"事实缺项: {metric}（判据不可裁）")
            actual = facts[metric]
            ok = OPS[str(c.eval["op"])](actual, c.eval["value"])
            results.append(
                {
                    "criterion_id": c.criterion_id,
                    "statement": c.statement,
                    "metric": metric,
                    "op": c.eval["op"],
                    "threshold": c.eval["value"],
                    "actual": actual,
                    "passed": bool(ok),
                    "mandatory": c.mandatory,
                    "on_fail_verdict": c.on_fail_verdict,
                }
            )
        return results


# --------------------------------------------------------------------------- #
# 三、红线分类器（词表外置，工具零领域语义）
# --------------------------------------------------------------------------- #
_ASCII_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _matcher(term: str) -> re.Pattern[str]:
    """ASCII 词按词边界匹配（`-`/`_` 视为边界），CJK 词按子串匹配。"""
    if term not in _ASCII_RE_CACHE:
        if re.fullmatch(r"[\x20-\x7e]+", term):
            body = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
        else:
            body = re.escape(term)
        _ASCII_RE_CACHE[term] = re.compile(body, re.IGNORECASE)
    return _ASCII_RE_CACHE[term]


class RedLineClassifier:
    """把"该不该自决"变成机器可判：命中红线类别即须呈 owner。"""

    def __init__(self, lexicon: Mapping[str, Any]):
        if lexicon.get("schema") != "tp.council.redline_lexicon/v1":
            raise ConstitutionError("红线词表 schema 不匹配")
        self.categories: Mapping[str, Sequence[str]] = lexicon["categories"]
        for cat in self.categories:
            if cat not in RED_LINE_KINDS:
                raise ConstitutionError(f"未定义的红线类别 {cat}（只允许 {RED_LINE_KINDS}）")
        for cat, terms in self.categories.items():
            if not terms:
                raise ConstitutionError(f"红线类别 {cat} 词表为空")
        self.version = lexicon.get("version", "v0")
        self.digest = sha256_obj(dict(lexicon))

    def classify(self, text: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for cat, terms in self.categories.items():
            for term in terms:
                m = _matcher(term).search(text)
                if m:
                    hits.append(
                        {"category": cat, "term": term, "span": [m.start(), m.end()], "matched": m.group(0)}
                    )
        return hits


def load_redline_lexicon(path: str | Path) -> RedLineClassifier:
    return RedLineClassifier(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# 四、多模型族独立表决（一族一票 + 争议项超级多数）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Ballot:
    family_id: str
    model_id: str
    verdict: str
    rationale: str
    criteria_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    cast_at: str = ""
    latency_ms: int = 0
    response_sha256: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ConstitutionError(f"词表外判决 {self.verdict!r}（只允许 {VERDICTS}）")
        if not self.family_id.strip() or not self.model_id.strip():
            raise ConstitutionError("票必须带 family_id 与 model_id")
        if not self.rationale.strip():
            raise ConstitutionError(f"{self.family_id}: 票必须带理由（无理由=不可审计）")

    @property
    def digest(self) -> str:
        return sha256_obj(
            {
                "family_id": self.family_id,
                "model_id": self.model_id,
                "verdict": self.verdict,
                "rationale": self.rationale,
                "criteria_refs": list(self.criteria_refs),
                "evidence_refs": list(self.evidence_refs),
                "cast_at": self.cast_at,
                "response_sha256": self.response_sha256,
            }
        )


class Conclave:
    """族级计票：同族重复票 → 拒收（防同族堆票），异族理由同文 → 独立性告警。"""

    def __init__(self, quorum: int = 3, supermajority: Fraction = Fraction(2, 3)):
        if quorum < 2:
            raise ConstitutionError("quorum 至少 2 族")
        if not (Fraction(1, 2) < supermajority <= Fraction(1, 1)):
            raise ConstitutionError("supermajority 必须在 (1/2, 1] 内")
        self.quorum = quorum
        self.supermajority = supermajority
        self._accepted: list[Ballot] = []
        self._rejected: list[dict[str, Any]] = []

    @property
    def accepted(self) -> tuple[Ballot, ...]:
        return tuple(self._accepted)

    def cast(self, ballot: Ballot) -> str:
        for b in self._accepted:
            if b.family_id == ballot.family_id:
                self._rejected.append(
                    {"family_id": ballot.family_id, "model_id": ballot.model_id, "reason": "duplicate_family"}
                )
                return "duplicate_family"
        self._accepted.append(ballot)
        return "accepted"

    def tally(self) -> dict[str, Any]:
        total = len(self._accepted)
        counts: dict[str, int] = {}
        for b in self._accepted:
            counts[b.verdict] = counts.get(b.verdict, 0) + 1
        collisions = []
        seen: dict[str, str] = {}
        for b in self._accepted:
            key = sha256_obj({"rationale": _norm_ws(b.rationale)})
            if key in seen and seen[key] != b.family_id and len(_norm_ws(b.rationale)) >= 40:
                collisions.append({"families": sorted([seen[key], b.family_id]), "rationale_sha256": key})
            else:
                seen[key] = b.family_id
        distinct = sorted(counts)
        top_verdict = None
        top_share = Fraction(0, 1)
        if counts:
            top_verdict = max(counts, key=lambda v: (counts[v], v))
            top_share = Fraction(counts[top_verdict], total)
        contested = len(distinct) > 1
        threshold = self.supermajority if contested else Fraction(1, 2)
        quorum_met = total >= self.quorum
        return {
            "families": total,
            "quorum_required": self.quorum,
            "quorum_met": quorum_met,
            "accepted": [
                {"family_id": b.family_id, "model_id": b.model_id, "verdict": b.verdict, "digest": b.digest,
                 "cast_at": b.cast_at, "latency_ms": b.latency_ms}
                for b in self._accepted
            ],
            "rejected": list(self._rejected),
            "counts": counts,
            "distinct_verdicts": distinct,
            "contested": contested,
            "threshold": f"{threshold.numerator}/{threshold.denominator}",
            "top_verdict": top_verdict,
            "top_share": f"{top_share.numerator}/{top_share.denominator}",
            "threshold_met": bool(quorum_met and top_verdict is not None and top_share >= threshold),
            "independence_warnings": collisions,
        }


# --------------------------------------------------------------------------- #
# 五、裁决器（红线优先 → 判据 → 票决 → 必要时升级）
# --------------------------------------------------------------------------- #
class Adjudicator:
    """宪政裁决流程。顺序不可换：红线 → 预登记判据 → 多族票决 → 升级。"""

    def __init__(
        self,
        charter: Charter,
        classifier: RedLineClassifier,
        quorum: int = 3,
        supermajority: Fraction = Fraction(2, 3),
    ):
        self.charter = charter
        self.classifier = classifier
        self.quorum = quorum
        self.supermajority = supermajority

    def adjudicate(
        self,
        case: Mapping[str, Any],
        ballots: Sequence[Ballot],
        facts: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        prereg_raw = case.get("preregistration")
        if not prereg_raw:
            raise PreregistrationError(f"{case.get('case_id')}: 争议项缺预登记判据，不得裁决")
        prereg = Preregistration(
            [Criterion(**c) for c in prereg_raw["criteria"]],
            frozen_by=prereg_raw.get("frozen_by", "unknown"),
            frozen_at=prereg_raw["frozen_at"],
            rationale=prereg_raw.get("rationale", ""),
        )
        prereg.assert_frozen_before(ballots)

        conclave = Conclave(quorum=self.quorum, supermajority=self.supermajority)
        for b in ballots:
            conclave.cast(b)
        tally = conclave.tally()

        scan_text = "\n".join(
            str(case.get(k, "")) for k in ("question", "scope", "disputed_action", "proposal")
        )
        hits = self.classifier.classify(scan_text)
        red_clause_ids = [c.rule_id for c in self.charter.red_line_clauses()]

        result: dict[str, Any] = {
            "schema": SCHEMA_ADJUDICATION,
            "case_id": case.get("case_id"),
            "charter_digest": self.charter.digest,
            "charter_version": self.charter.version,
            "redline_lexicon_digest": self.classifier.digest,
            "preregistration_freeze_hash": prereg.freeze_hash,
            "preregistration_frozen_at": prereg.frozen_at,
            "ballot_digests": [b.digest for b in ballots],
            "red_line_hits": hits,
            "committee": tally,
            "criteria_results": [],
            "criterion_vote_conflict": False,
            "self_decision": True,
            "owner_packet": None,
            "route": None,
            "verdict": None,
        }

        # ① 红线：命中即不得自决，只出决策包
        if hits:
            result.update(
                route="redline_escalation",
                verdict="ESCALATE_TO_OWNER",
                self_decision=False,
                owner_packet=self._owner_packet(case, hits, tally, red_clause_ids, prereg),
            )
            return result

        # ② 预登记判据（机器裁定）；判据失败给出其事先声明的判决
        criteria_results = prereg.evaluate(facts or {})
        result["criteria_results"] = criteria_results
        fail_verdicts = sorted(
            {r["on_fail_verdict"] for r in criteria_results
             if r["mandatory"] and not r["passed"] and r["on_fail_verdict"]}
        )
        if len(fail_verdicts) > 1:
            # 判据之间自相矛盾：不得挑一个有利的执行 → 升级
            result.update(
                route="escalation_no_confidence",
                verdict="ESCALATE_TO_OWNER",
                self_decision=False,
                owner_packet=self._owner_packet(case, [], tally, red_clause_ids, prereg,
                                                extra={"criterion_conflict": fail_verdicts}),
            )
            return result

        criteria_verdict = fail_verdicts[0] if fail_verdicts else None

        # ③ 多族票决
        committee_verdict = tally["top_verdict"] if tally["threshold_met"] else None
        if criteria_verdict:
            if committee_verdict and committee_verdict != criteria_verdict:
                result["criterion_vote_conflict"] = True  # 判据优先于票决，留痕
            result.update(route="criteria", verdict=criteria_verdict)
            return result
        if committee_verdict:
            result.update(route="committee", verdict=committee_verdict)
            return result

        # ④ 票数不足/无多数：升级，不得自决
        result.update(
            route="escalation_no_confidence",
            verdict="ESCALATE_TO_OWNER",
            self_decision=False,
            owner_packet=self._owner_packet(case, [], tally, red_clause_ids, prereg,
                                            extra={"reason": "committee_threshold_not_met"}),
        )
        return result

    def _owner_packet(
        self,
        case: Mapping[str, Any],
        hits: Sequence[Mapping[str, Any]],
        tally: Mapping[str, Any],
        red_clause_ids: Sequence[str],
        prereg: Preregistration,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """呈 owner 的决策包：事实/条款/票况/选项，**不含**机器自裁的结论。"""
        return {
            "schema": "tp.council.owner_packet/v1",
            "case_id": case.get("case_id"),
            "question": case.get("question"),
            "red_line_hits": list(hits),
            "red_line_categories": sorted({h["category"] for h in hits}),
            "charter_citations": self.charter.cite(red_clause_ids),
            "preregistration_freeze_hash": prereg.freeze_hash,
            "committee": tally,
            "evidence": case.get("evidence", []),
            "options_for_owner": case.get("owner_options", []),
            "recommended_option": case.get("recommended_option"),
            "recommendation_is_binding": False,
            "self_decision": False,
            "note": "红线事项（战略变更/安全/不可逆/治理规则）按宪章只能由 owner 终裁；本包只呈事实与选项，机器不代签。",
            "extra": dict(extra or {}),
        }


# --------------------------------------------------------------------------- #
# 六、审计链账本（append-only + hash 链）
# --------------------------------------------------------------------------- #
class Ledger:
    """append-only JSONL，每条记录带 prev_hash / entry_hash；``verify()`` 可证伪篡改。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def append(self, record: Mapping[str, Any], recorded_at: str | None = None) -> dict[str, Any]:
        prior = self.entries()
        prev_hash = prior[-1]["entry_hash"] if prior else "0" * 64
        entry = {
            "schema": SCHEMA_LEDGER,
            "seq": len(prior) + 1,
            "recorded_at": recorded_at or utc_now(),
            "prev_hash": prev_hash,
            "record": dict(record),
        }
        entry["entry_hash"] = sha256_obj({k: v for k, v in entry.items() if k != "entry_hash"})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry

    def verify(self) -> dict[str, Any]:
        prior_hash = "0" * 64
        problems = []
        entries = self.entries()
        for i, e in enumerate(entries, 1):
            if e.get("seq") != i:
                problems.append(f"seq 断裂 @{i}")
            if e.get("prev_hash") != prior_hash:
                problems.append(f"prev_hash 不连续 @{i}")
            recomputed = sha256_obj({k: v for k, v in e.items() if k != "entry_hash"})
            if recomputed != e.get("entry_hash"):
                problems.append(f"entry_hash 不匹配 @{i}（记录被改）")
            prior_hash = e.get("entry_hash")
        return {"ok": not problems, "entries": len(entries), "problems": problems, "head_hash": prior_hash}


# --------------------------------------------------------------------------- #
# 七、回放裁定（与 owner 实际裁决比对）
# --------------------------------------------------------------------------- #
def compare_with_owner(result: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    """回放判决 vs owner 实际裁决。

    - ``agreement``：机器判决 = owner 标签。
    - ``constitutional_divergence``：机器按红线/票数不足升级，而 owner 已实质裁决。
      这是**分权设计的结果**而非错误，但必须在案例里附可辩护分析。
    - ``mismatch``：其余不一致（不可辩护，逐件列明）。
    """
    ruling = case.get("owner_ruling") or {}
    owner_label = ruling.get("label")
    if owner_label not in VERDICTS:
        raise ConstitutionError(f"{case.get('case_id')}: owner_ruling.label 不在词表内: {owner_label!r}")
    engine = result["verdict"]
    if engine == owner_label:
        return {"case_id": case.get("case_id"), "kind": "agreement", "engine": engine, "owner": owner_label}
    if result.get("route") in ("redline_escalation", "escalation_no_confidence") and not result.get("self_decision"):
        has_analysis = bool((case.get("divergence_analysis") or "").strip())
        return {
            "case_id": case.get("case_id"),
            "kind": "constitutional_divergence" if has_analysis else "mismatch",
            "engine": engine,
            "owner": owner_label,
            "route": result.get("route"),
            "analysis_present": has_analysis,
        }
    return {"case_id": case.get("case_id"), "kind": "mismatch", "engine": engine, "owner": owner_label}


def acceptance_summary(comparisons: Sequence[Mapping[str, Any]], min_agreements: int = 2) -> dict[str, Any]:
    agreements = [c for c in comparisons if c["kind"] == "agreement"]
    divergences = [c for c in comparisons if c["kind"] == "constitutional_divergence"]
    mismatches = [c for c in comparisons if c["kind"] == "mismatch"]
    return {
        "cases": len(comparisons),
        "agreements": len(agreements),
        "divergences": len(divergences),
        "mismatches": len(mismatches),
        "min_agreements_required": min_agreements,
        "require_divergence_analysis": True,
        "criteria": {
            "agreements_ge_2": len(agreements) >= min_agreements,
            "divergence_ge_1": len(divergences) >= 1,
            "mismatches_eq_0": len(mismatches) == 0,
        },
        "passed": bool(
            len(agreements) >= min_agreements and len(divergences) >= 1 and len(mismatches) == 0
        ),
        "detail": list(comparisons),
    }