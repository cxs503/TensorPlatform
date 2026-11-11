"""合成沙箱仓库：给验收判据 A1–A6 造一个**可注入退化**的最小 git 仓。

**零领域语义**：这里的 ``synth`` 是一个 1D 阻尼 Jacobi 迭代核（合成数值实验），
参数名/指标名都是 ``RELAX_C00`` / ``energy_00`` 这类合成标识，不含任何船舶域词条。
每个能力的数字由一条参数行决定，因此 ``git blame`` 能精确定位"谁改坏了数字"。

沙箱布局::

    <repo>/
      synth/kernel.py      # 阻尼 Jacobi 求解 + 两个合成指标（energy/peak）
      synth/params.py      # RELAX_C00..RELAX_C09 十条参数行 ← 退化注入口
      verif/test_cap_XX.py # pytest 验证案例，经 plugin.record 上报数字
      capdrift/registry.json | gates.json | baseline.json | whitelist.json
      docs/NOTES.md        # 文档（A2：文档变更不该告警）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import model

GIT_ENV = {
    "GIT_AUTHOR_NAME": "capdrift-bot",
    "GIT_AUTHOR_EMAIL": "capdrift@example.invalid",
    "GIT_COMMITTER_NAME": "capdrift-bot",
    "GIT_COMMITTER_EMAIL": "capdrift@example.invalid",
}

KERNEL_SRC = '''"""synth kernel: 1D damped Jacobi iteration (synthetic numerical experiment).

Not a physics solver: it exists so the drift monitor has a numeric kernel whose
result depends on a single discretisation parameter, which makes degradation
injection and git-blame attribution testable.
"""
from __future__ import annotations


def solve(n: int, relax: float, iters: int = 60, source: float = 1.0):
    """Return (dirichlet_energy, peak_abs) after ``iters`` damped Jacobi sweeps."""
    h = 1.0 / (n + 1)
    u = [0.0] * (n + 2)
    for _ in range(iters):
        prev = u
        cur = prev[:]
        for i in range(1, n + 1):
            cur[i] = (1.0 - relax) * prev[i] + relax * 0.5 * (prev[i - 1] + prev[i + 1] + h * h * source)
        u = cur
    energy = 0.5 * h * sum(((u[i + 1] - u[i]) / h) ** 2 for i in range(n + 1))
    peak = max(abs(x) for x in u)
    return energy, peak
'''

PARAMS_HEADER = '''"""Synthetic solver parameters — one line per capability (degradation injection points).

Baseline value 0.60 is the reference relaxation factor; a change to 0.65+ moves the
energy metric by more than 5 %, which is exactly what the drift gate has to catch.
"""
'''

CASE_SRC = '''"""Verification case for capability {cap_id} (synthetic)."""
from synth import kernel, params
from tools.capability_drift.plugin import record


def test_{slug}():
    energy, peak = kernel.solve(n={n}, relax=params.RELAX_C{idx:02d}, iters={iters}, source={source})
    record("energy_{idx:02d}", energy)
    record("peak_{idx:02d}", peak)
    assert energy > 0.0
'''

README_SRC = """# synth sandbox

Synthetic repository used by `tools/capability_drift` acceptance tests.
Nothing here is a physics model; the kernel is a damped Jacobi iteration whose
numbers depend on one parameter line per capability.
"""

NOTES_SRC = """# Notes

