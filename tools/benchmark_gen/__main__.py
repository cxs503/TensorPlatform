"""``python -m tools.benchmark_gen`` entry point."""

import sys

from .generator import main

if __name__ == "__main__":
    sys.exit(main())