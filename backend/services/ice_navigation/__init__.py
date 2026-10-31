"""
Ice Navigation Module for TensorPlatform
SIR-PC2|I04: Ice Breaking Force Time Series & Experimental Validation

Physics-based ice-structure interaction model based on:
- Matlock et al. (1971): Ice-structure interaction
- Chatterjee et al. (1976): Dynamic ice forces on offshore structures
- Croasdale (1974): Ice forces on fixed and floating structures
- Nevel (1971): Characteristic length in ice flexure

This module implements:
1. Ice breaking force time series computation
2. Comparison with experimental/model test data
3. Spectral analysis of force signals
4. Validation metrics (peak, mean, spectral peak frequency)
"""

from .ice_breaking_force import IceBreakingForceModel
from .validation import IceForceValidator

__all__ = ['IceBreakingForceModel', 'IceForceValidator']
