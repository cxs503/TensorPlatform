"""Allow running as python -m tools.failure_minimizer"""
from .cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())
