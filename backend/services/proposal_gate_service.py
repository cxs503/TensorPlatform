"""Proposal Gate Service — 提案→战役播种供给链机器门。

三道机器门：
1. Schema 契约（fail-closed）：JSON Schema v1/v2 校验 + 兼容层映射
2. 跨战役去重器：NFKC 归一化 + 字符 3-gram Jaccard + 结构化字段冲突检测
3. 播种覆盖率账本：proposal_gate_ledger 表 + 幂等写入 + 统计接口

零领域语义：所有域词由配置文件注入，代码不含任何领域词条。
"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
from sqlalchemy import text
from sqlalchemy.orm import Session


# ───────────────────────── Configuration ─────────────────────────

_CONFIG_CACHE: dict | None = None
_SCHEMA_CACHE: dict[str, dict] = {}


def _load_config() -> dict:
    """Load proposal gate configuration (cached)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "proposal_gate_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        _CONFIG_CACHE = json.load(f)
    return _CONFIG_CACHE


def _load_schema(version: str) -> dict:
    """Load JSON Schema for given version (cached)."""
    if version in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[version]
    
    schema_path = Path(__file__).resolve().parent.parent.parent / "schemas" / f"proposal_{version}.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    _SCHEMA_CACHE[version] = schema
    return schema


# ───────────────────────── Schema Contract Gate ─────────────────────────

class SchemaValidationError(Exception):
    """Schema validation failed."""
    def __init__(self, errors: list[str], schema_version: str):
        self.errors = errors
        self.schema_version = schema_version
        super().__init__(f"Schema validation failed: {'; '.join(errors)}")


def validate_proposal_schema(proposal: dict, schema_version: str | None = None) -> dict:
    """
    Validate proposal against JSON Schema (fail-closed).
    
    Returns normalized proposal dict.
    Raises SchemaValidationError on failure.
    """
    config = _load_config()
    
    # Determine schema version
    if schema_version is None:
        schema_version = proposal.get("schema_version", config["schema_contract"]["default_version"])
    
    if schema_version not in config["schema_contract"]["supported_versions"]:
        raise SchemaValidationError(
            [f"Unsupported schema version: {schema_version}"],
            schema_version
        )
    
    # Load schema
    schema = _load_schema(schema_version)
    
    # Validate against schema
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for error in validator.iter_errors(proposal):
        path = ".".join(str(p) for p in error.absolute_path) if error.absolute_path else "<root>"
        errors.append(f"{path}: {error.message}")
    
    if errors:
        raise SchemaValidationError(errors, schema_version)
    
    # Additional business rules validation
    rules = config["schema_contract"]["validation_rules"]
    business_errors = []
    
    # Title non-empty
    title = proposal.get("title", "")
    if len(title.strip()) < rules["title_min_length"]:
        business_errors.append("title must be non-empty")
    if len(title) > rules["title_max_length"]:
        business_errors.append(f"title exceeds max length {rules['title_max_length']}")
    
    # Repo whitelist
    repo = proposal.get("repo", "")
    if repo not in config["repo_whitelist"]:
        business_errors.append(f"repo '{repo}' not in whitelist")
    
    # Goals validation (v1 allows fewer goals; they'll be padded during migration)
    goals = proposal.get("goals", [])
    if schema_version == "v2" and len(goals) < rules["goal_min_count"]:
        business_errors.append(f"must have at least {rules['goal_min_count']} goals")
    
    if rules["goal_threshold_required"]:
        for i, goal in enumerate(goals):
            if "threshold" not in goal or goal["threshold"] is None:
                business_errors.append(f"goal[{i}] missing required threshold")
    
    # is_maturity must be bool (v2 only)
    if schema_version == "v2":
        if "is_maturity" not in proposal:
            business_errors.append("is_maturity is required for v2 schema")
        elif not isinstance(proposal["is_maturity"], bool):
            business_errors.append("is_maturity must be boolean")
    
    if business_errors:
        raise SchemaValidationError(business_errors, schema_version)
    
    # Normalize proposal
    normalized = _normalize_proposal(proposal, schema_version)
    return normalized


def _normalize_proposal(proposal: dict, schema_version: str) -> dict:
    """Normalize proposal to internal v2 model."""
    if schema_version == "v2":
        # Already v2, just ensure defaults
        result = proposal.copy()
        result.setdefault("description", "")
        result.setdefault("dependencies_hint", [])
        result.setdefault("priority", "medium")
        result.setdefault("metadata", {})
        return result
    
    # v1 → v2 mapping (compatibility layer)
    result = {
        "title": proposal["title"],
        "repo": proposal["repo"],
        "description": proposal.get("description", ""),
        "goals": [],
        "dependencies_hint": proposal.get("dependencies", []),
        "is_maturity": False,  # v1 proposals are not maturity tasks by default
        "priority": proposal.get("priority", "medium"),
        "metadata": proposal.get("metadata", {}),
        "schema_version": "v2",
        "migrated_from": "v1",
    }
    
    # Map v1 goals to v2 format
    for goal in proposal.get("goals", []):
        v2_goal = {
            "name": goal.get("name", ""),
            "metric": goal.get("metric", "unspecified"),
            "threshold": goal.get("threshold", 0.0),
            "comparison": "<=",
        }
        result["goals"].append(v2_goal)
    
    # Ensure minimum 3 goals (pad with placeholders if needed)
    while len(result["goals"]) < 3:
        result["goals"].append({
            "name": f"placeholder_goal_{len(result['goals']) + 1}",
            "metric": "unspecified",
            "threshold": 0.0,
            "comparison": "<=",
        })
    
    return result


