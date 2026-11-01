"""Independent audit of the platform's structure-domain endpoints.

Modules
-------
baselines            closed-form references (Timoshenko-Gere plate, IACS S11/S7,
                     DNV-RP-C203 S-N) with citations
criteria             endpoint-agnostic criteria; pure functions of recorded calls
probe                probe plan + HTTP/cache layer
reference_candidate  analytic positive control with the platform's response schemas
audit                CLI: probe -> grade -> report/figures
"""
__all__ = ["baselines", "criteria", "probe", "reference_candidate", "audit"]
