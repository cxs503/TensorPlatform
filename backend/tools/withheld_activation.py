#!/usr/bin/env python3
"""Entry point for the WITHHELD reference-activation mechanism.

Thin wrapper so the mechanism can be driven from a repo checkout without setting
``PYTHONPATH`` by hand, mirroring the other ``backend/tools`` consoles::

    python backend/tools/withheld_activation.py acceptance --figures
    python backend/tools/withheld_activation.py seed  --state-dir artifacts/withheld_activation/state
    python backend/tools/withheld_activation.py scan  --state-dir artifacts/withheld_activation/state
    python backend/tools/withheld_activation.py gate  --state-dir artifacts/withheld_activation/state

Exit codes follow the CLI (0 pass, 1 usage/unexpected, 2 recompute gate refusal).
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main(argv=None) -> int:
    from services.withheld_activation.cli import main as cli_main

    return cli_main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())