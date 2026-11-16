#!/usr/bin/env python3
"""能力证据台账单一真源（SSoT）读取路径 + 双向分歧检测器（m22585 / r27-WP3）。

病根
----
同一 capability 在两个地方有各自沉默的判读：

* **编排侧工具** ``audit_capabilities``（operations worker →
  ``tools/audit/ledger_consumer_audit_tool.py``）渲染平台发布载荷
  ``backend/platform_harness/capabilities.json``（由 ``tools/capability_ledger/engine``
  的报告元数据复算发布），等级字段 ``evidence``；
* **仓库产物** ``artifacts/capability_ledger/capability_ledger_resolved.json``
  （m21819 证据对账落盘），等级字段 ``reconciled_level`` + ``basis`` + ``line_sha256``。

两条管线对同一批 21 条能力给出不同甚至相反的等级（稳性计算一项：工具 MOCK / 产物
REAL；几何静水力曲线一项：工具 REAL / 产物 WITHHELD；具体 id 一律由产物读出，本文件
不写死），且没有任何一方把分歧暴露出来 —— 读者只会看到「自己正在看的那一侧」。

本模块的契约
------------
1. **等级只能由产物推出**：本文件中不存在任何 ``capability_id -> 等级`` 常量表，
   所有等级都从 JSON 产物读入（见 ``tests/test_capability_ledger_ssot.py`` 的
   grep 负对照 ``test_no_hardcoded_level_table``）。
2. **同一解析器**：``parse_resolved`` / ``parse_harness`` 是两类产物的唯一解析入口，
   工具渲染层（``tools/audit/ledger_consumer_audit_tool.py``）与本模块共用，
   因此工具看到的等级与矩阵里的一致。
3. **分歧必须可定位**：两侧不一致时输出 divergence 记录
   （``capability_id`` / 两侧等级 / ``basis`` / ``line_sha256`` / 源文件 sha256），
   而不是各自沉默。
4. **口径可复算**：``with_evidence`` / ``without_evidence`` / ``divergence_tally``
   由产物逐行重算并与产物自述汇总比对（``summary_match``）。

真源选型
--------
* **等级真源（canonical）** = ``capability_ledger_resolved.json``：它是唯一带
  ``basis`` + ``line_sha256`` 的可核验产物（每条等级都能回溯到报告原文行），
  满足「等级只能由证据推出」。工具侧 harness 载荷是 *被检测对象*，不作为真源。
* **工具侧读数（tool）** = 平台发布载荷，优先实时读取（``SHIP_PLATFORM_ROOT`` /
  已知平台根 / 仓内 harness 路径），读不到时退回带 sha256 的落盘快照
  ``ssot_tool_snapshot.json``，并在 provenance 里标 ``stale=True``。

用法::

    python3 -m tools.capability_ledger.ssot --capture --write   # 抓工具侧 + 落矩阵
    python3 -m tools.capability_ledger.ssot --print             # 人读矩阵
    python3 -m tools.capability_ledger.ssot --check             # 不可解释分歧 → 退出码 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

LEVELS = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
#: 等级序（只用于比较两侧高低，不是等级表）
LEVEL_RANK = {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4}
DIVERGENCES = ("OK", "OVERCLAIM", "REVERIFY", "UNVERIFIED")

CANONICAL_REL = "artifacts/capability_ledger/capability_ledger_resolved.json"
MATRIX_REL = "artifacts/capability_ledger/ssot_matrix.json"
SNAPSHOT_REL = "artifacts/capability_ledger/ssot_tool_snapshot.json"
#: 平台发布载荷（编排侧工具的数据源）在工作树内 / 常见平台根下的相对位置
HARNESS_REL = Path("backend/platform_harness/capabilities.json")
PLATFORM_ROOTS = (
    "/root/ship-performance-platform-incoming/ship-performance-platform",
    "/root/tensor-ecosystem/ship-performance-platform",
)
NO_EVIDENCE_BASIS = "无证据"


# ────────────────────────────────────────────── 基础工具
def repo_root(start: str | Path | None = None) -> Path:
    if start is not None:
        return Path(start).resolve()
    return Path(__file__).resolve().parents[2]


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_of(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def _read_json_with_sha(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), sha256_bytes(raw)


# ────────────────────────────────────────────── 唯一解析器（两类产物）
def parse_resolved(payload: dict) -> dict[str, dict[str, Any]]:
    """解析 canonical 产物（list 型 ``capabilities``，等级字段 ``reconciled_level``）。

    输出 ``{capability_id: {claimed_level, reconciled_level, divergence, basis,
    evidence_count, domain, evidence_line_sha256(list), row_sha256, raw}}``。
    缺失字段按产物契约记空值，由 layer1 检查报违规——解析器本身不做等级判断。
    """
    caps = payload.get("capabilities")
    if not isinstance(caps, list):
        raise ValueError("canonical 产物 schema 不可识别（缺 list 型 capabilities）")
    out: dict[str, dict[str, Any]] = {}
    for row in caps:
        cid = str(row.get("id") or "")
        if not cid:
            raise ValueError("canonical 产物存在无 id 的能力行")
        refs = row.get("evidence_refs") or []
        out[cid] = {
            "capability_id": cid,
            "domain": row.get("domain"),
            "claimed_level": row.get("claimed_level"),
            "reconciled_level": row.get("reconciled_level"),
            "divergence": row.get("divergence"),
            "basis": row.get("basis", ""),
            "evidence_count": row.get("evidence_count", 0),
            "evidence_line_sha256": [
                str(r.get("line_sha256")) for r in refs if r.get("line_sha256")
            ],
            "latest_evidence_report": row.get("latest_evidence_report"),
            "row_sha256": sha256_of(row),
            "raw": row,
        }
    return out


def parse_harness(payload: dict) -> dict[str, dict[str, Any]]:
    """解析工具侧载荷（dict 型 ``capabilities``，等级字段 ``evidence``）。"""
    caps = payload.get("capabilities")
    if not isinstance(caps, dict):
        raise ValueError("工具侧载荷 schema 不可识别（缺 dict 型 capabilities）")
    out: dict[str, dict[str, Any]] = {}
    for cid, val in caps.items():
        if isinstance(val, dict):
            lvl = val.get("evidence")
            detail = {
                "evidence_count": val.get("evidence_count"),
                "grade_source": val.get("grade_source"),
                "engine_grade": val.get("evidence_engine_grade"),
                "reason": val.get("reason"),
                "registry_level": val.get("registry_level"),
            }
        else:
            lvl, detail = val, {}
        out[str(cid)] = {"capability_id": str(cid), "level": lvl,
                         "detail": detail,
                         "row_sha256": sha256_of({"id": cid, "level": lvl,
                                                  "detail": detail})}
    return out


# ────────────────────────────────────────────── 第一层：产物内部自洽
def expected_divergence(claimed: str | None, reconciled: str | None,
                        evidence_count: int) -> str:
    """由产物自身字段互推「应然」分歧标签（产物契约的推导规则，非等级表）。

    * 零证据行：自述 REAL → ``UNVERIFIED``（未证实不得计入 REAL），其余 → ``OK``
      （无可核验证据时对账值取保守 WITHHELD，不构成 overclaim）；
    * 有证据行：自述 > 对账 → ``OVERCLAIM``；自述 < 对账 → ``REVERIFY``；相等 → ``OK``。
    """
    if int(evidence_count or 0) <= 0:
        return "UNVERIFIED" if claimed == "REAL" else "OK"
    c, r = LEVEL_RANK.get(str(claimed), -1), LEVEL_RANK.get(str(reconciled), -1)
    if c > r:
        return "OVERCLAIM"
    if c < r:
        return "REVERIFY"
    return "OK"


def recompute_summary(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从产物逐行重算口径（with_evidence / without_evidence / divergence_tally）。"""
    tally = {k: 0 for k in DIVERGENCES}
    with_ev = 0
    evidence_rows = 0
    for r in rows.values():
        tally[r["divergence"]] = tally.get(r["divergence"], 0) + 1
        n = int(r["evidence_count"] or 0)
        evidence_rows += n
        if n > 0:
            with_ev += 1
    return {"capabilities": len(rows), "with_evidence": with_ev,
            "without_evidence": len(rows) - with_ev,
            "divergence_tally": tally, "evidence_rows": evidence_rows}


