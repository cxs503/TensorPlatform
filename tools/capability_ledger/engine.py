"""
能力证据台账引擎 —— 「扫描 → 落盘 → 查询」闭环（零领域语义）
=================================================================
本模块只做四件事，全部由 config/capability_ledger.v1.json 驱动：

1. 扫描（scan）：读报告元数据快照（tensor_reports 索引），按
   config/capabilities.json 里的模式做「强/弱」两级能力指纹匹配。
2. 推导（derive）：对每条命中证据抽取 fail / real_claim / stub / verify
   信号与误差数值，按规则给出该证据的等级判决，再按时间序取最新决定判决，
   并对 REAL 判决施加「无 fail 信号」否决。等级表中没有任何能力名。
3. 落盘（write）：产物带 as_of / schema_version / sha256；写盘路径可配置；
   平台期望路径缺失或不可写时 fail-soft（返回 warning，不抛异常）。
4. 查询（query）：任何情况下都返回 21 条能力（缺产物时降级为注册表初值 +
   warning），绝不 FileNotFoundError。

等级取值：REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_LEVELS = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
LEVEL_RANK = {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4}

DEFAULT_CONFIG_REL = "config/capability_ledger.v1.json"


# ────────────────────────────────────────── 基础工具
def repo_root(start: str | Path | None = None) -> Path:
    """定位仓库根（tools/capability_ledger/engine.py → 上溯两级）。"""
    if start is not None:
        return Path(start).resolve()
    return Path(__file__).resolve().parents[2]


def _read_json(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def canonical_json(obj: Any) -> str:
    """确定性序列化：排序键 + 紧凑分隔符，用于 sha256 与两次复算比对。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _norm_token(s: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(s).lower())


def _tokens(s: str) -> set[str]:
    return {_norm_token(t) for t in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", str(s)) if t}


# ────────────────────────────────────────── 数据结构
@dataclass
class EvidenceVerdict:
    report_id: Any
    mission_id: Any
    scenario_key: str
    created_at: str
    grade: str | None
    signals: list[str] = field(default_factory=list)
    max_error_pct: float | None = None
    reason: str = ""
    match_kind: str = "weak"

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "mission_id": self.mission_id,
            "scenario_key": self.scenario_key,
            "created_at": self.created_at,
            "grade": self.grade,
            "signals": self.signals,
            "max_error_pct": self.max_error_pct,
            "reason": self.reason,
            "match_kind": self.match_kind,
        }


# ────────────────────────────────────────── 配置 / 注册表 / 快照加载
def load_config(path: str | Path | None = None, root: str | Path | None = None) -> dict:
    root_p = repo_root(root)
    path_p = Path(path) if path is not None else root_p / DEFAULT_CONFIG_REL
    if not path_p.is_absolute():
        path_p = root_p / path_p
    if not path_p.exists():
        # fail-soft：配置缺失时用内置的最小可用配置
        return {
            "registry_path": "config/capabilities.json",
            "snapshot_path": "artifacts/capability_ledger/platform_reports_index.json",
            "ledger_output_path": "artifacts/capability_ledger/capability_ledger_v2.json",
            "platform_expected_paths": ["backend/platform_harness/capabilities.json"],
            "thresholds": {"max_reference_error_pct": 15.0, "real_requires_no_fail_signal": True},
            "signals": {},
            "matching": {"min_pattern_len": 4, "stop_patterns": [], "strong_first": True},
            "ledger_schema_version": "capability-ledger/2.1",
            "harness_schema_version": "platform-harness-capabilities/1.1",
            "_config_missing": str(path_p),
        }
    cfg = _read_json(path_p)
    cfg["_config_path"] = str(path_p)
    return cfg


def load_registry(cfg: dict, root: str | Path | None = None) -> dict:
    root_p = repo_root(root)
    p = Path(cfg.get("registry_path", "config/capabilities.json"))
    if not p.is_absolute():
        p = root_p / p
    data = _read_json(p)
    caps = data.get("capabilities", [])
    by_id = {c["capability_id"]: c for c in caps}
    return {"path": str(p), "schema_version": data.get("schema_version", ""), "capabilities": caps, "by_id": by_id}


