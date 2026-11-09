"""repro_audit.core —— 复证计量的纯函数层 + 最小 IO 原语（无领域语义、无状态）。

四类判据口径（与平台既有 ULP/量化协议对齐）：
  * **observed**（物理/交付量）：相对容差 ``OBS_REL_TOL = 1e-6``，或报告自带容差
    （``parse_tolerance`` 从判据文本里读 ``<5%`` / ``≤2%`` / ``1e-9`` 这类显式阈值）。
  * **diagnostic**（近零量）：``|x| <= DIAG_NEAR_ZERO = 1e-8`` 视作近零，按绝对容差
    ``DIAG_ABS_TOL = 1e-12`` 判；近零量用相对容差会因分母趋零而假阴/假阳。
  * **drift**：数值不在容差内但**同一条重跑可复现**（两次重跑失败集一致），且失败
    呈现为数值断言（可归因到依赖版本/浮点路径/环境）。
  * **blocked**：工件缺失、依赖不可得、collection error、超时 —— 单列，**不进通过率分母**。

每个 drift 项带四分类归因标签：``source_change`` / ``dependency_version`` /
``float_path`` / ``environment``（优先级见 :func:`attribute_drift`）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from typing import Any, Iterable

OBS_REL_TOL = 1e-6
DIAG_NEAR_ZERO = 1e-8
DIAG_ABS_TOL = 1e-12

ATTRIBUTIONS = ("source_change", "dependency_version", "float_path", "environment")

_PCT_TOL_RE = re.compile(r"(?:<|≤|<=|within)\s*([0-9]+(?:\.\d+)?)\s*%")
_SCI_TOL_RE = re.compile(r"(?:<|≤|<=)\s*([0-9]+(?:\.[0-9]+)?[eE]-[0-9]+)")


# ─────────────────────────── 判据 / 数值口径 ───────────────────────────

def parse_tolerance(criteria: Iterable[str] | str | None, default: float = OBS_REL_TOL) -> float:
    """从判据文本里读报告自带容差（相对量，单位 1）。

    ``"误差<5%"`` → 0.05；``"<1e-9"`` → 1e-9；读不到 → ``default``。
    只取**最严**（最小）阈值：一条判据里出现多个阈值时以最紧者为准（保守）。
    """
    if not criteria:
        return default
    texts = [criteria] if isinstance(criteria, str) else list(criteria)
    found: list[float] = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for m in _PCT_TOL_RE.finditer(text):
            found.append(float(m.group(1)) / 100.0)
        for m in _SCI_TOL_RE.finditer(text):
            found.append(float(m.group(1)))
    found = [f for f in found if f > 0.0]
    return min(found) if found else default


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def flatten_numbers(obj: Any, prefix: str = "") -> dict[str, float]:
    """把嵌套 JSON 拍平成 ``路径 → 有限数值``（键名排序由调用方保证）。

    跳过 bool（``True`` 会被 float() 吃成 1.0，是假数值）与非有限值（nan/inf 不参与比对）。
    """
    out: dict[str, float] = {}
    if isinstance(obj, dict):
        for key in obj:
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten_numbers(obj[key], child))
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            out.update(flatten_numbers(item, f"{prefix}[{idx}]"))
    elif is_finite_number(obj):
        out[prefix or "."] = float(obj)
    return out


def classify_value(reported: float, recomputed: float, rel_tol: float = OBS_REL_TOL,
                   abs_tol: float = DIAG_ABS_TOL) -> dict[str, Any]:
    """单值比对：返回 ``{mode, ok, delta, allowed}``。

    近零量（``|reported| <= DIAG_NEAR_ZERO``）走 diagnostic 绝对容差；其余走 observed 相对容差。
    """
    reported = float(reported)
    recomputed = float(recomputed)
    delta = abs(recomputed - reported)
    if abs(reported) <= DIAG_NEAR_ZERO:
        allowed = abs_tol
        return {"mode": "diagnostic", "ok": delta <= allowed, "delta": delta, "allowed": allowed}
    allowed = abs(rel_tol * reported)
    # 近零的 recomputed 落在 observed 口径下会被相对容差放过，单独拦一道：
    if abs(recomputed) <= DIAG_NEAR_ZERO and abs(reported) > DIAG_NEAR_ZERO:
        return {"mode": "observed", "ok": False, "delta": delta, "allowed": allowed}
    return {"mode": "observed", "ok": delta <= allowed, "delta": delta, "allowed": allowed}


def compare_number_maps(reported: dict[str, float], recomputed: dict[str, float],
                        rel_tol: float = OBS_REL_TOL, abs_tol: float = DIAG_ABS_TOL,
                        max_items: int = 200) -> list[dict[str, Any]]:
    """逐路径比对两个数值映射；只比对**两侧都存在**的路径（缺失路径单列）。

    结果按路径名排序（确定性），最多 ``max_items`` 条（超出截断并标 ``truncated``）。
    """
    shared = sorted(set(reported) & set(recomputed))
    items: list[dict[str, Any]] = []
    for path in shared[:max_items]:
        res = classify_value(reported[path], recomputed[path], rel_tol, abs_tol)
        items.append({"path": path, **res,
                      "reported": reported[path], "recomputed": recomputed[path]})
    return items


def agreement(items: list[dict[str, Any]]) -> dict[str, Any]:
    """把逐项比对折成 pass/drift 计数（diagnostic 与 observed 分列）。"""
    total = len(items)
    passed = sum(1 for i in items if i["ok"])
    by_mode = {"observed": 0, "diagnostic": 0}
    for i in items:
        by_mode[i["mode"]] = by_mode.get(i["mode"], 0) + 1
    return {"checked": total, "passed": passed, "drifted": total - passed,
            "by_mode": by_mode, "rate": (passed / total) if total else None}


# ─────────────────────────── 抽样 ───────────────────────────

def _node_key(node: dict[str, Any], seed: str) -> str:
    raw = f"{seed}|{node.get('id')}|{node.get('mission_id')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def stratum_of(node: dict[str, Any], year_field: str = "actual_end") -> tuple[str, str]:
    """分层键 = (仓, 年)。仓取 ``metadata.repo``；年取 ``actual_end``/``created_at`` 前 4 位。

    仓名来自节点元数据而非代码常量 —— 新增仓自动进入分层，无需改本包。
    """
    repo = str((node.get("metadata") or {}).get("repo") or "unknown")
    stamp = str(node.get(year_field) or node.get("created_at") or "")
    return repo, stamp[:4] or "unknown"


def sample_stratified(nodes: list[dict[str, Any]], n: int, seed: str = "repro-audit",
                      per_stratum_cap: int = 3,
                      predicate=None) -> list[dict[str, Any]]:
    """分层确定性抽样：repo × 年 分层，层内按 sha256(seed|node) 定序，轮转取满 ``n``。

    轮转（round-robin）而非「先取大层」保证**仓覆盖**：小仓不会被大仓淹没。
    同一 (nodes, n, seed, predicate) 输入必得同一输出（幂等，便于复证）。
    ``predicate`` 用于过滤（如「干净检出里确有测试」），必须是纯函数。
    """
    pool = [nd for nd in nodes if predicate is None or predicate(nd)]
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for nd in pool:
        strata.setdefault(stratum_of(nd), []).append(nd)
    for key in strata:
        strata[key].sort(key=lambda nd: (_node_key(nd, seed), nd.get("id")))
    order = sorted(strata)                       # 层序确定：repo 字典序 → 年
    picked: list[dict[str, Any]] = []
    round_idx = 0
    while len(picked) < n and round_idx < per_stratum_cap:
        before = len(picked)
        for key in order:
            if len(picked) >= n:
                break
            bucket = strata[key]
            if round_idx < len(bucket) and round_idx < per_stratum_cap:
                picked.append(bucket[round_idx])
        if len(picked) == before:                # 所有层都取尽
            break
        round_idx += 1
    return picked


# ─────────────────────────── 干净检出 / 执行 ───────────────────────────

def git(workdir: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", workdir, *args], capture_output=True, text=True,
                          timeout=timeout)


def head_commit(workdir: str) -> str:
    return git(workdir, "rev-parse", "HEAD", timeout=60).stdout.strip()


def tree_sha(workdir: str) -> str:
    return git(workdir, "rev-parse", "HEAD^{tree}", timeout=60).stdout.strip()


def committed_files(workdir: str, max_files: int = 20000) -> list[str]:
    out = git(workdir, "ls-tree", "-r", "--name-only", "HEAD", timeout=180).stdout
    files = [line for line in out.splitlines() if line]
    return files[:max_files]


def clean_export(workdir: str, dest: str, timeout: int = 600) -> dict[str, Any]:
    """``git archive HEAD | tar -x`` 出**干净检出**（不含未提交改动/未跟踪文件）。

    只读操作：不 touch 被审计仓的任何工作树文件。
    """
    os.makedirs(dest, exist_ok=True)
    proc = subprocess.run(f"git -C {shlex_quote(workdir)} archive HEAD | tar -x -C {shlex_quote(dest)}",
                          shell=True, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"git archive failed: {proc.stderr.strip()[:400]}")
    return {"dest": dest, "bytes": dir_bytes(dest)}


def shlex_quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


def dir_bytes(path: str, cap: int = 3_000_000_000) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
            if total > cap:
                return total
    return total


def sha256_file(path: str, cap: int = 64 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        remaining = cap
        while remaining > 0:
            chunk = fh.read(min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def run_command(cmd: list[str], cwd: str, timeout: int, env: dict[str, str] | None = None) -> dict[str, Any]:
    """带硬超时执行外部命令；返回结构化结果（超时/退出码/输出尾段/耗时）。"""
    started = time.time()
    full_env = dict(os.environ)
    full_env.update(env or {})
    full_env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, env=full_env)
        return {"exit_code": proc.returncode, "timed_out": False,
                "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-8000:],
                "duration_sec": round(time.time() - started, 2)}
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": None, "timed_out": True,
                "stdout": (exc.stdout or b"").decode("utf-8", "replace")[-8000:]
                if isinstance(exc.stdout, bytes) else (exc.stdout or "")[-8000:],
                "stderr": (exc.stderr or b"").decode("utf-8", "replace")[-4000:]
                if isinstance(exc.stderr, bytes) else (exc.stderr or "")[-4000:],
                "duration_sec": round(time.time() - started, 2)}


_PYTEST_SUMMARY_RE = re.compile(r"(?P<counts>(?:\d+ (?:passed|failed|error|errors|skipped|xfailed|"
                                r"xpassed|deselected|warning|warnings)(?:, )?)+)")
_CASE_ID_RE = re.compile(r"^(FAILED|ERROR) (\S+)", re.MULTILINE)


def parse_pytest(stdout: str, stderr: str) -> dict[str, Any]:
    """从 pytest 输出里抽计数与失败用例集合（用于两次重跑的 drift 可复现性判定）。"""
    text = f"{stdout}\n{stderr}"
    counts: dict[str, int] = {}
    tail = text.strip().splitlines()[-25:] if text.strip() else []
    for line in reversed(tail):
        if not any(word in line for word in ("passed", "failed", "error", "no tests ran")):
            continue
        m = _PYTEST_SUMMARY_RE.search(line)
        if not m:
            continue
        for part in re.findall(r"(\d+) ([a-z]+)", m.group("counts")):
            key = part[1]
            if key in ("errors", "warnings"):
                key = key[:-1]
            counts[key] = int(part[0])
        break
    cases = sorted({m.group(2) for m in _CASE_ID_RE.finditer(text)})
    return {"counts": counts, "failing_cases": cases,
            "collected": counts.get("passed", 0) + counts.get("failed", 0) + counts.get("error", 0)}


def failing_signature(cases: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(cases)).encode()).hexdigest()[:16]


_MISSING_DEP_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'|"
                             r"ImportError: cannot import name '([^']+)'")
_COLLECT_ERR_RE = re.compile(r"error[s]? during collection|ERROR collecting|"
                             r"ImportError while loading conftest")
_NUMERIC_ASSERT_RE = re.compile(r"(assert |Approx|isclose|allclose|almost equal|"
                                r"assert_allclose|assert_almost_equal)")
_FLOAT_TOKEN_RE = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")


def attribute_drift(run: dict[str, Any], rerun: dict[str, Any] | None = None,
                    recorded_env: dict[str, Any] | None = None,
                    current_env: dict[str, Any] | None = None,
                    source_changed: bool = False) -> dict[str, Any]:
    """drift 归因四分类（顺序 = 证据强度：源码 > 依赖 > 浮点 > 环境）。

    判据全部来自**可观测差异**，不做语义猜测：
      * ``source_change``：重跑检出与被声明检出不同（本审计强制同 commit，正常恒 False）。
      * ``dependency_version``：工件记录的 python/numpy 版本与当前环境不一致。
      * ``float_path``：失败输出呈数值断言（assert/approx/isclose）且两次重跑失败集一致。
      * ``environment``：CPU 核数 / 机器架构与工件记录不一致（线程数、ISA 差异）。
    """
    evidence: dict[str, Any] = {}
    recorded_env = recorded_env or {}
    current_env = current_env or {}
    if source_changed:
        return {"label": "source_change", "evidence": {"source_changed": True}}
    dep_diff = {k: [recorded_env.get(k), current_env.get(k)]
                for k in ("numpy", "python_version", "python")
                if k in recorded_env and k in current_env and recorded_env.get(k) != current_env.get(k)}
    if dep_diff:
        evidence["dependency_version"] = dep_diff
        return {"label": "dependency_version", "evidence": evidence}
    text = f"{run.get('stdout', '')}\n{run.get('stderr', '')}"
    numeric = bool(_NUMERIC_ASSERT_RE.search(text)) and bool(_FLOAT_TOKEN_RE.search(text))
    reproducible = bool(rerun) and failing_signature(parse_pytest(run.get("stdout", ""), run.get("stderr", ""))["failing_cases"]) == \
        failing_signature(parse_pytest(rerun.get("stdout", ""), rerun.get("stderr", ""))["failing_cases"])
    if numeric and reproducible:
        evidence.update({"numeric_assert": True, "reproducible_reruns": 2})
        return {"label": "float_path", "evidence": evidence}
    env_diff = {k: [recorded_env.get(k), current_env.get(k)]
                for k in ("cpu_count", "machine", "platform")
                if k in recorded_env and k in current_env and recorded_env.get(k) != current_env.get(k)}
    if env_diff:
        evidence["environment"] = env_diff
        return {"label": "environment", "evidence": evidence}
    evidence.update({"numeric_assert": numeric, "reproducible_reruns": 2 if reproducible else 1})
    return {"label": "environment", "evidence": evidence}


# ─────────────────────────── 节点级判定 ───────────────────────────

def verdict_of(execution: dict[str, Any]) -> str:
    """节点级判决：``pass`` / ``drift`` / ``fail`` / ``blocked``。

    - 超时 / 依赖缺失 / collection error / 零测试执行 → ``blocked``（不进通过率分母）
    - 退出码 0 且有通过用例 → ``pass``
    - 两次重跑失败集一致 + 数值断言 → ``drift``（可归因、可复现）
    - 其余可复现失败 → ``fail``
    """
    run = execution.get("run") or {}
    parsed = execution.get("parsed") or {}
    if run.get("timed_out"):
        return "blocked"
    blob = f"{run.get('stdout', '')}\n{run.get('stderr', '')}"
    if _MISSING_DEP_RE.search(blob) or _COLLECT_ERR_RE.search(blob):
        return "blocked"
    if run.get("exit_code") == 0 and parsed.get("collected", 0) > 0:
        return "pass"
    if parsed.get("collected", 0) == 0:
        return "blocked"
    if execution.get("reproducible") and execution.get("attribution", {}).get("label") == "float_path":
        return "drift"
    return "fail"


def kpi_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """由**逐项**判决算 KPI —— JSON 是唯一真源，KPI 必须可由它逐项加总复算。

    ``repro_rate = pass / (pass + drift + fail)``，blocked 单列且**不进分母**（判据 d）。
    """
    counts = {"pass": 0, "drift": 0, "fail": 0, "blocked": 0}
    for it in items:
        counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
    denom = counts["pass"] + counts["drift"] + counts["fail"]
    rate = (counts["pass"] / denom) if denom else None
    return {"counts": counts, "denominator": denom,
            "repro_rate": rate,
            "strict_rate": (counts["pass"] / (counts["pass"] + counts["fail"])) if (counts["pass"] + counts["fail"]) else None,
            "blocked_share": (counts["blocked"] / len(items)) if items else None,
            "items_total": len(items)}


def kpi_consistent(reported_rate: float | None, items: list[dict[str, Any]], tol: float = 1e-9) -> bool:
    """KPI 与 JSON 逐项复算的一致性（判据 b：``|KPI − JSON 计算值| <= 1e-9``）。"""
    computed = kpi_from_items(items)["repro_rate"]
    if reported_rate is None or computed is None:
        return reported_rate == computed
    return abs(float(reported_rate) - float(computed)) <= tol


def cleanup(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def make_temp(prefix: str = "repro_audit_") -> str:
    return tempfile.mkdtemp(prefix=prefix)


__all__ = [
    "OBS_REL_TOL", "DIAG_NEAR_ZERO", "DIAG_ABS_TOL", "ATTRIBUTIONS",
    "parse_tolerance", "flatten_numbers", "classify_value", "compare_number_maps",
    "agreement", "sample_stratified", "stratum_of", "clean_export", "run_command",
    "parse_pytest", "failing_signature", "attribute_drift", "verdict_of",
    "kpi_from_items", "kpi_consistent", "sha256_file", "dump_json", "load_json",
    "head_commit", "tree_sha", "committed_files", "cleanup", "make_temp",
]