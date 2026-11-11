"""``python -m tools.external_tools`` → the same CLI as ``external_tools.cli``.

A package without a ``__main__`` cannot be run as a module, which is the first
thing an operator tries; the thin delegation keeps one implementation only.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())