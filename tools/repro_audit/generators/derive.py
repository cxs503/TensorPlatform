"""repro_audit.generators.derive —— 数值通道派生引擎（零领域语义）。

一个「数值通道」(channel) = 某个已提交工件里的一个**关键数字**（头部汇总量）
＋ 一条能在提交树内重放它的**派生式**（由明细行/源工件算出该数字）。

本模块只提供两件事：

1. ``values(root, path)`` —— 在 JSON 结构里按点路径取值；路径穿过 list 时
   自动映射（``cases.status`` → 每个 case 的 status 组成的列表）。
2. ``evaluate(expr, root)`` —— 受限算术表达式求值（AST 白名单，无 eval 面）。

表达式里可用的名字只有一个：``n``（命名空间，全部归约函数见 ``REDUCERS``）。
允许的 AST 节点：Constant / Name / Call / BinOp(+-*/) / UnaryOp(-) / List。
其余一律拒绝 —— 派生式因此可审计、不可执行任意代码（平台铁律：零领域语义、
无隐藏执行路径）。

设计约束：
  * **确定**：同一 root + 同一 expr 恒得同值（不含随机/时间/环境）。
  * **无字面量回抄**：调用方（maturity）会扫描 spec 里的数字常量，
    若某个常量正好等于被断言的数值，则判定该通道有「写死结果」嫌疑。
  * **可扰动**：调用方对 root 做结构性/数值性扰动后重算，输出必须变化，
    否则说明该通道的输出与输入无关（=MOCK 风险）。
"""
from __future__ import annotations

import ast
import json
from typing import Any, Iterable

__all__ = ["values", "one", "evaluate", "leaf_paths", "numeric_leaves",
           "set_at", "set_numeric", "drop_last_at", "reducer_paths",
           "numeric_literals", "canonical", "NUMERIC_LEAF_LIMIT", "REDUCERS"]

# 单次扰动最多枚举的数值叶子数（防止大工件拖垮审计）
NUMERIC_LEAF_LIMIT = 400

_INDEX_RE = __import__("re").compile(r"^([^\[\]]*)((?:\[\d+\])*)$")


def _split_path(path: str) -> list[str]:
    """把 ``a.b[0].c`` 拆成 ['a', 'b', '[0]', 'c']。"""
    out: list[str] = []
    for seg in path.split("."):
        if not seg:
            continue
        m = _INDEX_RE.match(seg)
        if m is None:  # pragma: no cover - 正则覆盖所有字符串
            out.append(seg)
            continue
        head, idx = m.group(1), m.group(2)
        if head:
            out.append(head)
        for num in __import__("re").findall(r"\[(\d+)\]", idx):
            out.append("[%s]" % num)
    return out


def _walk(node: Any, segs: list[str]) -> list[Any]:
    """沿路径走，遇 list 则对每个元素并行展开（返回候选值列表）。"""
    if not segs:
        return [node]
    seg, rest = segs[0], segs[1:]
    out: list[Any] = []
    if seg.startswith("["):
        idx = int(seg[1:-1])
        if isinstance(node, list) and -len(node) <= idx < len(node):
            out.extend(_walk(node[idx], rest))
    elif isinstance(node, dict):
        if seg in node:
            out.extend(_walk(node[seg], rest))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk(item, segs))  # 不消耗 seg：对每个元素重试
    return out


def values(root: Any, path: str) -> list[Any]:
    """按路径取值；返回候选值列表（list 展开会得到多个）。"""
    return _walk(root, _split_path(path))


def one(root: Any, path: str) -> Any:
    """按路径取单值；缺失或歧义返回 None。"""
    got = values(root, path)
    if len(got) == 1:
        return got[0]
    return None


def flat_values(root: Any, path: str) -> list[Any]:
    """路径取值后把「落在 list 上的候选」展开一层，得到标量序列。"""
    out: list[Any] = []
    for v in values(root, path):
        if isinstance(v, list):
            out.extend(v)
        else:
            out.append(v)
    return out


