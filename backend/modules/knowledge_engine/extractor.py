"""Four-contract extractor — extract structured knowledge from finish() payloads and reports.

Extracts:
1. Problem Statement (goal, context, beneficiary, domain, tags)
2. Methodology (approach, tools, parameters, assumptions)
3. Evidence Chain (artifacts, metrics, test_results)
4. Conclusion (summary, outcome, key_findings, limitations, next_steps)
5. Uncertainty (confidence, sources_of_uncertainty, validation_status)
6. Provenance (source_type, source_id, repo, branch, commit, agent_id)

Design: Pure functions, no side effects, deterministic extraction.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .store import KnowledgeEntry


class FourContractExtractor:
    """Extract four-contract knowledge from various sources."""

    def __init__(self, extractor_version: str = "1.0.0"):
        self.extractor_version = extractor_version

    def extract_from_finish(
        self,
        finish_payload: dict,
        mission_id: Optional[int] = None,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        commit: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> KnowledgeEntry:
        """Extract knowledge entry from finish() call payload.

        Args:
            finish_payload: The capability_delta and other fields from finish()
            mission_id: Mission identifier
            repo: Repository name
            branch: Git branch
            commit: Git commit hash
            agent_id: Agent identifier

        Returns:
            KnowledgeEntry with four contracts populated
        """
        # Extract problem statement
        problem = self._extract_problem_statement(finish_payload, mission_id)

        # Extract methodology
        methodology = self._extract_methodology(finish_payload)

        # Extract evidence chain
        evidence = self._extract_evidence_chain(finish_payload)

        # Extract conclusion
        conclusion = self._extract_conclusion(finish_payload)

        # Extract uncertainty
        uncertainty = self._extract_uncertainty(finish_payload)

        # Build provenance
        provenance = {
            "source_type": "finish_call",
            "source_id": str(mission_id) if mission_id else "unknown",
            "repo": repo or "",
            "branch": branch or "",
            "commit": commit or "",
            "agent_id": agent_id or "",
            "extractor_version": self.extractor_version,
            "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        entry = KnowledgeEntry(
            entry_id="",  # Will be computed
            problem_statement=problem,
            methodology=methodology,
            evidence_chain=evidence,
            conclusion=conclusion,
            uncertainty=uncertainty,
            provenance=provenance,
        )
        entry.entry_id = entry.compute_id()

        return entry

    def extract_from_report(
        self,
        report: dict,
        report_path: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> KnowledgeEntry:
        """Extract knowledge entry from report.json.

        Args:
            report: Parsed report.json content
            report_path: Path to the report file
            repo: Repository name

        Returns:
            KnowledgeEntry with four contracts populated
        """
        # Extract problem statement
        problem = self._extract_problem_from_report(report)

        # Extract methodology
        methodology = self._extract_methodology_from_report(report)

        # Extract evidence chain
        evidence = self._extract_evidence_from_report(report, report_path)

        # Extract conclusion
        conclusion = self._extract_conclusion_from_report(report)

        # Extract uncertainty
        uncertainty = self._extract_uncertainty_from_report(report)

        # Build provenance
        provenance = {
            "source_type": "report_extract",
            "source_id": report_path or "unknown",
            "repo": repo or "",
            "branch": "",
            "commit": "",
            "agent_id": "",
            "extractor_version": self.extractor_version,
            "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        entry = KnowledgeEntry(
            entry_id="",
            problem_statement=problem,
            methodology=methodology,
            evidence_chain=evidence,
            conclusion=conclusion,
            uncertainty=uncertainty,
            provenance=provenance,
        )
        entry.entry_id = entry.compute_id()

        return entry

    def _extract_problem_statement(self, payload: dict, mission_id: Optional[int]) -> dict:
        """Extract problem statement from finish payload."""
        summary = payload.get("summary", "")
        capability_delta = payload.get("capability_delta", {})

        # Try to extract goal from summary or capability_delta
        goal = summary or capability_delta.get("summary", "")
        if not goal:
            goal = f"Mission {mission_id}" if mission_id else "Unknown task"

        # Extract domain from tags or infer from context
        tags = self._extract_tags(payload)
        domain = self._infer_domain(tags, summary)

        return {
            "goal": goal[:500],  # Truncate long goals
            "context": self._extract_context(payload),
            "beneficiary": self._extract_beneficiary(payload),
            "domain": domain,
            "tags": tags,
        }

    def _extract_methodology(self, payload: dict) -> dict:
        """Extract methodology from finish payload."""
        # Look for approach in summary or capability_delta
        summary = payload.get("summary", "")
        capability_delta = payload.get("capability_delta", {})

        approach = summary
        if not approach:
            approach = capability_delta.get("reason", "")

        # Extract tools from changed_files or other indicators
        tools = self._extract_tools(payload)

        # Extract parameters if present
        parameters = self._extract_parameters(payload)

        return {
            "approach": approach[:1000] if approach else "Not specified",
            "tools": tools,
            "parameters": parameters,
            "assumptions": self._extract_assumptions(payload),
        }

    def _extract_evidence_chain(self, payload: dict) -> dict:
        """Extract evidence chain from finish payload."""
        artifacts = []
        metrics = {}
        test_results = {}

        # Extract artifacts from changed_files
        changed_files = payload.get("changed_files", [])
        for f in changed_files:
            artifacts.append({
                "path": f,
                "type": self._classify_artifact_type(f),
            })

        # Extract checks as test results
        checks = payload.get("checks", "")
        if checks:
            test_results = self._parse_checks(checks)

        # Extract metrics from capability_delta evidence
        capability_delta = payload.get("capability_delta", {})
        evidence = capability_delta.get("evidence", {})
        if isinstance(evidence, dict):
            metrics = evidence

        return {
            "artifacts": artifacts,
            "metrics": metrics,
            "test_results": test_results,
        }

    def _extract_conclusion(self, payload: dict) -> dict:
        """Extract conclusion from finish payload."""
        summary = payload.get("summary", "")
        capability_delta = payload.get("capability_delta", {})

        # Determine outcome from category
        category = capability_delta.get("category", "")
        outcome = self._map_category_to_outcome(category)

        # Extract key findings
        key_findings = []
        if summary:
            key_findings.append(summary)

        # Extract limitations
        limitations = self._extract_limitations(payload)

        # Extract next steps
        next_steps = self._extract_next_steps(payload)

        return {
            "summary": summary[:500] if summary else "No summary provided",
            "outcome": outcome,
            "key_findings": key_findings,
            "limitations": limitations,
            "next_steps": next_steps,
        }

    def _extract_uncertainty(self, payload: dict) -> dict:
        """Extract uncertainty assessment from finish payload."""
        capability_delta = payload.get("capability_delta", {})
        category = capability_delta.get("category", "")

        # Infer confidence from category and evidence presence
        evidence = capability_delta.get("evidence", {})
        has_evidence = bool(evidence)

        if category in ("new_capability", "defect_fix") and has_evidence:
            confidence = "high"
            confidence_score = 0.9
        elif category == "capability_extension" and has_evidence:
            confidence = "medium"
            confidence_score = 0.7
        elif category == "no_substantive_delta":
            confidence = "low"
            confidence_score = 0.3
        else:
            confidence = "medium"
            confidence_score = 0.6

        return {
            "confidence": confidence,
            "confidence_score": confidence_score,
            "sources_of_uncertainty": self._identify_uncertainty_sources(payload),
            "validation_status": "partial",  # Default; can be upgraded with tests
        }

    # Report extraction methods

    def _extract_problem_from_report(self, report: dict) -> dict:
        """Extract problem statement from report."""
        title = report.get("title", "")
        summary = report.get("summary", "")
        tags = report.get("tags", [])

        # Try to extract from markdown
        markdown = report.get("markdown", "")
        goal = title or summary
        if not goal and markdown:
            # Extract first heading or paragraph
            match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
            if match:
                goal = match.group(1)

        return {
            "goal": goal[:500] if goal else "Unknown",
            "context": summary[:1000] if summary else "",
            "beneficiary": "",
            "domain": self._infer_domain(tags, title),
            "tags": tags if isinstance(tags, list) else [],
        }

    def _extract_methodology_from_report(self, report: dict) -> dict:
        """Extract methodology from report."""
        markdown = report.get("markdown", "")

        # Look for methodology section
        approach = ""
        if markdown:
            # Extract section about methods
            match = re.search(
                r"(?:##?\s*(?:方法|怎么做|Method|Approach).*?\n)(.*?)(?=\n##?|\Z)",
                markdown,
                re.DOTALL | re.IGNORECASE
            )
            if match:
                approach = match.group(1).strip()[:1000]

        if not approach:
            approach = report.get("summary", "")[:1000]

        return {
            "approach": approach if approach else "Not specified",
            "tools": [],
            "parameters": {},
            "assumptions": [],
        }

    def _extract_evidence_from_report(self, report: dict, report_path: Optional[str]) -> dict:
        """Extract evidence chain from report."""
        artifacts = []

        # Add the report itself as an artifact
        if report_path:
            artifacts.append({
                "path": report_path,
                "type": "report",
            })

        # Extract figures
        figures = report.get("figures", [])
        for fig in figures:
            if isinstance(fig, dict) and fig.get("name"):
                artifacts.append({
                    "path": fig["name"],
                    "type": "figure",
                })

        # Extract metrics from scenario_key or other fields
        metrics = {}
        if report.get("scenario_key"):
            metrics["scenario_key"] = report["scenario_key"]

        return {
            "artifacts": artifacts,
            "metrics": metrics,
            "test_results": {},
        }

    def _extract_conclusion_from_report(self, report: dict) -> dict:
        """Extract conclusion from report."""
        summary = report.get("summary", "")
        markdown = report.get("markdown", "")

        # Look for conclusion/error sections
        outcome = "success"
        limitations = []
        next_steps = []

        if markdown:
            # Check for honest FAIL
            if re.search(r"honest.?fail|失败|未达成", markdown, re.IGNORECASE):
                outcome = "honest_fail"

            # Extract limitations section
            match = re.search(
                r"(?:##?\s*(?:注意点|局限|Limitation|Caveat).*?\n)(.*?)(?=\n##?|\Z)",
                markdown,
                re.DOTALL | re.IGNORECASE
            )
            if match:
                limitations = [
                    line.strip()
                    for line in match.group(1).split("\n")
                    if line.strip() and not line.startswith("#")
                ][:5]

        return {
            "summary": summary[:500] if summary else "No summary",
            "outcome": outcome,
            "key_findings": [summary] if summary else [],
            "limitations": limitations,
            "next_steps": next_steps,
        }

    def _extract_uncertainty_from_report(self, report: dict) -> dict:
        """Extract uncertainty from report."""
        # Reports typically have lower confidence than finish calls
        return {
            "confidence": "medium",
            "confidence_score": 0.6,
            "sources_of_uncertainty": ["extracted_from_report"],
            "validation_status": "unvalidated",
        }

    # Helper methods

    def _extract_tags(self, payload: dict) -> list[str]:
        """Extract tags from payload."""
        tags = []
        capability_delta = payload.get("capability_delta", {})
        category = capability_delta.get("category", "")
        if category:
            tags.append(f"category:{category}")

        # Add domain tags if inferable
        summary = payload.get("summary", "")
        if "resistance" in summary.lower():
            tags.append("domain:resistance")
        if "ice" in summary.lower():
            tags.append("domain:ice")
        if "structural" in summary.lower():
            tags.append("domain:structural")

        return tags

    def _infer_domain(self, tags: list[str], text: str) -> str:
        """Infer domain from tags and text."""
        text_lower = text.lower() if text else ""
        tags_str = " ".join(tags).lower()

        if "resistance" in text_lower or "resistance" in tags_str:
            return "resistance"
        if "ice" in text_lower or "ice" in tags_str:
            return "ice"
        if "propulsion" in text_lower or "propulsion" in tags_str:
            return "propulsion"
        if "seakeeping" in text_lower or "seakeeping" in tags_str:
            return "seakeeping"
        if "maneuvering" in text_lower or "maneuvering" in tags_str:
            return "maneuvering"
        if "structural" in text_lower or "structural" in tags_str:
            return "structural"
        if "vibration" in text_lower or "noise" in text_lower:
            return "vibration_noise"
        if "green" in text_lower or "emission" in text_lower:
            return "green_navigation"

        return "platform"

    def _extract_context(self, payload: dict) -> str:
        """Extract context from payload."""
        capability_delta = payload.get("capability_delta", {})
        reason = capability_delta.get("reason", "")
        return reason[:1000] if reason else ""

    def _extract_beneficiary(self, payload: dict) -> str:
        """Extract beneficiary from payload."""
        # Infer from category
        capability_delta = payload.get("capability_delta", {})
        category = capability_delta.get("category", "")
        if category == "new_capability":
            return "platform_users"
        if category == "defect_fix":
            return "affected_users"
        return ""

    def _extract_tools(self, payload: dict) -> list[str]:
        """Extract tools from changed files."""
        tools = []
        changed_files = payload.get("changed_files", [])
        for f in changed_files:
            if f.endswith(".py"):
                tools.append("python")
            if f.endswith(".json"):
                tools.append("json")
            if "test" in f.lower():
                tools.append("pytest")
        return list(set(tools))

    def _extract_parameters(self, payload: dict) -> dict:
        """Extract parameters from payload."""
        # Look for parameters in evidence
        capability_delta = payload.get("capability_delta", {})
        evidence = capability_delta.get("evidence", {})
        if isinstance(evidence, dict):
            return {k: v for k, v in evidence.items() if not isinstance(v, (dict, list))}
        return {}

    def _extract_assumptions(self, payload: dict) -> list[str]:
        """Extract assumptions from payload."""
        # Look for assumptions in summary or markdown
        summary = payload.get("summary", "")
        assumptions = []
        if "assum" in summary.lower():
            # Try to extract assumption sentences
            sentences = re.split(r"[.!?]", summary)
            for s in sentences:
                if "assum" in s.lower():
                    assumptions.append(s.strip())
        return assumptions

    def _classify_artifact_type(self, path: str) -> str:
        """Classify artifact type from path."""
        path_lower = path.lower()
        if "test" in path_lower:
            return "test_log"
        if path_lower.endswith(".py"):
            return "code"
        if path_lower.endswith(".json"):
            return "data"
        if path_lower.endswith((".png", ".jpg", ".svg")):
            return "figure"
        if "report" in path_lower:
            return "report"
        return "data"

    def _parse_checks(self, checks: str) -> dict:
        """Parse checks string into test results."""
        # Try to extract test counts
        total = 0
        passed = 0
        failed = 0

        # Look for patterns like "5 passed, 2 failed"
        match = re.search(r"(\d+)\s+passed", checks)
        if match:
            passed = int(match.group(1))
            total += passed

        match = re.search(r"(\d+)\s+failed", checks)
        if match:
            failed = int(match.group(1))
            total += failed

        # Look for coverage
        coverage_pct = 0.0
        match = re.search(r"coverage[:\s]+(\d+(?:\.\d+)?)%", checks, re.IGNORECASE)
        if match:
            coverage_pct = float(match.group(1))

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "coverage_pct": coverage_pct,
        }

    def _map_category_to_outcome(self, category: str) -> str:
        """Map capability_delta category to outcome."""
        if category == "new_capability":
            return "success"
        if category == "capability_extension":
            return "success"
        if category == "defect_fix":
            return "success"
        if category == "reusable_knowledge":
            return "success"
        if category == "falsified_hypothesis":
            return "honest_fail"
        if category == "no_substantive_delta":
            return "partial"
        return "partial"

    def _extract_limitations(self, payload: dict) -> list[str]:
        """Extract limitations from payload."""
        # Look in summary for limitation keywords
        summary = payload.get("summary", "")
        limitations = []
        if "limit" in summary.lower() or "caveat" in summary.lower():
            sentences = re.split(r"[.!?]", summary)
            for s in sentences:
                if "limit" in s.lower() or "caveat" in s.lower():
                    limitations.append(s.strip())
        return limitations[:5]

    def _extract_next_steps(self, payload: dict) -> list[str]:
        """Extract next steps from payload."""
        # Look in summary for next step keywords
        summary = payload.get("summary", "")
        next_steps = []
        if "next" in summary.lower() or "future" in summary.lower():
            sentences = re.split(r"[.!?]", summary)
            for s in sentences:
                if "next" in s.lower() or "future" in s.lower():
                    next_steps.append(s.strip())
        return next_steps[:5]

    def _identify_uncertainty_sources(self, payload: dict) -> list[str]:
        """Identify sources of uncertainty."""
        sources = []
        capability_delta = payload.get("capability_delta", {})
        evidence = capability_delta.get("evidence", {})

        if not evidence:
            sources.append("no_quantitative_evidence")

        checks = payload.get("checks", "")
        if not checks:
            sources.append("no_test_results")

        return sources


# Convenience functions

def extract_from_finish(
    finish_payload: dict,
    mission_id: Optional[int] = None,
    repo: Optional[str] = None,
    **kwargs,
) -> KnowledgeEntry:
    """Extract knowledge entry from finish() payload."""
    extractor = FourContractExtractor()
    return extractor.extract_from_finish(finish_payload, mission_id, repo, **kwargs)


def extract_from_report(
    report: dict,
    report_path: Optional[str] = None,
    repo: Optional[str] = None,
) -> KnowledgeEntry:
    """Extract knowledge entry from report."""
    extractor = FourContractExtractor()
    return extractor.extract_from_report(report, report_path, repo)
