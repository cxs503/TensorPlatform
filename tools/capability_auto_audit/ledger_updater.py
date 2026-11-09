"""Auto-updating capability ledger after verification runs.

After each verification run, this module:
1. Reads the classification results from EvidenceClassifier
2. Reads accuracy data from ProvenanceDB (SQL-only, zero-hardcode)
3. Updates the capability ledger with:
   - evidence_level (REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED)
   - accuracy_pct (from SQL query, never hardcoded)
   - last_verified_utc (timestamp of last verification)
4. Writes the updated ledger to artifacts/capability_ledger/

Design: The ledger is the single source of truth for capability status.
All updates go through this module to ensure consistency.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .provenance_db import ProvenanceDB
from .evidence_classifier import EvidenceClassifier, ClassificationResult

WORKTREE = Path(__file__).resolve().parents[2]
LEDGER_DIR = WORKTREE / "artifacts" / "capability_ledger"
LEDGER_FILE = LEDGER_DIR / "capability_ledger.json"
LEDGER_BACKUP_DIR = LEDGER_DIR / "backups"


class LedgerUpdater:
    """Auto-updates the capability ledger after verification runs.

    Parameters
    ----------
    worktree : Path, optional
        Root of the TensorPlatform worktree.
    provenance_db : ProvenanceDB, optional
        Provenance database instance. If None, creates a new one.
    classifier : EvidenceClassifier, optional
        Evidence classifier instance. If None, creates a new one.
    """

    def __init__(
        self,
        worktree: Optional[Path] = None,
        provenance_db: Optional[ProvenanceDB] = None,
        classifier: Optional[EvidenceClassifier] = None,
    ):
        self.worktree = Path(worktree) if worktree else WORKTREE
        self.ledger_dir = self.worktree / "artifacts" / "capability_ledger"
        self.ledger_file = self.ledger_dir / "capability_ledger.json"
        self.backup_dir = self.ledger_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.provenance_db = provenance_db or ProvenanceDB()
        self.classifier = classifier or EvidenceClassifier(worktree=self.worktree)

    def update_after_verification(
        self,
        run_id: str,
        classification_results: Optional[Dict[str, ClassificationResult]] = None,
    ) -> Dict[str, Any]:
        """Update the capability ledger after a verification run.

        This is the main entry point. It:
        1. Backs up the current ledger
        2. Runs classification (if not provided)
        3. Queries accuracy data from SQL (zero-hardcode)
        4. Updates the ledger
        5. Returns a summary of changes

        Parameters
        ----------
        run_id : str
            Identifier for this verification run.
        classification_results : dict, optional
            Pre-computed classification results. If None, runs classification.

        Returns
        -------
        dict
            Summary of changes made to the ledger.
        """
        # Step 1: Backup current ledger
        backup_path = self._backup_ledger(run_id)

        # Step 2: Get classification results
        if classification_results is None:
            classification_results = self.classifier.classify_all()

        # Step 3: Load current ledger
        current_ledger = self._load_ledger()

        # Step 4: Update each capability
        changes = []
        now = datetime.now(timezone.utc).isoformat()

        for cap_id, result in classification_results.items():
            # Query accuracy from SQL (zero-hardcode enforcement)
            accuracy_data = self.provenance_db.get_all_accuracy_for_capability(cap_id)

            # Build updated capability record
            old_record = self._find_capability_record(current_ledger, cap_id)
            new_record = self._build_capability_record(
                cap_id, result, accuracy_data, run_id, now
            )

            # Track changes
            change = self._compute_change(old_record, new_record)
            if change["changed"]:
                changes.append(change)

            # Update in ledger
            self._update_capability_record(current_ledger, new_record)

        # Step 5: Update ledger metadata
        current_ledger["last_updated"] = now
        current_ledger["last_run_id"] = run_id
        current_ledger["update_count"] = current_ledger.get("update_count", 0) + 1

        # Step 6: Write updated ledger
        self._write_ledger(current_ledger)

        return {
            "run_id": run_id,
            "timestamp": now,
            "backup_path": str(backup_path),
            "capabilities_updated": len(changes),
            "changes": changes,
            "ledger_path": str(self.ledger_file),
        }

    def _backup_ledger(self, run_id: str) -> Path:
        """Create a backup of the current ledger."""
        if self.ledger_file.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_name = f"capability_ledger_{run_id}_{timestamp}.json"
            backup_path = self.backup_dir / backup_name
            shutil.copy2(self.ledger_file, backup_path)
            return backup_path
        return Path()

    def _load_ledger(self) -> Dict[str, Any]:
        """Load the current capability ledger."""
        if self.ledger_file.exists():
            with open(self.ledger_file, encoding="utf-8") as f:
                return json.load(f)
        # Return empty ledger structure
        return {
            "schema_version": "2.0",
            "title": "Capability Evidence Ledger (Auto-Updated)",
            "purpose": "Single source of truth for capability evidence levels and accuracy",
            "level_rank": {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4},
            "capabilities": [],
            "last_updated": None,
            "last_run_id": None,
            "update_count": 0,
        }

    def _write_ledger(self, ledger: Dict[str, Any]):
        """Write the updated ledger to disk."""
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_file, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, ensure_ascii=False)

    def _find_capability_record(self, ledger: Dict, cap_id: str) -> Optional[Dict]:
        """Find a capability record in the ledger."""
        for cap in ledger.get("capabilities", []):
            if cap.get("id") == cap_id:
                return cap
        return None

    def _build_capability_record(
        self,
        cap_id: str,
        result: ClassificationResult,
        accuracy_data: Dict[str, Any],
        run_id: str,
        timestamp: str,
    ) -> Dict[str, Any]:
        """Build a capability record for the ledger.

        All accuracy numbers come from SQL queries (zero-hardcode).
        """
        # Get accuracy from SQL (this is the key zero-hardcode enforcement)
        max_error = accuracy_data.get("max_error_pct")
        all_passed = accuracy_data.get("all_passed", False)

        record = {
            "id": cap_id,
            "domain": self.classifier.profiles.get(cap_id, {}).get("domain", "unknown"),
            "evidence_level": result.level,
            "confidence": result.confidence,
            "accuracy_pct": max_error,  # From SQL, never hardcoded
            "all_accuracy_passed": all_passed,
            "accuracy_record_count": accuracy_data.get("record_count", 0),
            "accuracy_sql_query": accuracy_data.get("sql_query", ""),  # Audit trail
            "last_verified_utc": timestamp,
            "last_run_id": run_id,
            "rule_fired": result.rule_fired,
            "signals": result.signals,
            "evidence_files": result.evidence_files[:10],  # Limit for readability
            "verification_artifacts": result.verification_artifacts[:5],
            "updated_at": timestamp,
        }
        return record

    def _compute_change(self, old_record: Optional[Dict], new_record: Dict) -> Dict[str, Any]:
        """Compute the change between old and new records."""
        if old_record is None:
            return {
                "capability_id": new_record["id"],
                "changed": True,
                "change_type": "added",
                "before": None,
                "after": {
                    "evidence_level": new_record["evidence_level"],
                    "accuracy_pct": new_record["accuracy_pct"],
                },
            }

        level_changed = old_record.get("evidence_level") != new_record["evidence_level"]
        accuracy_changed = old_record.get("accuracy_pct") != new_record["accuracy_pct"]

        return {
            "capability_id": new_record["id"],
            "changed": level_changed or accuracy_changed,
            "change_type": "level_change" if level_changed else ("accuracy_update" if accuracy_changed else "no_change"),
            "before": {
                "evidence_level": old_record.get("evidence_level"),
                "accuracy_pct": old_record.get("accuracy_pct"),
            },
            "after": {
                "evidence_level": new_record["evidence_level"],
                "accuracy_pct": new_record["accuracy_pct"],
            },
        }

    def _update_capability_record(self, ledger: Dict, new_record: Dict):
        """Update or add a capability record in the ledger."""
        capabilities = ledger.get("capabilities", [])
        for i, cap in enumerate(capabilities):
            if cap.get("id") == new_record["id"]:
                capabilities[i] = new_record
                return
        # Not found, add new
        capabilities.append(new_record)
        ledger["capabilities"] = capabilities

    def get_ledger_summary(self) -> Dict[str, Any]:
        """Get a summary of the current ledger state."""
        ledger = self._load_ledger()
        caps = ledger.get("capabilities", [])

        level_counts = {}
        for cap in caps:
            level = cap.get("evidence_level", "UNKNOWN")
            level_counts[level] = level_counts.get(level, 0) + 1

        return {
            "total_capabilities": len(caps),
            "level_distribution": level_counts,
            "last_updated": ledger.get("last_updated"),
            "last_run_id": ledger.get("last_run_id"),
            "update_count": ledger.get("update_count", 0),
            "ledger_path": str(self.ledger_file),
        }

    def rollback_to_run(self, run_id: str) -> Dict[str, Any]:
        """Rollback the ledger to a specific run's backup.

        Parameters
        ----------
        run_id : str
            The run ID to rollback to.

        Returns
        -------
        dict
            Rollback result with success status.
        """
        # Find backup file for this run
        backups = sorted(self.backup_dir.glob(f"capability_ledger_{run_id}_*.json"))
        if not backups:
            return {
                "success": False,
                "error": f"No backup found for run_id={run_id}",
                "available_backups": [f.name for f in self.backup_dir.glob("*.json")],
            }

        # Use the latest backup for this run
        backup_path = backups[-1]

        # Backup current state before rollback
        rollback_backup = self._backup_ledger(f"pre_rollback_{run_id}")

        # Restore from backup
        shutil.copy2(backup_path, self.ledger_file)

        return {
            "success": True,
            "restored_from": str(backup_path),
            "pre_rollback_backup": str(rollback_backup),
        }

    def list_backups(self) -> List[Dict[str, str]]:
        """List all available ledger backups."""
        backups = []
        for f in sorted(self.backup_dir.glob("*.json")):
            backups.append({
                "filename": f.name,
                "path": str(f),
                "size_bytes": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        return backups
