"""False-positive control: exactly four admissible whitelist classes.

Any scanner without a bounded whitelist dies of noise, and any scanner with an
unbounded whitelist dies of blindness. The compromise enforced here is a *closed*
set of four classes, defined by policy:

===========================  =========================================  ============
class                        admitted value / location                    scope
===========================  =========================================  ============
``sha256``                   a 64-hex digest                            value
``git_commit``               7–40 lowercase hex (SHA-1/abbrev)          value
``uuid``                     canonical 8-4-4-4-12 hex-dash              value
``test_fixture``             a fixture *path* (+ optional inline mark)  location
===========================  =========================================  ============

Deliberate asymmetries
----------------------
* value classes only ever silence the *entropy* route (a digest is high entropy
  but not a credential); a shaped pattern hit or a known-value hit is never
  silenced by a value class.
* the location class is opt-in per exit: the report gate keeps it **off**
  (``exits.report_gate.apply_location_whitelist=false``), because a published
  payload has no trustworthy "this is just a fixture" provenance.
* an inline marker alone is not enough — the path must also match a fixture
  glob, so ``secret-scan:allow`` cannot be used as a global escape hatch.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Dict, List, Optional, Pattern


class Whitelist:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", True))
        self.value_classes: Dict[str, Pattern[str]] = {}
        for name, pattern in (cfg.get("value_classes") or {}).items():
            self.value_classes[str(name)] = re.compile(str(pattern))
        self.location_classes: Dict[str, Dict[str, Any]] = {}
        for name, spec in (cfg.get("location_classes") or {}).items():
            self.location_classes[str(name)] = {
                "path_globs": [str(g) for g in spec.get("path_globs", [])],
                "inline_markers": [str(m) for m in spec.get("inline_markers", [])],
            }
        self.advisories: List[Dict[str, Any]] = []

    # --- value level ---------------------------------------------------
    def value_class(self, value: str) -> Optional[str]:
        if not self.enabled:
            return None
        for name, pattern in self.value_classes.items():
            if pattern.match(value):
                return name
        return None

    # --- location level ------------------------------------------------
    def match_class(self, path: str, line_text: str) -> Optional[str]:
        """Return the location class suppressing ``path``/``line_text``.

        A fixture path suppresses on its own (fixture *directories* are declared
        by policy, not by content). A non-fixture path is never suppressed, even
        with an inline marker — otherwise the marker is a universal back door.
        """
        if not self.enabled or not path:
            return None
        for name, spec in self.location_classes.items():
            if not spec["path_globs"]:
                continue
            if _any_glob(path, spec["path_globs"]):
                return name
        return None

    def inline_marker_present(self, line_text: str) -> bool:
        for spec in self.location_classes.values():
            if any(marker in line_text for marker in spec["inline_markers"]):
                return True
        return False


def _any_glob(path: str, globs: List[str]) -> bool:
    normalized = path.replace("\\", "/")
    for pattern in globs:
        if fnmatch.fnmatch(normalized, pattern):
            return True
        # `**` in fnmatch does not span separators; emulate with a translation.
        if "**" in pattern and _deep_glob(normalized, pattern):
            return True
    return False


def _deep_glob(path: str, pattern: str) -> bool:
    regex = re.escape(pattern).replace(r"\*\*", "\x00").replace(r"\*", "[^/]*").replace("\x00", ".*")
    return re.match("^" + regex + "$", path) is not None