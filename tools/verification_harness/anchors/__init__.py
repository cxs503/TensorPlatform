#!/usr/bin/env python3
"""Synthetic anchor applications of the verification harness.

This package is an *application* of the mechanism (it is deliberately not
imported by :mod:`tools.verification_harness.model` / ``registry`` / ``runner`` /
``evidence``).  Cases reference it by dotted path from
``tools/verification_harness/cases/*.json``, exactly like any external domain
would reference its own solver package.
"""
from __future__ import annotations

from . import synthetic  # noqa: F401

__all__ = ["synthetic"]