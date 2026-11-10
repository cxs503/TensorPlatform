"""Allow running as: python -m capability_discovery_multirepo"""
from .cli import main
import sys

sys.exit(main())