def load_snapshot(cfg: dict, root: str | Path | None = None) -> dict:
    """读报告元数据快照；缺失时返回空快照（fail-soft，不抛异常）。"""
    root_p = repo_root(root)
    p = Path(cfg.get("snapshot_path", ""))
    if not p.is_absolute():
        p = root_p / p
    if not p.exists():
        return {
            "schema_version": "platform-reports-index/1.0",
            "generated_at": "",
            "as_of": "",
            "records": [],
            "source_path": str(p),
            "missing": True,
            "warnings": [f"报告元数据快照缺失：{p}（按注册表初值降级）"],
        }
    data = _read_json(p)
    data.setdefault("records", [])
    data["source_path"] = str(p)
    data["missing"] = False
    return data


# ────────────────────────────────────────── 匹配
def _match_keys(reg_entry: dict, matching: dict) -> dict[str, set[str]]:
    cap_id = reg_entry["capability_id"]
    tail = cap_id.split(".")[-1]
    strong = {_norm_token(cap_id), _norm_token(tail)}
    # 强指纹去引号/去分隔符后仍可能跨分隔符出现（open-water vs open_water）
    strong |= {_norm_token(str(x)) for x in [cap_id, tail, cap_id.replace(".", "_")]}
    min_len = int(matching.get("min_pattern_len", 4))
    stop = {_norm_token(s) for s in matching.get("stop_patterns", [])}
    weak_raw = set(reg_entry.get("code_patterns", [])) | set(reg_entry.get("report_patterns", []))
    weak = {_norm_token(w) for w in weak_raw if len(str(w)) >= min_len}
    weak -= stop
    return {"strong": {s for s in strong if s}, "weak": weak}


def match_records(reg_entry: dict, records: list[dict], matching: dict) -> list[tuple[dict, str]]:
    """返回 [(record, 'strong'|'weak')]；强匹配存在时按 strong_first 丢弃弱匹配。"""
    keys = _match_keys(reg_entry, matching)
    strong_fields = matching.get("strong_fields", ["scenario_key", "tags"])
    weak_fields = matching.get("weak_fields", ["scenario_key", "tags", "title"])
    sub_min = int(matching.get("strong_substring_min_len", 6))
    strong_long = {k for k in keys["strong"] if len(k) >= sub_min}
    out: list[tuple[dict, str]] = []
    for rec in records:
        s_tokens: set[str] = set()
        s_whole: list[str] = []
        for f in strong_fields:
            v = rec.get(f)
            if isinstance(v, (list, tuple)):
                for item in v:
                    s_tokens |= _tokens(item)
                    s_whole.append(_norm_token(item))
            elif v:
                s_tokens |= _tokens(v)
                s_whole.append(_norm_token(v))
        s_norm = {_norm_token(x) for x in s_tokens}
        hit = bool(s_norm & keys["strong"])
        if not hit and strong_long:
            blob = "|".join(s_whole)
            hit = any(k in blob for k in strong_long)
        if hit:
            out.append((rec, "strong"))
            continue
        w_text = " ".join(
            str(rec.get(f, "")) if not isinstance(rec.get(f), (list, tuple)) else " ".join(map(str, rec.get(f)))
            for f in weak_fields
        ).lower()
        w_norm = _norm_token(w_text)
        if any(k and k in w_norm for k in keys["weak"]):
            out.append((rec, "weak"))
    if not out:
        return out
    if matching.get("strong_first", True) and any(k == "strong" for _, k in out):
        out = [(r, k) for r, k in out if k == "strong"]
    out.sort(key=lambda x: (str(x[0].get("created_at", "")), str(x[0].get("id", ""))))
    return out


# ────────────────────────────────────────── 判决
def _text_of(rec: dict) -> str:
    parts = [str(rec.get("scenario_key", "")), str(rec.get("title", "")), str(rec.get("summary", ""))]
    tags = rec.get("tags") or []
    if isinstance(tags, (list, tuple)):
        parts.append(" ".join(str(t) for t in tags))
    return " ".join(parts)


