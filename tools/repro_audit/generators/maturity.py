"""repro_audit.generators.maturity —— 数值通道成熟度审计 + 入库门（fail-closed）。

对 ``channel_specs.CHANNELS`` 里的每条通道评级：

===========  ==========================================================
等级          含义
===========  ==========================================================
``L0``       通道无法解析（工件缺失 / 派生式报错）
``L1``       重放一致：派生值 == 工件记录值（通道能算出来）
``L2``       + 幂等：同一输入两次派生逐字节相等，且通道身份四处齐全
             （engine / spec / source 三个 sha256 + 表达式原文）
``L3``       + 派生**有据**：派生式里没有等于被断言值的数值常量（无回抄），
             且对输入做结构性/数值性扰动后输出会变（输出真的依赖输入，
             不是常函数 —— 这是 MOCK 生成器的特征）
===========  ==========================================================

「覆盖率」= L3 通道数 / 通道样本数（默认 36）。入库门（``--gate``）是
fail-closed 的：任一通道等级低于门槛、覆盖率不足、或**台账陈旧**
（ledger 里记录的 engine/manifest/source 指纹与当前树不符 —— 说明工件不是
当前生成器产出的），门一律不通过并返回非零退出码。

用法::

    python3 -m tools.repro_audit.generators.maturity --audit \\
        --out artifacts/repro_audit/channel_maturity.json
    python3 -m tools.repro_audit.generators.maturity --gate --min-coverage 24
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from typing import Any

try:  # 包内导入 / 脚本直跑两用
    from . import channel_specs, derive
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))))
    from tools.repro_audit.generators import channel_specs, derive  # type: ignore

GRADE_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}

DEFAULT_LEDGER = "artifacts/repro_audit/channel_maturity.json"
DEFAULT_OUT = DEFAULT_LEDGER
DEFAULT_RTOL = 1e-9
DEFAULT_ABS_TOL = 1e-12
#: 扰动「有响应」的判定阈值：变化必须明显大于记录精度
RESPONSE_REL = 1e-6
MAX_MUTATIONS = 60

_HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_PATH = os.path.join(_HERE, "derive.py")
SPEC_PATH = os.path.join(_HERE, "channel_specs.py")


# ────────────────────────────── 基础工具 ──────────────────────────────

def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str) -> str:
    with open(path, "rb") as fh:
        return sha256_of_bytes(fh.read())


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _rel_tol_of(spec: dict) -> float:
    return float(spec.get("rtol", DEFAULT_RTOL))


def _close(expected: Any, actual: Any, rtol: float) -> bool:
    """数值判等：相对容差 + 近零绝对容差；非数值走 ==。"""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) <= max(
            DEFAULT_ABS_TOL, rtol * abs(float(expected)))
    return expected == actual


def _git_commit(repo_root: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", repo_root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    return out.stdout.strip() if out.returncode == 0 else None


# ────────────────────────────── 审计单元 ──────────────────────────────

def spec_identity(spec: dict, repo_root: str, source_sha: str | None) -> dict:
    """通道身份：引擎指纹 + 派生式指纹 + 源工件指纹。"""
    return {
        "engine_sha256": sha256_of_file(ENGINE_PATH),
        "spec_sha256": sha256_of_bytes(_canonical(spec).encode("utf-8")),
        "source_sha256": source_sha,
        "expr": spec["expr"],
        "manifest_version": channel_specs.MANIFEST_VERSION,
    }


def literal_echo(spec: dict, recorded: Any, rtol: float) -> list[float]:
    """派生式里的数值常量是否等于被断言值（回抄嫌疑）。"""
    if not isinstance(recorded, (int, float)) or isinstance(recorded, bool):
        return []
    return [c for c in derive.numeric_literals(spec["expr"])
            if _close(recorded, c, rtol)]


def _path_head(path: str) -> str:
    """路径的首段（用于圈定扰动范围）。"""
    seg = path.split(".")[0].split("[")[0]
    return seg


def _string_constants(spec: dict) -> list[str]:
    """派生式里的字符串常量（分类值），用作「分类改写」扰动的目标值。"""
    out = []
    for node in __import__("ast").walk(__import__("ast").parse(spec["expr"], mode="eval")):
        if isinstance(node, __import__("ast").Constant) \
                and isinstance(node.value, str) and node.value:
            out.append(node.value)
    return out


def _drop_at(root: Any, path: str, position: str) -> bool:
    """按位置（first/last/mid）删掉路径上第一个 list 的一个元素。"""
    segs = derive._split_path(path)  # noqa: SLF001 - 同包内部工具
    node = root
    target = None
    for seg in segs:
        if isinstance(node, list) and not seg.startswith("["):
            target = node
            break
        if seg.startswith("["):
            idx = int(seg[1:-1])
            if not isinstance(node, list) or idx >= len(node):
                return False
            node = node[idx]
        elif isinstance(node, dict):
            if seg not in node:
                return False
            node = node[seg]
        else:
            return False
    if target is None and isinstance(node, list):
        target = node
    if not target:
        return False
    if position == "first":
        target.pop(0)
    elif position == "mid" and len(target) > 2:
        target.pop(len(target) // 2)
    elif position == "last":
        target.pop()
    else:
        return False
    return True


def mutation_probe(spec: dict, root: Any, base_value: Any,
                   rtol: float, max_mutations: int = MAX_MUTATIONS) -> dict:
    """扰动敏感性：输出必须真的随输入变，否则判「常函数」（MOCK 风险）。

    四族扰动（全部机械、零领域知识；每族独立预算，互不挤占）：

    1. **结构性**：归约路径上第一个 list 的 first/mid/last 各删一个元素
       —— 计数/长度/均值必须变。
    2. **分类改写**：把扰动范围内一个字符串叶子改写成派生式里出现的分类值
       （如把某行状态改成被计数的那个状态）—— 计数必须变。
    3. **布尔翻转**：翻转一个布尔叶子 —— count_true/count_false 必须变。
    4. **数值缩放**：把数值叶子放大 1.001 —— 派生量必须变。

    任一族的任一扰动使输出变化超过 ``RESPONSE_REL`` 即认定「有响应」并立即停止。
    """
    paths = derive.reducer_paths(spec["expr"])
    heads = {_path_head(p) for p in paths}
    stats: dict[str, int] = {"structural": 0, "categorical": 0,
                             "boolean": 0, "extremum": 0, "numeric": 0}
    witness = None
    responsive = 0

    def _evaluate(mut_root: Any) -> Any:
        try:
            return derive.evaluate(spec["expr"], mut_root)
        except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
            return None

    def _register(kind: str, detail: str, new_value: Any) -> bool:
        nonlocal witness, responsive
        stats[kind] += 1
        if not isinstance(new_value, (int, float)) or isinstance(new_value, bool):
            return False
        if not isinstance(base_value, (int, float)):
            return False
        if abs(float(new_value) - float(base_value)) > max(
                DEFAULT_ABS_TOL, RESPONSE_REL * abs(float(base_value))):
            responsive += 1
            if witness is None:
                witness = {"kind": kind, "detail": detail,
                           "base": float(base_value), "mutated": float(new_value)}
            return True
        return False

    # 1) 结构性：first / mid / last
    for path in paths:
        for position in ("last", "first", "mid"):
            if stats["structural"] >= max_mutations:
                break
            mut = copy.deepcopy(root)
            if not _drop_at(mut, path, position):
                continue
            if _register("structural", "drop_%s:%s" % (position, path),
                         _evaluate(mut)):
                return _probe_result(stats, witness, responsive)

    # 2) 分类改写（跳过形如路径的常量）
    for const in [c for c in _string_constants(spec) if "." not in c and "/" not in c]:
        for leaf in derive.leaf_paths(root, "string", limit=max_mutations, within=heads):
            if stats["categorical"] >= max_mutations:
                break
            if derive.one(root, leaf) == const:
                continue
            mut = copy.deepcopy(root)
            try:
                derive.set_at(mut, leaf, const)
            except (KeyError, IndexError, TypeError):
                continue
            if _register("categorical", "set:%s=%s" % (leaf, const), _evaluate(mut)):
                return _probe_result(stats, witness, responsive)

    # 3) 布尔翻转
    for leaf in derive.leaf_paths(root, "bool", limit=max_mutations, within=heads):
        if stats["boolean"] >= max_mutations:
            break
        cur = derive.one(root, leaf)
        mut = copy.deepcopy(root)
        try:
            derive.set_at(mut, leaf, not cur)
        except (KeyError, IndexError, TypeError):
            continue
        if _register("boolean", "flip:%s" % leaf, _evaluate(mut)):
            return _probe_result(stats, witness, responsive)

    # 4) 极值叶子（min/max 归约专用：非极值元素扰动不改变极值）
    for path in paths:
        for leaf, new_value in _extremum_mutations(root, path):
            if stats["extremum"] >= max_mutations:
                break
            mut = copy.deepcopy(root)
            try:
                derive.set_numeric(mut, leaf, new_value)
            except (KeyError, IndexError, TypeError):
                continue
            if _register("extremum", "extreme:%s" % leaf, _evaluate(mut)):
                return _probe_result(stats, witness, responsive)

    # 5) 数值缩放
    for leaf in derive.numeric_leaves(root, limit=max_mutations, within=heads):
        if stats["numeric"] >= max_mutations:
            break
        cur = derive.one(root, leaf)
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            continue
        mut = copy.deepcopy(root)
        try:
            derive.set_numeric(mut, leaf, float(cur) * 1.001)
        except (KeyError, IndexError, TypeError):
            continue
        if _register("numeric", "scale:%s" % leaf, _evaluate(mut)):
            return _probe_result(stats, witness, responsive)

    return _probe_result(stats, witness, responsive)


def _extremum_mutations(root: Any, path: str) -> list[tuple[str, float]]:
    """为 min/max 归约找出「取到极值的那个叶子」，并给出方向正确的扰动。

    极值型归约对任意叶子的扰动都不敏感（扰动非极值元素不改变 min/max），
    因此必须正对极值叶子下手：min 情形把极值元素再往下压（min 会动），
    max 情形往上抬。返回 [(leaf_path, mutated_value), ...]。
    """
    got = derive.values(root, path)
    nums = [float(v) for v in got if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return []
    tail = path.split(".")[-1]
    head = path.split(".")[0].split("[")[0]
    leaves = derive.leaf_paths(root, "number", limit=50000, within={head})
    leaves = [lf for lf in leaves if lf.split(".")[-1].split("[")[0].endswith(tail)]
    out: list[tuple[str, float]] = []
    for leaf in leaves:
        cur = derive.one(root, leaf)
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            continue
        if float(cur) == min(nums):
            out.append((leaf, float(cur) * 0.999))
        if float(cur) == max(nums):
            out.append((leaf, float(cur) * 1.001))
    return out[:8]


def _probe_result(stats: dict, witness: Any, responsive: int) -> dict:
    return {"applied": sum(stats.values()), "responsive": responsive,
            "by_family": stats, "witness": witness}


def audit_channel(spec: dict, repo_root: str) -> dict:
    """审计单条通道：L0→L3。"""
    cid = spec["channel_id"]
    art_path = spec["artifact"]
    full = os.path.join(repo_root, art_path)
    record: dict[str, Any] = {
        "channel_id": cid,
        "artifact": art_path,
        "target": spec["target"],
        "kind": spec.get("kind"),
        "expr": spec["expr"],
        "rtol": _rel_tol_of(spec),
        "note": spec.get("note", ""),
        "grade": "L0",
        "reasons": [],
    }

    if not os.path.exists(full):
        record["reasons"].append("artifact_missing")
        return record

    source_sha = sha256_of_file(full)
    record["identity"] = spec_identity(spec, repo_root, source_sha)

    try:
        with open(full, "r", encoding="utf-8") as fh:
            root = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        record["reasons"].append("artifact_unreadable:%s" % exc)
        return record

    recorded = derive.one(root, spec["target"])
    record["recorded"] = recorded
    if recorded is None:
        record["reasons"].append("target_path_missing")
        return record

    rtol = _rel_tol_of(spec)
    try:
        derived_1 = derive.evaluate(spec["expr"], copy.deepcopy(root))
        derived_2 = derive.evaluate(spec["expr"], copy.deepcopy(root))
    except Exception as exc:  # noqa: BLE001 - 派生失败即 L0，原因入账
        record["reasons"].append("derive_error:%s:%s" % (type(exc).__name__, exc))
        return record

    record["derived"] = derived_1
    if isinstance(derived_1, (int, float)) and isinstance(recorded, (int, float)):
        record["rel_delta"] = (
            abs(float(derived_1) - float(recorded)) / abs(float(recorded))
            if float(recorded) != 0.0 else abs(float(derived_1))) 

    if not _close(recorded, derived_1, rtol):
        record["reasons"].append("replay_mismatch")
        return record
    record["grade"] = "L1"

    if _canonical(derived_1) != _canonical(derived_2):
        record["reasons"].append("non_deterministic")
        return record
    ident = record["identity"]
    if not (ident["engine_sha256"] and ident["spec_sha256"] and ident["source_sha256"]):
        record["reasons"].append("identity_incomplete")
        return record
    record["grade"] = "L2"

    echoes = literal_echo(spec, recorded, rtol)
    record["literal_echo"] = echoes
    if echoes:
        record["reasons"].append("literal_echo:%s" % echoes)
        return record

    probe = mutation_probe(spec, root, derived_1, rtol)
    record["mutation"] = probe
    if probe["responsive"] == 0:
        record["reasons"].append("output_insensitive_to_input")
        return record

    record["grade"] = "L3"
    return record


# ────────────────────────────── 全量审计 ──────────────────────────────

def audit(repo_root: str | None = None,
          channels: list[dict] | None = None) -> dict:
    """审计全部通道，返回可提交的台账（不含时间戳，幂等）。"""
    repo_root = os.path.abspath(repo_root or os.getcwd())
    specs = channels if channels is not None else channel_specs.CHANNELS
    records = [audit_channel(spec, repo_root) for spec in specs]

    grades = {"L0": 0, "L1": 0, "L2": 0, "L3": 0}
    for rec in records:
        grades[rec["grade"]] += 1
    total = len(records)
    covered = grades["L3"]

    by_artifact: dict[str, dict[str, int]] = {}
    for rec in records:
        slot = by_artifact.setdefault(rec["artifact"], {"L3": 0, "channels": 0})
        slot["channels"] += 1
        if rec["grade"] == "L3":
            slot["L3"] += 1

    return {
        "schema": "repro-audit/channel-maturity/v1",
        "manifest_version": channel_specs.MANIFEST_VERSION,
        "manifest_sha256": sha256_of_bytes(
            _canonical(channel_specs.CHANNELS).encode("utf-8")),
        "engine_sha256": sha256_of_file(ENGINE_PATH),
        "spec_module_sha256": sha256_of_file(SPEC_PATH),
        "commit": _git_commit(repo_root),
        "total": total,
        "grades": grades,
        "coverage": {"mature": covered, "total": total,
                     "rate": (covered / total) if total else 0.0,
                     "target": 24},
        "by_artifact": dict(sorted(by_artifact.items())),
        "channels": records,
    }


def _strip_volatile(ledger: dict) -> dict:
    """去掉允许逐次不同的字段（当前为零，保留扩展位）。"""
    out = copy.deepcopy(ledger)
    out.pop("generated_at", None)
    return out


def write_manifest(out_path: str) -> str:
    """把冻结的通道清单连同指纹落成工件（生成器身份的可审计副本）。"""
    payload = {
        "schema": "repro-audit/channel-manifest/v1",
        "manifest_version": channel_specs.MANIFEST_VERSION,
        "manifest_sha256": sha256_of_bytes(
            _canonical(channel_specs.CHANNELS).encode("utf-8")),
        "engine_sha256": sha256_of_file(ENGINE_PATH),
        "total": len(channel_specs.CHANNELS),
        "channels": channel_specs.CHANNELS,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    return out_path


def write_ledger(ledger: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    payload = _strip_volatile(ledger)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    return out_path


# ─────────────────── 基线：历史生成器注册表的同口径评级 ───────────────────

#: 历史生成器（tools/repro_audit/generators/gen_*.py）读文件的证据
_READ_PATTERNS = ("json.load(", "open(", ".read_text(", "yaml.safe_load(")


def _legacy_registry(repo_root: str) -> dict[str, str]:
    """artifact 路径 → 历史生成器文件（按 OUTPUT_PATH / TARGET_ARTIFACT 常量）。"""
    import re
    gen_dir = os.path.join(repo_root, os.path.dirname(os.path.relpath(__file__,
                                                                     repo_root)))
    if not os.path.isdir(gen_dir):
        gen_dir = _HERE
    reg: dict[str, str] = {}
    for fname in sorted(os.listdir(gen_dir)):
        if not (fname.startswith("gen_") and fname.endswith(".py")):
            continue
        fpath = os.path.join(gen_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                src = fh.read()
        except OSError:  # pragma: no cover
            continue
        for m in re.finditer(r'(?:OUTPUT_PATH|TARGET_ARTIFACT)\s*=\s*["\']([^"\']+)["\']',
                             src):
            reg.setdefault(m.group(1), fpath)
    return reg


def legacy_baseline(repo_root: str | None = None,
                    channels: list[dict] | None = None) -> dict:
    """用**同一套 L1/L2/L3 口径**给历史生成器注册表评级（只读基线）。

    历史 ``gen_*.py`` 的形态是「返回整份工件 dict 的函数」。本函数逐通道：
      * 找不到生成器 → ``none``；
      * 生成器跑不起来 → ``L0``；
      * 值对得上 → ``L1``；两次逐字节相等 → ``L2``；
      * 再要求：源文件里没有等于被断言值的数值字面量（无回抄），且生成器
        **对外部证据有依赖**（读过提交树里的文件）——否则它的输出只是自身
        字面量的函数，改工件不会改输出，判不了 L3。
    """
    import importlib.util
    import re

    repo_root = os.path.abspath(repo_root or os.getcwd())
    specs = channels if channels is not None else channel_specs.CHANNELS
    registry = _legacy_registry(repo_root)
    records = []

    for spec in specs:
        cid = spec["channel_id"]
        art = os.path.join(repo_root, spec["artifact"])
        rec: dict[str, Any] = {"channel_id": cid, "artifact": spec["artifact"],
                               "generator": None, "grade": "none", "reasons": []}
        gen_path = registry.get(spec["artifact"])
        if gen_path is None or not os.path.exists(art):
            rec["reasons"].append("no_generator_for_artifact")
            records.append(rec)
            continue
        rec["generator"] = os.path.relpath(gen_path, repo_root)
        rec["grade"] = "L0"  # 找到了生成器，但还没证明它重放得出该数字
        rtol = _rel_tol_of(spec)
        try:
            with open(art, "r", encoding="utf-8") as fh:
                recorded = derive.one(json.load(fh), spec["target"])
        except (OSError, json.JSONDecodeError) as exc:
            rec["reasons"].append("artifact_unreadable:%s" % exc)
            records.append(rec)
            continue
        rec["recorded"] = recorded

        try:
            spec_mod = importlib.util.spec_from_file_location("_legacy_gen", gen_path)
            mod = importlib.util.module_from_spec(spec_mod)
            spec_mod.loader.exec_module(mod)  # type: ignore[union-attr]
            doc_1 = mod.generate()
            doc_2 = mod.generate()
        except Exception as exc:  # noqa: BLE001
            rec["reasons"].append("generator_error:%s:%s"
                                  % (type(exc).__name__, exc))
            rec["grade"] = "L0"
            records.append(rec)
            continue

        derived = derive.one(doc_1, spec["target"])
        rec["derived"] = derived
        if not _close(recorded, derived, rtol):
            rec["reasons"].append("replay_mismatch")
            records.append(rec)
            continue
        rec["grade"] = "L1"
        if _canonical(doc_1) != _canonical(doc_2):
            rec["reasons"].append("non_deterministic")
            records.append(rec)
            continue
        rec["grade"] = "L2"

        with open(gen_path, "r", encoding="utf-8") as fh:
            src = fh.read()
        lits = [float(x) for x in re.findall(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?", src)]
        echo = [c for c in lits if isinstance(recorded, (int, float))
                and not isinstance(recorded, bool) and _close(recorded, c, rtol)]
        rec["literal_echo_count"] = len(echo)
        reads_evidence = any(pat in src for pat in _READ_PATTERNS)
        rec["reads_committed_evidence"] = reads_evidence
        if echo:
            rec["reasons"].append("literal_echo")
            records.append(rec)
            continue
        if not reads_evidence:
            rec["reasons"].append("no_external_input")
            records.append(rec)
            continue
        rec["grade"] = "L3"
        records.append(rec)

    grades = {"none": 0, "L0": 0, "L1": 0, "L2": 0, "L3": 0}
    for rec in records:
        grades[rec["grade"]] = grades.get(rec["grade"], 0) + 1
    total = len(records)
    return {
        "schema": "repro-audit/channel-baseline/v1",
        "source": "legacy gen_*.py registry (tools/repro_audit/generators)",
        "total": total,
        "grades": grades,
        "coverage": {"mature": grades["L3"], "total": total,
                     "rate": (grades["L3"] / total) if total else 0.0},
        "channels": records,
    }


# ────────────────────────────── 入库门 ──────────────────────────────

def gate(repo_root: str | None = None,
         ledger_path: str = DEFAULT_LEDGER,
         min_coverage: int = 24,
         min_grade: str = "L3",
         total_required: int | None = None,
         channels: list[dict] | None = None) -> dict:
    """fail-closed 入库门。

    通过条件（全部满足）：
      1. 台账存在且可解析；
      2. 台账里的 manifest/engine/spec 指纹 == 当前树（否则台账陈旧，
         说明工件不是当前生成器产出的）；
      3. 台账里每条通道的等级 == 新鲜审计的等级（否则重放不一致）；
      4. 每条通道等级 >= min_grade；
      5. 成熟通道数 >= min_coverage，且通道样本数 >= total_required。
    """
    repo_root = os.path.abspath(repo_root or os.getcwd())
    failures: list[dict] = []

    if not os.path.exists(ledger_path):
        return {"passed": False, "reason": "ledger_missing",
                "ledger": ledger_path, "failures": [{"reason": "ledger_missing"}]}

    try:
        with open(ledger_path, "r", encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "reason": "ledger_unreadable:%s" % exc,
                "failures": [{"reason": "ledger_unreadable", "detail": str(exc)}]}

    fresh = audit(repo_root, channels)
    for key in ("manifest_sha256", "engine_sha256", "spec_module_sha256"):
        if ledger.get(key) != fresh[key]:
            failures.append({"reason": "stale_ledger", "field": key,
                             "ledger": ledger.get(key), "current": fresh[key]})

    old = {c["channel_id"]: c for c in ledger.get("channels", [])}
    new = {c["channel_id"]: c for c in fresh["channels"]}
    if sorted(old) != sorted(new):
        failures.append({"reason": "channel_set_mismatch",
                         "only_in_ledger": sorted(set(old) - set(new)),
                         "only_in_tree": sorted(set(new) - set(old))})
    for cid, rec in new.items():
        if cid in old and old[cid].get("grade") != rec["grade"]:
            failures.append({"reason": "replay_grade_drift", "channel_id": cid,
                             "ledger": old[cid].get("grade"), "current": rec["grade"]})

    floor = GRADE_ORDER[min_grade]
    for rec in fresh["channels"]:
        if GRADE_ORDER[rec["grade"]] < floor:
            failures.append({"reason": "below_min_grade", "channel_id": rec["channel_id"],
                             "grade": rec["grade"], "min_grade": min_grade,
                             "why": rec.get("reasons")})

    mature = fresh["coverage"]["mature"]
    if mature < min_coverage:
        failures.append({"reason": "coverage_shortfall", "mature": mature,
                         "min_coverage": min_coverage})
    if total_required is not None and fresh["total"] < total_required:
        failures.append({"reason": "sample_too_small", "total": fresh["total"],
                         "required": total_required})

    passed = not failures
    return {
        "passed": passed,
        "reason": None if passed else "%d failures" % len(failures),
        "ledger": ledger_path,
        "min_grade": min_grade,
        "min_coverage": min_coverage,
        "coverage": fresh["coverage"],
        "grades": fresh["grades"],
        "failures": failures,
    }


# ─────────────────── 报表引用门（报告 → 通道覆盖） ───────────────────

_ARTIFACT_REF_RE = __import__("re").compile(r"artifacts/[A-Za-z0-9_./-]+\.json")


def referenced_artifacts(report_path: str) -> list[str]:
    """从报告的原始文本里抽出被引用的 artifacts/*.json 路径（稳定排序）。"""
    with open(report_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return sorted(set(m.group(0) for m in _ARTIFACT_REF_RE.finditer(text)))


def gate_report(repo_root: str, report_path: str,
                ledger_path: str = DEFAULT_LEDGER) -> dict:
    """fail-closed：报告引用的每个工件都必须有 ≥1 条成熟通道。

    这是入库门的「引用面」规则：报告里的每个关键数字要么落在已登记通道上，
    要么该工件根本不该出现在报告里。未登记的引用一律判失败。
    """
    with open(ledger_path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    mature_artifacts = {a for a, slot in ledger.get("by_artifact", {}).items()
                        if slot.get("L3", 0) > 0}
    refs = referenced_artifacts(report_path)
    unbacked = [r for r in refs if r not in mature_artifacts]
    return {
        "passed": not unbacked,
        "reason": None if not unbacked else "%d unbacked references" % len(unbacked),
        "report": report_path,
        "referenced": refs,
        "unbacked": unbacked,
    }


# ────────────────────────────── CLI ──────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数值通道成熟度审计 / 入库门")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true", help="审计并写台账")
    mode.add_argument("--gate", action="store_true", help="fail-closed 入库门")
    mode.add_argument("--print", dest="echo", action="store_true",
                      help="审计并打印到 stdout（不写文件）")
    mode.add_argument("--baseline-legacy", metavar="OUT",
                      help="用同一口径评级历史 gen_*.py 注册表（基线）并写 JSON")
    ap.add_argument("--repo", default=".", help="仓库根目录")
    ap.add_argument("--out", default=DEFAULT_OUT, help="台账输出路径")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER, help="入库门读取的台账")
    ap.add_argument("--min-coverage", type=int, default=24)
    ap.add_argument("--min-grade", default="L3", choices=sorted(GRADE_ORDER))
    ap.add_argument("--total-required", type=int, default=None,
                    help="通道样本数下限（默认不检查）")
    ap.add_argument("--report", default=None,
                    help="额外检查：该报告引用的工件都必须有成熟通道")
    ap.add_argument("--baseline", default=None,
                    help="配合 --gate：输出基线 JSON 的路径（可选）")
    args = ap.parse_args(argv)

    if args.baseline_legacy:
        base = legacy_baseline(args.repo)
        os.makedirs(os.path.dirname(os.path.abspath(args.baseline_legacy)) or ".",
                    exist_ok=True)
        with open(args.baseline_legacy, "w", encoding="utf-8") as fh:
            json.dump(base, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        print(json.dumps({"baseline": args.baseline_legacy,
                          "grades": base["grades"],
                          "coverage": base["coverage"]},
                         indent=2, ensure_ascii=False))
        return 0

    if args.gate:
        result = gate(args.repo, args.ledger, args.min_coverage,
                      args.min_grade, args.total_required)
        if args.report:
            rep = gate_report(args.repo, args.report, args.ledger)
            result["report_gate"] = rep
            if not rep["passed"]:
                result["passed"] = False
                result["reason"] = "report_citations_unbacked"
                result.setdefault("failures", []).append(
                    {"reason": "report_citations_unbacked",
                     "unbacked": rep["unbacked"]})
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["passed"] else 1

    ledger = audit(args.repo)
    if args.echo:
        print(json.dumps(ledger, indent=2, ensure_ascii=False))
        return 0
    write_ledger(ledger, args.out)
    manifest_out = os.path.join(os.path.dirname(args.out), "channels.json")
    write_manifest(manifest_out)
    print(json.dumps({"ledger": args.out, "manifest": manifest_out,
                      "coverage": ledger["coverage"],
                      "grades": ledger["grades"]},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())