def layer1_check(rows: dict[str, dict[str, Any]],
                 declared_summary: dict | None = None) -> dict[str, Any]:
    """第一层：产物内部自洽（claimed vs reconciled vs divergence vs basis vs 汇总）。"""
    checks: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    checks.append({"name": "row_count_21", "ok": len(rows) == 21,
                   "detail": f"rows={len(rows)}"})

    for cid, r in sorted(rows.items()):
        exp = expected_divergence(r["claimed_level"], r["reconciled_level"],
                                 int(r["evidence_count"] or 0))
        if exp != r["divergence"]:
            violations.append({"check": "divergence_derivable", "capability_id": cid,
                               "expected": exp, "declared": r["divergence"]})
        if r["divergence"] not in DIVERGENCES:
            violations.append({"check": "divergence_in_enum", "capability_id": cid,
                               "declared": r["divergence"]})
        if r["claimed_level"] not in LEVELS or r["reconciled_level"] not in LEVELS:
            violations.append({"check": "levels_in_enum", "capability_id": cid,
                               "claimed": r["claimed_level"],
                               "reconciled": r["reconciled_level"]})
        # basis 与证据行必须互相支持
        if int(r["evidence_count"] or 0) > 0:
            if not r["evidence_line_sha256"]:
                violations.append({"check": "evidence_line_sha256_present",
                                   "capability_id": cid})
            refs = (r.get("raw") or {}).get("evidence_refs") or []
            if r["evidence_count"] != len(refs):
                violations.append({"check": "evidence_count_matches_refs",
                                   "capability_id": cid,
                                   "declared": r["evidence_count"], "refs": len(refs)})
            if not str(r["basis"] or "").strip():
                violations.append({"check": "basis_non_empty", "capability_id": cid})
            rep = r.get("latest_evidence_report")
            if rep and str(rep) not in str(r["basis"] or ""):
                violations.append({"check": "basis_cites_latest_evidence",
                                   "capability_id": cid, "latest": rep})
        else:
            if not str(r["basis"] or "").strip():
                violations.append({"check": "basis_non_empty", "capability_id": cid})
            if NO_EVIDENCE_BASIS not in str(r["basis"] or ""):
                violations.append({"check": "no_evidence_basis_convention",
                                   "capability_id": cid, "basis": r["basis"]})

    recomputed = recompute_summary(rows)
    declared = declared_summary or {}
    # 汇总可能写成嵌套 ``divergence_tally`` 或平铺键，两种都认
    declared_tally = declared.get("divergence_tally")
    if not isinstance(declared_tally, dict):
        declared_tally = {k: declared[k] for k in DIVERGENCES if k in declared}
    tally_ok = all(declared_tally.get(k) == v
                   for k, v in recomputed["divergence_tally"].items())
    count_ok = (declared.get("with_evidence") == recomputed["with_evidence"]
                and declared.get("without_evidence") == recomputed["without_evidence"])
    if not tally_ok:
        violations.append({"check": "summary_divergence_tally_recomputable",
                           "declared": declared_tally,
                           "recomputed": recomputed["divergence_tally"]})
    if not count_ok:
        violations.append({"check": "summary_evidence_counts_recomputable",
                           "declared": {"with_evidence": declared.get("with_evidence"),
                                        "without_evidence": declared.get("without_evidence")},
                           "recomputed": {"with_evidence": recomputed["with_evidence"],
                                          "without_evidence": recomputed["without_evidence"]}})
    if declared.get("capabilities") not in (None, len(rows)):
        violations.append({"check": "summary_capability_count",
                           "declared": declared.get("capabilities"),
                           "recomputed": len(rows)})

    checks.append({"name": "layer1_violations_zero", "ok": not violations,
                   "detail": f"violations={len(violations)}"})
    checks.append({"name": "summary_recomputable", "ok": tally_ok and count_ok})
    return {"status": "ok" if not violations else "fail",
            "checks": checks, "violations": violations,
            "recomputed_summary": recomputed, "declared_summary": declared}


