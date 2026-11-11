"""Policy loading / validation / profile switching.

Everything the scanner knows is injected here. A missing or malformed policy is a
**hard error** (the gates convert it into ``decision=unknown`` = BLOCK), because
the failure mode of "no rules configured" must never be "nothing detected".
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Dict, List, Optional

DEFAULT_POLICY_PATH = "data/secret_scan/policy.json"
POLICY_ENV = "SECRET_SCAN_POLICY"

REQUIRED_PATHS = (
    "detectors.patterns.rules",
    "detectors.entropy.min_length",
    "detectors.entropy.min_bits_per_char",
    "decoders.max_depth",
    "redaction.placeholder",
    "output.max_preview_chars",
    "whitelist.value_classes",
)


class PolicyError(RuntimeError):
    """Policy unreadable / invalid → gates must block."""


def _dotted_set(root: Dict[str, Any], dotted: str, value: Any) -> None:
    node = root
    parts = dotted.split(".")
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _dotted_get(root: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = root
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


class Policy:
    """Immutable view over an injected policy document."""

    def __init__(self, raw: Dict[str, Any], path: str = "<inline>", profile: str = "full"):
        self.raw = raw
        self.path = path
        self.profile = profile
        blob = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.sha256 = hashlib.sha256(blob).hexdigest()
        self.known_values: List[str] = []
        self.known_value_labels: List[str] = []
        self.advisories: List[Dict[str, Any]] = []
        # known values are loaded eagerly so that "required but empty" is a
        # construction-time (fail-closed) error, not a silent no-op detector.
        if raw.get("detectors", {}).get("known_values"):
            _load_known_values(self)

    # --- accessors -----------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        return _dotted_get(self.raw, dotted, default)

    @property
    def patterns(self) -> List[Dict[str, Any]]:
        return list(self.get("detectors.patterns.rules", []))

    @property
    def entropy(self) -> Dict[str, Any]:
        return dict(self.get("detectors.entropy", {}))

    @property
    def known_cfg(self) -> Dict[str, Any]:
        return dict(self.get("detectors.known_values", {}))

    @property
    def decoders(self) -> Dict[str, Any]:
        return dict(self.get("decoders", {}))

    @property
    def whitelist(self) -> Dict[str, Any]:
        return dict(self.get("whitelist", {}))

    @property
    def redaction(self) -> Dict[str, Any]:
        return dict(self.get("redaction", {}))

    @property
    def output(self) -> Dict[str, Any]:
        return dict(self.get("output", {}))

    @property
    def exits(self) -> Dict[str, Any]:
        return dict(self.get("exits", {}))

    @property
    def history(self) -> Dict[str, Any]:
        return dict(self.get("history", {}))

    @property
    def neutrality(self) -> Dict[str, Any]:
        return dict(self.get("neutrality", {}))

    # --- derivation ----------------------------------------------------
    def with_profile(self, profile: str) -> "Policy":
        """Return a policy with a named profile applied (e.g. ``baseline``)."""
        overrides = self.get("profiles.{}".format(profile), None)
        if overrides is None:
            raise PolicyError("unknown_profile:{}".format(profile))
        raw = copy.deepcopy(self.raw)
        for key, value in overrides.items():
            _dotted_set(raw, key, value)
        derived = Policy(raw, path=self.path, profile=profile)
        derived.known_values = self.known_values
        derived.known_value_labels = self.known_value_labels
        derived.advisories = list(self.advisories)
        return derived


def default_policy_path() -> str:
    return os.environ.get(POLICY_ENV, DEFAULT_POLICY_PATH)


def load_policy(path: Optional[str] = None, *, profile: str = "full") -> Policy:
    """Load + validate a policy. Raises :class:`PolicyError` on any problem."""
    resolved = path or default_policy_path()
    if not resolved or not os.path.isfile(resolved):
        raise PolicyError("policy_missing:{}".format(resolved))
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:  # pragma: no cover - IO/parse path
        raise PolicyError("policy_unreadable:{}:{}".format(resolved, exc)) from exc
    if not isinstance(raw, dict):
        raise PolicyError("policy_not_object:{}".format(resolved))
    for dotted in REQUIRED_PATHS:
        if _dotted_get(raw, dotted, None) is None:
            raise PolicyError("policy_missing_key:{}".format(dotted))
    if not raw["detectors"]["patterns"]["rules"]:
        raise PolicyError("policy_empty_patterns")
    if not raw["whitelist"].get("value_classes", {}):
        raise PolicyError("policy_empty_whitelist")
    policy = Policy(raw, path=resolved)
    if profile != "full":
        policy = policy.with_profile(profile)
    return policy


def _load_known_values(policy: Policy) -> None:
    """Load known secret values into memory only — they are never persisted.

    Sources (policy-injected) may be ``{"env": NAME}`` or ``{"file": PATH}``.
    A file holding one value per line is the canonical form. When the policy
    declares ``required`` and nothing was loaded, that is fail-closed: the
    "known value" detector cannot silently become a no-op.
    """
    cfg = policy.known_cfg
    labels: List[str] = []
    values: List[str] = []
    min_len = int(cfg.get("min_length", 8))
    for source in cfg.get("sources", []) or []:
        loaded: List[str] = []
        if "env" in source:
            raw = os.environ.get(str(source["env"]), "")
            loaded = [line.strip() for line in raw.splitlines() if line.strip()]
        elif "file" in source:
            path = str(source["file"])
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    loaded = [line.strip() for line in handle if line.strip()]
            except OSError:
                policy.advisories.append({"code": "known_source_missing", "source": path})
                loaded = []
        for item in loaded:
            if len(item) >= min_len:
                values.append(item)
                labels.append(str(source.get("label") or source.get("env") or source.get("file")))
    policy.known_values = values
    policy.known_value_labels = labels
    if cfg.get("required") and not values:
        raise PolicyError("known_values_required_but_empty")