#!/usr/bin/env python3
"""预测盲程序：把「先承诺概率、后观测结局」做成可复算的计分前段。

三段时间严格分开（这是整件事的地基）
====================================
1. **plan**：按注入的节点配置枚举待预测问句，按**预登记规则**给每个问句声明概率
   ``p``。规则写在配置里（注入），代码里没有域词条，也没有「看结果再调 p」的开关。
2. **commit**：把 ``sha256(canonical({case_id, kind, target, p, rationale}))`` 作为
   承诺哈希写进哈希链账本（``tools.abm.ledger.TraceLedger``）。承诺一旦入账，任何
   事后改动 p 都会使承诺哈希对不上。
3. **observe**：跑机器判定的检查器，把结局 ``0/1`` 与检查明细写进同一账本。

``integrity()`` 复算「同一问句的首个 observe 必须晚于其 commit」，违规即列出——
这就是「先承诺后观测」的机器证据。计分只吃 ``(p, outcome)``，交给
``metrics.score``；本模块不参与评分口径，避免自己给自己发分。

检查器（全部确定性、可离线复算，与领域无关）
============================================
* ``file_anchor``     —— 文件当前内容 sha256 == 「最后改动它的提交」里的 blob sha256
* ``utf8_text``       —— 文件字节能否按 UTF-8 严格解码
* ``json_parse``      —— 文件能否按 JSON 解析
* ``json_top_is_dict`` —— 解析后顶层是否为 object

诚实边界
========
* 「盲」只能证明**顺序**（commit 早于 observe），不能证明**认知**（操作者有没有先偷看）。
  因此配置里必须写 ``prior_knowledge_disclosure``，报告里必须转述。
* 本模块不判断问句是否有科学意义；问句价值由配置作者负责，模块只保证可复算。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.abm.ledger import TraceLedger, canonical_json, sha256_hex

from . import metrics
from .evidence import blob_sha256_at_commit, last_commit_for_path, sha256_file
from .ledger import ForecastRecord

COMMIT_TYPE = "calib.item.commit"
OBSERVE_TYPE = "calib.item.observe"
SCHEMA = "blind_calibration/episode/v1"

TEXT_EXTS = (".md", ".py", ".json", ".txt", ".csv", ".jsonl", ".yml", ".yaml")


# --------------------------------------------------------------------------- #
# 1) plan
# --------------------------------------------------------------------------- #
def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _iter_files(run_dir: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, _dn, filenames in os.walk(run_dir):
        for name in sorted(filenames):
            out.append(Path(dirpath) / name)
    return sorted(out)


def declare_probability(kind: str, repo_path: str, rule: dict[str, Any]) -> tuple[float, str]:
    """按预登记规则给单个问句声明概率（不读文件内容）。"""
    if kind == "file_anchor":
        r = rule["file_anchor"]
        return float(r["p"]), str(r["ground"])
    if kind == "utf8_text":
        r = rule["utf8_text"]
        text_exts = tuple(str(x).lower() for x in r.get("text_exts", TEXT_EXTS))
        if _ext(repo_path) in text_exts:
            return float(r["p_text"]), str(r["ground"])
        return float(r["p_binary"]), str(r["ground"])
    if kind == "json_parse":
        r = rule["json_parse"]
        return float(r["p"]), str(r["ground"])
    if kind == "json_top_is_dict":
        r = rule["json_top_is_dict"]
        return float(r["p"]), str(r["ground"])
    raise ValueError(f"未知问句类型: {kind}")


def plan(config: dict[str, Any], repo: str | os.PathLike[str] = ".",
         *, require_git_clean: bool = True) -> dict[str, Any]:
    """枚举问句并声明概率（不执行任何检查器、不读文件内容）。"""
    repo_p = Path(repo).resolve()
    rule = config["declaration_rule"]
    kinds = list(config.get("questions") or [])
    items: list[dict[str, Any]] = []
    nodes = []
    for node in config["nodes"]:
        run_dir = Path(str(node["run_dir"]))
        abs_dir = run_dir if run_dir.is_absolute() else repo_p / run_dir
        node_row = {"node_id": str(node["node_id"]), "run_dir": str(node["run_dir"]),
                    "exists": abs_dir.is_dir(), "n_files": 0}
        if abs_dir.is_dir():
            files = _iter_files(abs_dir)
            node_row["n_files"] = len(files)
            for f in files:
                rel_repo = Path(os.path.relpath(f, repo_p)).as_posix()
                for kind in kinds:
                    if kind == "json_parse" and _ext(rel_repo) != ".json":
                        continue
                    if kind == "json_top_is_dict" and _ext(rel_repo) != ".json":
                        continue
                    p, ground = declare_probability(kind, rel_repo, rule)
                    items.append({
                        "case_id": f"{node_row['node_id']}|{kind}|{rel_repo}",
                        "node_id": node_row["node_id"],
                        "kind": kind,
                        "target": rel_repo,
                        "declared_p": p,
                        "rationale": ground,
                    })
        nodes.append(node_row)

    git_clean = _git_status_clean(repo_p, [str(n["run_dir"]) for n in config["nodes"]])
    if require_git_clean and git_clean is False:
        raise RuntimeError("声明时刻工作树对目标目录不干净：file_anchor 的先验条件不成立，"
                           "拒绝出题（避免用噪声先验污染校准集）")
    program = {
        "schema": SCHEMA,
        "declared_at": str(config.get("declared_at") or ""),
        "declared_by": str(config.get("declared_by") or ""),
        "repo_root": str(repo_p),
        "git_clean_at_declaration": git_clean,
        "n_items": len(items),
        "nodes": nodes,
        "questions": kinds,
        "items": items,
        "prior_knowledge_disclosure": list(config.get("prior_knowledge_disclosure") or []),
    }
    program["program_digest"] = sha256_hex({k: v for k, v in program.items()
                                            if k != "program_digest"})
    return program


def _git_status_clean(repo: Path, paths: Sequence[str]) -> bool | None:
    import subprocess
    try:
        proc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", *paths],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return not proc.stdout.decode("utf-8", "replace").strip()


# --------------------------------------------------------------------------- #
# 2) commit / 3) observe
# --------------------------------------------------------------------------- #
def commit(ledger: TraceLedger, program: dict[str, Any], *, ts: str | None = None) -> int:
    """把每个问句的承诺哈希写进账本（先承诺）。"""
    n = 0
    for item in program["items"]:
        body = {"case_id": item["case_id"], "kind": item["kind"],
                "target": item["target"], "declared_p": float(item["declared_p"]),
                "rationale": item["rationale"],
                "program_digest": program["program_digest"]}
        ledger.append(COMMIT_TYPE, "forecaster",
                      {**body, "commitment_sha256": sha256_hex(body)},
                      gate="calibration", ts=ts or program.get("declared_at") or None,
                      evidence_refs=[f"program:{program['program_digest']}"])
        n += 1
    return n


def check(kind: str, repo_path: str, *, repo: str | os.PathLike[str] = ".") -> tuple[int, dict[str, Any]]:
    """执行单个检查器，返回 (outcome, detail)。"""
    repo_p = Path(repo).resolve()
    path = Path(repo_path)
    abs_path = path if path.is_absolute() else repo_p / path
    if kind == "file_anchor":
        if not abs_path.is_file():
            return 0, {"reason": "file_missing"}
        actual = sha256_file(abs_path)
        commit_sha = last_commit_for_path(repo_p, repo_path)
        anchor = blob_sha256_at_commit(repo_p, commit_sha, repo_path) if commit_sha else None
        return (1 if anchor == actual else 0), {"sha256": actual, "anchor_commit": commit_sha,
                                                "anchor_sha256": anchor}
    if kind == "utf8_text":
        try:
            abs_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            return 0, {"error": str(exc)}
        except OSError as exc:
            return 0, {"error": str(exc)}
        return 1, {}
    if kind in ("json_parse", "json_top_is_dict"):
        try:
            data = json.loads(abs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return 0, {"error": str(exc)}
        if kind == "json_parse":
            return 1, {"top_type": type(data).__name__}
        return (1 if isinstance(data, dict) else 0), {"top_type": type(data).__name__}
    raise ValueError(f"未知问句类型: {kind}")


def observe(ledger: TraceLedger, program: dict[str, Any], *,
            repo: str | os.PathLike[str] = ".", ts: str | None = None) -> int:
    """跑检查器、写观测记录（后观测）。"""
    n = 0
    for item in program["items"]:
        outcome, detail = check(item["kind"], item["target"], repo=repo)
        ledger.append(OBSERVE_TYPE, "verifier",
                      {"case_id": item["case_id"], "outcome": int(outcome),
                       "detail": detail, "program_digest": program["program_digest"]},
                      gate="calibration", verdict="pass" if outcome else "fail",
                      ts=ts, evidence_refs=[f"target:{item['target']}"])
        n += 1
    return n


# --------------------------------------------------------------------------- #
# 4) 盲性复算 + 转计分记录
# --------------------------------------------------------------------------- #
def integrity(ledger: TraceLedger) -> dict[str, Any]:
    """复算：同一 case_id 的首个 observe 必须晚于其 commit。"""
    slots: dict[str, dict[str, Any]] = {}
    for rec in ledger.records:
        cid = (rec.get("payload") or {}).get("case_id")
        if not cid:
            continue
        slot = slots.setdefault(str(cid), {"commit": None, "observe": None,
                                           "declared_p": None, "outcome": None})
        if rec["record_type"] == COMMIT_TYPE and slot["commit"] is None:
            slot["commit"] = int(rec["seq"])
            slot["declared_p"] = (rec.get("payload") or {}).get("declared_p")
        elif rec["record_type"] == OBSERVE_TYPE and slot["observe"] is None:
            slot["observe"] = int(rec["seq"])
            slot["outcome"] = (rec.get("payload") or {}).get("outcome")
    violations = []
    for cid, s in sorted(slots.items()):
        if s["commit"] is None:
            violations.append({"case_id": cid, "reason": "observed_without_commitment"})
        elif s["observe"] is not None and s["observe"] < s["commit"]:
            violations.append({"case_id": cid, "reason": "observation_before_commitment",
                               "commit_seq": s["commit"], "observe_seq": s["observe"]})
    unobserved = sorted(cid for cid, s in slots.items() if s["observe"] is None)
    return {"schema": "blind_calibration/integrity/v1", "ok": not violations,
            "n_items": len(slots), "violations": violations, "unobserved": unobserved,
            "order_rule": "first observe seq > commit seq (per case_id)"}


def forecasts(ledger: TraceLedger, *, source: str = "blind_program") -> list[ForecastRecord]:
    """把已提交+已观测的问句转成计分记录（未观测的跳过，并可从 integrity 看到）。"""
    commits: dict[str, dict[str, Any]] = {}
    obs: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for rec in ledger.records:
        p = rec.get("payload") or {}
        cid = p.get("case_id")
        if not cid:
            continue
        if rec["record_type"] == COMMIT_TYPE and cid not in commits:
            commits[cid] = p
            order.append(cid)
        elif rec["record_type"] == OBSERVE_TYPE and cid not in obs:
            obs[cid] = p
    out: list[ForecastRecord] = []
    for cid in order:
        if cid not in obs:
            continue
        out.append(ForecastRecord(
            case_id=cid,
            prob=float(commits[cid]["declared_p"]),
            outcome=int(obs[cid]["outcome"]),
            source=source,
            group=str(commits[cid].get("kind") or ""),
            evidence_refs=(f"target:{commits[cid].get('target')}",
                           f"commitment:{commits[cid].get('commitment_sha256')}"),
        ))
    return out


def group_summary(records: Sequence[ForecastRecord], n_bins: int = metrics.DEFAULT_N_BINS
                  ) -> dict[str, Any]:
    """按问句类型拆开看：哪类问句的置信度最不可信（暴露系统性偏乐观）。"""
    out: dict[str, Any] = {}
    for g in sorted({r.group for r in records}):
        sel = [r for r in records if r.group == g]
        out[g] = {
            "n": len(sel),
            "brier": metrics.brier_score([r.prob for r in sel], [r.outcome for r in sel]),
            "mean_prob": sum(r.prob for r in sel) / len(sel),
            "hit_rate": sum(r.outcome for r in sel) / len(sel),
            "max_deviation": metrics.reliability_curve(
                [r.prob for r in sel], [r.outcome for r in sel], n_bins)["max_deviation"],
        }
    return out