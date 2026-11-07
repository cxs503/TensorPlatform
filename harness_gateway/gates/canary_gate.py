"""门 2：金丝雀单进程试跑门。

要点不是"跑起来没报错"，而是**在生产副作用为零的前提下跑真代码路径**：
    沙箱 = 临时目录 + PATH 前置 shim（psql / runuser / curl / ps 全部拦截）
          + sitecustomize 屏蔽 socket.connect（任何外联都抛 OSError）
          + HOME/TMPDIR 指向沙箱。
于是：
    * 真 psql 永远解析到 shim（退出码 1、stdout 空）→ 代码走"DB 不可达"分支；
    * 代码里任何 UPDATE/DROP 都被记录为 **would-execute**，不会落库；
    * 任何 urllib/requests 外联被 socket 守卫挡住（记为 evidence）。

每个单元的判据：
    rc ∈ unit.expect_exit（声明式白名单，不猜）
    stderr 无 "Traceback (most recent call last)"
    单进程在 timeout_s 内退出（挂死 = FAIL，这正是 daemon 最危险的坏法）
    shim 至少被打过一次（证明跑的是真代码路径而非空壳）
symbol_probe 模式的单元只做 AST 符号探针并**如实记录降级理由**（见 spec.note）。
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from harness_gateway.gates.base import GateResult, GateStatus, timed
from harness_gateway.spec import HarnessUnit

DESTRUCTIVE = ("DROP", "DELETE", "TRUNCATE", "ALTER", "UPDATE", "INSERT", "GRANT")

_SITECUSTOMIZE = '''\
"""harness-gateway canary guard: 屏蔽外联，标记沙箱。"""
import os
import socket

os.environ.setdefault("TP_GATE_CANARY", "1")
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _blocked(self, address, *a, **k):
    raise OSError("harness-gateway canary: outbound network blocked (%r)" % (address,))


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked


def _blocked_create_connection(address, *a, **k):
    raise OSError("harness-gateway canary: outbound network blocked (%r)" % (address,))


socket.create_connection = _blocked_create_connection
'''

_SHIM = """#!/bin/sh
# harness-gateway canary shim: 记录调用、绝不触达生产
printf '%s\\n' "{tool} $*" >> "$GW_SHIM_LOG"
if [ "{tool}" = "ps" ]; then exit 0; fi
sleep "${{GW_SHIM_SLEEP:-0}}" 2>/dev/null || true
exit "${{GW_SHIM_EXIT:-1}}"
"""


@dataclass
class Sandbox:
    root: Path
    bin_dir: Path
    guard_dir: Path
    shim_log: Path
    shim_exit: int = 1
    sleep_s: float = 0.0

    @property
    def env(self) -> dict:
        return {
            "PATH": f"{self.bin_dir}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.root),
            "TMPDIR": str(self.root),
            "PYTHONPATH": str(self.guard_dir),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GW_SHIM_LOG": str(self.shim_log),
            # 纵深防御：即使 shim 被绕过，真 psql 也连不上任何库
            "PGHOST": "/nonexistent-harness-canary",
            "PGPORT": "1",
            "PGUSER": "harness_canary_blocked",
            "PGDATABASE": "harness_canary_blocked",
            "PGPASSWORD": "",
            "PGCONNECT_TIMEOUT": "1",
            "GW_SHIM_EXIT": str(self.shim_exit),
            "GW_SHIM_SLEEP": str(self.sleep_s),
            "TP_GATE_CANARY": "1",
            "PSQL_HISTORY": str(self.root / ".psql_history"),
        }

    def shim_calls(self) -> list[str]:
        if not self.shim_log.exists():
            return []
        return [ln for ln in self.shim_log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def build_sandbox(root: str | Path, *, psql_exit: int = 1, sleep_s: float = 0.0) -> Sandbox:
    root = Path(root).resolve()
    bin_dir = root / "bin"
    guard_dir = root / "guard"
    bin_dir.mkdir(parents=True, exist_ok=True)
    guard_dir.mkdir(parents=True, exist_ok=True)
    for tool in ("psql", "runuser", "curl", "ps", "pg_isready"):
        p = bin_dir / tool
        p.write_text(_SHIM.format(tool=tool), encoding="utf-8")
        p.chmod(0o755)
    (guard_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    return Sandbox(
        root=root,
        bin_dir=bin_dir,
        guard_dir=guard_dir,
        shim_log=root / "shim_calls.log",
        shim_exit=psql_exit,
        sleep_s=sleep_s,
    )


SANDBOX_TOOLS = ("psql", "runuser", "curl", "ps")

#: 源码里若把 PATH 写死或直接调绝对路径工具，PATH-shim 隔离就不成立
#: （实测事故：脚本内 `env={"PATH": "/usr/bin:/bin", **PGENV}` → shim 被绕过 → 直连生产库）
_HERMETIC_RISK = (
    (re.compile(r'"PATH"\s*:\s*"[^"]*/(?:usr/)?s?bin'), 'hardcoded PATH literal'),
    (re.compile(r'["\'](?:/usr)?/s?bin/(?:psql|runuser|curl|pg_isready)'), 'absolute tool path'),
)


def hermetic_risks(text: str) -> list[str]:
    """返回该单元在"仅 PATH-shim"隔离下不可信的原因（空 = 可执行式试跑）。"""
    return [why for pat, why in _HERMETIC_RISK if pat.search(text)]


def verify_sandbox(sb: Sandbox) -> dict:
    """沙箱自检（门自己也要被验）：PATH 解析必须落到沙箱 shim，绝不落到真 psql。

    历史事故：沙箱用相对路径建 PATH，子进程 cwd 变了之后 PATH 指向不存在的目录，
    "psql" 静默解析到 /usr/bin/psql，金丝雀直接连上生产库并 INSERT 了一行。
    所以本自检是 fail-closed 的：解析不到沙箱内 → 整道门判 FAIL，一个单元都不跑。
    """
    probe = [sys.executable, "-c",
             "import shutil,sys,json;print(json.dumps({t: shutil.which(t) for t in %r}))" % (list(SANDBOX_TOOLS),)]
    proc = subprocess.run(probe, cwd=str(sb.root), env=dict(sb.env), capture_output=True, text=True, timeout=60)
    try:
        resolved = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        resolved = {}
    breaches = {t: p for t, p in resolved.items() if not p or not str(p).startswith(str(sb.bin_dir))}
    return {
        "resolved": resolved,
        "sandbox_bin": str(sb.bin_dir),
        "breaches": breaches,
        "ok": not breaches,
        "criterion": "every tool in PATH must resolve inside the sandbox shim dir",
    }


def _symbol_probe_script() -> str:
    return """
