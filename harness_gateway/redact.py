"""凭据扫描与脱敏。

为什么这是**第一性的门**而不是整洁性偏好：
    harness 层的 daemon 为了免密跑 psql，历史上把生产库口令写成字面量
    （`PGPASSWORD = "..."`）。一旦把这些文件原样镜像进 TensorPlatform 并推送
    GitHub，等于把活口令公开 —— 镜像入库必须先脱敏，且脱敏后仍须能被
    py_compile 编译、被金丝雀执行（所以替换成同长度的合法字符串字面量，
    而不是删行）。

本模块只做纯文本变换，不解析语法：
    scan(text)          -> 发现项（name/line/placeholder），用于门禁拦截
    redact(text)        -> (脱敏文本, 发现项)
脱敏占位符形如 ``<REDACTED:pgpassword_literal>``，保持字符串字面量语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# (名称, 正则)。约定：若正则含命名组 value，只替换 value；否则整体匹配即敏感值。
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 1) 字面量直赋：PGENV = {"PGPASSWORD": "..."}
    (
        "pgpassword_literal",
        re.compile(r'(?P<prefix>PGPASSWORD"?\s*[:=]\s*")(?P<value>(?!<REDACTED:)[^"\n]{4,})(?P<suffix>")'),
    ),
    # 2) env 兜底缺省值：os.environ.get("TP_PG_PASSWORD", "...") —— 同样是把活口令写进源码
    (
        "env_default_secret",
        re.compile(
            r'(?P<prefix>[A-Za-z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY)[A-Za-z0-9_]*"?\s*,\s*")'
            r'(?P<value>(?!<REDACTED:)[^"\n]{8,})(?P<suffix>")',
            re.IGNORECASE,
        ),
    ),
    # 3) 通用 kv：password / token / api_key ...
    (
        "password_kv",
        re.compile(
            r'(?P<prefix>"?(?:password|passwd|secret|api_key|apikey|access_token|LLM_API_KEY)"?\s*[:=]\s*")'
            r'(?P<value>(?!<REDACTED:)[^"\n]{6,})(?P<suffix>")',
            re.IGNORECASE,
        ),
    ),
    # 4) HTTP 头与厂商令牌格式
    ("bearer_token", re.compile(r'(?P<prefix>Bearer\s+)(?P<value>(?!<REDACTED:)[A-Za-z0-9._\-]{16,})')),
    ("openai_style_key", re.compile(r'(?P<value>(?!<REDACTED:)\bsk-[A-Za-z0-9_\-]{16,})')),
    ("github_pat", re.compile(r'(?P<value>(?!<REDACTED:)\bghp_[A-Za-z0-9]{20,})')),
)

#: 脱敏后文件中允许出现的占位符前缀；secret 门据此区分"真凭据"与"已脱敏标记"。
PLACEHOLDER_PREFIX = "<REDACTED:"


@dataclass
class Finding:
    pattern: str
    line: int
    placeholder: str
    preview: str  # 只保留前 4 字符，绝不回显完整凭据

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "line": self.line,
            "placeholder": self.placeholder,
            "preview": self.preview,
        }


@dataclass
class RedactionReport:
    findings: list[Finding] = field(default_factory=list)
    changed: bool = False

    @property
    def count(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "changed": self.changed,
            "findings": [f.to_dict() for f in self.findings],
        }


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan(text: str) -> list[Finding]:
    """扫描文本中的明文凭据（不修改文本）。"""
    findings: list[Finding] = []
    for name, pat in PATTERNS:
        for m in pat.finditer(text):
            value = m.group("value") if "value" in (m.groupdict() or {}) else m.group(0)
            preview = value[:2] + "***"
            placeholder = ""
            if "prefix" in (m.groupdict() or {}):
                placeholder = f"{m.group('prefix')}{PLACEHOLDER_PREFIX}{name}>"
            findings.append(
                Finding(pattern=name, line=_line_of(text, m.start()), placeholder=placeholder, preview=preview)
            )
    findings.sort(key=lambda f: f.line)
    return findings


def redact(text: str) -> tuple[str, RedactionReport]:
    """把明文凭据替换为 ``<REDACTED:pattern>`` 占位符。"""
    report = RedactionReport()
    out = text
    for name, pat in PATTERNS:
        def _sub(m: re.Match[str], _name: str = name) -> str:
            gd = m.groupdict()
            if "prefix" in gd:
                report.findings.append(
                    Finding(
                        pattern=_name,
                        line=_line_of(text, m.start()),
                        placeholder=f"{m.group('prefix')}{PLACEHOLDER_PREFIX}{_name}>",
                        preview=(m.group("value")[:2] + "***"),
                    )
                )
                return f"{m.group('prefix')}{PLACEHOLDER_PREFIX}{_name}>{m.group('suffix')}"
            report.findings.append(
                Finding(
                    pattern=_name,
                    line=_line_of(text, m.start()),
                    placeholder=f"{PLACEHOLDER_PREFIX}{_name}>",
                    preview=(m.group(0)[:2] + "***"),
                )
            )
            return f"{PLACEHOLDER_PREFIX}{_name}>"

        out = pat.sub(_sub, out)
    report.findings.sort(key=lambda f: f.line)
    report.changed = out != text
    return out, report


def assert_clean(text: str) -> list[Finding]:
    """返回仍存在的明文凭据发现项（用于门禁 FAIL 判据）。"""
    return scan(text)