def judge_record(rec: dict, cfg: dict) -> EvidenceVerdict:
    """单条证据 → 等级判决（纯规则，无能力名）。"""
    sig = cfg.get("signals", {})
    thr = cfg.get("thresholds", {})
    max_err_thr = float(thr.get("max_reference_error_pct", 15.0))
    text = _text_of(rec).lower()
    tags = [str(t).lower() for t in (rec.get("tags") or [])]

    signals: list[str] = []
    for pat in sig.get("stub", []):
        if re.search(pat, text):
            signals.append("stub")
            break
    fail_hits = [p for p in sig.get("fail", []) if re.search(p, text)]
    if fail_hits:
        signals.append("fail")
    real_hits = [p for p in sig.get("real_claim", []) if re.search(p, text)]
    if "real" in tags:
        real_hits.append("tag:REAL")
    if real_hits:
        signals.append("real_claim")
    if any(re.search(p, text) for p in sig.get("verify", [])):
        signals.append("verify")

    errs: list[float] = []
    ep = sig.get("error_pct")
    if ep:
        for m in re.finditer(ep, text):
            try:
                errs.append(abs(float(m.group(1))))
            except (TypeError, ValueError):
                continue
    max_err = max(errs) if errs else None

    grade: str | None = None
    reason = ""
    if "stub" in signals:
        grade, reason = "STUB", "证据文本含空壳指示词"
    elif "fail" in signals:
        # 显式失败/越窗/缺陷证据：不得判 REAL（负面证据否决）
        grade, reason = "MOCK", "证据含失败/越窗/缺陷信号，绝对精度不成立"
    elif "real_claim" in signals:
        if thr.get("real_requires_no_fail_signal", True) and max_err is not None and max_err > max_err_thr:
            grade, reason = "MOCK", f"自述 REAL 但解析出误差 {max_err}% > 阈值 {max_err_thr}%"
        else:
            grade, reason = "REAL", "显式 REAL 声明且无失败信号、误差在阈值内"
    elif max_err is not None and max_err > max_err_thr:
        grade, reason = "MOCK", f"误差 {max_err}% > 阈值 {max_err_thr}%"
    elif "verify" in signals:
        grade, reason = "WITHHELD", "有验证动作但无可判定的绝对量/判决"

    return EvidenceVerdict(
        report_id=rec.get("id"),
        mission_id=rec.get("mission_id"),
        scenario_key=str(rec.get("scenario_key", "")),
        created_at=str(rec.get("created_at", "")),
        grade=grade,
        signals=signals,
        max_error_pct=max_err,
        reason=reason,
    )