# ───────────────────────── Deduplicator Gate ─────────────────────────

def _normalize_text(text: str) -> str:
    """NFKC normalization + lowercase + whitespace collapse."""
    # NFKC normalization
    normalized = unicodedata.normalize("NFKC", text)
    # Lowercase
    normalized = normalized.lower()
    # Collapse whitespace
    normalized = " ".join(normalized.split())
    return normalized


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    """Extract character n-grams from text."""
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i+n] for i in range(len(text) - n + 1)}


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union)


def compute_proposal_signature(proposal: dict) -> str:
    """Compute SHA256 signature of proposal (for idempotency)."""
    # Canonical JSON representation
    canonical = json.dumps(proposal, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_duplicate_proposal(
    proposal: dict,
    db: Session,
    organization_id: int,
) -> dict | None:
    """
    Find duplicate proposal in ledger.
    
    Returns duplicate record if found, None otherwise.
    """
    config = _load_config()
    dedup_config = config["deduplicator"]
    
    if not dedup_config["enabled"]:
        return None
    
    # Compute signature
    signature = compute_proposal_signature(proposal)
    
    # Check exact match first (idempotency)
    existing = db.execute(
        text("""
            SELECT mission_id, plan_id, sha256, schema_version, verdict, dup_of, score, at
            FROM proposal_gate_ledger
            WHERE organization_id = :org AND sha256 = :sig
        """),
        {"org": organization_id, "sig": signature},
    ).mappings().first()
    
    if existing:
        return dict(existing)
    
    # Text similarity check
    ngram_size = dedup_config["ngram_size"]
    jaccard_threshold = dedup_config["jaccard_threshold"]
    dep_overlap_threshold = dedup_config["dependency_overlap_threshold"]
    
    # Normalize proposal text
    title_norm = _normalize_text(proposal.get("title", ""))
    desc_norm = _normalize_text(proposal.get("description", ""))
    full_text = f"{title_norm} {desc_norm}"
    proposal_ngrams = _char_ngrams(full_text, ngram_size)
    
    # Get dependencies as set
    proposal_deps = set(proposal.get("dependencies_hint", []))
    
    # Fetch recent proposals from same repo
    candidates = db.execute(
        text("""
            SELECT mission_id, plan_id, sha256, schema_version, verdict, dup_of, score, at,
                   proposal_data
            FROM proposal_gate_ledger
            WHERE organization_id = :org AND repo = :repo AND verdict != 'duplicate'
            ORDER BY at DESC
            LIMIT 100
        """),
        {"org": organization_id, "repo": proposal.get("repo", "")},
    ).mappings().all()
    
    best_match = None
    best_score = 0.0
    
    for candidate in candidates:
        candidate_data = json.loads(candidate["proposal_data"]) if candidate["proposal_data"] else {}
        
        # Text similarity
        cand_title = _normalize_text(candidate_data.get("title", ""))
        cand_desc = _normalize_text(candidate_data.get("description", ""))
        cand_text = f"{cand_title} {cand_desc}"
        cand_ngrams = _char_ngrams(cand_text, ngram_size)
        
        text_similarity = _jaccard_similarity(proposal_ngrams, cand_ngrams)
        
        # Dependency overlap
        cand_deps = set(candidate_data.get("dependencies_hint", []))
        if proposal_deps and cand_deps:
            dep_overlap = _jaccard_similarity(proposal_deps, cand_deps)
        else:
            dep_overlap = 0.0
        
        # Combined score
        # If same repo and high dependency overlap → likely duplicate
        if dep_overlap >= dep_overlap_threshold:
            combined_score = 0.7 * text_similarity + 0.3 * dep_overlap
        else:
            combined_score = text_similarity
        
        if combined_score >= jaccard_threshold and combined_score > best_score:
            best_score = combined_score
            best_match = dict(candidate)
            best_match["similarity_score"] = combined_score
    
    return best_match


# ───────────────────────── Ledger Gate ─────────────────────────

def ensure_ledger_table(db: Session) -> None:
    """Create proposal_gate_ledger table if not exists."""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS proposal_gate_ledger (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL,
            mission_id INTEGER,
            plan_id INTEGER,
            sha256 VARCHAR(64) NOT NULL,
            schema_version VARCHAR(10) NOT NULL,
            repo VARCHAR(100) NOT NULL,
            title VARCHAR(500) NOT NULL,
            verdict VARCHAR(20) NOT NULL,
            dup_of VARCHAR(64),
            score DECIMAL(5,4),
            proposal_data TEXT,
            validation_errors TEXT,
            at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE(organization_id, mission_id, sha256)
        )
    """))
    db.commit()


def record_proposal_verdict(
    db: Session,
    organization_id: int,
    mission_id: int | None,
    plan_id: int | None,
    proposal: dict,
    schema_version: str,
    verdict: str,
    dup_of: str | None = None,
    score: float | None = None,
    validation_errors: list[str] | None = None,
) -> dict:
    """Record proposal verdict in ledger (idempotent)."""
    signature = compute_proposal_signature(proposal)
    
    # Check idempotency
    existing = db.execute(
        text("""
            SELECT id, verdict FROM proposal_gate_ledger
            WHERE organization_id = :org AND mission_id = :mid AND sha256 = :sig
        """),
        {"org": organization_id, "mid": mission_id, "sig": signature},
    ).mappings().first()
    
    if existing:
        return {"id": existing["id"], "verdict": existing["verdict"], "idempotent": True}
    
    # Insert new record
    result = db.execute(
        text("""
            INSERT INTO proposal_gate_ledger
                (organization_id, mission_id, plan_id, sha256, schema_version, repo, title,
                 verdict, dup_of, score, proposal_data, validation_errors)
            VALUES (:org, :mid, :pid, :sig, :sver, :repo, :title, :verdict, :dup, :score, :data, :errors)
            RETURNING id, at
        """),
        {
            "org": organization_id,
            "mid": mission_id,
            "pid": plan_id,
            "sig": signature,
            "sver": schema_version,
            "repo": proposal.get("repo", ""),
            "title": proposal.get("title", ""),
            "verdict": verdict,
            "dup": dup_of,
            "score": score,
            "data": json.dumps(proposal, ensure_ascii=False),
            "errors": json.dumps(validation_errors or [], ensure_ascii=False),
        },
    ).mappings().first()
    
    db.commit()
    
    return {
        "id": result["id"],
        "at": result["at"].isoformat() if result["at"] else None,
        "verdict": verdict,
        "idempotent": False,
    }


def get_ledger_stats(db: Session, organization_id: int) -> dict:
    """Get ledger statistics (four verdict counts + duplicate rate)."""
    stats = db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE verdict = 'accepted') as accepted_count,
                COUNT(*) FILTER (WHERE verdict = 'rejected') as rejected_count,
                COUNT(*) FILTER (WHERE verdict = 'quarantined') as quarantined_count,
                COUNT(*) FILTER (WHERE verdict = 'duplicate') as duplicate_count,
                COUNT(*) as total_count
            FROM proposal_gate_ledger
            WHERE organization_id = :org
        """),
        {"org": organization_id},
    ).mappings().first()
    
    total = stats["total_count"] or 0
    duplicate_rate = (stats["duplicate_count"] or 0) / total if total > 0 else 0.0
    
    return {
        "accepted": stats["accepted_count"] or 0,
        "rejected": stats["rejected_count"] or 0,
        "quarantined": stats["quarantined_count"] or 0,
        "duplicate": stats["duplicate_count"] or 0,
        "total": total,
        "duplicate_rate": round(duplicate_rate, 4),
    }


