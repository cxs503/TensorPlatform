"""Child entry point: ``python -m tools.longrun.jobrunner <cfg.json>``.

Kept separate from :mod:`tools.longrun.runner` so the guarded child has a stable
module path in ``sys.argv``/``/proc/<pid>/cmdline`` (useful for autopsy evidence).
"""

from __future__ import annotations

import sys

from .runner import main

if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    sys.exit(main(sys.argv[1:]))