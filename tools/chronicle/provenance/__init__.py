"""Full-text numeric provenance for chronicle reports (W18-N10).

Upgrades the zero-hand-copy discipline from two enforced blocks
(``tools/chronicle/report/provenance.py``) to machine-provable 100%
full-document coverage:

* ``tokens.py``        -- total numeric-token extractor + binding/coverage gate
* ``exemptions.yaml``  -- machine-auditable exemption rules (hash-anchored)

Every digit in a manuscript must be inside exactly one extracted token, and
every token must be either bound to a provenance record (``{{prov:pid}}``) or
exempted by a hash-anchored rule.  Anything else is an unbound number and the
gate says FAIL.
"""
from .tokens import (  # noqa: F401
    EIGHT_FORMS,
    NumericToken,
    check_coverage,
    extract_numeric_tokens,
    load_exemptions,
)
