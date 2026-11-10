"""Case specifications: fully data-driven wiring of a black-box model + marginals.

A *case* is a JSON document (see ``config/uq_cases/``) that names

* the black-box model by dotted path (``package.module:callable``), including
  any fixed keyword arguments, the scalar calling convention and which field of
  the returned object is the response;
* the uncertain inputs, each with a marginal law;
* the PCE configuration and the Monte Carlo budget;
* the acceptance criteria (thresholds the runner must machine-decide).

Nothing domain specific lives in this module or in the kernel: the physical
content of a study arrives as data through the case file, which is exactly the
platform rule ("域数据经 API/配置/工件注入").
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .marginals import Marginal, marginals_from_spec


def load_module_from_file(file_path: str | Path):
    """Load a standalone python file as a module (no package ``__init__`` chain).

    Domain modules under ``backend/api/domain/`` are individually importable
    files whose package ``__init__`` pulls in the web stack; the contract gate
    only installs pytest, so a file-based load keeps the case runner runnable
    there.  The synthetic module name is derived from the file stem.
    """
    import importlib.util
    import sys

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"model file not found: {path}")
    mod_name = f"_uq_case_model_{path.stem}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def resolve_callable(path: str, file_path: Optional[str] = None):
    """Resolve ``"pkg.module:attribute"`` (or ``pkg.module.attribute``) to an object.

    When ``file_path`` is given the module is loaded straight from that file,
    which avoids importing package ``__init__`` side effects.
    """
    if ":" in path:
        mod_name, attr = path.split(":", 1)
    else:
        mod_name, _, attr = str(path).rpartition(".")
    if not mod_name or not attr:
        raise ValueError(f"cannot parse callable path {path!r}; use 'pkg.module:callable'")
    if file_path:
        module = load_module_from_file(file_path)
    else:
        module = importlib.import_module(mod_name)
    obj = module
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


@dataclass
class ModelSpec:
    path: str
    output: Optional[str] = None          # attribute to read from the return value
    call_style: str = "kwargs"            # "kwargs" -> f(**inputs); "array" -> f(array)
    fixed: Dict[str, float] = field(default_factory=dict)
    file: Optional[str] = None            # load the module from this file instead of sys.path

    def load(self):
        return resolve_callable(self.path, self.file)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ModelSpec":
        style = str(d.get("call_style", "kwargs")).lower()
        if style not in ("kwargs", "array"):
            raise ValueError(f"unknown call_style {style!r}")
        return ModelSpec(path=str(d["path"]), output=d.get("output"),
                         call_style=style, fixed=dict(d.get("fixed", {})),
                         file=d.get("file"))


@dataclass
class CaseSpec:
    case_id: str
    title: str
    model: ModelSpec
    inputs: Sequence[Dict[str, Any]]
    pce: Dict[str, Any]
    mc: Dict[str, Any]
    criteria: Dict[str, Any]
    reference: Optional[Dict[str, Any]] = None
    notes: str = ""
    presentation: Dict[str, Any] = field(default_factory=dict)
    docs: Dict[str, str] = field(default_factory=dict)

    @property
    def marginals(self) -> List[Marginal]:
        return list(marginals_from_spec(self.inputs))

    @staticmethod
    def load(path: str | Path) -> "CaseSpec":
        doc = json.loads(Path(path).read_text())
        return CaseSpec(
            case_id=str(doc["case_id"]),
            title=str(doc.get("title", doc["case_id"])),
            model=ModelSpec.from_dict(doc["model"]),
            inputs=list(doc["inputs"]),
            pce=dict(doc.get("pce", {})),
            mc=dict(doc.get("mc", {})),
            criteria=dict(doc.get("criteria", {})),
            reference=dict(doc["reference"]) if doc.get("reference") else None,
            notes=str(doc.get("notes", "")),
            presentation=dict(doc.get("presentation", {})),
            docs=dict(doc.get("docs", {})),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "model": {
                "path": self.model.path,
                "output": self.model.output,
                "call_style": self.model.call_style,
                "fixed": self.model.fixed,
                "file": self.model.file,
            },
            "inputs": [dict(i) for i in self.inputs],
            "pce": dict(self.pce),
            "mc": dict(self.mc),
            "criteria": dict(self.criteria),
            "reference": self.reference,
            "presentation": dict(self.presentation),
            "docs": dict(self.docs),
            "notes": self.notes,
        }