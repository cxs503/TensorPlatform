"""严格数值解析器（T6 纪律：禁宽正则，只做边界明确的字符扫描）。

为什么不用正则
--------------
报告里的数字后面常跟单位、括号注释、区间、不确定带（``−21.5%（落在参考不确定带内）``、
``6 009``、``±0.07``、``4.200/4.000``、``95%CI 半宽 ±0.07``）。宽正则（如
``[-+]?\\d*\\.?\\d+``）会把这些上下文一起吞掉，把「判据数字」误读成「实测值」——历史
上正是这类误读造成误差表错配。这里改为**显式状态机**：

* 只从清洗后字符串的**开头**取一个数（``scan_leading_number``）；
* 整串必须**完全**消耗才算「纯数字单元」（``parse_number_cell``），否则返回 ``None``，
  由调用方决定是不采信还是用前缀值 + 注释文本；
* 千分位（普通空格 / NBSP / 窄空格 / 逗号）与全角字符先归一；
* 科学计数法、``±`` 前缀、Unicode 负号（U+2212）显式支持。

百分比标定（``scale``）
-----------------------
返回 ``ParsedNumber(value, is_percent, scale_assumed)``：

* 带 ``%`` → 值即百分数，``is_percent=True``；
* 无 ``%`` 且 ``|v| <= 0.2`` → 按**比值**处理（× 100），``scale_assumed=True`` 留痕；
* 否则按「已经是百分数」处理。

``0.2`` 这个阈值是显式假设（报告用比值写偏差时数值总在 O(0.01–0.2) 量级；用百分数
写时小于 0.2% 的读数会带 ``%``），单测里钉死并在报告「局限」节声明。
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass

_PLACEHOLDERS = {
    "",
    "-",
    "--",
    "—",
    "–",
    "/",
    "?",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "nil",
    "未知",
    "无",
    "未测",
    "—（无基准）",
}
_SCALE_ASSUME_MAX = 0.2


@dataclass(frozen=True)
class ParsedNumber:
    value: float
    raw: str
    is_percent: bool = False
    scale_assumed: bool = False
    rest: str = ""

    @property
    def as_percent(self) -> float:
        """以百分数为单位的值（比值自动 ×100）。"""
        return self.value * 100.0 if not self.is_percent else self.value

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "raw": self.raw,
            "is_percent": self.is_percent,
            "scale_assumed": self.scale_assumed,
        }


def clean_cell(cell: str) -> str:
    """归一化单元格：全角→半角、去 markdown 强调/反引号/强调星号、压空白。"""
    s = unicodedata.normalize("NFKC", cell or "")
    s = s.replace("\u2212", "-")  # Unicode minus
    s = s.replace("\u2013", "-").replace("\u2014", "—")
    for token in ("**", "__", "`", "~~"):
        s = s.replace(token, "")
    s = s.replace("*", "")
    # 千分位与窄空格
    for sp in ("\u00a0", "\u2009", "\u202f", "\u2007"):
        s = s.replace(sp, " ")
    s = s.replace("\u3000", " ")
    return " ".join(s.split()).strip()


def _strip_thousands(s: str) -> str:
    """``6 009`` / ``1,234.5`` → ``6009`` / ``1234.5``；只在数字内部去分隔符。"""
    out: list[str] = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch in " ,":
            prev_digit = i > 0 and s[i - 1].isdigit()
            next_digit = i + 1 < n and s[i + 1].isdigit()
            if prev_digit and next_digit:
                continue
        out.append(ch)
    return "".join(out)


def _scan_number_prefix(text: str) -> tuple[float, str, bool, bool] | None:
    """从开头扫一个数，返回 (value, rest, is_percent, had_pm)。

    支持的形状：``-12.5`` ``+3`` ``±0.07`` ``≈4.2e-6`` ``.5`` ``12`` ``1_000``（不认下划线）。
    """
    s = text.lstrip()
    if not s:
        return None
    had_pm = False
    neg = False
    while s and s[0] in "+-±≈~<>＝=":  # 允许的数值前缀（比较符按「下界」处理）
        ch = s[0]
        if ch == "-":
            neg = not neg
        elif ch in "±≈":
            had_pm = True
        s = s[1:].lstrip()
    start = 0
    digits = 0
    seen_dot = False
    exp = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit():
            digits += 1
            i += 1
        elif ch == "." and not seen_dot and not exp:
            seen_dot = True
            i += 1
        elif ch in "eE" and digits and not exp:
            # 指数部分必须紧跟数字或符号
            j = i + 1
            if j < len(s) and s[j] in "+-":
                j += 1
            if j < len(s) and s[j].isdigit():
                exp = True
                i = j
            else:
                break
        elif ch in " ," and not exp and i + 1 < len(s) and s[i + 1].isdigit():
            i += 1  # 千分位
        else:
            break
    token = s[start:i]
    if not digits:
        return None
    rest = s[i:].strip()
    is_percent = rest.startswith("%")
    if is_percent:
        rest = rest[1:].strip()
    try:
        value = float(_strip_thousands(token))
    except ValueError:  # pragma: no cover - 状态机已保证可解析
        return None
    if neg:
        value = -value
    return value, rest, is_percent, had_pm


def scan_leading_number(cell: str) -> ParsedNumber | None:
    """取单元格**开头**的数字，允许尾部带注释文本（``rest``）。"""
    cleaned = clean_cell(cell)
    if cleaned.casefold() in _PLACEHOLDERS:
        return None
    scanned = _scan_number_prefix(cleaned)
    if scanned is None:
        return None
    value, rest, is_percent, had_pm = scanned
    if math.isnan(value) or math.isinf(value):
        return None
    scale_assumed = False
    # 只有**纯数字单元格**才做比值→百分数标定；带尾部注释的单元格按字面百分数处理
    # （``2/2 完整阶梯严格单调``、``0.169（区间 [..]）`` 这类若强行 ×100 会凭空造出 200% 误差）。
    if not is_percent and not rest.strip() and abs(value) <= _SCALE_ASSUME_MAX:
        value = value * 100.0
        is_percent = True
        scale_assumed = True
    elif not is_percent:
        # 无 ``%`` 且量级已 >0.2：按**字面百分数**处理（``10.526`` = 10.526%），
        # 否则 as_percent 会二次 ×100，把 10.5% 读成 1052%（曾经的真实 bug）。
        is_percent = True
    return ParsedNumber(
        value=value,
        raw=cleaned,
        is_percent=is_percent,
        scale_assumed=scale_assumed,
        rest=rest,
    )


def parse_number_cell(cell: str) -> ParsedNumber | None:
    """整串必须是**纯数字单元**才采信（尾部注释、区间、比值一律判非纯数字）。"""
    parsed = scan_leading_number(cell)
    if parsed is None:
        return None
    tail = parsed.rest.strip(" 。.,;；，")
    if tail:
        return None
    return parsed


def parse_percent_cell(cell: str, *, strict: bool = False) -> ParsedNumber | None:
    """误差列专用：默认允许尾部注释（但仍在同一单元格内，值取前缀）。"""
    return parse_number_cell(cell) if strict else scan_leading_number(cell)


def parse_interval(text: str) -> tuple[float, float] | None:
    """解析 ``[14.47, 36.23]`` / ``23.4–35.2`` 形式的区间（用于识别不确定带）。"""
    cleaned = clean_cell(text)
    for opener, closer in (("[", "]"), ("(", ")"), ("（", "）"), ("【", "】")):
        if opener in cleaned and closer in cleaned:
            body = cleaned.split(opener, 1)[1].split(closer, 1)[0]
            parts = [p for p in body.replace("，", ",").split(",") if p.strip()]
            nums = [parse_number_cell(p) for p in parts]
            if len(nums) >= 2 and all(n is not None for n in nums):
                return float(nums[0].value), float(nums[1].value)
    for sep in ("–", "~", "至", "—"):
        if sep in cleaned:
            left, right = cleaned.split(sep, 1)
            a, b = parse_number_cell(left), parse_number_cell(right)
            if a is not None and b is not None:
                return float(a.value), float(b.value)
    return None