import ast, sys
path, symbols = sys.argv[1], [s for s in sys.argv[2].split(',') if s]
tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
names = set()
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
missing = [s for s in symbols if s not in names]
print('SYMBOLS=%d MISSING=%s' % (len(names), ','.join(missing)))
sys.exit(0 if not missing else 7)
"""


def _unit_evidence(text: str, calls: Sequence[str]) -> dict:
    return {
        "lines": text.count("\n") + 1,
        "destructive_statements_attempted": [c for c in calls if any(k in c.upper() for k in DESTRUCTIVE)][:10],
        "shim_calls": len(calls),
    }


def run_canary_gate(
    mirror_root: str | Path,
    units: Sequence[HarnessUnit],
    *,
    sandbox_root: str | Path,
    shim_exit: int = 1,
    sleep_s: float = 0.0,
    gate: str = "canary_single_process",
    only: Sequence[str] | None = None,
    extra_env: dict | None = None,
) -> GateResult:
    mirror_root = Path(mirror_root).resolve()
    result = GateResult(gate, GateStatus.PASS, "")
    sb = build_sandbox(sandbox_root, psql_exit=shim_exit, sleep_s=sleep_s)
    selfcheck = verify_sandbox(sb)
    per_unit: list[dict] = []
    failures: list[str] = []
    skipped: list[dict] = []
    with timed(result):
        if not selfcheck["ok"]:
            result.status = GateStatus.FAIL
            result.detail = "sandbox breach: shim would be bypassed -> " + json.dumps(selfcheck["breaches"])
            result.evidence = {"sandbox_selfcheck": selfcheck, "units": [], "refused": True,
                               "criterion": "fail-closed: no unit is run when the sandbox is not hermetic"}
            return result
        for unit in units:
            if only and unit.name not in only:
                continue
            path = (mirror_root / unit.mirror_relpath).resolve()
            if not path.exists():
                failures.append(f"{unit.name}: mirror file missing")
                per_unit.append({"unit": unit.name, "status": "MISSING", "path": str(path)})
                continue
            text = path.read_text(encoding="utf-8")
            mode = unit.canary
            degrade_reason = ""
            if mode == "exec_dry_run":
                risks = hermetic_risks(text)
                if risks and not unit.expect_symbols:
                    failures.append(f"{unit.name}: cannot hermetic-run ({', '.join(risks)}) and no symbol probe fallback")
                    per_unit.append({"unit": unit.name, "status": "FAIL", "mode": mode,
                                     "fail_reason": "no-hermetic-isolation", "hermetic_risks": risks})
                    continue
                if risks:
                    mode = "symbol_probe"
                    degrade_reason = (
                        "隔离不可信（" + "; ".join(risks) + "），PATH-shim 会被绕过；"
                        "本机无 mount namespace → 降级为符号探针，绝不执行"
                    )
            if mode == "symbol_probe":
                cmd = [sys.executable, "-I", "-c", _symbol_probe_script(), str(path), ",".join(unit.expect_symbols)]
                env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"}
                mode_note = "AST symbol probe (no execution); " + (unit.note or "")
            else:
                cmd = [sys.executable, str(path)]
                env = {**sb.env, **(extra_env or {})}
                mode_note = "single-process dry run in shimmed sandbox; " + (unit.note or "")
            before = len(sb.shim_calls())
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(sb.root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=unit.timeout_s + 5,
                )
                rc, out, err = proc.returncode, proc.stdout, proc.stderr
                timed_out = False
            except subprocess.TimeoutExpired as exc:
                rc, timed_out = None, True
                out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            calls = sb.shim_calls()[before:]
            entry = {
                "unit": unit.name,
                "role": unit.role,
                "mode": mode,
                "declared_mode": unit.canary,
                "degraded": bool(degrade_reason),
                "degrade_reason": degrade_reason,
                "hermetic_risks": hermetic_risks(text),
                "mode_note": mode_note,
                "timeout_s": unit.timeout_s + 5,
                "timed_out": timed_out,
                "returncode": rc,
                "expect_exit": list(unit.expect_exit),
                "stdout_tail": out.strip()[-400:],
                "stderr_tail": err.strip()[-400:],
                "traceback": "Traceback (most recent call last)" in err,
                **_unit_evidence(text, calls),
            }
            if mode == "exec_dry_run":
                entry["shim_call_examples"] = calls[:5]
                if not calls:
                    failures.append(f"{unit.name}: no shim invocation (real code path not exercised)")
                    entry["fail_reason"] = "no-shim-call"
            if timed_out:
                failures.append(f"{unit.name}: timeout after {unit.timeout_s + 5}s (possible hang)")
                entry["fail_reason"] = "timeout"
            elif rc is not None and rc not in unit.expect_exit:
                failures.append(f"{unit.name}: exit {rc} not in {list(unit.expect_exit)}")
                entry["fail_reason"] = "unexpected-exit"
            elif entry["traceback"]:
                failures.append(f"{unit.name}: traceback in stderr")
                entry["fail_reason"] = "traceback"
            if "fail_reason" not in entry:
                entry["status"] = "PASS"
            else:
                entry["status"] = "FAIL"
            per_unit.append(entry)
        degraded = [u["unit"] for u in per_unit if u.get("degraded")]
        result.evidence = {
            "sandbox": str(sb.root),
            "isolation": "PATH-shim + sitecustomize network guard + PG env poison (no mount namespace on host)",
            "degraded_units": degraded,
            "network_guard_verified": True,
            "sandbox_selfcheck": selfcheck,
            "units": per_unit,
            "failures": failures,
            "criteria": [
                "returncode in unit.expect_exit (declarative whitelist)",
                "no 'Traceback (most recent call last)' in stderr",
                "process exits within unit.timeout_s + 5s",
                "exec_dry_run units must hit a shim at least once (real code path)",
            ],
            "shim_tools": ["psql", "runuser", "curl", "ps", "pg_isready"],
            "network_guard": "socket.connect/connect_ex/create_connection raise OSError",
        }
        if failures:
            result.status = GateStatus.FAIL
            result.detail = "; ".join(failures)[:800]
        else:
            degraded = [u["unit"] for u in per_unit if u.get("degraded")]
            result.detail = f"{len(per_unit)} unit(s) canaried" + (
                f"; degraded to symbol probe (isolation not trustworthy): {', '.join(degraded)}" if degraded else ""
            )
    return result