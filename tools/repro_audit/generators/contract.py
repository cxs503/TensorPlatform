"""repro_audit.generators.contract —— 生成器契约 + 干净重放 + 入库门（fail-closed）。

**问题（本模块要结构性解决的）**：一个 JSON 工件里写着关键数字，不等于这个数字
可重放。三种伪复现在审计里长得一模一样：

1. **手抄**：数字是作者敲进去的常量，工件里没有任何能重算它的东西；
2. **回抄**：所谓"生成器"把工件的数字读出来再原样写回去（重放永远一致，
   工件被手改时同样一致——复证失效）；
3. **近似复算**：生成器实现了一个"简化模型"，形状对、数值不对。

**契约（本模块机器裁决的）**：

* **C1 可定位**：每个关键数字所在工件，在提交树内能定位到一个生成器模块，
  且模块 sha256 入台账 → 缺失即 G0。
* **C2 可重放**：在**干净检出**（``git archive`` 解出的提交树，不是工作树）里把
  生成器跑起来，重放结果在被断言路径上的值必须与工件值一致（rtol 见通道 spec）。
  不一致即 G1。两次重放（幂等）除白名单字段外必须相同。
* **C3 非手抄**：两种子证据，按生成器形态分别取证——
  ``head_tamper``：把工件里被断言的头部值改掉再重放，重算结果必须仍是**原值**
  （证明头部不是从工件抄的）；``detail_tamper``：改动它引用的明细行/入量再重放，
  结果必须**跟着变**（证明它真的在算，不是返回常量）。
* **C4 非近似**：不做"简化模型"侧的自动判定，改由 C2 的数值比对承担——
  近似模型会直接把值跑到 G1（历史 ``gen_*.py`` 基线就是这么翻红的）。

等级：

===== ================================================================
G0    生成器缺失 / 跑不起来 / 没产出 —— 没有数值通道
G1    跑起来了但被断言值与工件不一致（近似复算 / 漂移 / 非确定性）
G2    C1 + C2(单次) + C3(head_tamper) 成立 —— 数值通道成立
G3    G2 + 幂等 + C3(detail_tamper) [+ 生成器不读工件本身 → ``independent``]
===== ================================================================

``independent`` 是**更强的一档**（生成器完全不读被审计工件，只吃独立入量），
单独统计、单独报告：它才是"数字有独立来源"的证据。

**零领域语义**：本模块不认识任何学科词/船型/仓名。通道清单与生成器清单都是
**JSON 数据**（``artifacts/repro_audit/*.json``），模块只做契约裁决。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any

from . import derive

__all__ = [
    "GRADES", "GRADE_RANK", "WHITELIST_FIELDS", "build_ledger", "clean_root",
    "contract_check", "gate", "get_path", "grade_channels", "ledger_freshness",
    "legacy_specs", "load_channels", "load_specs", "run_producer", "static_audit",
    "values_match",
]

#: 允许每次运行不同的字段（时间戳/耗时/主机名/运行 id）
WHITELIST_FIELDS: set[str] = {
    "timestamp", "generated_at", "created_at", "datetime", "hostname",
    "duration_sec", "wall_time", "wall_clock_ms", "cpu_ms", "started_at",
    "finished_at", "elapsed_sec", "runtime_sec", "run_id",
}

GRADES = {
    "G0": "generator_missing_or_failed",
    "G1": "replay_value_mismatch",
    "G2": "reproduces_from_committed_tree",
    "G3": "reproduces_idempotent_and_derivation_verified",
}
GRADE_RANK = {name: i for i, name in enumerate(GRADES)}

_KINDS = {"producer", "derive"}


# ─────────────────────────── 基础工具 ───────────────────────────
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str | None) -> str | None:
    if not path:
        return None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def get_path(obj: Any, path: str) -> Any:
    """取 JSON 路径：``a.b[0].c`` / ``[0].c`` / ``a``。缺失抛 KeyError/IndexError。"""
    cur = obj
    token = ""
    i, n = 0, len(path)
    while i < n:
        ch = path[i]
        if ch == ".":
            if token:
                cur = cur[token]
                token = ""
            i += 1
        elif ch == "[":
            if token:
                cur = cur[token]
                token = ""
            j = path.index("]", i)
            cur = cur[int(path[i + 1:j])]
            i = j + 1
            if i < n and path[i] == ".":
                i += 1
        else:
            token += ch
            i += 1
    if token:
        cur = cur[token]
    return cur


def _normalise(obj: Any) -> Any:
    """去掉白名单字段后的可比对象（递归、排序）。"""
    if isinstance(obj, dict):
        return {k: _normalise(v) for k, v in sorted(obj.items()) if k not in WHITELIST_FIELDS}
    if isinstance(obj, list):
        return [_normalise(v) for v in obj]
    return obj


def values_match(expected: Any, actual: Any, rtol: float = 1e-9) -> bool:
    """结构 + 数值（相对容差）比对。bool 必须严格相等（不当 0/1 处理）。"""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == actual:
            return True
        scale = max(abs(float(expected)), abs(float(actual)), 1e-12)
        return abs(float(expected) - float(actual)) / scale <= rtol
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            values_match(a, b, rtol) for a, b in zip(expected, actual))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(
            values_match(expected[k], actual[k], rtol) for k in expected)
    return expected == actual


# ─────────────────────────── 载入规格 ───────────────────────────
def load_specs(specs_path: str) -> dict:
    """载入生成器规格 + 通道清单（全部是数据，模块本身零领域语义）。"""
    with open(specs_path, "r", encoding="utf-8") as fh:
        specs = json.load(fh)
    specs.setdefault("generators", specs.pop("producers", []))
    specs.setdefault("channel_manifest", "artifacts/repro_audit/channels.json")
    for gen in specs["generators"]:
        gen.setdefault("kind", "producer")
        if gen["kind"] not in _KINDS:
            raise ValueError(f"unknown generator kind: {gen['kind']}")
        if gen.get("input") is None:
            gen["input"] = gen["artifact"]
        if gen["kind"] == "derive" and not gen.get("output"):
            raise ValueError(f"derive generator needs output: {gen.get('id')}")
    return specs


def load_channels(manifest_path: str) -> list[dict]:
    """载入冻结的数值通道清单（``{"channels": [...]}`` 或裸列表）。"""
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    channels = data["channels"] if isinstance(data, dict) else data
    for ch in channels:
        ch.setdefault("rtol", 1e-9)
    return channels


def run_key(spec: dict) -> str:
    """同一份重放可产出多个工件：用 run_key 归组，避免重复跑。"""
    return spec.get("run_key") or json.dumps(
        {k: spec.get(k) for k in ("module", "kind", "entry", "cwd", "argv", "kwargs", "env")},
        sort_keys=True)


# ─────────────────────────── 静态审计 ───────────────────────────
_PATH_ARG_FUNCS = {"open", "load", "loads", "read_text", "read_bytes", "Path", "exists"}


def _path_literals(tree: ast.AST) -> list[str]:
    """收集所有"被当作文件路径用的字符串字面量"。"""
    found: list[str] = []

    class V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else "")
            if name in _PATH_ARG_FUNCS:
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.append(arg.value)
            self.generic_visit(node)

    V().visit(tree)
    return found


def _distinctive(value: float) -> bool:
    """是不是"有信息量"的常量：6 位以上有效数字的浮点，或 |v|>=1000 的整数。

    小整数（0/1/2/3/12 这类）在正常代码里到处都是（长度、维度、容差），拿它们
    判"手抄"只会误伤。真正的抄写是把"算出来的那个数"敲进去——那种数通常有
    6 位以上有效数字。
    """
    if value == 0:
        return False
    text = repr(float(value)).replace("-", "").replace(".", "").lstrip("0").rstrip("0")
    if float(value).is_integer():
        return abs(value) >= 1000
    return len(text) >= 6


def static_audit(module_path: str, artifact_rel: str, asserted_values: list[Any]) -> dict:
    """静态审计：自读路径字面量 / 字面量回抄 / 非确定性来源。

    * ``self_read``：模块里出现等于目标工件路径（或其 basename）的路径字面量，
      且用在 open/Path/load 这类调用里。
    * ``literal_backcopy``：模块里出现与被断言值数值相等的**有信息量**常量
      （见 :func:`_distinctive`；小整数不判，避免误伤长度/维度常量）。
    * ``nondeterminism``：``random.*`` / ``time.time`` / ``datetime.now`` 等；
      仅提示，真正的裁决靠两次重放比对。
    """
    try:
        src = open(module_path, "r", encoding="utf-8", errors="ignore").read()
    except OSError as exc:
        return {"self_read": [], "literal_backcopy": [], "nondeterminism": [],
                "error": f"{type(exc).__name__}: {exc}", "sha256": None}
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:  # pragma: no cover
        return {"self_read": [], "literal_backcopy": [], "nondeterminism": [],
                "error": f"SyntaxError: {exc}", "sha256": _sha256_bytes(src.encode())}

    targets = {artifact_rel, os.path.basename(artifact_rel)}
    self_read = sorted({p for p in _path_literals(tree)
                        if p in targets or p.endswith(artifact_rel)})

    wanted = [float(v) for v in asserted_values
              if isinstance(v, (int, float)) and not isinstance(v, bool) and _distinctive(float(v))]
    backcopy: list[float] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            val = float(node.value)
            for w in wanted:
                scale = max(abs(w), abs(val), 1e-12)
                if val != 0 and abs(val - w) / scale <= 1e-12:
                    backcopy.append(val)
                    break
    nondet = sorted({n for n in ("random.", "time.time", "datetime.now", "uuid", "os.urandom")
                     if n in src})
    return {"self_read": self_read, "literal_backcopy": sorted(set(backcopy)),
            "nondeterminism": nondet, "sha256": _sha256_bytes(src.encode())}


# ─────────────────────────── 干净检出 ───────────────────────────
def clean_root(repo_root: str, dest: str, ref: str | None = "HEAD") -> str:
    """解出干净检出，返回目录。

    ``ref="HEAD"``：``git archive`` 出**提交树**（重放必须在提交树上做——工作树带着
    未提交改动时"重放一致"不能证明任何东西）。``ref=None``：直接拷贝源目录
    （仅用于合成夹具/负控沙箱）。
    """
    repo_root = os.path.abspath(repo_root)
    os.makedirs(dest, exist_ok=True)
    root = os.path.join(dest, "tree")
    if os.path.isdir(root):
        shutil.rmtree(root)
    if ref is None:
        shutil.copytree(repo_root, root, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        return root
    tar_path = os.path.join(dest, "_head.tar")
    subprocess.run(["git", "-C", repo_root, "archive", "--format=tar", ref, "-o", tar_path],
                   check=True, capture_output=True)
    os.makedirs(root, exist_ok=True)
    with tarfile.open(tar_path) as tf:
        tf.extractall(root)  # noqa: S202 - 只解自己仓的 HEAD
    os.remove(tar_path)
    return root


# ─────────────────────────── 生成器重放 ───────────────────────────
def run_producer(spec: dict, root: str, repo_root: str, clear: list[str] | None = None,
                 python: str | None = None, timeout: int | None = None,
                 env_extra: dict[str, str] | None = None,
                 clear_watch: bool = True) -> dict:
    """在干净检出 ``root`` 里跑一次生成器，返回执行结果（含运行时读/写审计）。"""
    python = python or sys.executable
    kind = spec.get("kind", "producer")
    watch = spec["artifact"] if kind == "producer" else spec["output"]
    produced = os.path.join(root, watch)
    doomed = ([watch] if clear_watch else []) + list(clear or [])
    for rel in doomed:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            os.remove(p)
    os.makedirs(os.path.dirname(produced), exist_ok=True)
    cwd = os.path.join(root, spec.get("cwd", "."))

    tmpdir = tempfile.mkdtemp(prefix="repro_runner_")
    payload = {
        "repo_root": root,
        "module": spec["module"],
        "entry": spec.get("entry"),
        # derive 走脚本；有 entry 的生产者走可调用入口；其余走脚本
        "kind": "script" if kind == "derive" else (
            "callable" if spec.get("entry") else "script"),
        "argv": spec.get("argv") or [spec["module"]],
        "kwargs": spec.get("kwargs") or {},
        "setup": spec.get("setup") or [],
        "sys_path": spec.get("sys_path") or [],
        "watch": watch,
        "result_path": os.path.join(tmpdir, "result.json"),
    }
    payload_path = os.path.join(tmpdir, "payload.json")
    with open(payload_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    env = dict(os.environ)
    env.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
    env.update({k: str(v) for k, v in (env_extra or {}).items()})
    env["PYTHONHASHSEED"] = "0"
    env.pop("PYTHONPATH", None)
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_runner.py")
    started = time.time()
    try:
        proc = subprocess.run([python, runner, payload_path], cwd=cwd, env=env,
                              capture_output=True, text=True,
                              timeout=timeout or int(spec.get("timeout", 900)))
        rc, stderr = proc.returncode, proc.stderr[-1500:]
    except subprocess.TimeoutExpired:
        rc, stderr = -9, "timeout"

    result: dict = {"ok": False, "error": None, "reads": [], "writes": [],
                    "artifact_sha256": None,
                    "duration_sec": round(time.time() - started, 3)}
    try:
        with open(payload["result_path"], "r", encoding="utf-8") as fh:
            result.update(json.load(fh))
    except OSError:
        result["error"] = f"runner_no_result(rc={rc}): {stderr[-400:]}"
    if not result.get("ok") and not result.get("error"):
        result["error"] = f"rc={rc}: {stderr[-400:]}"
    result.update({"returncode": rc, "produced_path": produced,
                   "produced_exists": os.path.exists(produced), "_root": root,
                   "_watch": watch})
    return result


def self_read_paths(run: dict, artifact_rel: str) -> list[str]:
    """重放期间是否读过被审计工件（解释器层 open 审计，路径拼装逃不掉）。"""
    artifact = os.path.abspath(os.path.join(run.get("_root") or "", artifact_rel))
    return sorted({os.path.abspath(p) for p in run.get("reads", [])
                   if os.path.abspath(p) == artifact})


def _idempotency(prev_path: str, cur_path: str, rtol: float = 1e-9) -> dict:
    try:
        a = json.loads(open(prev_path, "r", encoding="utf-8").read())
        b = json.loads(open(cur_path, "r", encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        return {"equal": False, "detail": {"error": f"{type(exc).__name__}: {exc}"}}
    return {"equal": values_match(_normalise(a), _normalise(b), rtol),
            "detail": {"bytes_run1": os.path.getsize(prev_path),
                       "bytes_run2": os.path.getsize(cur_path)}}


# ─────────────────────────── 取重放值 ───────────────────────────
def produced_value(spec: dict, payload: Any, channel: dict, root: str) -> Any:
    """从重放产出里取该通道的值。

    ``producer`` 型：产出就是工件本身 → 按 target 路径取。
    ``derive`` 型：产出是 ``{"channels": {channel_id: value}}`` → 按通道 id 取。
    """
    if spec.get("kind") == "derive":
        # 通道 id 里可能带 "."（如 xxx#a.b），不能走点路径解析，直接按 key 取
        return payload["channels"][channel["channel_id"]]
    return get_path(payload, channel["target"])


def _read_committed(repo_root: str, artifact: str) -> Any:
    with open(os.path.join(repo_root, artifact), "r", encoding="utf-8") as fh:
        return json.load(fh)


# ─────────────────────────── 契约取证（tamper） ───────────────────────────
def _perturb_channel(root_src: dict, spec: dict, channel: dict) -> tuple[dict | None, str]:
    """为 detail_tamper 造一份入量扰动副本：只改它派生用的明细，不碰头部值。

    候选扰动按"最容易改变该通道取值"的顺序**惰性**生成并逐个试算，只有重算结果
    确实变了才采纳（否则"删掉的那行恰好是 pass=True"这类空扰动会被误判为
    "与明细无关"）。评审注意：这里刻意不做全量候选物化——大工件上逐候选深拷贝
    会把审计拖成分钟级。

    候选顺序：
      1. 结构：reducer 路径在文档里真指向 list 时裁掉末行（``len``/``count`` 类变动）；
      2. 叶子精确命中该路径的数值/布尔/字符串叶子（``min``/``max``/``get`` 类）；
      3. 当前值"就是被比较/被聚合的那个值"的叶子；
      4. 字符串叶子改写成表达式里的比较目标（计数为 0 的通道只有这一条路能动）；
      5. 其余叶子（有上限，防拖死）。
    """
    expr = channel.get("expr", "")
    rtol = float(channel.get("rtol", 1e-9))
    try:
        base = derive.evaluate(expr, root_src)
    except (KeyError, IndexError, TypeError, ValueError):
        return None, ""

    str_targets = sorted({node.value for node in ast.walk(ast.parse(expr, mode="eval"))
                          if isinstance(node, ast.Constant) and isinstance(node.value, str)
                          and not any(ch in node.value for ch in ".[]")})
    paths = derive.reducer_paths(expr)

    def _leaf_desc() -> list[tuple[str, Any, Any]]:
        """(leaf, current_value, override_value)，按优先级排好、有上限。"""
        exact, hit, relabel, rest = [], [], [], []
        for path in paths:
            head = path.split(".")[0].split("[")[0]
            for kind in ("number", "bool", "string"):
                for leaf in derive.leaf_paths(root_src, kind=kind, within=[head], limit=4000):
                    try:
                        cur = derive.one(root_src, leaf)
                    except (KeyError, IndexError, TypeError):
                        continue
                    if leaf == path or leaf.startswith(path + ".") or leaf.startswith(path + "["):
                        exact.append((leaf, cur, None))
                    elif isinstance(cur, bool):
                        # 布尔叶子：count_true/count_false/count_not_none 只能靠它动
                        hit.append((leaf, cur, None))
                    elif isinstance(cur, str):
                        if cur in str_targets:
                            hit.append((leaf, cur, None))
                        for tgt in str_targets:
                            if tgt != cur:
                                relabel.append((leaf, cur, tgt))
                    elif values_match(base, cur, rtol):
                        hit.append((leaf, cur, None))
                        if isinstance(cur, (int, float)):
                            # 聚合型通道（min/max）：把极值往下挪，保证重算结果一定变
                            hit.append((leaf, cur, float(cur) * 0.9 - 0.5))
                    else:
                        rest.append((leaf, cur, None))
        return exact[:60] + hit[:60] + relabel[:40] + rest[:40]

    # 1) 结构扰动
    for path in paths:
        trial = json.loads(json.dumps(root_src))
        if not derive.drop_last_at(trial, path):
            continue
        try:
            if not values_match(base, derive.evaluate(expr, trial), rtol):
                return trial, f"drop_last:{path}"
        except (KeyError, IndexError, TypeError, ValueError):
            continue

    # 2) 叶子扰动（惰性逐个试）
    for leaf, cur, to in _leaf_desc():
        trial = json.loads(json.dumps(root_src))
        try:
            if to is not None:
                derive.set_at(trial, leaf, to)
                label = f"set:{leaf}={to}"
            elif isinstance(cur, bool):
                derive.set_at(trial, leaf, not cur)
                label = f"toggle:{leaf}"
            elif isinstance(cur, str):
                derive.set_at(trial, leaf, "TAMPERED")
                label = f"relabel:{leaf}"
            else:
                derive.set_numeric(trial, leaf,
                                   float(cur) * 1.1 + (0.5 if cur == 0 else 0.0))
                label = f"scale:{leaf}"
            if not values_match(base, derive.evaluate(expr, trial), rtol):
                return trial, label
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None, ""


def contract_check(spec: dict, channel: dict, root: str, repo_root: str,
                   committed: Any) -> dict:
    """C3 取证：head_tamper（头部改了也照样重算回原值）+ detail_tamper（明细改了跟着变）。"""
    out = {"head_tamper": None, "detail_tamper": None, "detail_method": None}
    kind = spec.get("kind")
    rtol = float(channel.get("rtol", 1e-9))
    if kind == "producer":
        # 不读工件的生产者，头部篡改对它没有任何影响 → 天然满足
        out["head_tamper"] = True
        out["detail_tamper"] = True
        out["detail_method"] = "independent_producer"
        return out

    audit_dir = os.path.join(root, "_contract_audit")
    os.makedirs(audit_dir, exist_ok=True)
    committed_root = _read_committed(repo_root, spec["artifact"])

    # ① 头部篡改：把被断言的值 +1%，看重算结果是否仍是原值
    head_copy = json.loads(json.dumps(committed_root))
    try:
        cur = get_path(head_copy, channel["target"])
        if isinstance(cur, bool):
            derive.set_at(head_copy, channel["target"], not cur)
        elif isinstance(cur, (int, float)):
            derive.set_numeric(head_copy, channel["target"], float(cur) * 1.01 + 0.5)
        else:
            derive.set_at(head_copy, channel["target"], "TAMPERED")
    except (KeyError, IndexError, TypeError):
        pass
    head_in = os.path.join(audit_dir, "head_input.json")
    head_out = os.path.join(audit_dir, "head_output.json")
    with open(head_in, "w", encoding="utf-8") as fh:
        json.dump(head_copy, fh)
    run = run_producer(spec, root, repo_root,
                       env_extra={"REPRO_INPUT": head_in, "REPRO_OUTPUT": head_out},
                       clear_watch=False)
    try:
        payload = json.loads(open(head_out, "r", encoding="utf-8").read())
        out["head_tamper"] = values_match(committed, produced_value(spec, payload, channel, root), rtol)
    except (OSError, ValueError, KeyError, IndexError):
        out["head_tamper"] = False

    # ② 明细篡改：改它引用的明细行/入量，重算结果必须变
    detail_copy, method = _perturb_channel(committed_root, spec, channel)
    out["detail_method"] = method
    if detail_copy is None:
        out["detail_tamper"] = None
        return out
    det_in = os.path.join(audit_dir, "detail_input.json")
    det_out = os.path.join(audit_dir, "detail_output.json")
    with open(det_in, "w", encoding="utf-8") as fh:
        json.dump(detail_copy, fh)
    run = run_producer(spec, root, repo_root,
                       env_extra={"REPRO_INPUT": det_in, "REPRO_OUTPUT": det_out},
                       clear_watch=False)
    try:
        payload = json.loads(open(det_out, "r", encoding="utf-8").read())
        out["detail_tamper"] = not values_match(
            committed, produced_value(spec, payload, channel, root), rtol)
    except (OSError, ValueError, KeyError, IndexError):
        out["detail_tamper"] = None
    return out


# ─────────────────────────── 评级 ───────────────────────────
def _grade_one(spec: dict, ch: dict, run: dict, static: dict, tamper: dict,
               committed_value: Any) -> dict:
    """对单个（生成器, 通道）给出等级记录。"""
    rc = float(ch.get("rtol", 1e-9))
    rec: dict[str, Any] = {
        "channel_id": ch["channel_id"], "artifact": ch["artifact"], "target": ch["target"],
        "generator": spec.get("module"), "generator_id": spec.get("id"),
        "generator_kind": spec.get("kind"), "grade": "G0",
        "reason": GRADES["G0"], "detail": {}, "tier": None,
    }
    rec["detail"]["run_ok"] = run.get("ok")
    rec["detail"]["run_seconds"] = run.get("duration_sec")
    if not run.get("ok") or not (run.get("_produced_exists") or {}).get(ch["artifact"]):
        rec["detail"]["error"] = run.get("error")
        return rec
    try:
        payload = json.loads(open((run.get("_produced") or {})[ch["artifact"]],
                                  "r", encoding="utf-8").read())
        rec["produced_value"] = produced_value(spec, payload, ch, run.get("_root") or "")
    except (OSError, KeyError, IndexError, ValueError, TypeError) as exc:
        rec["reason"] = "produced_target_missing"
        rec["detail"]["produced_error"] = f"{type(exc).__name__}: {exc}"
        return rec

    if not values_match(committed_value, rec["produced_value"], rc):
        rec["grade"], rec["reason"] = "G1", GRADES["G1"]
        return rec

    st = static.get(spec["module"], {})
    rec["detail"].update({
        "value_match": True, "rel_tol": rc,
        "static_self_read": st.get("self_read", []),
        "literal_backcopy": st.get("literal_backcopy", []),
        "nondeterminism_hint": st.get("nondeterminism", []),
        "generator_sha256": st.get("sha256"),
    })
    if st.get("literal_backcopy"):
        rec["grade"], rec["reason"] = "G1", "literal_backcopy_in_generator"
        return rec

    ev = tamper.get(ch["channel_id"], {})
    rec["detail"].update({"head_tamper": ev.get("head_tamper"),
                          "detail_tamper": ev.get("detail_tamper"),
                          "detail_method": ev.get("detail_method")})
    rec["grade"], rec["reason"] = "G2", GRADES["G2"]
    if ev.get("head_tamper") is False:
        rec["reason"] = "head_value_copied"
        return rec

    self_read = self_read_paths(run, ch["artifact"])
    independent = not self_read
    idem = (run.get("_idempotent") or {}).get(ch["artifact"], {})
    rec["detail"].update({"self_read_runtime": self_read, "independent": independent,
                          "idempotent": idem.get("equal")})
    rec["tier"] = "independent" if independent else "derivation"
    if independent or ev.get("detail_tamper") is True:
        if idem.get("equal") is True:
            rec["grade"], rec["reason"] = "G3", GRADES["G3"]
        elif idem.get("equal") is None:
            rec["reason"] = "idempotency_not_checked"
        else:
            rec["reason"] = "replay_not_idempotent"
    elif ev.get("detail_tamper") is False:
        rec["reason"] = "value_independent_of_details"
    else:
        rec["reason"] = "derivation_not_verified"
    return rec


def grade_channels(specs: dict, channels: list[dict], runs: dict[str, dict],
                   repo_root: str, static: dict[str, dict] | None = None,
                   tamper: dict[str, dict] | None = None) -> list[dict]:
    """把每个通道评到 G0..G3：同一工件有多个生成器时取**最强证据**。

    选择规则：等级高者优先；同等级下 ``independent``（不读工件的生产者）优先；
    再同则按 ``generators`` 里的登记顺序。因此"派生型能过、独立生产者也能过"的
    通道会被记成 independent，而不是被较弱的派生式掩盖。
    """
    static, tamper = static or {}, tamper or {}
    by_artifact: dict[str, list[dict]] = {}
    for spec in specs.get("generators", []):
        by_artifact.setdefault(spec["artifact"], []).append(spec)

    graded: list[dict] = []
    for ch in channels:
        base = {"channel_id": ch["channel_id"], "artifact": ch["artifact"],
                "target": ch["target"], "kind": ch.get("kind"),
                "note": ch.get("note", "")}
        try:
            committed_doc = _read_committed(repo_root, ch["artifact"])
            committed = get_path(committed_doc, ch["target"])
        except (OSError, KeyError, IndexError, ValueError, TypeError) as exc:
            rec = dict(base, generator=None, generator_kind=None, tier=None,
                       grade="G0", reason="artifact_or_target_missing", detail={
                           "committed_error": f"{type(exc).__name__}: {exc}"})
            graded.append(rec)
            continue

        specs_here = by_artifact.get(ch["artifact"]) or []
        if not specs_here:
            graded.append(dict(base, generator=None, generator_kind=None, tier=None,
                               grade="G0", reason=GRADES["G0"],
                               detail={"gap": "no_generator_registered"},
                               committed_value=committed))
            continue

        best: dict | None = None
        for spec in sorted(specs_here, key=lambda s: 0 if s.get("kind") == "producer" else 1):
            rec = _grade_one(spec, ch, runs.get(run_key(spec), {}), static, tamper, committed)
            rank = (GRADE_RANK[rec["grade"]], 1 if rec.get("tier") == "independent" else 0)
            if best is None or rank > best["_rank"]:
                rec["_rank"] = rank
                best = rec
        assert best is not None
        best.pop("_rank", None)
        best["committed_value"] = committed
        graded.append(dict(base, **{k: v for k, v in best.items()
                                    if k not in ("channel_id", "artifact", "target", "kind", "note")}))
    return graded


# ─────────────────────────── 台账 ───────────────────────────
def build_ledger(repo_root: str, specs_path: str, ref: str | None = "HEAD",
                 workdir: str | None = None, idempotency: bool = True,
                 contract_evidence: bool = True, only: list[str] | None = None) -> dict:
    """跑完整审计：干净检出 → 生成器重放（+幂等重放+篡改取证）→ 逐通道评级。"""
    repo_root = os.path.abspath(repo_root)
    specs = load_specs(specs_path)
    manifest = os.path.join(repo_root, specs["channel_manifest"])
    channels = load_channels(manifest)
    workdir = workdir or tempfile.mkdtemp(prefix="repro_contract_")
    root = clean_root(repo_root, workdir, ref=ref)

    groups: dict[str, list[dict]] = {}
    for spec in specs.get("generators", []):
        if only and (spec.get("id") or spec.get("module")) not in only:
            continue
        groups.setdefault(run_key(spec), []).append(spec)

    runs: dict[str, dict] = {}
    for key, members in groups.items():
        first = members[0]
        clear = [s["output"] for s in members if s.get("kind") == "derive"]
        run = run_producer(first, root, repo_root, clear=clear[1:])
        produced = {m["artifact"]: os.path.join(
            root, m["artifact"] if m.get("kind", "producer") == "producer" else m["output"])
            for m in members}
        run["_produced"] = produced
        run["_produced_exists"] = {a: os.path.exists(p) for a, p in produced.items()}
        if idempotency and run["ok"]:
            copies = {}
            for art, path in produced.items():
                if os.path.exists(path):
                    copies[art] = path + ".run1"
                    shutil.copyfile(path, copies[art])
            run2 = run_producer(first, root, repo_root, clear=clear[1:])
            run["_idempotent"] = {}
            for art, copy in copies.items():
                rtol = min((float(c.get("rtol", 1e-9)) for c in channels
                            if c["artifact"] == art), default=1e-9)
                run["_idempotent"][art] = _idempotency(copy, produced[art], rtol)
            run["_run2_ok"] = run2["ok"]
            run["_run2_seconds"] = run2["duration_sec"]
        runs[key] = run

    # C3 篡改取证（只对派生型生成器；生产者型天然独立）
    tamper: dict[str, dict] = {}
    if contract_evidence:
        for spec in specs.get("generators", []):
            if run_key(spec) not in runs:
                continue
            for ch in channels:
                if ch["artifact"] != spec["artifact"]:
                    continue
                try:
                    committed_doc = _read_committed(repo_root, spec["artifact"])
                    committed = get_path(committed_doc, ch["target"])
                except (OSError, KeyError, IndexError, TypeError):
                    continue
                tamper[ch["channel_id"]] = contract_check(spec, ch, root, repo_root, committed)

    # 静态审计（被断言值从工件里读，不手抄）
    static: dict[str, dict] = {}
    for spec in specs.get("generators", []):
        if run_key(spec) not in runs or spec["module"] in static:
            continue
        mod_path = os.path.join(repo_root, spec["module"])
        try:
            committed_doc = _read_committed(repo_root, spec["artifact"])
        except (OSError, ValueError):
            committed_doc = None
        vals = []
        for ch in channels:
            if ch["artifact"] != spec["artifact"] or committed_doc is None:
                continue
            try:
                vals.append(get_path(committed_doc, ch["target"]))
            except (KeyError, IndexError, TypeError):
                pass
        static[spec["module"]] = static_audit(mod_path, spec["artifact"], vals)

    graded = grade_channels(specs, channels, runs, repo_root, static, tamper)
    mature = [g for g in graded if GRADE_RANK[g["grade"]] >= GRADE_RANK["G2"]]
    independent = [g for g in graded if g.get("tier") == "independent"
                   and GRADE_RANK[g["grade"]] >= GRADE_RANK["G2"]]

    provenance: dict[str, Any] = {
        "specs_sha256": _sha256_file(os.path.abspath(specs_path)),
        "manifest_sha256": _sha256_file(manifest),
        "ref": ref,
        "generators": {},
    }
    for spec in specs.get("generators", []):
        key = run_key(spec)
        if key not in runs:
            continue
        run = runs[key]
        provenance["generators"][spec.get("id") or spec["module"]] = {
            "module": spec["module"],
            "kind": spec.get("kind"),
            "artifact": spec["artifact"],
            "module_sha256": _sha256_file(os.path.join(repo_root, spec["module"])),
            "run_ok": run.get("ok"),
            "seconds": run.get("duration_sec"),
            "idempotent": {a: (v or {}).get("equal")
                           for a, v in (run.get("_idempotent") or {}).items()},
            "reads": [os.path.relpath(p, root) for p in run.get("reads", [])
                      if p.startswith(root)][:20],
        }
    provenance["chain_sha256"] = _sha256_bytes(json.dumps(provenance, sort_keys=True).encode())

    return {
        "schema": "repro_audit.generator_contract/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_ref": ref,
        "specs_path": os.path.relpath(os.path.abspath(specs_path), repo_root),
        "coverage": {
            "mature": len(mature), "total": len(graded),
            "rate": round(len(mature) / len(graded), 4) if graded else 0.0,
            "independent": len(independent),
            "derivation_only": len(mature) - len(independent),
        },
        "grades": {name: sum(1 for g in graded if g["grade"] == name) for name in GRADES},
        "channels": graded,
        "gaps": [{"channel_id": g["channel_id"], "artifact": g["artifact"],
                  "reason": g["reason"]}
                 for g in graded if GRADE_RANK[g["grade"]] < GRADE_RANK["G2"]],
        "provenance": provenance,
    }


def ledger_freshness(ledger: dict, repo_root: str, specs_path: str | None = None) -> dict:
    """台账是否与当前提交树一致（生成器/清单 sha256 链未变）。"""
    problems: list[str] = []
    prov = ledger.get("provenance") or {}
    if specs_path and os.path.exists(specs_path):
        if _sha256_file(os.path.abspath(specs_path)) != prov.get("specs_sha256"):
            problems.append("specs_changed_since_ledger")
    for gid, rec in (prov.get("generators") or {}).items():
        cur = _sha256_file(os.path.join(repo_root, rec["module"])) if rec.get("module") else None
        if cur != rec.get("module_sha256"):
            problems.append(f"generator_changed_since_ledger:{gid}")
    return {"fresh": not problems, "problems": problems}


def gate(ledger: dict, min_coverage: int = 24, min_grade: str = "G2",
         freshness: dict | None = None) -> dict:
    """入库门（fail-closed）：覆盖不足 / 等级不足 / 无溯源 / 台账陈旧 → 拒。"""
    reasons: list[str] = []
    coverage = ledger.get("coverage") or {}
    if int(coverage.get("mature", 0)) < int(min_coverage):
        reasons.append(f"coverage_below_threshold:{coverage.get('mature')}<{min_coverage}")
    floor = GRADE_RANK[min_grade]
    below = sorted(g["channel_id"] for g in ledger.get("channels", [])
                   if g["grade"] != "G0" and GRADE_RANK.get(g["grade"], 0) < floor)
    if below:
        reasons.append(f"channels_below_min_grade:{','.join(below[:5])}")
    unproven = sorted(g["channel_id"] for g in ledger.get("channels", [])
                      if GRADE_RANK.get(g["grade"], 0) >= GRADE_RANK["G2"]
                      and not (g.get("detail") or {}).get("generator_sha256"))
    if unproven:
        reasons.append(f"channels_without_provenance:{','.join(unproven[:5])}")
    if freshness and not freshness.get("fresh", True):
        reasons.extend(freshness.get("problems") or ["ledger_stale"])
    return {"passed": not reasons, "min_coverage": min_coverage,
            "min_grade": min_grade, "reasons": reasons}


# ─────────────────────────── 历史 gen_*.py 基线 ───────────────────────────
def legacy_specs(repo_root: str, generator_dir: str = "tools/repro_audit/generators") -> dict:
    """把历史 ``gen_*.py``（"复算桩"）自动登记成生成器规格。

    用途：**同一把尺子**量"改造前"的覆盖度（before），避免两套口径自证改进。
    这些桩多是"简化模型"，重放值对不上工件 → 落在 G1。
    """
    gdir = os.path.join(repo_root, generator_dir)
    generators: list[dict] = []
    if not os.path.isdir(gdir):
        return {"generators": generators}
    for fname in sorted(os.listdir(gdir)):
        if not fname.startswith("gen_") or not fname.endswith(".py"):
            continue
        path = os.path.join(gdir, fname)
        try:
            src = open(path, "r", encoding="utf-8", errors="ignore").read()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        has_generate = any(isinstance(n, ast.FunctionDef) and n.name == "generate"
                           for n in tree.body)
        out = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) \
                    and any(getattr(t, "id", "") == "OUTPUT_PATH" for t in node.targets):
                out = node.value.value
        if not out or not has_generate:
            continue
        generators.append({
            "id": f"legacy:{fname[:-3]}",
            "artifact": out,
            "module": f"{generator_dir}/{fname}",
            "entry": "generate",
            "kind": "producer",
            "claim": "legacy_stub",
            "timeout": 300,
        })
    return {"channel_manifest": "artifacts/repro_audit/channels.json",
            "generators": generators}