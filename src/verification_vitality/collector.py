"""``pytest --collect-only`` 的真实收集结果解析。

活性巡检必须区分两件事：

* **静态可解析**（AST 层导入能对上）——证明符号没搬走；
* **真的被收集**（pytest 层能建 test item）——证明 conftest / 夹具 / 数据文件 /
  插件标记这些运行时前置条件没坏。

只用静态检查会漏掉 conftest 缺失、数据文件缺失、插件未注册；只用收集结果会在
pytest 根本没被调用（或 ``--continue-on-collection-errors`` 把错误吃掉）时漏判。
两者都要。

关键点：``--continue-on-collection-errors`` + 解析 ``ERROR <file> - <reason>`` 行，
这样一次进程就能拿到全幅误差，而不是被 pytest 首错中断（CI 里正是"中断=绿"的温床）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ERROR_RE = re.compile(r"^ERROR\s+(?P<path>\S+)(?:\s+-\s+(?P<msg>.*))?$")
SECTION_ERROR_RE = re.compile(r"^_+\s*ERROR (?:collecting|importing test module)?\s*(?P<path>[^_\s].*?)\s*_+$")
EXC_RE = re.compile(r"^(?:E\s+)?(?P<exc>ModuleNotFoundError|ImportError|FileNotFoundError|"
                    r"SyntaxError|AttributeError|TypeError|ValueError)\b[:,]?\s*(?P<msg>.*)$")
NODE_RE = re.compile(r"^(?P<path>[^:\n]+\.py)::")
SUMMARY_RE = re.compile(r"(?P<n>\d+)\s+tests?\s+collected")


@dataclass
class CollectError:
    file: str
    error_kind: str
    message: str = ""


@dataclass
class CollectResult:
    exit_code: int
    errors: List[CollectError] = field(default_factory=list)
    collected: Dict[str, int] = field(default_factory=dict)
    collected_total: int = 0
    duration_s: float = 0.0
    command: List[str] = field(default_factory=list)
    stderr_tail: str = ""
    timed_out: bool = False
    mode: str = "single"
    chunk_aborts: List[Dict[str, object]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.errors and not self.timed_out

    def as_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["ok"] = self.ok
        return d

    def error_for(self, rel_path: str) -> Optional[CollectError]:
        target = rel_path.replace("\\", "/")
        for err in self.errors:
            if err.file.replace("\\", "/").endswith(target) or target.endswith(err.file.replace("\\", "/")):
                return err
        return None


def classify_error(message: str) -> str:
    """把 pytest 的收集期报错归类（用于把"符号迁移"与"数据缺失"分开）。"""
    m = message or ""
    if "ModuleNotFoundError" in m or "ImportError" in m:
        return "import_error"
    if "FileNotFoundError" in m or "No such file" in m:
        return "data_file_missing"
    if "fixture" in m.lower():
        return "fixture_error"
    if "SyntaxError" in m:
        return "syntax_error"
    if "cannot import name" in m:
        return "import_error"
    return "collection_error"


def _pythonpath_env(repo_root: Path, extra: Sequence[Path]) -> Dict[str, str]:
    """复刻 CI 的导入环境：``PYTHONPATH`` 只有仓库根 + 调用方显式给的额外根。

    注意**不要**偷偷把 ``src/`` 塞进去：仓内测试自己负责让 src-layout 可导入
    （conftest 插路径）。巡检若替它们兜底，就会把"这个包里其实导不进来"这个缺陷
    治好，事故重放的正反例就失效了——这是本模块最初踩过的坑。
    """
    env = dict(os.environ)
    parts = [str(repo_root.resolve())] + [str(Path(p).resolve()) for p in extra]
    # 父进程的 PYTHONPATH 一律丢弃：它可能是相对路径，会相对**被试仓库**解析，
    # 从而把被试仓库的 src/ 暗中塞进导入路径、替它挡掉 import 错误（事故重放会失效）。
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def run_pytest_collect(repo_root: Path, targets: Sequence[str | Path], *,
                       timeout: float = 90.0,
                       extra_pythonpath: Sequence[Path] = (),
                       python: str = sys.executable) -> CollectResult:
    """在 ``repo_root`` 里跑一次 ``pytest --collect-only`` 并解析结果。

    ``targets`` 可以是目录或文件，相对于 ``repo_root``。
    """
    repo_root = Path(repo_root)
    rel_targets = [str(Path(t)) for t in targets]
    cmd = [python, "-m", "pytest", "--collect-only", "-q",
           "--continue-on-collection-errors", "-p", "no:cacheprovider", *rel_targets]
    extra = [Path(p) for p in extra_pythonpath]
    t0 = time.time()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True,
                              timeout=timeout, env=_pythonpath_env(repo_root, extra))
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - 由调用方断言
        timed_out = True
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = 124
    duration = time.time() - t0

    errors: List[CollectError] = []
    by_file: Dict[str, CollectError] = {}
    collected: Dict[str, int] = {}
    current: Optional[CollectError] = None  # 正在解析的 ERRORS 小节
    for raw_line in out.splitlines():
        line = raw_line.strip()
        sm = SECTION_ERROR_RE.match(line)
        if sm:
            path = sm.group("path").strip()
            current = by_file.get(path)
            if current is None:
                current = CollectError(file=path, error_kind="collection_error")
                by_file[path] = current
                errors.append(current)
            continue
        if current is not None:
            exc = EXC_RE.match(line)
            if exc:
                # 异常行是根因，优先级高于 traceback 首行（后者只是"哪个文件哪一行"）
                current.error_kind = classify_error(exc.group("exc") + ": " + exc.group("msg"))
                current.message = f"{exc.group('exc')}: {exc.group('msg')}".strip()
                continue
            if (not current.message or current.error_kind == "collection_error") and line \
                    and not line.startswith(("^", "Hint:", "Traceback", "___")):
                current.message = line
                current.error_kind = classify_error(line)
        m = ERROR_RE.match(line)
        if m:
            path = m.group("path")
            msg = (m.group("msg") or "").strip()
            existing = by_file.get(path)
            if existing is None:
                err = CollectError(file=path, error_kind=classify_error(msg), message=msg)
                by_file[path] = err
                errors.append(err)
            elif msg and not existing.message:
                existing.message = msg
                existing.error_kind = classify_error(msg)
            current = None
            continue
        nm = NODE_RE.match(line)
        if nm:
            collected[nm.group("path")] = collected.get(nm.group("path"), 0) + 1
    total = sum(collected.values())

    return CollectResult(exit_code=code, errors=errors, collected=collected,
                         collected_total=total, duration_s=round(duration, 3),
                         command=cmd, stderr_tail="\n".join(err.strip().splitlines()[-25:]),
                         timed_out=timed_out)

_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
_ERRORS_RE = re.compile(r"(\d+)\s+errors?")


@dataclass
class PassRateResult:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    exit_code: int = 0
    duration_s: float = 0.0
    timed_out: bool = False
    output_tail: str = ""

    @property
    def executed(self) -> int:
        """真正跑起来的用例数（收集期错误不是用例，不计入分母）。"""
        return self.passed + self.failed

    @property
    def pass_rate(self) -> Optional[float]:
        if self.executed == 0:
            return None
        return self.passed / self.executed

    def as_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["executed"] = self.executed
        d["pass_rate"] = None if self.pass_rate is None else round(self.pass_rate, 4)
        return d


def run_pytest_pass_rate(repo_root: Path, targets: Sequence[str | Path], *,
                         timeout: float = 120.0,
                         extra_pythonpath: Sequence[Path] = (),
                         python: str = sys.executable) -> PassRateResult:
    """真跑一遍给定测试模块，取通过率（升账引擎的"执行证据"来源）。

    通过率只从 pytest 自己的汇总行读，不做任何估算——静态推断的通过率不得冒充执行证据。
    """
    repo_root = Path(repo_root)
    rel_targets = [str(Path(t)) for t in targets]
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "--continue-on-collection-errors", *rel_targets]
    extra = [Path(p) for p in extra_pythonpath]
    t0 = time.time()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True,
                              timeout=timeout, env=_pythonpath_env(repo_root, extra))
        out, code = proc.stdout + "\n" + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        timed_out = True
        raw = exc.stdout or ""
        out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        code = 124
    res = PassRateResult(exit_code=code, duration_s=round(time.time() - t0, 3), timed_out=timed_out)
    m = _PASSED_RE.search(out)
    if m:
        res.passed = int(m.group(1))
    m = _FAILED_RE.search(out)
    if m:
        res.failed = int(m.group(1))
    m = _ERRORS_RE.search(out)
    if m:
        res.errors = int(m.group(1))
    res.output_tail = "\n".join([ln for ln in out.strip().splitlines() if ln.strip()][-8:])
    return res


def is_degenerate(result: CollectResult) -> bool:
    """一次收集是否"看着失败却什么都没说"——典型是 conftest 里的 ``pytest.skip``
    在收集期把整个 session 掐掉（exit≠0、零错误、零用例）。这种退化必须识别出来，
    否则整仓巡检会因为一个目录的 conftest 而变成"全绿的空转"。"""
    return (result.collected_total == 0 and not result.errors
            and result.exit_code != 0 and not result.timed_out)


def _chunks(items: Sequence[str], size: int) -> List[List[str]]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def run_pytest_collect_chunked(repo_root: Path, targets: Sequence[str | Path], *,
                               chunk_size: int = 8, timeout: float = 180.0,
                               extra_pythonpath: Sequence[Path] = (),
                               python: str = sys.executable) -> CollectResult:
    """先整批跑；若整批退化成空转，则按目录分块重跑并合并（故障隔离，零领域语义）。"""
    targets = [str(t) for t in targets]
    first = run_pytest_collect(repo_root, targets, timeout=timeout,
                               extra_pythonpath=extra_pythonpath, python=python)
    if not is_degenerate(first) or len(targets) <= 1:
        return first
    merged = CollectResult(exit_code=first.exit_code, command=first.command,
                           mode="chunked", stderr_tail=first.stderr_tail,
                           duration_s=first.duration_s)
    merged.chunk_aborts.append({"targets": targets, "stderr_tail": first.stderr_tail[-400:],
                                "error_kind": "session_aborted"})
    for chunk in _chunks(targets, chunk_size):
        res = run_pytest_collect(repo_root, chunk, timeout=timeout,
                                 extra_pythonpath=extra_pythonpath, python=python)
        merged.duration_s = round(merged.duration_s + res.duration_s, 3)
        for err in res.errors:
            merged.errors.append(err)
        for path, n in res.collected.items():
            merged.collected[path] = merged.collected.get(path, 0) + n
        if is_degenerate(res):
            merged.chunk_aborts.append({"targets": chunk,
                                        "stderr_tail": res.stderr_tail[-400:],
                                        "error_kind": "session_aborted"})
        if merged.exit_code == 0:
            merged.exit_code = res.exit_code
    merged.collected_total = sum(merged.collected.values())
    return merged
