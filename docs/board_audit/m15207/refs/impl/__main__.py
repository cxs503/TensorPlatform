"""Allow `python3 -m tools.kpi_trace` to work."""
from __future__ import annotations

from .entry import main

raise SystemExit(main())
