"""
Permission Enforcer — Write-path whitelist + domain-term scanning.

Enforces:
- Tenants can only write to their own repo paths (whitelist enforcement)
- Cross-repo write operations are rejected
- Code containing domain-specific terms is rejected for platform repos
- Domain term denylist loaded from config (data-driven, not hardcoded)
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
import time

from tensorplatform.tenant.registry import TenantRegistry


class WritePermissionError(Exception):
    """Raised when a write operation violates tenant boundaries."""
    def __init__(self, tenant_repo: str, target_path: str, reason: str):
        self.tenant_repo = tenant_repo
        self.target_path = target_path
        self.reason = reason
        super().__init__(f"Write permission denied for tenant '{tenant_repo}': {reason}")


class DomainTermViolation(Exception):
    """Raised when code contains domain-specific terms that violate platform neutrality."""
    def __init__(self, tenant_repo: str, file_path: str, matched_terms: List[str]):
        self.tenant_repo = tenant_repo
        self.file_path = file_path
        self.matched_terms = matched_terms
        super().__init__(
            f"Domain term violation in '{file_path}' for tenant '{tenant_repo}': "
            f"found terms: {matched_terms}"
        )


@dataclass
class WriteRequest:
    """A write operation request."""
    tenant_repo: str          # Tenant making the write
    target_path: str          # Path being written to
    content: str = ""         # Content being written (for domain scanning)
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class WriteAuditRecord:
    """Audit record for a write operation."""
    tenant_repo: str
    target_path: str
    allowed: bool
    reason: str
    domain_terms_found: List[str] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_repo": self.tenant_repo,
            "target_path": self.target_path,
            "allowed": self.allowed,
            "reason": self.reason,
            "domain_terms_found": self.domain_terms_found,
            "timestamp": self.timestamp,
        }


class DomainTermScanner:
    """
    Scans code for domain-specific terms.
    
    Loads denylist from config/domain_word_denylist.json (data-driven).
    Supports both ASCII words (with word boundary matching) and CJK words (substring matching).
    """

    def __init__(self, denylist_path: Optional[str] = None):
        """
        Initialize scanner with denylist.
        
        Args:
            denylist_path: Path to denylist JSON file. If None, uses default config path.
        """
        self.ascii_words: List[str] = []
        self.cjk_words: List[str] = []
        self._ascii_pattern: Optional[re.Pattern] = None
        self._loaded = False

        if denylist_path:
            self.load_denylist(denylist_path)

    def load_denylist(self, path: str) -> None:
        """Load denylist from JSON file."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.ascii_words = [w.lower() for w in data.get("ascii_words", [])]
        self.cjk_words = data.get("cjk_words", [])

        # Build regex pattern for ASCII words (word boundary matching)
        if self.ascii_words:
            escaped = [re.escape(w) for w in self.ascii_words]
            pattern_str = r'(?<![A-Za-z0-9_])(' + '|'.join(escaped) + r')(?![A-Za-z0-9_])'
            self._ascii_pattern = re.compile(pattern_str, re.IGNORECASE)

        self._loaded = True

    def load_from_words(self, ascii_words: List[str], cjk_words: List[str]) -> None:
        """Load denylist directly from word lists (for testing)."""
        self.ascii_words = [w.lower() for w in ascii_words]
        self.cjk_words = list(cjk_words)

        if self.ascii_words:
            escaped = [re.escape(w) for w in self.ascii_words]
            pattern_str = r'(?<![A-Za-z0-9_])(' + '|'.join(escaped) + r')(?![A-Za-z0-9_])'
            self._ascii_pattern = re.compile(pattern_str, re.IGNORECASE)

        self._loaded = True

    def scan(self, content: str) -> List[str]:
        """
        Scan content for domain terms.
        
        Returns:
            List of matched domain terms (empty if none found)
        """
        if not self._loaded:
            return []

        matched: Set[str] = set()

        # Check ASCII words with word boundary
        if self._ascii_pattern:
            for m in self._ascii_pattern.finditer(content):
                matched.add(m.group(0).lower())

        # Check CJK words with substring matching
        for word in self.cjk_words:
            if word in content:
                matched.add(word)

        return sorted(matched)

    def scan_file(self, file_path: str) -> List[str]:
        """Scan a file for domain terms."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return self.scan(content)
        except (IOError, UnicodeDecodeError):
            return []


class PermissionEnforcer:
    """
    Enforces write permissions for the 12-repo tenant system.
    
    Rules:
    1. Tenant can only write to paths within their own repo
    2. Cross-repo writes are rejected
    3. Platform repos (TensorPlatform) must not contain domain-specific terms
    4. All write operations are audited
    """

    # Repos that must maintain zero domain semantics
    PLATFORM_NEUTRAL_REPOS = {
        "TensorPlatform",
    }

    def __init__(
        self,
        registry: TenantRegistry,
        scanner: Optional[DomainTermScanner] = None,
        workspace_root: str = "/root/tp-agent/work",
    ):
        """
        Initialize permission enforcer.
        
        Args:
            registry: Tenant registry
            scanner: Domain term scanner (optional)
            workspace_root: Root directory of the workspace
        """
        self.registry = registry
        self.scanner = scanner or DomainTermScanner()
        self.workspace_root = workspace_root
        self._audit_log: List[WriteAuditRecord] = []

    def _normalize_path(self, path: str) -> str:
        """Normalize a path to absolute form."""
        p = Path(path)
        if not p.is_absolute():
            p = Path(self.workspace_root) / p
        return str(p.resolve())

    def _extract_repo_from_path(self, path: str) -> Optional[str]:
        """Extract repo name from a path."""
        normalized = self._normalize_path(path)
        # Path format: /workspace/repo_name/...
        parts = Path(normalized).parts
        # Find the repo part (after workspace root)
        ws_parts = Path(self.workspace_root).parts
        if len(parts) > len(ws_parts):
            return parts[len(ws_parts)]
        return None

    def check_write_permission(self, request: WriteRequest) -> Tuple[bool, str]:
        """
        Check if a write operation is allowed.
        
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        tenant_repo = request.tenant_repo
        target_path = request.target_path

        # Check tenant exists
        if not self.registry.is_registered(tenant_repo):
            return False, f"Tenant '{tenant_repo}' not registered"

        # Check tenant is enabled
        meta = self.registry.get(tenant_repo)
        if meta and not meta.enabled:
            return False, f"Tenant '{tenant_repo}' is disabled"

        # Extract target repo from path
        target_repo = self._extract_repo_from_path(target_path)
        if target_repo is None:
            return False, f"Cannot determine target repo from path: {target_path}"

        # Check cross-repo write
        if target_repo != tenant_repo:
            return False, f"Cross-repo write denied: tenant '{tenant_repo}' cannot write to '{target_repo}'"

        # Check domain terms for platform-neutral repos
        if tenant_repo in self.PLATFORM_NEUTRAL_REPOS and request.content:
            matched_terms = self.scanner.scan(request.content)
            if matched_terms:
                return False, f"Domain terms found in platform-neutral repo: {matched_terms}"

        return True, "Write allowed"

    def enforce_write(self, request: WriteRequest) -> bool:
        """
        Enforce write permission. Raises exception if denied.
        
        Returns:
            True if write is allowed
            
        Raises:
            WritePermissionError: If write is denied due to cross-repo violation
            DomainTermViolation: If write contains domain terms in platform repo
        """
        allowed, reason = self.check_write_permission(request)

        record = WriteAuditRecord(
            tenant_repo=request.tenant_repo,
            target_path=request.target_path,
            allowed=allowed,
            reason=reason,
            timestamp=time.time(),
        )

        if not allowed:
            # Check if it's a domain term violation
            if request.tenant_repo in self.PLATFORM_NEUTRAL_REPOS and request.content:
                matched_terms = self.scanner.scan(request.content)
                if matched_terms:
                    record.domain_terms_found = matched_terms
                    self._audit_log.append(record)
                    raise DomainTermViolation(request.tenant_repo, request.target_path, matched_terms)

            self._audit_log.append(record)
            raise WritePermissionError(request.tenant_repo, request.target_path, reason)

        self._audit_log.append(record)
        return True

    def scan_file_for_domain_terms(self, file_path: str, tenant_repo: str) -> List[str]:
        """
        Scan a file for domain terms.
        
        Args:
            file_path: Path to file to scan
            tenant_repo: Tenant repo context
            
        Returns:
            List of matched domain terms
        """
        return self.scanner.scan_file(file_path)

    def scan_directory(self, dir_path: str, tenant_repo: str, extensions: Optional[List[str]] = None) -> Dict[str, List[str]]:
        """
        Scan a directory for domain terms.
        
        Args:
            dir_path: Directory to scan
            tenant_repo: Tenant repo context
            extensions: File extensions to scan (default: .py, .json, .md)
            
        Returns:
            Dict mapping file paths to matched terms
        """
        if extensions is None:
            extensions = [".py", ".json", ".md", ".txt", ".yml", ".yaml"]

        results: Dict[str, List[str]] = {}
        dir_path = Path(dir_path)

        if not dir_path.exists():
            return results

        for ext in extensions:
            for file_path in dir_path.rglob(f"*{ext}"):
                matched = self.scanner.scan_file(str(file_path))
                if matched:
                    results[str(file_path)] = matched

        return results

    def get_audit_log(self, tenant_repo: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get write audit log."""
        records = self._audit_log
        if tenant_repo:
            records = [r for r in records if r.tenant_repo == tenant_repo]
        return [r.to_dict() for r in records[-limit:]]

    def get_denied_writes(self, tenant_repo: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all denied write attempts."""
        records = [r for r in self._audit_log if not r.allowed]
        if tenant_repo:
            records = [r for r in records if r.tenant_repo == tenant_repo]
        return [r.to_dict() for r in records]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize enforcer state."""
        return {
            "total_writes": len(self._audit_log),
            "denied_writes": len([r for r in self._audit_log if not r.allowed]),
            "platform_neutral_repos": list(self.PLATFORM_NEUTRAL_REPOS),
            "scanner_loaded": self.scanner._loaded,
            "scanner_ascii_words": len(self.scanner.ascii_words),
            "scanner_cjk_words": len(self.scanner.cjk_words),
        }