def derive_capability(reg_entry: dict, records: list[dict], cfg: dict) -> dict[str, Any]:
    """单能力 → 台账条目（时间序最新决定判决 + 负面证据否决）。"""
    matching = cfg.get("matching", {})
    matched = match_records(reg_entry, records, matching)
    # 计数用全量匹配（不被 strong_first 削减），判决只用强指纹
    counting = dict(matching); counting["strong_first"] = False
    matched_all = match_records(reg_entry, records, counting)
    verdicts = [judge_record(r, cfg) for r, kind in matched]
    for v, (_r, kind) in zip(verdicts, matched):
        v.match_kind = kind

    strong_verdicts = [v for v, (_r, kind) in zip(verdicts, matched) if kind == "strong"]
    decisive = [v for v in strong_verdicts if v.grade]
    # 时间序已在 match_records 内排好；取最新决定判决
    if decisive:
        latest = decisive[-1]
        grade = latest.grade
        grade_source = "evidence"
        reason = latest.reason
        # REAL 判决的否决复核：其后任何失败信号证据都使其降级
        if grade == "REAL":
            later = [v for v in strong_verdicts if v.created_at > latest.created_at and "fail" in v.signals]
            if later:
                grade = "MOCK"
                grade_source = "evidence_veto"
                reason = f"更晚的证据 {later[-1].report_id} 含失败信号，否决 REAL"
    else:
        reg_level = reg_entry.get("initial_level", "WITHHELD")
        if reg_level not in EVIDENCE_LEVELS:
            reg_level = "WITHHELD"
        if verdicts and reg_level in ("WITHHELD", "REAL"):
            # 只有弱指纹证据：不足以决定等级（不得据一次关键词命中升级/降级）
            grade, grade_source, reason = "WITHHELD", "weak_evidence_only", "仅有弱指纹证据，不足以判定等级"
        else:
            grade, grade_source, reason = reg_level, "registry_initial", "无决定判决证据，按注册表初值"

    return {
        "capability_id": reg_entry["capability_id"],
        "label": reg_entry.get("label", ""),
        "evidence": grade,
        "evidence_rank": LEVEL_RANK.get(grade, 3),
        "evidence_count": len(matched_all),
        "decisive_evidence_count": len([v for v in verdicts if v.grade]),
        "grade_source": grade_source,
        "registry_level": reg_entry.get("initial_level", "WITHHELD"),
        "registry_consistent": _registry_consistent(reg_entry.get("initial_level", "WITHHELD"), grade, grade_source),
        "reason": reason,
        "matched_reports": [v.report_id for v in verdicts],
        "evidence_detail": [v.to_dict() for v in verdicts],
    }


def _registry_consistent(registry_level: str, grade: str, grade_source: str) -> bool:
    """
    与注册表现值一致性：
    - 无证据（grade_source=registry_initial）时等级必须等于注册表值；
    - 有证据时等级由扫描复算给出，允许与注册表初值不同（注册表只登记「无证据默认」）。
    """
    if grade_source == "registry_initial":
        return grade == registry_level
    return True


# ────────────────────────────────────────── 台账构建
def build_ledger(cfg: dict, root: str | Path | None = None) -> dict[str, Any]:
    root_p = repo_root(root)
    registry = load_registry(cfg, root_p)
    snapshot = load_snapshot(cfg, root_p)
    records = snapshot.get("records", [])
    warnings: list[str] = list(snapshot.get("warnings", []))

    caps = []
    for reg in registry["capabilities"]:
        caps.append(derive_capability(reg, records, cfg))
    caps.sort(key=lambda c: c["capability_id"])

    as_of = snapshot.get("as_of") or snapshot.get("generated_at") or ""
    body = {
        "schema_version": cfg.get("ledger_schema_version", "capability-ledger/2.1"),
        "as_of": as_of,
        "registry_path": _rel(registry["path"], root_p),
        "registry_schema_version": registry["schema_version"],
        "snapshot_path": _rel(snapshot.get("source_path", ""), root_p),
        "snapshot_records": len(records),
        "thresholds": cfg.get("thresholds", {}),
        "capabilities": caps,
        "level_distribution": _distribution(caps),
        "warnings": warnings,
    }
    body["sha256"] = sha256_of(body)
    return body


def _rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(root))
    except (ValueError, OSError):
        return str(path)


def _distribution(caps: list[dict]) -> dict[str, int]:
    dist = {lv: 0 for lv in EVIDENCE_LEVELS}
    for c in caps:
        dist[c["evidence"]] = dist.get(c["evidence"], 0) + 1
    return dist