Documentation-only file. Editing it must never raise a capability drift alarm.
"""

REL_TOL = 0.05
ABS_FLOOR = 1e-12


@dataclass
class SyntheticCapability:
    index: int
    id: str
    solver: str
    metrics: Sequence[str]
    param_name: str
    pattern: str
    case_node: str
    n: int
    iters: int
    source: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "id": self.id,
            "solver": self.solver,
            "metrics": list(self.metrics),
            "param_name": self.param_name,
            "pattern": self.pattern,
            "case_node": self.case_node,
            "n": self.n,
            "iters": self.iters,
            "source": self.source,
        }


@dataclass
class Sandbox:
    repo: Path
    registry_dir: Path
    capabilities: List[SyntheticCapability] = field(default_factory=list)

    @property
    def params_path(self) -> Path:
        return self.repo / "synth" / "params.py"

    def capability(self, index: int) -> SyntheticCapability:
        return self.capabilities[index]

    def baseline_path(self) -> Path:
        return self.registry_dir / "baseline.json"


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_ENV)
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def git_commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message, "--quiet")
    return _git(repo, "rev-parse", "HEAD")


def _capability_spec(index: int) -> SyntheticCapability:
    n = 24 + 4 * (index % 5)          # 24..40
    iters = 60
    source = 1.0 + 0.1 * (index % 3)  # 1.0 / 1.1 / 1.2
    metrics = [f"energy_{index:02d}"]
    if index == 0:
        metrics.append("peak_00")     # 首个能力带两个指标，演示多指标对比
    return SyntheticCapability(
        index=index,
        id=f"synth.solver.energy_{index:02d}",
        solver="synth",
        metrics=tuple(metrics),
        param_name=f"RELAX_C{index:02d}",
        pattern=rf"^RELAX_C{index:02d} = ",
        case_node=f"verif/test_cap_{index:02d}.py::test_cap_{index:02d}",
        n=n,
        iters=iters,
        source=source,
    )


def build_sandbox(dest: str | Path, *, n_capabilities: int = 10, base_relax: float = 0.60) -> Sandbox:
    """在 ``dest`` 新建 git 仓（HEAD=初始基线目录树），返回句柄。"""
    repo = Path(dest).resolve()
    if repo.exists():
        shutil.rmtree(repo)
    (repo / "synth").mkdir(parents=True)
    (repo / "verif").mkdir(parents=True)
    (repo / "capdrift").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "synth" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "synth" / "kernel.py").write_text(KERNEL_SRC, encoding="utf-8")
    params_lines = [PARAMS_HEADER]
    caps: List[SyntheticCapability] = []
    for i in range(n_capabilities):
        cap = _capability_spec(i)
        caps.append(cap)
        params_lines.append(f"{cap.param_name} = {base_relax:.2f}\n")
        (repo / "verif" / f"test_cap_{i:02d}.py").write_text(
            CASE_SRC.format(
                cap_id=cap.id,
                slug=f"cap_{i:02d}",
                idx=i,
                n=cap.n,
                iters=cap.iters,
                source=cap.source,
            ),
            encoding="utf-8",
        )
    (repo / "synth" / "params.py").write_text("".join(params_lines), encoding="utf-8")
    (repo / "README.md").write_text(README_SRC, encoding="utf-8")
    (repo / "docs" / "NOTES.md").write_text(NOTES_SRC, encoding="utf-8")

    registry = {
        "schema_version": 1,
        "repo_root": "..",
        "capabilities": [
            {
                "id": cap.id,
                "solver": cap.solver,
                "cases": [{"id": cap.case_node, "node": cap.case_node, "metrics": list(cap.metrics)}],
                "metrics": list(cap.metrics),
                "paths": ["synth/", f"verif/test_cap_{cap.index:02d}.py"],
                "blame": [{"path": "synth/params.py", "pattern": cap.pattern}],
            }
            for cap in caps
        ],
    }
    gates = {
        "schema_version": 1,
        "defaults": {"rel_tol": REL_TOL, "abs_floor": ABS_FLOOR},
        "capabilities": {cap.id: {m: {"rel_tol": REL_TOL} for m in cap.metrics} for cap in caps},
    }
    (repo / "capdrift" / "registry.json").write_text(
        json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (repo / "capdrift" / "gates.json").write_text(
        json.dumps(gates, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (repo / "capdrift" / "baseline.json").write_text(
        json.dumps({"schema_version": 1, "values": {}}, indent=2) + "\n", encoding="utf-8"
    )
    (repo / "capdrift" / "whitelist.json").write_text(
        json.dumps({"schema_version": 1, "waivers": []}, indent=2) + "\n", encoding="utf-8"
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", GIT_ENV["GIT_AUTHOR_NAME"])
    _git(repo, "config", "user.email", GIT_ENV["GIT_AUTHOR_EMAIL"])
    git_commit(repo, "baseline: synthetic solver + verification cases + registry")
    return Sandbox(repo=repo, registry_dir=repo / "capdrift", capabilities=caps)


def read_param(repo: Path, cap: SyntheticCapability) -> float:
    for line in (Path(repo) / "synth" / "params.py").read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^{cap.param_name} = ([0-9.]+)", line)
        if m:
            return float(m.group(1))
    raise KeyError(f"{cap.param_name} not found")


def write_param(repo: Path, cap: SyntheticCapability, value: float) -> float:
    """改参数行，返回旧值（不提交）。"""
    p = Path(repo) / "synth" / "params.py"
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(
        rf"^{cap.param_name} = [0-9.]+$", f"{cap.param_name} = {value:.3f}", text, flags=re.M
    )
    if n != 1:
        raise RuntimeError(f"param line {cap.param_name} not replaced")
    p.write_text(new, encoding="utf-8")
    return float(re.search(rf"^{cap.param_name} = ([0-9.]+)$", text, flags=re.M).group(1))


def inject_degradation(repo: Path, cap: SyntheticCapability, value: float, message: str = "") -> str:
    """改参数 + 提交 → 返回引入退化的 commit。"""
    write_param(repo, cap, value)
    return git_commit(repo, message or f"tune {cap.param_name} to {value:.3f}")


def benign_edit(repo: Path, kind: str, index: int) -> str:
    """无害变更（文档/注释/空白/死代码），返回被改文件相对路径。"""
    repo = Path(repo)
    if kind == "docs":
        rel = "docs/NOTES.md"
        p = repo / rel
        p.write_text(p.read_text(encoding="utf-8") + f"\n- doc line {index}\n", encoding="utf-8")
        return rel
    if kind == "readme":
        rel = "README.md"
        p = repo / rel
        p.write_text(p.read_text(encoding="utf-8") + f"\n<!-- note {index} -->\n", encoding="utf-8")
        return rel
    if kind == "case_comment":
        rel = f"verif/test_cap_{index % 10:02d}.py"
        p = repo / rel
        p.write_text(p.read_text(encoding="utf-8") + f"\n# harmless comment {index}\n", encoding="utf-8")
        return rel
    if kind == "solver_comment":
        rel = "synth/params.py"
        p = repo / rel
        p.write_text(p.read_text(encoding="utf-8") + f"# benign note {index}\n", encoding="utf-8")
        return rel
    if kind == "dead_code":
        rel = "synth/kernel.py"
        p = repo / rel
        p.write_text(
            p.read_text(encoding="utf-8") + f"\n\ndef _unused_helper_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
        return rel
    raise ValueError(f"unknown benign edit kind: {kind}")


def write_registry_bundle(
    sandbox: Sandbox,
    *,
    gates: Optional[Dict[str, object]] = None,
    waivers: Optional[List[Dict[str, object]]] = None,
) -> None:
    """覆写 gates.json / whitelist.json（用于阈值可调与白名单验收）。"""
    if gates is not None:
        (sandbox.registry_dir / "gates.json").write_text(
            json.dumps(gates, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if waivers is not None:
        (sandbox.registry_dir / "whitelist.json").write_text(
            json.dumps({"schema_version": 1, "waivers": waivers}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def default_gates(sandbox: Sandbox, rel_tol: float = REL_TOL) -> Dict[str, object]:
    return {
        "schema_version": 1,
        "defaults": {"rel_tol": rel_tol, "abs_floor": ABS_FLOOR},
        "capabilities": {
            cap.id: {m: {"rel_tol": rel_tol} for m in cap.metrics} for cap in sandbox.capabilities
        },
    }


def load_sandbox_bundle(sandbox: Sandbox):
    return model.load_bundle(sandbox.registry_dir)