# ────────────────────────────────────────────── 工具侧读数（实时 / 快照）
def tool_source_candidates(root: Path) -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SHIP_PLATFORM_ROOT")
    if env:
        cands.append(Path(env) / HARNESS_REL)
    cands.append(Path(root) / HARNESS_REL)
    for pr in PLATFORM_ROOTS:
        cands.append(Path(pr) / HARNESS_REL)
    return cands


def capture_tool_source(root: Path) -> dict[str, Any]:
    """抓取工具侧读数（第一个可解析候选），带路径与 sha256 provenance。"""
    tried: list[str] = []
    for cand in tool_source_candidates(root):
        try:
            payload, sha = _read_json_with_sha(cand)
            levels = parse_harness(payload)
        except Exception as exc:  # noqa: BLE001 — 候选逐个试，最后如实报出
            tried.append(f"{cand}: {type(exc).__name__}: {exc}")
            continue
        return {"provenance": {"kind": "live", "path": str(cand), "sha256": sha,
                               "as_of": payload.get("as_of"),
                               "source_ledger_sha256": payload.get("source_ledger_sha256"),
                               "stale": False, "tried": tried},
                "levels": levels}
    snap_path = Path(root) / SNAPSHOT_REL
    if snap_path.is_file():
        snap, sha = _read_json_with_sha(snap_path)
        levels = parse_harness({"capabilities": snap.get("capabilities", {})})
        return {"provenance": {"kind": "snapshot", "path": str(snap_path),
                               "sha256": sha, "as_of": snap.get("as_of"),
                               "source": snap.get("source"), "stale": True,
                               "tried": tried},
                "levels": levels}
    raise FileNotFoundError("工具侧载荷不可读，且无快照：" + "; ".join(tried))