# ────────────────────────────────────────── 落盘（fail-soft）
def verify_ledger(ledger: dict) -> dict[str, Any]:
    """自校验：sha256 一致性 + 21 条 + 等级枚举 + 注册表一致性 + 可复算。"""
    checks: list[dict[str, Any]] = []
    body = {k: v for k, v in ledger.items() if k != "sha256"}
    checks.append({"name": "sha256_recomputable", "ok": sha256_of(body) == ledger.get("sha256"),
                   "detail": ledger.get("sha256", "")[:16]})
    caps = ledger.get("capabilities", [])
    checks.append({"name": "capability_count_21", "ok": len(caps) == 21, "detail": str(len(caps))})
    checks.append({"name": "levels_in_enum",
                   "ok": all(c.get("evidence") in EVIDENCE_LEVELS for c in caps),
                   "detail": sorted({c.get("evidence") for c in caps})})
    checks.append({"name": "evidence_count_present",
                   "ok": all(isinstance(c.get("evidence_count"), int) for c in caps)})
    checks.append({"name": "as_of_present", "ok": bool(ledger.get("as_of")), "detail": ledger.get("as_of", "")})
    checks.append({"name": "schema_version_present",
                   "ok": bool(ledger.get("schema_version")), "detail": ledger.get("schema_version", "")})
    checks.append({"name": "registry_consistency",
                   "ok": all(c.get("registry_consistent", False) for c in caps)})
    failed = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not failed, "failed": failed, "checks": checks}


def recompute_twice(cfg: dict, root: str | Path | None = None) -> tuple[dict, dict, bool]:
    a = build_ledger(cfg, root)
    b = build_ledger(cfg, root)
    return a, b, canonical_json(a) == canonical_json(b)


def to_harness_payload(ledger: dict, cfg: dict) -> dict:
    """转成平台 harness 期望的 schema（capabilities 为 id→记录 的映射）。"""
    caps: dict[str, Any] = {}
    for c in ledger.get("capabilities", []):
        caps[c["capability_id"]] = {
            "evidence": c["evidence"],
            "evidence_engine_grade": c["evidence"],
            "evidence_count": c["evidence_count"],
            "grade_source": c["grade_source"],
            "registry_level": c["registry_level"],
            "registry_consistent": c["registry_consistent"],
            "reason": c["reason"],
            "matched_reports": c["matched_reports"],
            "repo": "TensorPlatform",
            "campaign": "capability-ledger",
            "as_of": ledger.get("as_of", ""),
        }
    return {
        "schema_version": cfg.get("harness_schema_version", "platform-harness-capabilities/1.1"),
        "as_of": ledger.get("as_of", ""),
        "campaign": "capability-ledger",
        "source_ledger_sha256": ledger.get("sha256", ""),
        "ledger_schema_version": ledger.get("schema_version", ""),
        "capability_count": len(caps),
        "capabilities": caps,
    }


def write_artifact(ledger: dict, path: str | Path) -> dict[str, Any]:
    """原子写；目录不可创建/不可写时 fail-soft 返回 warning。"""
    result: dict[str, Any] = {"path": str(path), "written": False, "warnings": []}
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
        result["written"] = True
        result["bytes"] = p.stat().st_size
    except (OSError, ValueError) as e:
        result["warnings"].append(f"写盘失败（fail-soft）：{path}: {e}")
    return result


def resolve_platform_path(cfg: dict, root: str | Path | None = None) -> tuple[Path, list[str]]:
    """解析平台期望路径：env 覆盖 > 配置候选（存在的优先）。"""
    root_p = repo_root(root)
    warnings: list[str] = []
    env_root = os.environ.get(cfg.get("platform_root_env", ""), "")
    candidates: list[Path] = []
    if env_root:
        candidates.append(Path(env_root) / "backend/platform_harness/capabilities.json")
    for cand in cfg.get("platform_expected_paths", []):
        cand_p = Path(cand)
        candidates.append(cand_p if cand_p.is_absolute() else root_p / cand_p)
    for cand in candidates:
        if cand.exists() or cand.parent.is_dir():
            return cand, warnings
    warnings.append("平台期望路径均不可用（fail-soft）：" + ", ".join(str(c) for c in candidates))
    return candidates[0] if candidates else root_p / "backend/platform_harness/capabilities.json", warnings


def publish_to_platform(ledger: dict, cfg: dict, root: str | Path | None = None,
                        path: str | Path | None = None) -> dict[str, Any]:
    """把台账以 harness schema 落回平台期望路径；失败只记 warning。"""
    target, warnings = resolve_platform_path(cfg, root)
    if path is not None:
        target = Path(path)
    payload = to_harness_payload(ledger, cfg)
    res = write_artifact(payload, target)
    res["warnings"] = warnings + res["warnings"]
    res["schema_version"] = payload["schema_version"]
    res["capability_count"] = payload["capability_count"]
    return res