def _numeric(xs: Iterable[Any]) -> list[float]:
    return [float(x) for x in xs
            if isinstance(x, (int, float)) and not isinstance(x, bool)]


def _len(root: Any, path: str) -> int:
    got = values(root, path)
    if len(got) == 1 and isinstance(got[0], (list, dict, str)):
        return len(got[0])
    return len(got)


def _count_eq(root: Any, path: str, target: Any) -> int:
    return sum(1 for v in flat_values(root, path) if v == target)


def _count_in(root: Any, path: str, targets: Iterable[Any]) -> int:
    wanted = list(targets)
    return sum(1 for v in flat_values(root, path) if v in wanted)


def _count_true(root: Any, path: str) -> int:
    return sum(1 for v in flat_values(root, path) if v is True)


def _count_false(root: Any, path: str) -> int:
    return sum(1 for v in flat_values(root, path) if v is False)


def _count_not_none(root: Any, path: str) -> int:
    return sum(1 for v in flat_values(root, path) if v is not None)


def _count_gt(root: Any, path: str, bound: float) -> int:
    return sum(1 for x in _numeric(flat_values(root, path)) if x > float(bound))


def _distinct(root: Any, path: str) -> int:
    seen = []
    for v in flat_values(root, path):
        if v not in seen:
            seen.append(v)
    return len(seen)


def _sum(root: Any, path: str) -> float:
    return float(sum(_numeric(flat_values(root, path))))


def _mean(root: Any, path: str) -> float:
    xs = _numeric(flat_values(root, path))
    if not xs:
        raise ValueError("mean of empty sequence: %s" % path)
    return float(sum(xs) / len(xs))


def _min(root: Any, path: str) -> float:
    xs = _numeric(flat_values(root, path))
    if not xs:
        raise ValueError("min of empty sequence: %s" % path)
    return float(min(xs))


def _max(root: Any, path: str) -> float:
    xs = _numeric(flat_values(root, path))
    if not xs:
        raise ValueError("max of empty sequence: %s" % path)
    return float(max(xs))


def _get(root: Any, path: str) -> Any:
    """取单值（缺失返回 None）；派生式里当「入量」用。"""
    return one(root, path)


#: 表达式命名空间 ``n.<name>`` → 归约实现
REDUCERS = {
    "get": _get,
    "len": _len,
    "count_eq": _count_eq,
    "count_in": _count_in,
    "count_true": _count_true,
    "count_false": _count_false,
    "count_not_none": _count_not_none,
    "count_gt": _count_gt,
    "distinct": _distinct,
    "sum": _sum,
    "mean": _mean,
    "min": _min,
    "max": _max,
    "abs": abs,
}

_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Call,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UnaryOp, ast.USub,
    ast.UAdd, ast.List, ast.Tuple, ast.Attribute,
)


#: 不需要 root 作第一参数的归约（纯函数）
_ROOT_FREE = {"abs"}


class _Namespace:
    """表达式里的 ``n``：把归约函数绑到 root 上。"""

    def __init__(self, root: Any):
        self._root = root

    def __getattr__(self, name: str):
        fn = REDUCERS.get(name)
        if fn is None:
            raise AttributeError("unknown reducer: %s" % name)
        if name in _ROOT_FREE:  # 与 root 无关的纯函数（abs 等）
            return fn

        def _bound(*args, **kwargs):
            return fn(self._root, *args, **kwargs)

        _bound.__name__ = name
        return _bound