def write_tool_snapshot(root: Path, capture: dict[str, Any]) -> Path:
    prov = capture["provenance"]
    body = {
        "schema_version": "capability-ledger-tool-snapshot/1.0",
        "note": "编排侧工具（audit_capabilities）读数的落盘快照；等级逐项来自 source "
                "的原文（sha256 见 source_sha256），供离线复算与测试使用。",
        "source": prov.get("path"),
        "source_sha256": prov.get("sha256"),
        "as_of": prov.get("as_of"),
        "capabilities": {cid: {"evidence": v["level"], **v["detail"]}
                         for cid, v in sorted(capture["levels"].items())},
    }
    path = Path(root) / SNAPSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def ledger_contract_view(root: Path | None = None) -> dict[str, Any]:
    """第三源（只记录，不参与判决）：消费者契约门对 ``capability_ledger.json`` 的读数。

    编排侧工具的正文尾部会打印 ``未证实=… OVERCLAIM=…``，其数字来自
    ``tools.audit.ledger_paths.resolve_ledger()`` 解析的**另一份**产物
    （``capability_ledger.json``，schema 2.0，逐条 evidence 列表，尚未按最新证据对账）。
    它与 canonical 的 ``divergence_tally`` **不可直接比数**（口径不同），但绝不允许沉默：
    此处记录其路径/sha256/计数，并在矩阵 ``notes`` 里说明可比性边界。
    """
    root = repo_root(root)
    try:
        from tools.audit import ledger_paths as LP

        ref = LP.resolve_ledger()
        return {"path": ref.get("path_rel"), "sha256": ref.get("sha256"),
                "schema_version": ref.get("schema_version"),
                "unverified": ref.get("unverified"),
                "overclaim": ref.get("overclaim"),
                "status": ref.get("status"), "reasons": ref.get("reasons"),
                "note": ("该计数来自 capability_ledger.json（对账前台账）的独立口径，"
                         "与 canonical 的 divergence_tally 不可直接比数；"
                         "仅用于暴露「工具契约行数字 ≠ 产物口径」这一残留差异。")}
    except Exception as exc:  # noqa: BLE001 — 第三源只做记录，失败不得影响矩阵
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


