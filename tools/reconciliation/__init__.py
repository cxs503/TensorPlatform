"""Reconciliation package (terminal-state ↔ report ledger tooling).

Zero domain semantics: this package only knows about campaign nodes, missions,
reports and the supplement outbox — never about a physical/engineering domain.
"""