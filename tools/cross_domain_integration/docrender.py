"""报告模板渲染: 把 JSON 里的数字注入 markdown (零手抄)。

机制面:
* ``flatten(report)`` 把嵌套 dict 展开成 ``a.b.c`` 键 (列表按下标 ``a.0.b``);
* ``render(template, values)`` 替换 ``{{key}}``; 键缺失即抛 :class:`TemplateError`
  —— 报告模板里写了不存在的量必须炸，不许静默留空 (否则等于手抄);
* 支持 ``{{key|fmt}}`` 数值格式化 (``.3g`` / ``.2f`` / ``.1%``)。

用法::

    python -m tools.cross_domain_integration.docrender \
        --report artifacts/<report>.json --template docs/<tpl>.md --out docs/<doc>.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# 模板常写在 markdown 表格里，管道符会被转义成 ``\|`` → 正则要容忍前导反斜杠
PLACEHOLDER = re.compile(r"\{\{([^{}|\\]+)(?:\\?\|\s*([^{}|]+?))?\}\}")


class TemplateError(ValueError):
    """模板引用了不存在的量。"""


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out[key] = v
            out.update(flatten(v, key))
    elif isinstance(obj, list):
        out[prefix] = obj
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}.{i}"))
    else:
        out[prefix] = obj
    return out


def _format(value: Any, spec: str | None) -> str:
    if spec and isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return format(value, spec)
        except (ValueError, TypeError) as exc:
            raise TemplateError(f"bad format spec {spec!r}: {exc}") from exc
    if value is None:
        return "WITHHELD"
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def render(template: str, values: dict[str, Any]) -> str:
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        if key not in values:
            missing.append(key)
            return m.group(0)
        return _format(values[key], m.group(2))

    out = PLACEHOLDER.sub(sub, template)
    if missing:
        raise TemplateError(f"template references unknown keys: {sorted(set(missing))}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="render report markdown from JSON")
    ap.add_argument("--report", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    template = Path(args.template).read_text(encoding="utf-8")
    try:
        text = render(template, flatten(report))
    except TemplateError as exc:
        print(f"template error: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"rendered {args.out} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())