# ────────────────────────────────────────────── 第二层：工具 vs 产物
def layer2_matrix(artifact: dict[str, dict[str, Any]],
                  tool_levels: dict[str, dict[str, Any]],
                  tool_provenance: dict[str, Any]) -> list[dict[str, Any]]:
    """第二层：21×2 逐项比对，产出可定位矩阵行（含 basis / line_sha256 / 源 sha256）。"""
    rows: list[dict[str, Any]] = []
    tool_sha = tool_provenance.get("sha256")
    for cid in sorted(set(artifact) | set(tool_levels)):
        a = artifact.get(cid)
        t = tool_levels.get(cid)
        a_level = a["reconciled_level"] if a else None
        t_level = t["level"] if t else None
        agree = (a is not None and t is not None and a_level == t_level)
        basis = (a or {}).get("basis", "")
        line_shas = (a or {}).get("evidence_line_sha256") or []
        rec = None
        if not agree:
            rec = {
                "capability_id": cid,
                "artifact_level": a_level,
                "tool_level": t_level,
                "artifact_basis": basis,
                "artifact_basis_sha256": sha256_text(basis) if basis else None,
                "artifact_basis_line_sha256": line_shas[-1] if line_shas else None,
                "artifact_row_sha256": (a or {}).get("row_sha256"),
                "artifact_evidence_count": (a or {}).get("evidence_count"),
                "tool_basis": ((t or {}).get("detail") or {}).get("reason"),
                "tool_source_sha256": tool_sha,
                "tool_row_sha256": (t or {}).get("row_sha256"),
                "explained": bool(basis) and t is not None,
                "direction": ("tool>artifact(工具高判)"
                              if a_level and t_level
                              and LEVEL_RANK.get(t_level, -1) > LEVEL_RANK.get(a_level, -1)
                              else "tool<artifact(工具低判)"),
            }
        rows.append({
            "capability_id": cid,
            "domain": (a or {}).get("domain"),
            "artifact": {
                "level": a_level,
                "claimed_level": (a or {}).get("claimed_level"),
                "internal_divergence": (a or {}).get("divergence"),
                "basis": basis,
                "basis_sha256": sha256_text(basis) if basis else None,
                "evidence_line_sha256": line_shas,
                "row_sha256": (a or {}).get("row_sha256"),
            },
            "tool": {
                "level": t_level,
                "source": tool_provenance.get("path"),
                "source_sha256": tool_sha,
                "row_sha256": (t or {}).get("row_sha256"),
                "detail": (t or {}).get("detail"),
            },
            "agree": agree,
            "divergence_record": rec,
        })
    return rows


def _real_verifiable(matrix_rows: Iterable[dict],
                     artifact: dict[str, dict]) -> int:
    n = 0
    for r in matrix_rows:
        a = artifact.get(r["capability_id"]) or {}
        if r["artifact"]["level"] == "REAL" and int(a.get("evidence_count") or 0) > 0:
            n += 1
    return n