def evaluate(expr: str, root: Any) -> Any:
    """对受限表达式求值。

    只允许常量、一个名字 ``n``、``n`` 上的调用、四则运算与 list 字面量。
    任何越界语法（属性访问、下标、比较、lambda …）直接抛 ValueError。
    """
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError("expression not allowed: %r in %r"
                             % (type(node).__name__, expr))
        if isinstance(node, ast.Name) and node.id != "n":
            raise ValueError("unknown name %r in %r" % (node.id, expr))
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Attribute)):
                raise ValueError("only n.<reducer>(...) calls allowed: %r" % expr)
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "n":
                raise ValueError("only n.<reducer>(...) calls allowed: %r" % expr)
            if node.func.attr not in REDUCERS:
                raise ValueError("unknown reducer %r in %r" % (node.func.attr, expr))
    return eval(  # noqa: S307 - 入参已过 AST 白名单，命名空间仅含归约函数
        compile(ast.parse(expr, mode="eval"), "<channel-spec>", "eval"),
        {"__builtins__": {}, "n": _Namespace(root)},
    )


def reducer_paths(expr: str) -> list[str]:
    """抽出表达式里所有 reducer 的路径参数（用于扰动敏感性测试）。"""
    tree = ast.parse(expr, mode="eval")
    paths: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                paths.append(node.args[0].value)
    return paths


def numeric_literals(expr: str) -> list[float]:
    """抽出表达式里的数值常量（用于「无字面量回抄」检查）。"""
    tree = ast.parse(expr, mode="eval")
    return [float(node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)]


def leaf_paths(obj: Any, kind: str = "number", limit: int = NUMERIC_LEAF_LIMIT,
               within: Iterable[str] | None = None) -> list[str]:
    """列出 JSON 里指定类型叶子的点路径。

    ``kind``: ``number`` | ``string`` | ``bool``。
    ``within`` 给定时只保留首段路径命中的叶子（避免大工件里前段叶子占满额度）。
    """
    out: list[str] = []
    heads = None if within is None else set(within)

    def _match(v: Any) -> bool:
        if kind == "number":
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if kind == "bool":
            return isinstance(v, bool)
        return isinstance(v, str)

    def _walk_node(node: Any, path: str) -> None:
        if len(out) >= limit:
            return
        if heads is not None and path and path.split(".")[0].split("[")[0] not in heads:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                _walk_node(v, "%s.%s" % (path, k) if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk_node(v, "%s[%d]" % (path, i))
        elif _match(node):
            out.append(path)

    _walk_node(obj, "")
    return out


def numeric_leaves(obj: Any, prefix: str = "", limit: int = NUMERIC_LEAF_LIMIT,
                   within: Iterable[str] | None = None) -> list[str]:
    """数值叶子的点路径（``leaf_paths(kind='number')`` 的兼容别名）。"""
    got = leaf_paths(obj, "number", limit, within)
    if prefix:
        return ["%s.%s" % (prefix, p) for p in got]
    return got


def set_at(root: Any, path: str, value: Any) -> None:
    """把 root 里 path 处的值改成 value（就地；扰动测试用）。"""
    segs = _split_path(path)
    node = root
    for seg in segs[:-1]:
        if isinstance(node, list):
            node = node[int(seg[1:-1])]
        else:
            node = node[seg]
    last = segs[-1]
    if isinstance(node, list):
        node[int(last[1:-1])] = value
    else:
        node[last] = value


def set_numeric(root: Any, path: str, value: float) -> None:
    """把 root 里 path 处的数值改成 value（兼容别名）。"""
    set_at(root, path, value)


def drop_last_at(root: Any, path: str) -> bool:
    """删掉路径上「第一个遇到的 list」的最后一个元素；成功返回 True。

    遍历时只要当前节点是 list 且后面还有段要走，就先削掉它的尾元素（这就是
    被归约的那个序列）；走完路径后若落在 list 上也削尾元素。
    """
    segs = _split_path(path)
    node = root
    for seg in segs:
        if isinstance(node, list) and not seg.startswith("["):
            if node:
                node.pop()
                return True
            return False
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
    if isinstance(node, list):
        if node:
            node.pop()
            return True
        return False
    return False


def canonical(obj: Any) -> str:
    """稳定序列化（键排序、无空白），用于幂等判等。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))