#!/usr/bin/env python3
"""Adversarial-corpus recall measurement for the G1-G3 domain boundary gates.

Why this exists
---------------
``tests/test_boundary_gates.py`` proves the gate goes red on the violations it was
*written for*.  It says nothing about the violations it silently walks past: a
boundary gate is a filter, and a filter is only as good as its **recall** on the
adversarial population of leaks it claims to stop.  Same failure class as "green
tests on a stub" -- the artifact exists, the guarantee does not.

Method
------
1. ``build_corpus()`` synthesises a corpus of boundary cases per gate family.
   Every case is a self-contained payload (a mini repo file or an OpenAPI
   document):

   ==========  ==========================================================
   family      boundary cases injected
   ==========  ==========================================================
   G1 import   static / from / relative-alias / try-except / TYPE_CHECKING
               imports; runtime imports (``import_module``, ``__import__``,
               f-string, ``+`` concat, module-level alias, opaque path);
               camelCase module; leak planted outside the enumerated surface
   G2 endpoint vocabulary in path, case variants, path parameters,
               separator-split and separator-stripped tokens, and document
               metadata (tags / summary / description / operationId)
   G3 constant declared case (bare, attribute, dict key, default arg), case
               variants, embedded (prefix/suffix-glued) names, string and
               f-string splits, ``getattr`` strings, docstring/comment mentions
   ==========  ==========================================================

   Vocabularies are **injected as data**; the built-in synthetic vocabulary
   (``widget`` / ``flux`` / ``GADGET_RHO`` ...) carries no domain word, which
   keeps the platform test surface domain-neutral while still exercising the
   detection mechanics.

2. Each case is measured twice on the *same* payload:
   ``before`` = pre-hardening detectors (static imports only, path-only
   vocabulary, case-sensitive left-delimited constants); ``after`` = hardened
   detectors.  The recall delta is therefore attributable to the rules, not to
   easier cases.

3. Per-case verdicts: leak cases must be detected, clean cases must stay quiet
   (specificity / false-positive measurement), control cases encode behaviour
   already pinned by the test suite and must be 100 % detected.

4. Every miss gets a machine-assigned root cause from ``ROOT_CAUSES``
   (``rule_missing`` / ``logic_gap`` / ``coverage`` / ``scope``), its fix rule
   id and the switch that turns the fix on, so the miss list is a work order
   rather than a complaint.

Usage
-----
    python3 tools/audit/boundary_gate_recall.py            # measure + artifacts
    python3 tools/audit/boundary_gate_recall.py --no-live  # corpus only (no repo scan)

Exit code 0 = measurement completed (recall is data, not a verdict); 2 = the
measurement pipeline is broken (a control case not detected).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "tools" / "audit" / "boundary_gates.py"
SCHEMA = "boundary_gate_recall/v1"

#: Synthetic vocabulary -- deliberately domain-free.
SYNTH_VOCAB: Dict[str, Any] = {
    "import_tokens": ("widget", "flux"),
    "endpoint_pattern": "widget|flux|gadget-cfd|hull-form",
    "hoist_constants": ("GADGET_RHO", "SPROCKET_LENGTH"),
}

#: Surface glob the hardened configuration scans (the adversary parks a leak there).
SURFACE_GLOB = "backend/services/*.py"

#: Detector configurations.  ``before`` reproduces the pre-hardening gate.
CONFIGS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "before": {
        "g1": {"dynamic_imports": False, "surface_globs": None},
        "g2": {"metadata_scan": False, "normalize_separators": False},
        "g3": {"variants": False, "embedded": False, "constant_folding": False},
    },
    "after": {
        "g1": {"dynamic_imports": True, "surface_globs": (SURFACE_GLOB,)},
        "g2": {"metadata_scan": True, "normalize_separators": True},
        "g3": {"variants": True, "embedded": True, "constant_folding": True},
    },
}


# ---------------------------------------------------------------------------
# Root-cause taxonomy: one entry per adversarial variant (programmatic)
# ---------------------------------------------------------------------------

#: variant -> (category, cause, fix_rule, fix_switch)
ROOT_CAUSES: Dict[str, Tuple[str, str, str, str]] = {
    # -- G1 ----------------------------------------------------------------
    "static_import": ("none", "ast.Import is walked", "G1-R1", "-"),
    "from_import": ("none", "ast.ImportFrom is walked", "G1-R2", "-"),
    "relative_alias": ("none", "relative import alias names are candidates", "G1-R3", "-"),
    "nested_try": ("none", "ast.walk reaches nested scopes", "G1-R1", "-"),
    "type_checking": ("none", "ast.walk reaches guarded blocks", "G1-R1", "-"),
    "dyn_import_module": (
        "rule_missing",
        "gate only inspected ast.Import/ImportFrom; a module path handed to "
        "importlib.import_module() is a string literal, invisible to the AST import nodes",
        "G1-R4", "dynamic_imports=True",
    ),
    "dyn_dunder_import": (
        "rule_missing", "same hole as dyn_import_module for __import__()",
        "G1-R4", "dynamic_imports=True",
    ),
    "dyn_fstring": (
        "rule_missing",
        "f-string module path: no AST import node, and the literal part must be folded "
        "before token matching can see it",
        "G1-R5", "dynamic_imports=True",
    ),
    "dyn_concat": (
        "rule_missing", "module path split by '+' concatenation defeats literal-only folding",
        "G1-R5", "dynamic_imports=True",
    ),
    "dyn_via_alias": (
        "rule_missing",
        "path bound to a module-level NAME then imported indirectly (indirect literal)",
        "G1-R6", "dynamic_imports=True",
    ),
    "dyn_opaque": (
        "logic_gap",
        "path assembled from a runtime value: not machine-checkable, so it must be surfaced "
        "as an advisory instead of silently passing",
        "G1-R7", "dynamic_imports=True",
    ),
    "camel_module": ("none", "token matcher case-folds and prefix-matches segments", "G1-R1", "-"),
    "unlisted_surface_file": (
        "coverage",
        "surface was a closed whitelist (CORE_SURFACE_FILES/DIRS); a leak planted in an "
        "unenumerated core file was never read",
        "G1-R8", "surface_globs=('backend/services/*.py',)",
    ),
    "orm_lookalike": ("none", "lookalike must stay quiet", "G1-R9", "-"),
    "stdlib_only": ("none", "neutral imports must stay quiet", "G1-R9", "-"),
    "dom_local": ("none", "neutral imports must stay quiet", "G1-R9", "-"),
    "dyn_clean_target": ("none", "resolved non-domain dynamic import must stay quiet", "G1-R4", "-"),
    "mention_no_import": ("none", "a bare identifier is not an import", "G1-R9", "-"),
    # -- G2 ----------------------------------------------------------------
    "path_token": ("none", "path vocabulary is inspected", "G2-R1", "-"),
    "path_case": ("none", "path matching is case-insensitive", "G2-R1", "-"),
    "path_param": ("none", "path parameters are part of the path string", "G2-R1", "-"),
    "path_plural": ("none", "substring match covers plurals", "G2-R1", "-"),
    "sep_split": (
        "logic_gap",
        "token 'gadget-cfd' vs path '/v1/gadget_cfd': separator choice (hyphen vs "
        "underscore) defeats literal alternation",
        "G2-R2", "normalize_separators=True",
    ),
    "sep_removed": (
        "logic_gap",
        "token 'hull-form' vs path '/v1/hullform': a removed separator defeats literal alternation",
        "G2-R2", "normalize_separators=True",
    ),
    "meta_tag": (
        "rule_missing",
        "only the path string was inspected; tags/summary/description/operationId are "
        "published as domain vocabulary too",
        "G2-R3", "metadata_scan=True",
    ),
    "meta_summary": ("rule_missing", "same as meta_tag (summary field)", "G2-R3", "metadata_scan=True"),
    "meta_operation_id": (
        "rule_missing", "operationId carries the vocabulary in camelCase ('computeWidget')",
        "G2-R3", "metadata_scan=True",
    ),
    "neutral_path": ("none", "neutral path must stay quiet", "G2-R4", "-"),
    "neutral_meta": ("none", "neutral metadata must stay quiet", "G2-R4", "-"),
    "lookalike_path": ("none", "lookalike path must stay quiet", "G2-R4", "-"),
    # -- G3 ----------------------------------------------------------------
    "declared_bare": ("none", "declared-case constant in code is matched", "G3-R1", "-"),
    "declared_attr": ("none", "declared-case constant on an attribute is matched", "G3-R1", "-"),
    "declared_dict_key": ("none", "declared-case constant as a dict key is matched", "G3-R1", "-"),
    "declared_default_arg": ("none", "declared-case constant as a default arg is matched", "G3-R1", "-"),
    "declared_suffix": (
        "none",
        "suffix-glued name (GADGET_RHO_KG_M3) was already inside the historical no-trailing "
        "word-boundary regex",
        "G3-R1", "-",
    ),
    "case_variant_assign": (
        "logic_gap",
        "matching was case-sensitive, so the lower-case spelling of the same constant sailed through",
        "G3-R2", "variants=True",
    ),
    "case_variant_mixed": ("logic_gap", "mixed-case spelling", "G3-R2", "variants=True"),
    "case_variant_getattr": (
        "logic_gap", "getattr(mod, 'gadget_rho') carries the lower-case name in a string",
        "G3-R2", "variants=True",
    ),
    "case_variant_in_string": ("logic_gap", "lower-case name inside a string literal", "G3-R2", "variants=True"),
    "prefix_concat": (
        "logic_gap",
        "historical pattern required a leading word boundary, so a name glued to a prefix "
        "(DEEP_GADGET_RHO) did not match",
        "G3-R3", "embedded=True",
    ),
    "string_concat_split": (
        "rule_missing",
        "name split across '+'-concatenated literals never appears contiguously on a line, "
        "so a line-based text scan cannot see it",
        "G3-R4", "constant_folding=True",
    ),
    "fstring_split": (
        "rule_missing",
        "same as string_concat_split for f-string pieces bound to a module-level bare string",
        "G3-R4", "constant_folding=True",
    ),
    "nested_class": ("none", "ast.walk reaches class bodies", "G1-R1", "-"),
    "star_import": ("none", "the module segment of a star-import is a candidate", "G1-R2", "-"),
    "dyn_nested_call": (
        "rule_missing", "runtime import nested inside a lambda/comprehension body: still a "
        "Call node, invisible while only ast.Import nodes were walked",
        "G1-R4", "dynamic_imports=True",
    ),
    "dyn_triple_quote": (
        "rule_missing", "triple-quoted module path is a plain string constant",
        "G1-R4", "dynamic_imports=True",
    ),
    "dyn_keyword_arg": (
        "logic_gap", "module path passed as a keyword (name=) argument was not the first "
        "positional argument",
        "G1-R10", "dynamic_imports=True",
    ),
    "dyn_fromlist": (
        "rule_missing", "the path is the first positional argument, but the call node was never "
        "inspected before the runtime-import rule existed",
        "G1-R4", "dynamic_imports=True",
    ),
    "dyn_relative": (
        "rule_missing", "dot-lead relative runtime path: still a call node, never inspected before",
        "G1-R4", "dynamic_imports=True",
    ),
    "dyn_exec_string": (
        "rule_missing", "an import statement inside an exec/eval source string is not an AST "
        "import node of this module",
        "G1-R11", "dynamic_imports=True",
    ),
    "dyn_aliased_machinery": (
        "logic_gap", "the machinery was rebound to a local alias (from importlib import "
        "import_module as im), so the callee name was unrecognised",
        "G1-R12", "dynamic_imports=True",
    ),
    "dyn_getattr_machinery": (
        "logic_gap", "machinery fetched by name (getattr(importlib, 'import_module')) "
        "presents an empty callee name",
        "G1-R13", "dynamic_imports=True",
    ),
    "dyn_opaque_encoded": (
        "logic_gap", "path decoded from an encoded blob: unfolldable, therefore surfaced as "
        "an advisory (manual review) instead of passing silently",
        "G1-R7", "dynamic_imports=True",
    ),
    "dyn_getattr_neutral": ("none", "neutral dynamically fetched machinery must stay quiet", "G1-R13", "-"),
    "meta_description": ("rule_missing", "same as meta_tag (description field)", "G2-R3", "metadata_scan=True"),
    "meta_param_name": (
        "rule_missing", "query/path parameter *names* are published vocabulary, not just the "
        "path string or the four operation fields",
        "G2-R5", "metadata_scan=True",
    ),
    "path_double_sep": (
        "logic_gap", "an empty path segment splits the token ('gadget//cfd'); literal "
        "alternation cannot bridge it",
        "G2-R2", "normalize_separators=True",
    ),
    "path_segment_boundary": (
        "logic_gap", "the token is split across two path segments ('gadget/cfd')",
        "G2-R2", "normalize_separators=True",
    ),
    "doc_info_title": (
        "rule_missing", "document-level metadata (info.title/description, top-level tags) is "
        "published by /openapi.json but was outside the scan",
        "G2-R6", "metadata_scan=True",
    ),
    "doc_tags": ("rule_missing", "same as doc_info_title (top-level tags)", "G2-R6", "metadata_scan=True"),
    "meta_operation_id_upper": ("rule_missing", "upper-case operationId", "G2-R3", "metadata_scan=True"),
    "neutral_doc_info": ("none", "neutral document metadata must stay quiet", "G2-R6", "-"),
    "in_dunder_all": ("none", "declared-case name inside __all__ is a text hit", "G3-R1", "-"),
    "class_attr_declared": ("none", "declared-case class attribute is a text hit", "G3-R1", "-"),
    "annotation_string": ("logic_gap", "lower-case name inside a type-annotation string", "G3-R2", "variants=True"),
    "fstring_expression": ("none", "declared-case name inside an f-string expression", "G3-R1", "-"),
    "implicit_concat": (
        "logic_gap", "implicit adjacent-literal concatenation is merged by the parser into one "
        "constant whose pieces are non-contiguous in the source line",
        "G3-R4", "constant_folding=True",
    ),
    "bytes_literal": ("logic_gap", "lower-case name inside a bytes literal", "G3-R2", "variants=True"),
    "dot_path_case": ("logic_gap", "lower-case name on an attribute path", "G3-R2", "variants=True"),
    "decorator_string": ("logic_gap", "lower-case name in a decorator argument", "G3-R2", "variants=True"),
    "neutral_bytes": ("none", "neutral bytes literal must stay quiet", "G3-R5", "-"),
    "docstring_mention": (
        "none",
        "string-context hit: detected and triaged as 'string' (documentation duty, still a hit)",
        "G3-R1", "-",
    ),
    "comment_mention": (
        "none",
        "comment-context hit: detected and triaged as 'comment' (documentation duty, still a hit)",
        "G3-R1", "-",
    ),
    "neutral_constant": ("none", "unrelated constant must stay quiet", "G3-R5", "-"),
    "neutral_identifier": ("none", "non-constant identifier must stay quiet", "G3-R5", "-"),
    "magic_number_only": (
        "scope",
        "a bare numeric literal (rho = 1025.0) carries no name; value-level magic numbers are "
        "another gate's job -- declared out of G3 scope",
        "G3-R6", "-",
    ),
}


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

@dataclass
class Case:
    id: str
    gate: str            # G1 | G2 | G3
    variant: str
    kind: str            # leak | clean | control
    rationale: str
    files: Dict[str, str] = field(default_factory=dict)      # G1/G3 payload
    openapi: Optional[Dict[str, Any]] = None                 # G2 payload


def _py(body: str) -> str:
    return body if body.endswith("\n") else body + "\n"


def _openapi(paths: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {"openapi": "3.0.0", "paths": dict(paths)}


def _op(**extra: Any) -> Dict[str, Any]:
    return {"post": {"responses": {"200": {"description": "ok"}}, **extra}}


def build_corpus() -> List[Case]:
    cases: List[Case] = []
    add = cases.append
    s1 = "backend/api/domain/_adv_sample.py"
    s3 = "backend/api/domain/_adv_consts.py"

    # ------------------------------------------------------------------ G1 --
    add(Case("G1-L01", "G1", "static_import", "leak", "plain module import",
             {s1: _py("import widget.solver\n\nVALUE = 1\n")}))
    add(Case("G1-L02", "G1", "from_import", "leak", "from-import of a domainised package",
             {s1: _py("from widget import solver\n\nVALUE = 1\n")}))
    add(Case("G1-L03", "G1", "relative_alias", "leak",
             "package-relative import whose alias is the domain package",
             {s1: _py("from . import widget_solver\n\nVALUE = 1\n")}))
    add(Case("G1-L04", "G1", "nested_try", "leak",
             "optional import inside try/except in a nested function",
             {s1: _py("def load():\n    try:\n        import flux.backend\n"
                      "    except ImportError:\n        flux = None\n    return flux\n")}))
    add(Case("G1-L05", "G1", "type_checking", "leak", "type-checking-only import",
             {s1: _py("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
                      "    from widget.types import Solver\n")}))
    add(Case("G1-L06", "G1", "dyn_import_module", "leak",
             "runtime import with a literal module path",
             {s1: _py("import importlib\n\ndef load():\n"
                      "    return importlib.import_module('widget.solver')\n")}))
    add(Case("G1-L07", "G1", "dyn_dunder_import", "leak",
             "__import__ with a literal module path",
             {s1: _py("mod = __import__('flux.solver')\n")}))
    add(Case("G1-L08", "G1", "dyn_fstring", "leak",
             "f-string module path; the literal prefix carries the vocabulary",
             {s1: _py("import importlib\n\ndef load(name='solver'):\n"
                      "    return importlib.import_module(f'widget.{name}')\n")}))
    add(Case("G1-L09", "G1", "dyn_concat", "leak",
             "module path assembled by '+' concatenation",
             {s1: _py("import importlib\n\ndef load():\n"
                      "    return importlib.import_module('wid' + 'get.solver')\n")}))
    add(Case("G1-L10", "G1", "dyn_via_alias", "leak",
             "path bound to a module-level name, imported indirectly",
             {s1: _py("import importlib\n\nMODULE = 'widget.solver'\n\ndef load():\n"
                      "    return importlib.import_module(MODULE)\n")}))
    add(Case("G1-L11", "G1", "unlisted_surface_file", "leak",
             "leak parked in a core file outside the enumerated surface",
             {"backend/services/extra_helper.py":
              _py("from widget import solver\n\ndef helper():\n    return solver\n")}))
    add(Case("G1-L12", "G1", "camel_module", "leak",
             "camelCase module name (case folding + prefix match)",
             {s1: _py("import WidgetSolver\n\nVALUE = 1\n")}))
    add(Case("G1-L13", "G1", "dyn_opaque", "leak",
             "module path built from a runtime value (unfoldable)",
             {s1: _py("import importlib\n\ndef load(pkg):\n"
                      "    return importlib.import_module(f'{pkg}.solver')\n")}))
    add(Case("G1-C01", "G1", "orm_lookalike", "clean",
             "ORM relationship import must not fire the token matcher",
             {s1: _py("from sqlalchemy.orm import relationship\n")}))
    add(Case("G1-C02", "G1", "stdlib_only", "clean", "standard-library imports",
             {s1: _py("import json\nimport os\nfrom pathlib import Path\n")}))
    add(Case("G1-C03", "G1", "dom_local", "clean", "platform-internal import",
             {s1: _py("from api.domain import tensor\n")}))
    add(Case("G1-C04", "G1", "dyn_clean_target", "clean",
             "runtime import of a neutral module path",
             {s1: _py("import importlib\n\ndef load():\n"
                      "    return importlib.import_module('services.report_gate')\n")}))
    add(Case("G1-C05", "G1", "mention_no_import", "clean",
             "identifier that looks like the vocabulary but is not an import",
             {s1: _py("widget_count = 3\nflux_rate = 0.5\n")}))

    # ------------------------------------------------------------------ G2 --
    add(Case("G2-L01", "G2", "path_token", "leak", "domainised path segment",
             openapi=_openapi({"/v1/widget/run": _op()})))
    add(Case("G2-L02", "G2", "path_case", "leak", "upper-case path segment",
             openapi=_openapi({"/v1/WIDGET/run": _op()})))
    add(Case("G2-L03", "G2", "path_param", "leak", "vocabulary inside a path parameter",
             openapi=_openapi({"/v1/items/{widget_id}/x": _op()})))
    add(Case("G2-L04", "G2", "path_plural", "leak", "pluralised path segment",
             openapi=_openapi({"/v1/fluxes/list": _op()})))
    add(Case("G2-L05", "G2", "sep_split", "leak",
             "hyphen token vs underscore path (separator mismatch)",
             openapi=_openapi({"/v1/gadget_cfd/run": _op()})))
    add(Case("G2-L06", "G2", "sep_removed", "leak",
             "hyphen token vs separator-stripped path",
             openapi=_openapi({"/v1/hullform/run": _op()})))
    add(Case("G2-L07", "G2", "meta_tag", "leak",
             "neutral path, vocabulary in the operation tag",
             openapi=_openapi({"/v1/compute/run": _op(tags=["widget"])})))
    add(Case("G2-L08", "G2", "meta_summary", "leak",
             "neutral path, vocabulary in the summary",
             openapi=_openapi({"/v1/compute/run": _op(summary="widget solver run")})))
    add(Case("G2-L09", "G2", "meta_operation_id", "leak",
             "neutral path, vocabulary in camelCase operationId",
             openapi=_openapi({"/v1/compute/run": _op(operationId="computeWidget")})))
    add(Case("G2-C01", "G2", "neutral_path", "clean", "neutral path",
             openapi=_openapi({"/v1/calculations/run": _op(), "/v1/jobs/{id}": _op()})))
    add(Case("G2-C02", "G2", "neutral_meta", "clean", "neutral metadata",
             openapi=_openapi({"/v1/compute/run": _op(tags=["compute"], summary="run a job")})))
    add(Case("G2-C03", "G2", "lookalike_path", "clean", "lookalike path segment",
             openapi=_openapi({"/v1/relationships/list": _op()})))

    # ------------------------------------------------------------------ G3 --
    add(Case("G3-L01", "G3", "declared_bare", "leak", "declared-case constant",
             {s3: _py("GADGET_RHO = 1025.0\n")}))
    add(Case("G3-L02", "G3", "declared_attr", "leak", "constant as an attribute access",
             {s3: _py("def f(cfg):\n    return cfg.GADGET_RHO\n")}))
    add(Case("G3-L03", "G3", "declared_dict_key", "leak", "constant as a dict key",
             {s3: _py("TABLE = {'GADGET_RHO': 1025.0}\n")}))
    add(Case("G3-L04", "G3", "declared_default_arg", "leak", "constant as a default argument",
             {s3: _py("GADGET_RHO = 1025.0\n\ndef density(rho=GADGET_RHO):\n    return rho\n")}))
    add(Case("G3-L05", "G3", "declared_suffix", "leak", "suffix-glued constant name",
             {s3: _py("GADGET_RHO_KG_M3 = 1025.0\n")}))
    add(Case("G3-L06", "G3", "prefix_concat", "leak", "prefix-glued constant name",
             {s3: _py("DEEP_GADGET_RHO = 1025.0\n")}))
    add(Case("G3-L07", "G3", "case_variant_assign", "leak", "lower-case spelling",
             {s3: _py("gadget_rho = 1025.0\n")}))
    add(Case("G3-L08", "G3", "case_variant_mixed", "leak", "mixed-case spelling",
             {s3: _py("Gadget_Rho = 1025.0\n")}))
    add(Case("G3-L09", "G3", "case_variant_getattr", "leak", "lower-case name in getattr string",
             {s3: _py("def g(mod):\n    return getattr(mod, 'gadget_rho')\n")}))
    add(Case("G3-L10", "G3", "case_variant_in_string", "leak", "lower-case name inside a dict value",
             {s3: _py("META = {'name': 'gadget_rho'}\n")}))
    add(Case("G3-L11", "G3", "string_concat_split", "leak",
             "name split across '+' concatenated literals (never contiguous on a line)",
             {s3: _py("NAME = 'GADGET_' + 'RHO'\n")}))
    add(Case("G3-L12", "G3", "fstring_split", "leak",
             "name split across f-string pieces via a module-level bare string",
             {s3: _py("SUFFIX = 'RHO'\nNAME = f'GADGET_{SUFFIX}'\n")}))
    add(Case("G3-L13", "G3", "docstring_mention", "leak", "constant mentioned in a docstring",
             {s3: _py('"""Notes: GADGET_RHO is the reference density."""\n\nVALUE = 1\n')}))
    add(Case("G3-L14", "G3", "comment_mention", "leak", "constant mentioned in a comment",
             {s3: _py("# SPROCKET_LENGTH must stay in the domain body\nVALUE = 1\n")}))
    add(Case("G3-C01", "G3", "neutral_constant", "clean", "unrelated constant",
             {s3: _py("MAX_RETRIES = 3\n")}))
    add(Case("G3-C02", "G3", "neutral_identifier", "clean", "non-constant identifier",
             {s3: _py("gadget = object()\n")}))
    add(Case("G3-C03", "G3", "magic_number_only", "clean",
             "bare magic number, no name (declared out of G3 scope)",
             {s3: _py("rho = 1025.0\n")}))

    # ------------------------------------------------- batch 2 (width) ------
    # The first batch proved the specific blind spots; this batch widens the
    # population so the recall figure carries a usable binomial confidence
    # interval (>= 59 leak cases => a 62/62 result certifies >= 95 % at 95 %).
    add(Case("G1-L14", "G1", "nested_class", "leak", "import inside a class body",
             {s1: _py("class Holder:\n    import widget.solver as s\n")}))
    add(Case("G1-L15", "G1", "star_import", "leak", "star-import of a domainised module",
             {s1: _py("from widget.solver import *\n")}))
    add(Case("G1-L16", "G1", "dyn_nested_call", "leak",
             "runtime import nested inside a lambda",
             {s1: _py("import importlib\nf = lambda: importlib.import_module('widget.core')\n")}))
    add(Case("G1-L17", "G1", "dyn_triple_quote", "leak",
             "triple-quoted module path",
             {s1: _py("import importlib\nx = importlib.import_module(\"\"\"widget.core\"\"\")\n")}))
    add(Case("G1-L18", "G1", "dyn_keyword_arg", "leak",
             "module path passed as a keyword argument",
             {s1: _py("import importlib\nx = importlib.import_module(name='widget.core')\n")}))
    add(Case("G1-L19", "G1", "dyn_fromlist", "leak",
             "dunder import with a fromlist",
             {s1: _py("x = __import__('widget.core', fromlist=['solver'])\n")}))
    add(Case("G1-L20", "G1", "dyn_relative", "leak",
             "package-relative runtime import",
             {s1: _py("import importlib\nx = importlib.import_module('.widget.core', package='api')\n")}))
    add(Case("G1-L21", "G1", "dyn_exec_string", "leak",
             "import statement hidden in an exec() source string",
             {s1: _py("exec('import widget.core')\n")}))
    add(Case("G1-L22", "G1", "dyn_aliased_machinery", "leak",
             "import machinery rebound to a local alias",
             {s1: _py("from importlib import import_module as im\n"
                      "x = im('widget.core')\n")}))
    add(Case("G1-L23", "G1", "dyn_getattr_machinery", "leak",
             "import machinery fetched by name",
             {s1: _py("import importlib\n"
                      "x = getattr(importlib, 'import_module')('widget.core')\n")}))
    add(Case("G1-L24", "G1", "dyn_opaque_encoded", "leak",
             "module path decoded from an encoded blob",
             {s1: _py("import base64, importlib\n"
                      "x = importlib.import_module(base64.b64decode('d2lkZ2V0LmNvcmU='))\n")}))
    add(Case("G1-C06", "G1", "dyn_getattr_neutral", "clean",
             "neutral dynamically fetched machinery",
             {s1: _py("import importlib, services\n"
                      "x = getattr(importlib, 'import_module')('services.report_gate')\n")}))

    add(Case("G2-L10", "G2", "meta_description", "leak",
             "neutral path, vocabulary in the description",
             openapi=_openapi({"/v1/compute/run": _op(description="calls the widget solver")})))
    add(Case("G2-L11", "G2", "meta_param_name", "leak",
             "vocabulary in a query parameter name",
             openapi=_openapi({"/v1/compute/run": _op(
                 parameters=[{"name": "widget_id", "in": "query", "schema": {"type": "string"}}])})))
    add(Case("G2-L12", "G2", "path_double_sep", "leak",
             "empty path segment splits the token",
             openapi=_openapi({"/v1/gadget//cfd/run": _op()})))
    add(Case("G2-L13", "G2", "path_segment_boundary", "leak",
             "token split across two path segments",
             openapi=_openapi({"/v1/gadget/cfd/run": _op()})))
    add(Case("G2-L14", "G2", "doc_info_title", "leak",
             "vocabulary in the document info.title",
             openapi={"openapi": "3.0.0", "info": {"title": "Widget Compute API", "version": "1"},
                      "paths": {"/v1/compute/run": _op()}}))
    add(Case("G2-L15", "G2", "doc_tags", "leak",
             "vocabulary in the document-level tag list",
             openapi={"openapi": "3.0.0", "info": {"title": "Compute API", "version": "1"},
                      "tags": [{"name": "widget", "description": "solver"}],
                      "paths": {"/v1/compute/run": _op()}}))
    add(Case("G2-L16", "G2", "meta_operation_id_upper", "leak",
             "upper-case operationId",
             openapi=_openapi({"/v1/compute/run": _op(operationId="COMPUTEWIDGET")})))
    add(Case("G2-C04", "G2", "neutral_doc_info", "clean",
             "neutral document metadata",
             openapi={"openapi": "3.0.0", "info": {"title": "Compute API", "version": "1"},
                      "tags": [{"name": "compute"}],
                      "paths": {"/v1/compute/run": _op()}}))

    add(Case("G3-L15", "G3", "in_dunder_all", "leak", "declared-case name in __all__",
             {s3: _py("__all__ = ['GADGET_RHO']\n")}))
    add(Case("G3-L16", "G3", "class_attr_declared", "leak", "declared-case class attribute",
             {s3: _py("class C:\n    GADGET_RHO = 1025.0\n")}))
    add(Case("G3-L17", "G3", "annotation_string", "leak",
             "lower-case name in a type-annotation string",
             {s3: _py("def f(x: 'gadget_rho'):\n    return x\n")}))
    add(Case("G3-L18", "G3", "fstring_expression", "leak",
             "declared-case name inside an f-string expression",
             {s3: _py("GADGET_RHO = 1025.0\nMSG = f'{GADGET_RHO}'\n")}))
    add(Case("G3-L19", "G3", "implicit_concat", "leak",
             "name split across implicitly concatenated literals",
             {s3: _py("NAME = ('GADGET_' 'RHO')\n")}))
    add(Case("G3-L20", "G3", "bytes_literal", "leak",
             "lower-case name inside a bytes literal",
             {s3: _py("RAW = b'gadget_rho'\n")}))
    add(Case("G3-L21", "G3", "dot_path_case", "leak",
             "lower-case name on an attribute path",
             {s3: _py("def f(cfg):\n    return cfg.gadget_rho\n")}))
    add(Case("G3-L22", "G3", "decorator_string", "leak",
             "lower-case name in a decorator argument",
             {s3: _py("def register(name):\n    return lambda fn: fn\n\n"
                      "@register('gadget_rho')\ndef f():\n    return 1\n")}))
    add(Case("G3-C04", "G3", "neutral_bytes", "clean", "neutral bytes literal",
             {s3: _py("RAW = b'max_retries'\n")}))

    # ------------------------------------------------------------ controls --
    # Behaviour already pinned by tests/test_boundary_gates.py: these must be
    # detected in *both* configurations (E4 negative control).
    add(Case("CTL-01", "G1", "static_import", "control",
             "documented guarantee: planted static import goes red",
             {s1: _py("import widget.solver\n")}))
    add(Case("CTL-02", "G1", "relative_alias", "control",
             "documented guarantee: relative alias import goes red",
             {s1: _py("from . import widget_solver\n")}))
    add(Case("CTL-03", "G2", "path_token", "control",
             "documented guarantee: path vocabulary goes red",
             openapi=_openapi({"/v1/widget/run": _op(), "/v1/health": _op()})))
    add(Case("CTL-04", "G3", "declared_bare", "control",
             "documented guarantee: declared-case constant goes red",
             {s3: _py("GADGET_RHO = 1025.0\n")}))
    add(Case("CTL-05", "G1", "from_import", "control",
             "documented guarantee: from-import of several names goes red",
             {s1: _py("from widget import solver, types\n")}))
    # Specificity control: the documented "green on a clean tree" guarantee is a
    # quiet-case expectation, so it is scored in the clean block, not here.
    add(Case("CTL-06", "G1", "orm_lookalike", "clean",
             "documented guarantee: clean tree stays green",
             {s1: _py("from sqlalchemy.orm import relationship\n")}))
    return cases


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def load_gate_module():
    spec = importlib.util.spec_from_file_location("boundary_gates_under_test", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_case(gate, case: Case, config: Dict[str, Any], vocab: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one case against one detector configuration."""
    tmp = Path(tempfile.mkdtemp(prefix=f"bgrecall_{case.id}_"))
    detail: Dict[str, Any] = {
        "case": case.id, "gate": case.gate, "variant": case.variant, "kind": case.kind,
        "rationale": case.rationale,
    }
    try:
        if case.gate in ("G1", "G3"):
            for rel, text in case.files.items():
                path = tmp / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            extra = sorted(case.files)
            if case.gate == "G1":
                result = gate.gate_g1_imports(
                    tmp, extra, tokens=vocab["import_tokens"], **config["g1"]
                )
                hits = [v.get("file", "") for v in result.get("violations") or []]
                hits += [a.get("file", "") for a in result.get("advisories") or []]
            else:
                result = gate.gate_g3_constants(
                    tmp, extra, constants=vocab["hoist_constants"], **config["g3"]
                )
                hits = [v.get("file", "") for v in result.get("violations") or []]
            detail["raw_count"] = result.get("count")
            detail["hit_files"] = sorted(set(hits))
            detail["detected"] = bool(hits)
        else:
            doc_path = tmp / "openapi.json"
            doc_path.write_text(json.dumps(case.openapi), encoding="utf-8")
            result = gate.gate_g2_endpoints(
                "http://127.0.0.1:0/unused",
                openapi_file=str(doc_path),
                timeout=0.1,
                vocab_pattern=vocab["endpoint_pattern"],
                **config["g2"],
            )
            endpoints = result.get("endpoints") or []
            metadata = result.get("metadata_hits") or []
            detail["raw_count"] = result.get("count")
            detail["metadata_hits"] = len(metadata)
            detail["hit_files"] = sorted(
                {e["path"] for e in endpoints} | {m["path"] for m in metadata}
            )
            detail["detected"] = bool(endpoints or metadata)
        detail["expected"] = "detect" if case.kind in ("leak", "control") else "quiet"
        detail["pass"] = detail["detected"] if detail["expected"] == "detect" else not detail["detected"]
    finally:
        for path in sorted(tmp.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file():
                path.unlink()
        for path in sorted(tmp.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir():
                path.rmdir()
        tmp.rmdir()
    return detail


def _ratio(num: int, den: int) -> Optional[float]:
    return (num / den) if den else None


def summarize(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    def block(kind: str, want_detected: bool) -> Dict[str, Any]:
        subset = [r for r in results if r["kind"] == kind]
        hit = [r for r in subset if r["detected"]]
        return {
            "cases": len(subset),
            "detected": len(hit),
            "ids": [r["case"] for r in subset if bool(r["detected"]) == want_detected],
            "rate": _ratio(len(hit), len(subset)),
        }

    leaks = block("leak", True)
    clean = block("clean", False)
    clean_hit = [r for r in results if r["kind"] == "clean" and r["detected"]]
    leaks["missed_ids"] = [r["case"] for r in results if r["kind"] == "leak" and not r["detected"]]
    control = block("control", True)
    clean["false_positive_ids"] = [r["case"] for r in clean_hit]
    clean["false_positive_rate"] = _ratio(len(clean_hit), clean["cases"])
    per_gate: Dict[str, Any] = {}
    for gate_name in ("G1", "G2", "G3"):
        subset = [r for r in results if r["gate"] == gate_name]
        leak = [r for r in subset if r["kind"] == "leak"]
        cln = [r for r in subset if r["kind"] == "clean"]
        ctl = [r for r in subset if r["kind"] == "control"]
        per_gate[gate_name] = {
            "leak_cases": len(leak),
            "leak_detected": sum(1 for r in leak if r["detected"]),
            "recall": _ratio(sum(1 for r in leak if r["detected"]), len(leak)),
            "clean_cases": len(cln),
            "clean_quiet": sum(1 for r in cln if not r["detected"]),
            "false_positives": sum(1 for r in cln if r["detected"]),
            "control_cases": len(ctl),
            "control_detected": sum(1 for r in ctl if r["detected"]),
        }
    return {"leak": leaks, "clean": clean, "control": control, "per_gate": per_gate}


def measure(gate, corpus: Sequence[Case], vocab: Dict[str, Any], config_name: str) -> Dict[str, Any]:
    config = CONFIGS[config_name]
    results = [run_case(gate, case, config, vocab) for case in corpus]
    return {
        "config_name": config_name,
        "switches": config,
        "results": results,
        **summarize(results),
    }


def build_miss_ledger(before: Dict[str, Any], after: Dict[str, Any],
                      corpus: Sequence[Case]) -> List[Dict[str, Any]]:
    """One entry per leak case missed by the pre-hardening configuration."""
    by_id = {case.id: case for case in corpus}
    after_by_id = {r["case"]: r for r in after["results"]}
    ledger: List[Dict[str, Any]] = []
    for record in before["results"]:
        if record["detected"] or record["kind"] != "leak":
            continue
        case = by_id[record["case"]]
        category, cause, fix_rule, switch = ROOT_CAUSES[case.variant]
        ledger.append(
            {
                "case": case.id,
                "gate": case.gate,
                "variant": case.variant,
                "rationale": case.rationale,
                "root_cause_category": category,
                "root_cause": cause,
                "fix_rule": fix_rule,
                "fix_switch": switch,
                "detected_after_fix": after_by_id[case.id]["detected"],
                "verification": "corpus re-scan",
            }
        )
    return ledger


def live_repo_ab(gate) -> Dict[str, Any]:
    """A/B the live repository tree: same tree, two detector configurations."""
    out: Dict[str, Any] = {"repo_root": str(ROOT)}
    for name, config in CONFIGS.items():
        g1 = gate.gate_g1_imports(ROOT, **config["g1"])
        g3 = gate.gate_g3_constants(ROOT, **config["g3"])
        out[name] = {
            "G1_violations": g1["count"],
            "G1_advisories": len(g1.get("advisories") or []),
            "G1_files_scanned": g1["files_scanned"],
            "G3_violations": g3["count"],
            "G3_by_form": g3.get("by_form"),
            "G3_by_context": g3.get("by_context"),
            "G3_folding": g3.get("constant_folding_enabled"),
            "G3_files_scanned": g3["files_scanned"],
        }
    return out


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p) (exact, no SciPy dependency)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    import math

    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def recall_lower_bound(recall: Optional[float], n: int, alpha: float = 0.05) -> Optional[float]:
    """Clopper-Pearson one-sided lower confidence bound for a measured recall.

    A point recall of 100 % on 20 cases is *not* evidence of >= 95 % recall; the
    interval is what makes the number falsifiable.  Returns ``None`` without data.
    """
    if not n or recall is None:
        return None
    k = int(round(recall * n))
    if k <= 0:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if 1.0 - _binom_cdf(k - 1, n, mid) > alpha:
            hi = mid
        else:
            lo = mid
    return round(0.5 * (lo + hi), 6)


def evaluate(payload: Dict[str, Any]) -> Dict[str, Any]:
    before, after = payload["before"], payload["after"]
    ledger = payload["miss_ledger"]
    covered = sum(1 for entry in ledger if entry["root_cause"].strip())
    recall_after = after["leak"]["rate"]
    recall_before = before["leak"]["rate"]
    criteria = {
        "E1_recall_after_fix": recall_after,
        "E1_threshold": 0.95,
        "E1_samples": f"{after['leak']['detected']}/{after['leak']['cases']}",
        "E1_pass": (recall_after or 0) >= 0.95,
        "E1_recall_one_sided95_lower": recall_lower_bound(recall_after, after["leak"]["cases"]),
        "E1_statistically_certified": (recall_lower_bound(recall_after, after["leak"]["cases"]) or 0)
        >= 0.95,
        "E2_misses": len(ledger),
        "E2_root_cause_coverage": _ratio(covered, len(ledger)) if ledger else 1.0,
        "E2_pass": covered == len(ledger),
        "E3_recall_before_fix": recall_before,
        "E3_recall_after_fix": recall_after,
        "E3_recall_lift": (recall_after or 0) - (recall_before or 0),
        "E3_pass": (recall_after or 0) >= 0.95,
        "E4_control_detect_rate": _ratio(after["control"]["detected"], after["control"]["cases"]),
        "E4_control_samples": f"{after['control']['detected']}/{after['control']['cases']}",
        "E4_pass": after["control"]["detected"] == after["control"]["cases"],
        "false_positive_rate_after": after["clean"]["false_positive_rate"],
        "specificity_after": 1 - (after["clean"]["false_positive_rate"] or 0),
        "corpus_size": len(payload["corpus"]),
        "leak_cases": sum(1 for c in payload["corpus"] if c["kind"] == "leak"),
        "clean_cases": sum(1 for c in payload["corpus"] if c["kind"] == "clean"),
        "control_cases": sum(1 for c in payload["corpus"] if c["kind"] == "control"),
    }
    criteria["all_pass"] = all(
        criteria[key] for key in ("E1_pass", "E2_pass", "E3_pass", "E4_pass")
    )
    return criteria


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def render_markdown(payload: Dict[str, Any]) -> str:
    before, after, crit = payload["before"], payload["after"], payload["criteria"]
    lines = [
        "# 边界门对抗语料召回率度量（P22-N10）",
        "",
        f"- 生成时间 (UTC): `{payload['generated_at']}`",
        f"- 语料规模: {crit['corpus_size']} 例（leak {crit['leak_cases']} / clean "
        f"{crit['clean_cases']} / control {crit['control_cases']}）",
        f"- 合成词表（域无关）: `{json.dumps(payload['vocabulary'], ensure_ascii=False)}`",
        "",
        "## 判据",
        "",
        "| 判据 | 目标 | 实测 | 结论 |",
        "|---|---|---|---|",
        f"| E1 边界门对抗语料召回率 | ≥ 95% | {_pct(crit['E1_recall_after_fix'])} "
        f"({crit['E1_samples']}) | {'PASS' if crit['E1_pass'] else 'FAIL'} |",
        f"| E1' 召回率单侧 95% 置信下界（Clopper-Pearson） | ≥ 95% | "
        f"{_pct(crit['E1_recall_one_sided95_lower'])} | "
        f"{'PASS' if crit['E1_statistically_certified'] else 'FAIL'} |",
        f"| E2 漏检清单根因覆盖率 | 100% | {_pct(crit['E2_root_cause_coverage'])} "
        f"({crit['E2_misses']} 条漏检) | {'PASS' if crit['E2_pass'] else 'FAIL'} |",
        f"| E3 补规则后召回率 | ≥ 95% | {_pct(crit['E3_recall_after_fix'])}"
        f"（补规则前 {_pct(crit['E3_recall_before_fix'])}，提升 {_pct(crit['E3_recall_lift'])}）"
        f" | {'PASS' if crit['E3_pass'] else 'FAIL'} |",
        f"| E4 负控（已知边界案例）检出 | 100% | {_pct(crit['E4_control_detect_rate'])} "
        f"({crit['E4_control_samples']}) | {'PASS' if crit['E4_pass'] else 'FAIL'} |",
        f"| 特异性（clean 例不误报） | 数据 | {_pct(crit['specificity_after'])} | 数据 |",
        "",
        "## 逐门召回（补规则前 → 补规则后）",
        "",
        "| 门 | leak 数 | 前召回 | 后召回 | clean 数 | 前误报 | 后误报 |",
        "|---|---|---|---|---|---|---|",
    ]
    for gate_name in ("G1", "G2", "G3"):
        b, a = before["per_gate"][gate_name], after["per_gate"][gate_name]
        lines.append(
            f"| {gate_name} | {a['leak_cases']} | {_pct(b['recall'])} | {_pct(a['recall'])} "
            f"| {a['clean_cases']} | {b['false_positives']} | {a['false_positives']} |"
        )
    lines += [
        "",
        "## 漏检清单与根因",
        "",
        "| 案例 | 门 | 变体 | 根因类别 | 根因 | 修复规则 | 开关 | 修复后检出 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entry in payload["miss_ledger"]:
        lines.append(
            f"| {entry['case']} | {entry['gate']} | `{entry['variant']}` | "
            f"{entry['root_cause_category']} | {entry['root_cause']} | {entry['fix_rule']} | "
            f"`{entry['fix_switch']}` | {'是' if entry['detected_after_fix'] else '否'} |"
        )
    live = payload.get("live_repo") or {}
    if live:
        lines += [
            "",
            "## 真仓 A/B（同一棵树，两种配置）",
            "",
            "| 配置 | G1 违规 | G1 advisory | G1 扫描文件 | G3 违规 | G3 形态分布 |",
            "|---|---|---|---|---|---|",
        ]
        for name in ("before", "after"):
            row = live[name]
            lines.append(
                f"| {name} | {row['G1_violations']} | {row['G1_advisories']} | "
                f"{row['G1_files_scanned']} | {row['G3_violations']} | "
                f"`{json.dumps(row['G3_by_form'] or {}, ensure_ascii=False)}` |"
            )
    lines += [
        "",
        "## 复现",
        "",
        "```",
        "python3 tools/audit/boundary_gate_recall.py",
        "python3 -m pytest -q tests/test_boundary_gate_recall.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def render_figures(payload: Dict[str, Any], out_dir: Path) -> List[Dict[str, Any]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    figures: List[Dict[str, Any]] = []
    gates = ("G1", "G2", "G3")
    before = [100 * (payload["before"]["per_gate"][g]["recall"] or 0) for g in gates]
    after = [100 * (payload["after"]["per_gate"][g]["recall"] or 0) for g in gates]

    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    xs = list(range(len(gates)))
    width = 0.36
    bars_b = ax.bar([x - width / 2 for x in xs], before, width, label="before hardening",
                    color="#c0504d")
    bars_a = ax.bar([x + width / 2 for x in xs], after, width, label="after hardening",
                    color="#4f81bd")
    ax.axhline(95, linestyle="--", linewidth=1.2, color="#333333")
    ax.text(len(gates) - 0.62, 96, "acceptance 95%", fontsize=8, color="#333333")
    for bars in (bars_b, bars_a):
        for rect in bars:
            ax.annotate(f"{rect.get_height():.0f}%",
                        (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                        textcoords="offset points", xytext=(0, 2), ha="center", fontsize=7)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{g} boundary gate" for g in gates])
    ax.set_ylabel("adversarial recall [%]")
    ax.set_ylim(0, 110)
    ax.set_title("Boundary gate recall on the adversarial corpus")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    path = out_dir / "recall_before_after.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figures.append({"name": "recall_before_after.png",
                    "caption": "Per-gate adversarial recall before/after rule hardening (dashed = 95% acceptance)",
                    "path": str(path)})

    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    cats: Dict[str, int] = {}
    for entry in payload["miss_ledger"]:
        cats[entry["root_cause_category"]] = cats.get(entry["root_cause_category"], 0) + 1
    labels = list(cats)
    values = [cats[k] for k in labels]
    ax.barh(labels, values, color="#8064a2")
    for i, value in enumerate(values):
        ax.text(value + 0.1, i, str(value), va="center", fontsize=8)
    ax.set_xlabel("missed adversarial cases (before hardening)")
    ax.set_title("Miss taxonomy: why the pre-hardening gate let leaks through")
    ax.grid(axis="x", alpha=0.3)
    path = out_dir / "miss_taxonomy.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figures.append({"name": "miss_taxonomy.png",
                    "caption": "Root-cause taxonomy of the pre-hardening misses",
                    "path": str(path)})

    for figure in figures:
        figure["png_base64"] = __import__("base64").b64encode(
            Path(figure["path"]).read_bytes()
        ).decode("ascii")
    return figures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_measurement(with_live: bool = True, figures_dir: Optional[Path] = None) -> Dict[str, Any]:
    gate = load_gate_module()
    corpus = build_corpus()
    before = measure(gate, corpus, SYNTH_VOCAB, "before")
    after = measure(gate, corpus, SYNTH_VOCAB, "after")
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "gate_module": str(GATE_PATH.relative_to(ROOT)),
        "vocabulary": {k: list(v) if isinstance(v, tuple) else v for k, v in SYNTH_VOCAB.items()},
        "surface_glob": SURFACE_GLOB,
        "corpus": [
            {
                "id": case.id,
                "gate": case.gate,
                "variant": case.variant,
                "kind": case.kind,
                "rationale": case.rationale,
                "root_cause_category": ROOT_CAUSES[case.variant][0],
                "fix_rule": ROOT_CAUSES[case.variant][2],
            }
            for case in corpus
        ],
        "before": before,
        "after": after,
    }
    payload["miss_ledger"] = build_miss_ledger(before, after, corpus)
    payload["live_repo"] = live_repo_ab(gate) if with_live else {}
    payload["criteria"] = evaluate(payload)
    if figures_dir is not None:
        payload["figures"] = [
            {k: v for k, v in figure.items() if k != "png_base64"}
            for figure in render_figures(payload, figures_dir)
        ]
        payload["_figures_full"] = render_figures(payload, figures_dir)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json-out", default="artifacts/m6643_boundary_gate_recall/recall_report.json")
    parser.add_argument("--markdown-out", default="docs/reports/m6643/boundary_gate_recall.md")
    parser.add_argument("--figures-dir", default="artifacts/m6643_boundary_gate_recall/figures")
    parser.add_argument("--no-live", action="store_true", help="skip the live-repo A/B scan")
    parser.add_argument("--no-figures", action="store_true", help="skip matplotlib rendering")
    args = parser.parse_args(argv)

    payload = run_measurement(
        with_live=not args.no_live,
        figures_dir=None if args.no_figures else (ROOT / args.figures_dir),
    )
    full_figures = payload.pop("_figures_full", [])

    json_path = Path(args.json_out)
    if not json_path.is_absolute():
        json_path = ROOT / json_path
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_path = Path(args.markdown_out)
    if not md_path.is_absolute():
        md_path = ROOT / md_path
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    crit = payload["criteria"]
    print(f"[boundary_gate_recall] corpus={crit['corpus_size']} leaks={crit['leak_cases']} "
          f"clean={crit['clean_cases']} controls={crit['control_cases']}")
    print(f"  recall before={_pct(crit['E3_recall_before_fix'])} "
          f"after={_pct(crit['E1_recall_after_fix'])} "
          f"control={_pct(crit['E4_control_detect_rate'])}")
    print(f"  misses={crit['E2_misses']} root-cause coverage={_pct(crit['E2_root_cause_coverage'])} "
          f"specificity={_pct(crit['specificity_after'])}")
    print(f"  artifacts: {json_path} {md_path} "
          f"{[f['name'] for f in full_figures]}")
    print(f"  all criteria pass: {crit['all_pass']}")
    return 0 if crit["E4_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())