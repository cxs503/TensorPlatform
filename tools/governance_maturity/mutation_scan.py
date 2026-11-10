"""AST-level, domain-agnostic mutation scanner with a sharded kill-rate ledger.

Why
---
"The test suite passes" says nothing about *what the suite can detect*.  This module
mutates a target source file at the AST level (no domain knowledge needed) and
re-runs a declared test command against each mutant in an isolated sandbox:

* killed   -- test command fails (the suite noticed the semantic damage);
* survived -- test command passes (the suite is blind to that mutation);
* timeout  -- test command exceeded its budget (reported separately, never
  silently counted as a kill);
* invalid  -- the mutated file no longer parses / the mutant is not applicable.

Operators (semantic) and equivalent-mutation controls are separate: controls
are behaviour-preserving edits that *must survive* -- their survival rate is
the negative-control **precision** of the instrument (a harness that kills
everything is not measuring test quality).

Domain independence is structural: operators only look at Python syntax
(statements, comparisons, constants, subscripts, calls, booleans).  Target
modules and test commands come from a JSON target document, so no domain term
lives in this file.

Sharding
--------
Every mutant has an index; ``--shard i/n`` keeps ``mutants[i::n]``.  A shard
writes its own ledger; :func:`aggregate` merges the shards, checks that the
union is complete and computes the kill rate with a 95% interval
(Wilson interval plus a stratified bootstrap over modules).
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SEMANTIC_OPERATORS: tuple[str, ...] = (
    "delete_statement",
    "negate_condition",
    "boundary_swap",
    "scale_constant",
    "shift_index",
    "swap_args",
    "flip_bool",
)
CONTROL_OPERATORS: tuple[str, ...] = ("insert_pass", "dead_branch")
ALL_OPERATORS: tuple[str, ...] = SEMANTIC_OPERATORS + CONTROL_OPERATORS

_KILL_STATUSES = ("killed",)
_SURVIVE_STATUSES = ("survived",)
_NON_COUNTED = ("timeout", "invalid", "error", "infra_error")

#: Failure signatures that mean "the sandbox could not run", not "the suite
#: caught the mutation".  Counting these as kills inflates the kill rate.
INFRA_ERROR_MARKERS = (
    "No space left on device",
    "No usable temporary directory",
    "OSError: [Errno 28]",
    "Cannot allocate memory",
    "MemoryError",
    "Read-only file system",
    "Disk quota exceeded",
)


def classify_result(status: str, output: str) -> str:
    """Downgrade infrastructure failures so they never count as kills."""
    if status != "killed":
        return status
    text = output or ""
    if any(marker in text for marker in INFRA_ERROR_MARKERS):
        return "infra_error"
    return status


# --------------------------------------------------------------------------- #
# AST plumbing
# --------------------------------------------------------------------------- #
def _index_tree(tree: ast.AST) -> list[tuple[ast.AST, ast.AST | None, str | None, int | None, tuple]]:
    """Deterministic depth-first list of (node, parent, field, index, path)."""
    out: list[tuple[ast.AST, ast.AST | None, str | None, int | None, tuple]] = []

    def rec(node: ast.AST, parent: ast.AST | None, field: str | None, index: int | None, path: tuple) -> None:
        out.append((node, parent, field, index, path))
        for name, value in ast.iter_fields(node):
            if isinstance(value, ast.AST):
                rec(value, node, name, None, path + (("f", name),))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        rec(item, node, name, i, path + (("f", name), ("i", i)))

    rec(tree, None, None, None, ())
    return out


def _nodes_with_parents(tree: ast.AST) -> list[tuple[ast.AST, ast.AST | None, str | None, int | None]]:
    """Backwards-compatible view without paths."""
    return [(n, p, f, i) for (n, p, f, i, _path) in _index_tree(tree)]


def _resolve(tree: ast.AST, path: tuple) -> tuple[ast.AST, ast.AST | None, str | None, int | None]:
    """Navigate a freshly parsed tree by the recorded path."""
    node: ast.AST = tree
    owner: ast.AST | None = None
    field: str | None = None
    index: int | None = None
    for kind, key in path:
        if kind == "f":
            owner, field = node, key
            index = None
            node = getattr(node, key)
        else:
            index = key
            node = node[key]  # type: ignore[index]
    return node, owner, field, index


def _is_docstring_slot(node: ast.AST, parent: ast.AST, field_name: str | None,
                       index: int | None) -> bool:
    """True only for an actual string-literal expression in slot 0 of a body."""
    return (
        field_name == "body"
        and index == 0
        and isinstance(parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _applicable(node: ast.AST, parent: ast.AST | None, field_name: str | None, index: int | None,
                operator: str) -> bool:
    if operator in ("delete_statement", "insert_pass", "dead_branch"):
        return isinstance(node, ast.stmt) and index is not None and isinstance(parent, ast.AST) \
            and not _is_docstring_slot(node, parent, field_name, index) \
            and not isinstance(node, (ast.Import, ast.ImportFrom))
    if operator == "negate_condition":
        return isinstance(node, (ast.Compare, ast.BoolOp))
    if operator == "boundary_swap":
        return isinstance(node, ast.Compare)
    if operator == "scale_constant":
        return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool) and node.value != 0
    if operator == "shift_index":
        return isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
            and isinstance(node.slice.value, int) and not isinstance(node.slice.value, bool)
    if operator == "swap_args":
        return isinstance(node, ast.Call) and len(node.args) >= 2
    if operator == "flip_bool":
        return isinstance(node, ast.Constant) and isinstance(node.value, bool)
    return False


_FLIP_CMP = {
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
_BOUNDARY = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt}


def _mutate_node(tree: ast.AST, path: tuple, operator: str) -> tuple[ast.AST | None, str]:
    """Mutate *in place* on a freshly parsed tree; return (tree, description)."""
    node, parent, field_name, index = _resolve(tree, path)
    if not _applicable(node, parent, field_name, index, operator):
        return None, "node not applicable"
    try:
        before = ast.unparse(node)
    except Exception:  # pragma: no cover
        before = type(node).__name__

    if operator == "delete_statement":
        body = getattr(parent, field_name)  # type: ignore[arg-type]
        assert isinstance(body, list) and index is not None
        body.pop(index)
        if not body:
            body.append(ast.Pass())
    elif operator == "insert_pass":
        body = getattr(parent, field_name)  # type: ignore[arg-type]
        assert isinstance(body, list) and index is not None
        body.insert(index + 1, ast.Pass())
    elif operator == "dead_branch":
        body = getattr(parent, field_name)  # type: ignore[arg-type]
        assert isinstance(body, list) and index is not None
        body.insert(index, ast.If(test=ast.Constant(value=False), body=[ast.Pass()], orelse=[]))
    elif operator == "negate_condition":
        if isinstance(node, ast.Compare):
            node.ops = [_FLIP_CMP.get(type(op), type(op))() for op in node.ops]
        else:
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif operator == "boundary_swap":
        node.ops = [_BOUNDARY.get(type(op), type(op))() for op in node.ops]
    elif operator == "scale_constant":
        node.value = node.value * 1e3
    elif operator == "shift_index":
        node.slice = ast.Constant(value=node.slice.value + 1)
    elif operator == "swap_args":
        node.args[0], node.args[1] = node.args[1], node.args[0]
    elif operator == "flip_bool":
        node.value = not node.value
    else:  # pragma: no cover - guarded by caller
        return None, f"unknown operator {operator!r}"

    try:
        ast.fix_missing_locations(tree)
        source = ast.unparse(tree)
    except Exception as exc:  # pragma: no cover - unparse is very stable
        return None, f"unparse failed: {exc}"
    after = ast.unparse(node).splitlines()[0]
    return tree, f"{operator}: {before}  ->  {after}"


# --------------------------------------------------------------------------- #
# mutant enumeration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Mutant:
    mutant_id: str
    target: str
    operator: str
    kind: str  # "semantic" | "control"
    lineno: int
    path: tuple  # tuple of ("f", field_name) / ("i", index) steps from the module root

    def as_dict(self) -> dict[str, Any]:
        return {
            "mutant_id": self.mutant_id,
            "target": self.target,
            "operator": self.operator,
            "kind": self.kind,
            "lineno": self.lineno,
            "path": [list(step) for step in self.path],
        }

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "Mutant":
        return Mutant(
            mutant_id=str(doc["mutant_id"]),
            target=str(doc["target"]),
            operator=str(doc["operator"]),
            kind=str(doc["kind"]),
            lineno=int(doc.get("lineno", 0)),
            path=tuple((str(k), int(v)) if k == "i" else (str(k), str(v)) for k, v in doc["path"]),
        )


def render_mutant(source: str, mutant: Mutant) -> str | None:
    """Apply one recorded mutation to *source*; return the mutated file text."""
    tree = ast.parse(source)
    try:
        mutated, _description = _mutate_node(tree, mutant.path, mutant.operator)
    except (AttributeError, IndexError, TypeError, KeyError):
        return None
    if mutated is None:
        return None
    return ast.unparse(mutated)


def enumerate_mutants(
    target: str,
    source: str,
    operators: Sequence[str] = ALL_OPERATORS,
    max_controls: int = 48,
) -> list[Mutant]:
    """List mutation sites without rendering mutant sources (fast)."""
    tree = ast.parse(source)
    indexed = _index_tree(tree)
    semantic: list[Mutant] = []
    controls: list[Mutant] = []
    seen: set[str] = set()
    for node, parent, field_name, index, path in indexed:
        for operator in operators:
            if not _applicable(node, parent, field_name, index, operator):
                continue
            key = hashlib.sha256(f"{operator}|{path}".encode()).hexdigest()[:12]
            if key in seen:
                continue
            seen.add(key)
            mutant = Mutant(
                mutant_id=f"{target}::{operator}@{getattr(node, 'lineno', 0) or 0}::{key}",
                target=target,
                operator=operator,
                kind="control" if operator in CONTROL_OPERATORS else "semantic",
                lineno=getattr(node, "lineno", 0) or 0,
                path=path,
            )
            (controls if mutant.kind == "control" else semantic).append(mutant)
    if max_controls and len(controls) > max_controls:
        step = len(controls) / max_controls
        controls = [controls[int(i * step)] for i in range(max_controls)]
    return semantic + controls


# --------------------------------------------------------------------------- #
# target specification & sandbox execution
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TargetSpec:
    name: str
    repo: str
    repo_root: str
    module: str
    test_cmd: tuple[str, ...]
    copy_paths: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_s: float = 120.0
    pythonpath: str | None = None

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "TargetSpec":
        return TargetSpec(
            name=str(doc["name"]),
            repo=str(doc["repo"]),
            repo_root=str(doc.get("repo_root", ".")),
            module=str(doc["module"]),
            test_cmd=tuple(doc["test_cmd"]),
            copy_paths=tuple(doc.get("copy_paths", ())),
            env={str(k): str(v) for k, v in doc.get("env", {}).items()},
            timeout_s=float(doc.get("timeout_s", 120.0)),
            pythonpath=doc.get("pythonpath"),
        )


def load_targets(path: str | Path) -> list[TargetSpec]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return [TargetSpec.from_dict(d) for d in doc["targets"]]


def _copy_into(src_root: Path, rel: str, dst_root: Path) -> None:
    src = src_root / rel
    dst = dst_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True, copy_function=shutil.copy2)
    else:
        shutil.copy2(src, dst)


def prepare_sandbox(target: TargetSpec, root: Path, sandbox: Path) -> Path:
    """Copy the declared paths (hard links) into a throw-away tree."""
    src_root = (root / target.repo_root).resolve() if not Path(target.repo_root).is_absolute() \
        else Path(target.repo_root)
    for rel in target.copy_paths:
        if rel.startswith("worktree::"):
            _copy_into(root, rel[len("worktree::"):], sandbox)
        else:
            _copy_into(src_root, rel, sandbox)
    return sandbox


def _run(cmd: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> tuple[int, str, float]:
    started = time.time()
    try:
        proc = subprocess.run(
            list(cmd), cwd=str(cwd), env=dict(env), capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), time.time() - started
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(out, bytes):  # pragma: no cover - text=True keeps str
            out = out.decode("utf-8", "replace")
        return 124, out, time.time() - started


def _base_env(target: TargetSpec, sandbox: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(target.env)
    parts = [str(sandbox)]
    if target.pythonpath:
        parts.append(target.pythonpath)
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("PYTHONHASHSEED", "0")
    return env


def run_shard(
    target: TargetSpec,
    root: str | Path,
    shard: int,
    n_shards: int,
    *,
    keep_sandbox: str | Path | None = None,
    mutants_path: str | Path | None = None,
) -> dict[str, Any]:
    """Enumerate mutants, run this shard's subset, return the shard ledger."""
    root = Path(root).resolve()
    module_path = (root / target.repo_root / target.module) if not Path(target.repo_root).is_absolute() \
        else Path(target.repo_root) / target.module
    module_path = module_path.resolve()
    source = module_path.read_text(encoding="utf-8")
    module_digest = "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
    if mutants_path is not None:
        doc = json.loads(Path(mutants_path).read_text(encoding="utf-8"))
        if doc.get("module_sha256") != module_digest:
            raise ValueError(
                f"mutant file was enumerated from a different module revision: "
                f"{doc.get('module_sha256')} != {module_digest}"
            )
        mutants = [Mutant.from_dict(m) for m in doc["mutants"]]
    else:
        mutants = enumerate_mutants(target.name, source)
    selected = mutants[shard - 1 :: n_shards] if n_shards > 1 else mutants

    sandbox = Path(keep_sandbox) if keep_sandbox else Path(tempfile.mkdtemp(prefix=f"gm-{target.name}-"))
    sandbox.mkdir(parents=True, exist_ok=True)
    prepare_sandbox(target, root, sandbox)
    target_file = sandbox / target.module
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(source, encoding="utf-8")
    env = _base_env(target, sandbox)

    pristine_rc, pristine_out, pristine_dur = _run(target.test_cmd, sandbox, env, target.timeout_s)
    baseline = {
        "returncode": pristine_rc,
        "duration_s": round(pristine_dur, 3),
        "status": "pass" if pristine_rc == 0 else "fail",
        "output_tail": pristine_out[-2000:],
    }
    results: list[dict[str, Any]] = []
    try:
        for mutant in selected:
            mutated_source = render_mutant(source, mutant)
            if mutated_source is None:
                results.append(
                    {
                        "mutant_id": mutant.mutant_id,
                        "operator": mutant.operator,
                        "kind": mutant.kind,
                        "lineno": mutant.lineno,
                        "description": "not renderable",
                        "status": "invalid",
                        "duration_s": 0.0,
                        "returncode": None,
                        "output_tail": "",
                    }
                )
                continue
            target_file.write_text(mutated_source, encoding="utf-8")
            rc, out, dur = _run(target.test_cmd, sandbox, env, target.timeout_s)
            if rc == 0:
                status = "survived"
            elif rc == 124:
                status = "timeout"
            else:
                status = classify_result("killed", out)
            results.append(
                {
                    "mutant_id": mutant.mutant_id,
                    "operator": mutant.operator,
                    "kind": mutant.kind,
                    "lineno": mutant.lineno,
                    "description": f"{mutant.operator} @ line {mutant.lineno}",
                    "status": status,
                    "duration_s": round(dur, 3),
                    "returncode": rc,
                    "output_tail": out[-800:],
                }
            )
    finally:
        target_file.write_text(source, encoding="utf-8")

    ledger = {
        "schema": "mutation-shard/1.0",
        "target": target.name,
        "repo": target.repo,
        "module": target.module,
        "repo_root": str(root / target.repo_root),
        "module_sha256": module_digest,
        "test_cmd": list(target.test_cmd),
        "shard": shard,
        "n_shards": n_shards,
        "n_mutants_total": len(mutants),
        "n_mutants_selected": len(selected),
        "baseline": baseline,
        "results": results,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not keep_sandbox:
        shutil.rmtree(sandbox, ignore_errors=True)
    return ledger


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def wilson_interval(kills: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = kills / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_interval(
    per_target: Mapping[str, tuple[int, int]],
    n_boot: int = 2000,
    seed: int = 20261110,
) -> tuple[float, float]:
    """Stratified bootstrap over targets, resampling mutants within each target."""
    if not per_target:
        return (0.0, 0.0)
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(n_boot):
        kills = total = 0
        for k, n in per_target.values():
            if n <= 0:
                continue
            p = k / n
            sample_kills = sum(1 for _ in range(n) if rng.random() < p)
            kills += sample_kills
            total += n
        stats.append(kills / total if total else 0.0)
    stats.sort()
    lo = stats[int(0.025 * (len(stats) - 1))]
    hi = stats[int(0.975 * (len(stats) - 1))]
    return (lo, hi)


def aggregate(ledgers: Iterable[Mapping[str, Any]], expected_shards: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Merge shard ledgers into a kill-rate ledger with 95% intervals."""
    by_target: dict[str, list[Mapping[str, Any]]] = {}
    for ledger in ledgers:
        by_target.setdefault(str(ledger["target"]), []).append(ledger)

    targets: dict[str, Any] = {}
    for name, shards in sorted(by_target.items()):
        shards = sorted(shards, key=lambda d: int(d["shard"]))
        results = [r for s in shards for r in s["results"]]
        semantic = [r for r in results if r["kind"] == "semantic"]
        controls = [r for r in results if r["kind"] == "control"]
        killed = sum(1 for r in semantic if r["status"] == "killed")
        survived = sum(1 for r in semantic if r["status"] == "survived")
        timeouts = sum(1 for r in semantic if r["status"] == "timeout")
        invalid = sum(1 for r in semantic if r["status"] == "invalid")
        infra_errors = sum(1 for r in semantic if r["status"] == "infra_error")
        control_survived = sum(1 for r in controls if r["status"] == "survived")
        control_killed = sum(1 for r in controls if r["status"] == "killed")
        control_timeout = sum(1 for r in controls if r["status"] == "timeout")
        control_infra = sum(1 for r in controls if r["status"] == "infra_error")

        n_shards = int(shards[0]["n_shards"])
        expected = (expected_shards or {}).get(name, n_shards)
        complete = len(shards) == expected and expected == n_shards
        baseline_ok = all(s["baseline"]["status"] == "pass" for s in shards)
        kill_rate = killed / (killed + survived) if (killed + survived) else None
        lo, hi = wilson_interval(killed, killed + survived)
        per_operator: dict[str, dict[str, int]] = {}
        for r in semantic:
            slot = per_operator.setdefault(r["operator"], {"killed": 0, "survived": 0, "timeout": 0})
            if r["status"] in slot:
                slot[r["status"]] += 1
        for slot in per_operator.values():
            denom = slot["killed"] + slot["survived"]
            slot["kill_rate"] = slot["killed"] / denom if denom else None
        targets[name] = {
            "repo": shards[0]["repo"],
            "module": shards[0]["module"],
            "module_sha256": shards[0]["module_sha256"],
            "test_cmd": shards[0]["test_cmd"],
            "n_shards": n_shards,
            "n_shards_present": len(shards),
            "mutants_total": shards[0]["n_mutants_total"],
            "semantic_total": len(semantic),
            "killed": killed,
            "survived": survived,
            "timeout": timeouts,
            "invalid": invalid,
            "infra_error": infra_errors,
            "kill_rate": kill_rate,
            "wilson95": [lo, hi],
            "per_operator": per_operator,
            "controls_total": len(controls),
            "control_survived": control_survived,
            "control_killed": control_killed,
            "control_timeout": control_timeout,
            "control_infra_error": control_infra,
            "control_judged": control_survived + control_killed,
            "control_precision": (control_survived / (control_survived + control_killed)
                                  if (control_survived + control_killed) else None),
            "control_failures": [
                {"mutant_id": r["mutant_id"], "operator": r["operator"], "lineno": r["lineno"],
                 "status": r["status"]}
                for r in controls if r["status"] != "survived"
            ][:50],
            "baseline_ok": baseline_ok,
            "shards_complete": complete,
            "survivors": [r for r in semantic if r["status"] == "survived"][:200],
        }

    per_target_pairs = {
        name: (t["killed"], t["killed"] + t["survived"])
        for name, t in targets.items()
        if t["killed"] is not None and (t["killed"] + t["survived"]) > 0
    }
    total_killed = sum(t["killed"] for t in targets.values())
    total_judged = sum(t["killed"] + t["survived"] for t in targets.values())
    overall = {
        "targets": len(targets),
        "repos": sorted({t["repo"] for t in targets.values()}),
        "semantic_mutants": total_judged,
        "infra_error": sum(t["infra_error"] for t in targets.values()),
        "killed": total_killed,
        "kill_rate": total_killed / total_judged if total_judged else None,
        "wilson95": list(wilson_interval(total_killed, total_judged)),
        "bootstrap95": list(bootstrap_interval(per_target_pairs)),
        "controls": sum(t["controls_total"] for t in targets.values()),
        "control_infra_error": sum(t["control_infra_error"] for t in targets.values()),
        "control_precision": (
            sum(t["control_survived"] for t in targets.values())
            / sum(t["control_judged"] for t in targets.values())
            if sum(t["control_judged"] for t in targets.values())
            else None
        ),
        "all_shards_complete": all(t["shards_complete"] for t in targets.values()),
        "all_baselines_pass": all(t["baseline_ok"] for t in targets.values()),
        "meets_min_mutants": all(t["semantic_total"] >= 50 for t in targets.values()),
    }
    return {"schema": "mutation-ledger/1.0", "targets": targets, "overall": overall}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_enumerate(args: argparse.Namespace) -> int:
    target = next(t for t in load_targets(args.targets) if t.name == args.target)
    root = Path(args.root).resolve()
    module = root / target.repo_root / target.module
    source = module.read_text(encoding="utf-8")
    mutants = enumerate_mutants(target.name, source)
    digest = "sha256:" + hashlib.sha256(source.encode()).hexdigest()
    summary = {
        "target": target.name,
        "repo": target.repo,
        "module": target.module,
        "module_sha256": digest,
        "semantic": sum(1 for m in mutants if m.kind == "semantic"),
        "control": sum(1 for m in mutants if m.kind == "control"),
        "per_operator": {},
    }
    for m in mutants:
        summary["per_operator"][m.operator] = summary["per_operator"].get(m.operator, 0) + 1
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "schema": "mutant-catalogue/1.0",
                    "target": target.name,
                    "repo": target.repo,
                    "module": target.module,
                    "module_sha256": digest,
                    "mutants": [m.as_dict() for m in mutants],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    target = next(t for t in load_targets(args.targets) if t.name == args.target)
    ledger = run_shard(target, args.root, args.shard, args.n_shards, mutants_path=args.mutants)
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    print(json.dumps({k: ledger[k] for k in ("target", "shard", "n_mutants_selected", "baseline")}, indent=2))
    return 0 if ledger["baseline"]["status"] == "pass" else 1


def _cmd_aggregate(args: argparse.Namespace) -> int:
    ledgers = []
    for pattern in args.inputs:
        for path in sorted(Path().glob(pattern)) if any(ch in pattern for ch in "*?[") else [Path(pattern)]:
            ledgers.append(json.loads(path.read_text(encoding="utf-8")))
    ledger = aggregate(ledgers)
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    print(json.dumps(ledger["overall"], indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--targets", required=True, help="target document (JSON)")
    parser.add_argument("--root", default=".", help="repository root used to resolve repo_root entries")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enum = sub.add_parser("enumerate", help="print mutant counts per operator")
    p_enum.add_argument("--target", required=True)
    p_enum.add_argument("--json-out", default=None, help="write the mutant catalogue here")
    p_enum.set_defaults(func=_cmd_enumerate)

    p_run = sub.add_parser("run", help="run one shard")
    p_run.add_argument("--target", required=True)
    p_run.add_argument("--shard", type=int, required=True)
    p_run.add_argument("--n-shards", type=int, required=True)
    p_run.add_argument("--json-out", required=True)
    p_run.add_argument("--mutants", default=None, help="pre-enumerated mutant catalogue")
    p_run.set_defaults(func=_cmd_run)

    p_agg = sub.add_parser("aggregate", help="merge shard ledgers")
    p_agg.add_argument("--inputs", nargs="+", required=True)
    p_agg.add_argument("--json-out", required=True)
    p_agg.set_defaults(func=_cmd_aggregate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())