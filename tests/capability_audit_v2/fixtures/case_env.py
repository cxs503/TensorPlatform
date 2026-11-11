"""Synthetic test module that fails for an *environment* reason (unwired provider).

The audit must classify this as ENV_ERROR and keep the capability at WITHHELD:
host wiring problems must never be laundered into a capability verdict.
"""


def test_requires_unwired_provider():
    raise RuntimeError("ProviderNotRegistered: no provider registered for ('synthetic', 'x')")