# ───────────────────────── Main Gate Orchestrator ─────────────────────────

def process_proposal(
    db: Session,
    organization_id: int,
    mission_id: int | None,
    plan_id: int | None,
    proposal: dict,
    schema_version: str | None = None,
) -> dict:
    """
    Process proposal through all three gates.
    
    Returns verdict record.
    """
    config = _load_config()
    
    # Gate 1: Schema validation
    try:
        normalized = validate_proposal_schema(proposal, schema_version)
        actual_version = "v2"  # After normalization, always v2
    except SchemaValidationError as e:
        # Quarantine failed proposals
        record = record_proposal_verdict(
            db, organization_id, mission_id, plan_id,
            proposal, schema_version or "unknown",
            "quarantined",
            validation_errors=e.errors,
        )
        return {
            "verdict": "quarantined",
            "reason": "schema_validation_failed",
            "errors": e.errors,
            "ledger_id": record["id"],
        }
    
    # Gate 2: Deduplication
    duplicate = find_duplicate_proposal(normalized, db, organization_id)
    if duplicate:
        record = record_proposal_verdict(
            db, organization_id, mission_id, plan_id,
            normalized, actual_version,
            "duplicate",
            dup_of=duplicate["sha256"],
            score=duplicate.get("similarity_score"),
        )
        return {
            "verdict": "duplicate",
            "reason": "duplicate_proposal",
            "dup_of": duplicate["sha256"],
            "similarity_score": duplicate.get("similarity_score"),
            "ledger_id": record["id"],
        }
    
    # Gate 3: Accept and record
    record = record_proposal_verdict(
        db, organization_id, mission_id, plan_id,
        normalized, actual_version,
        "accepted",
    )
    
    return {
        "verdict": "accepted",
        "normalized_proposal": normalized,
        "ledger_id": record["id"],
    }
