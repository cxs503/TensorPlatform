"""Shared helpers for the evidence-pipeline test suite."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.evidence_pipeline.config import ROOT, load_config
from tools.evidence_pipeline.runner import RunResult, run_benchmark

BENCHMARKS = ROOT / "config" / "evidence_pipeline" / "benchmarks"
CONTROLS = ROOT / "config" / "evidence_pipeline" / "controls"

PASSING_BENCHMARKS: Sequence[str] = (
    "syn-alpha-quadrature",
    "syn-beta-exchange",
    "syn-gamma-partition",
)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def _walk_set(obj: Any, tokens: Sequence[Any], value: Any) -> Any:
    if len(tokens) == 1:
        obj[tokens[0]] = value
        return obj
    head, rest = tokens[0], tokens[1:]
    if isinstance(obj, list):
        obj[head] = _walk_set(obj[head], rest, value)
    else:
        if head not in obj:
            obj[head] = {}
        obj[head] = _walk_set(obj[head], rest, value)
    return obj


def mutate_config(
    source: str | Path,
    target: str | Path,
    changes: Mapping[str, Any] | None = None,
    drop: Iterable[Sequence[Any]] = (),
) -> Path:
    """Copy a benchmark config, applying dotted-path changes (``"a.0.b"``)."""
    data = copy.deepcopy(load_json(source))
    for dotted, value in (changes or {}).items():
        tokens = [int(t) if t.lstrip("-").isdigit() else t for t in dotted.split(".")]
        data = _walk_set(data, tokens, value)
    for dotted in drop:
        tokens = [int(t) if str(t).lstrip("-").isdigit() else t for t in dotted]
        container = data
        for token in tokens[:-1]:
            container = container[token]
        container.pop(tokens[-1], None)
    if isinstance(target, Path) and target.suffix == ".json":
        return write_json(target, data)
    return Path(target)


def run(config: str | Path, out_dir: str | Path, **kwargs: Any) -> RunResult:
    cfg = load_config(config)
    return run_benchmark(cfg, config_path=str(config), out_dir=str(out_dir), **kwargs)


def localise(config_name: str, tmp_path: Path, subdir: str = "cfg") -> Path:
    """Copy a committed benchmark config with its baseline redirected into tmp."""
    src = BENCHMARKS / f"{config_name}.json"
    data = load_json(src)
    data.setdefault("regression", {})["baseline"] = str(tmp_path / "baselines" / f"{config_name}.json")
    return write_json(tmp_path / subdir / f"{config_name}.json", data)


def artifact_of(out_dir: str | Path, node: str) -> Path:
    return Path(out_dir) / f"{node}.artifact.json"


def record_of(out_dir: str | Path, node: str) -> Path:
    return Path(out_dir) / f"{node}.run.json"