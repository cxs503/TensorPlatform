"""
开放数字化底座 Python SDK
官方客户端库
"""

__version__ = "1.0.0"
__author__ = "Open Digital Foundation"

from .client import foundationClient
from .models import Datasets, Deployment, Models, Projects, Training

__all__ = [
    "foundationClient",
    "Projects",
    "Datasets",
    "Models",
    "Training",
    "Deployment",
]