def build_matrix(root: Path | None = None, artifact_path: Path | None = None,
                 tool_capture: dict[str, Any] | None = None) -> dict[str, Any]:
    """装配 SSoT 矩阵：第一层自洽 + 第二层 21×2 比对 + 口径复算。"""
    root = repo_root(root)
    artifact_path = Path(artifact_path) if artifact_path else (root / CANONICAL_REL)
    artifact_payload, artifact_sha = _read_json_with_sha(artifact_path)
    artifact = parse_resolved(artifact_payload)
    capture = tool_capture or capture_tool_source(root)
    check1 = layer1_check(artifact, artifact_payload.get("summary"))
    matrix_rows = layer2_matrix(artifact, capture["levels"], capture["provenance"])

    disagree = [r for r in matrix_rows if not r["agree"]]
    unexplained = [r for r in disagree
                   if not (r["divergence_record"] or {}).get("explained")]
    dist_artifact = {lv: 0 for lv in LEVELS}
    dist_tool = {lv: 0 for lv in LEVELS}
    for r in matrix_rows:
        if r["artifact"]["level"] in dist_artifact:
            dist_artifact[r["artifact"]["level"]] += 1
        if r["tool"]["level"] in dist_tool:
            dist_tool[r["tool"]["level"]] += 1

    try:
        art_rel = str(artifact_path.relative_to(root))
    except ValueError:
        art_rel = str(artifact_path)

    return {
        "schema_version": "capability-ledger-ssot-matrix/1.0",
        "mission": "m22585",
        "sources": {
            "artifact": {"path": art_rel, "sha256": artifact_sha,
                         "role": "canonical（唯一带 basis + line_sha256 的可核验产物）",
                         "schema_version": artifact_payload.get("schema_version")},
            "tool": {**capture["provenance"],
                     "role": "被检测对象（编排侧 audit_capabilities 读数）"},
        },
        "counts": {
            "capabilities": len(matrix_rows),
            "agree": len(matrix_rows) - len(disagree),
            "disagree": len(disagree),
            "unexplained": len(unexplained),
            "artifact_level_distribution": dist_artifact,
            "tool_level_distribution": dist_tool,
        },
        "summary_recompute": {
            "declared": artifact_payload.get("summary") or {},
            "recomputed": check1["recomputed_summary"],
            "with_evidence": check1["recomputed_summary"]["with_evidence"],
            "without_evidence": check1["recomputed_summary"]["without_evidence"],
            "divergence_tally": check1["recomputed_summary"]["divergence_tally"],
            "match": not [v for v in check1["violations"]
                          if str(v.get("check", "")).startswith("summary_")],
        },
        "layer1": {"status": check1["status"], "checks": check1["checks"],
                   "violations": check1["violations"]},
        "rows": matrix_rows,
        "divergence_records": [r["divergence_record"] for r in disagree],
        "ledger_contract_source": ledger_contract_view(root),
        "unregistered_domains": artifact_payload.get("unregistered_domains") or [],
        "unregistered_domains_note": ("只读清单：注册表覆盖缺口由产物给出，"
                                      "本单不写注册表（config/capabilities.json 冻结）"),
        "real_verifiable_count": _real_verifiable(matrix_rows, artifact),
        "real_verifiable_note": (
            "REAL 可核验计数只统计「canonical 产物判 REAL 且 evidence_count>0」的行；"
            "工具侧单独判 REAL 而产物无证据的行不计入，另列 tool_only_real。"),
        "tool_only_real": sorted(
            r["capability_id"] for r in matrix_rows
            if r["tool"]["level"] == "REAL" and r["artifact"]["level"] != "REAL"),
    }


def write_matrix(root: Path, matrix: dict[str, Any]) -> Path:
    path = Path(root) / MATRIX_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matrix, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


