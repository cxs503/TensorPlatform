"""Scorer robustness experiment: randomized trials for text intervention sensitivity.

Tests three intervention types against a deterministic report scorer:
- Rhetorical inflation (措辞膨胀): flowery language, same content
- Number fabrication (数字伪造): injected plausible but fake numbers
- Citation inflation (引用膨胀): added irrelevant authoritative citations

Metrics: flip_rate, Cohen's d, negative/positive controls.
"""

__version__ = "0.1.0"