# ────────────────────────────────────────── 查询（永不抛异常）
def query(path: str | Path | None = None, cfg: dict | None = None,
          root: str | Path | None = None) -> dict[str, Any]:
    """
    查询台账。产物缺失/损坏时 fail-soft：返回 21 条能力（注册表初值）+ warning。
    """
    root_p = repo_root(root)
    cfg = cfg or load_config(root=root_p)
    warnings: list[str] = []
    ledger_path = Path(path) if path is not None else Path(cfg.get("ledger_output_path", ""))
    if not ledger_path.is_absolute():
        ledger_path = root_p / ledger_path
    ledger: dict | None = None
    if ledger_path.exists():
        try:
            ledger = _read_json(ledger_path)
            body = {k: v for k, v in ledger.items() if k != "sha256"}
            if sha256_of(body) != ledger.get("sha256"):
                warnings.append(f"台账 sha256 不匹配（可能被手改）：{ledger_path}")
        except (json.JSONDecodeError, OSError) as e:
            warnings.append(f"台账不可解析（fail-soft）：{ledger_path}: {e}")
            ledger = None
    else:
        warnings.append(f"台账产物缺失（fail-soft），按注册表初值返回：{ledger_path}")

    if ledger is None:
        ledger = build_ledger(cfg, root_p)
        ledger = copy.deepcopy(ledger)
        ledger["grade_source_note"] = "registry_initial_fallback"
        warnings.append("已用内存复算结果代替落盘产物（仍返回 21 条能力）")

    return {
        "status": "ok" if not warnings else "degraded",
        "schema_version": ledger.get("schema_version"),
        "as_of": ledger.get("as_of"),
        "sha256": ledger.get("sha256"),
        "capability_count": len(ledger.get("capabilities", [])),
        "level_distribution": ledger.get("level_distribution"),
        "capabilities": [
            {
                "capability_id": c["capability_id"],
                "evidence": c["evidence"],
                "evidence_count": c["evidence_count"],
                "grade_source": c.get("grade_source"),
                "registry_level": c.get("registry_level"),
                "reason": c.get("reason"),
            }
            for c in ledger.get("capabilities", [])
        ],
        "warnings": warnings,
    }


def selfcheck(cfg: dict | None = None, root: str | Path | None = None) -> dict[str, Any]:
    """--selfcheck：结构校验 + 两次复算一致 + 与落盘产物一致。"""
    root_p = repo_root(root)
    cfg = cfg or load_config(root=root_p)
    a, b, deterministic = recompute_twice(cfg, root_p)
    report: dict[str, Any] = {
        "status": "ok",
        "deterministic": deterministic,
        "sha256_first": a.get("sha256"),
        "sha256_second": b.get("sha256"),
        "checks": [],
        "warnings": [],
    }
    report["checks"].append({"name": "two_recomputes_identical", "ok": deterministic})
    v = verify_ledger(a)
    report["checks"].extend(v["checks"])
    # 与落盘产物比对（缺失则 fail-soft warning，不算失败）
    out = Path(cfg.get("ledger_output_path", ""))
    if not out.is_absolute():
        out = root_p / out
    if out.exists():
        try:
            on_disk = _read_json(out)
            same = canonical_json({k: val for k, val in on_disk.items() if k != "sha256"}) == \
                   canonical_json({k: val for k, val in a.items() if k != "sha256"})
            report["checks"].append({"name": "artifact_matches_recompute", "ok": same,
                                     "detail": str(out)})
        except (json.JSONDecodeError, OSError) as e:
            report["warnings"].append(f"落盘产物不可读：{out}: {e}")
    else:
        report["warnings"].append(f"落盘产物不存在（待 --recompute --write）：{out}")
    failed = [c["name"] for c in report["checks"] if not c["ok"]]
    report["failed"] = failed
    report["status"] = "ok" if not failed else "fail"
    return report


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")