# ────────────────────────────────────────────── 供工具渲染层共用的一个入口
def artifact_levels(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """工具渲染层查询用：返回 canonical 产物逐能力判读（读不到则空表，不抛异常）。"""
    root = repo_root(root)
    try:
        payload, sha = _read_json_with_sha(root / CANONICAL_REL)
        rows = parse_resolved(payload)
    except Exception:  # noqa: BLE001 — 渲染层不得因台账问题 500/崩
        return {}
    for r in rows.values():
        r["artifact_path"] = str(root / CANONICAL_REL)
        r["artifact_sha256"] = sha
    return rows


# ────────────────────────────────────────────── CLI
def render_text(matrix: dict[str, Any]) -> str:
    c = matrix["counts"]
    lines = [
        f"SSoT 矩阵 {c['capabilities']} 项：一致 {c['agree']} / 分歧 {c['disagree']}"
        f"（不可解释 {c['unexplained']}）",
        f"canonical: {matrix['sources']['artifact']['path']} "
        f"sha256={matrix['sources']['artifact']['sha256'][:16]}",
        f"tool     : {matrix['sources']['tool'].get('path')} "
        f"sha256={(matrix['sources']['tool'].get('sha256') or '')[:16]}",
        f"口径复算 with_evidence={matrix['summary_recompute']['with_evidence']} "
        f"without_evidence={matrix['summary_recompute']['without_evidence']} "
        f"tally={matrix['summary_recompute']['divergence_tally']} "
        f"match={matrix['summary_recompute']['match']}",
        f"layer1={matrix['layer1']['status']} "
        f"违规 {len(matrix['layer1']['violations'])} 项",
        f"可核验 REAL 计数={matrix['real_verifiable_count']} "
        f"（工具侧单独判 REAL：{', '.join(matrix['tool_only_real']) or '无'}）",
        "",
        f"{'capability':26s}{'artifact':14s}{'tool':14s}{'agree':7s}内部分歧",
    ]
    for r in matrix["rows"]:
        lines.append(f"{r['capability_id']:26s}{str(r['artifact']['level']):14s}"
                     f"{str(r['tool']['level']):14s}{str(r['agree']):7s}"
                     f"{r['artifact']['internal_divergence']}")
    if matrix["divergence_records"]:
        lines.append("")
        lines.append("分歧记录（可定位）：")
        for d in matrix["divergence_records"]:
            lines.append(f"  {d['capability_id']:26s} {d['artifact_level']} vs "
                         f"{d['tool_level']} [{d['direction']}] basis="
                         f"{str(d['artifact_basis'])[:56]} "
                         f"line_sha256={d['artifact_basis_line_sha256']}")
    if matrix["unregistered_domains"]:
        lines.append("")
        lines.append("注册表覆盖缺口（只读）: " + "；".join(
            str(g.get("domain") if isinstance(g, dict) else g)
            for g in matrix["unregistered_domains"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="capability_ledger.ssot",
                                 description="能力证据台账单一真源矩阵 + 双向分歧检测")
    ap.add_argument("--root", default=None)
    ap.add_argument("--artifact", default=None, help="canonical 产物路径覆盖")
    ap.add_argument("--capture", action="store_true", help="抓工具侧读数并落快照")
    ap.add_argument("--write", action="store_true", help="落 ssot_matrix.json")
    ap.add_argument("--print", dest="do_print", action="store_true", help="打印矩阵")
    ap.add_argument("--json", action="store_true", help="打印矩阵 JSON")
    ap.add_argument("--check", action="store_true",
                    help="layer1 违规或不可解释分歧 → 退出码 1")
    args = ap.parse_args(argv)

    root = repo_root(args.root)
    capture = None
    if args.capture:
        capture = capture_tool_source(root)
        if capture["provenance"]["kind"] == "live":
            write_tool_snapshot(root, capture)
    art = Path(args.artifact) if args.artifact else None
    matrix = build_matrix(root, artifact_path=art, tool_capture=capture)
    if args.write:
        p = write_matrix(root, matrix)
        print(f"[ssot] matrix -> {p}")
    if args.json:
        print(json.dumps(matrix, ensure_ascii=False, indent=1, sort_keys=True))
    if args.do_print or not (args.write or args.json or args.check):
        print(render_text(matrix))
    if args.check:
        bad = matrix["layer1"]["status"] != "ok" or matrix["counts"]["unexplained"] > 0
        print(f"[ssot] check {'FAIL' if bad else 'OK'} "
              f"layer1={matrix['layer1']['status']} "
              f"unexplained={matrix['counts']['unexplained']}")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())