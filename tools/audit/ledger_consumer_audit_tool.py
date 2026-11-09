#!/usr/bin/env python3
"""``audit_capabilities`` 工具的台账消费者（消费者 #1）。

历史病根：本工具在 ``ops/tp_worker.py::_cap_sources`` 里手写候选路径表（最早是**单一
硬编码绝对路径**，文件只剩 ``.pyc`` → FileNotFoundError，工具对全部 agent 恒返回
「读取能力清单失败」，证据分级仪整月不可用）。随后补丁把候选表变成「每个消费者各存
一份」，于是四个消费者可能解析到不同文件。m5352 起路径与指纹只来自
:mod:`tools.audit.ledger_paths`（消费者契约门），本模块只负责「按契约取文件 + 渲染」。

对外三个入口：
* :func:`ledger_ref` —— 契约访问器（四消费者之一，返回台账身份 path/sha256/status）；
* :func:`audit_capabilities` —— agent 工具正文（保留历史格式，尾部附契约行）；
* :func:`render` —— 给定台账引用时的纯渲染（降级可读，不抛异常）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402


#: 降级契约：无可读真值来源时，工具**必须**显式返回 UNKNOWN（而不是异常，也不是
#: 看似正常的空表）。证据分级仪的失灵绝不能表现为「能力都正常」。
UNKNOWN_HEADER = ("读取能力清单失败：无可用真值来源 —— 证据等级降级为 UNKNOWN"
                  "（不把任何未证实能力当作已证实）。\n"
                  "[证据等级] UNKNOWN\n"
                  "[降级原因] NO_EVIDENCE_SOURCE\n"
                  "[已尝试]")


def ledger_ref() -> dict:
    """消费者契约访问器：台账身份（path/sha256/status/...）。"""
    return LP.resolve_ledger()


def render_caps(payload: dict) -> str:
    """两种 schema：台账对账结果（capabilities=list）/ 平台 harness 清单（capabilities=dict）。

    与历史实现逐字兼容（agent 侧正文格式是既有回归判据的一部分）。
    """
    caps = payload.get("capabilities")
    lines: list[str] = []
    if isinstance(caps, list):  # 台账对账结果：自述 vs 对账双列，防止把自述当事实
        summ = payload.get("summary") or {}
        tally = summ.get("divergence_tally") or {}
        lines.append(
            f"能力清单 ({len(caps)} 个；证据行 {summ.get('evidence_rows', '?')}；"
            f"分歧 OVERCLAIM {tally.get('OVERCLAIM', '?')} / REVERIFY {tally.get('REVERIFY', '?')} / "
            f"UNVERIFIED {tally.get('UNVERIFIED', '?')} / OK {tally.get('OK', '?')}):"
        )
        for c in sorted(caps, key=lambda x: str(x.get("id"))):
            lines.append(
                f"{str(c.get('id', '?')):26s} 自述 {str(c.get('claimed_level', '?')):13s} "
                f"对账 {str(c.get('reconciled_level', '?')):13s} {str(c.get('divergence', '?')):10s} "
                f"域 {c.get('domain', '?')}"
            )
        gaps = payload.get("unregistered_domains") or []
        if gaps:
            lines.append(f"注册表覆盖缺口 ({len(gaps)} 个): " + "；".join(str(g.get("domain")) for g in gaps))
        if payload.get("violations"):
            lines.append(f"自检违规 {len(payload['violations'])} 项：" + "；".join(str(v) for v in payload["violations"]))
        return "\n".join(lines)
    if isinstance(caps, dict):
        lines.append(f"能力清单 ({len(caps)} 个):")
        for k, v in sorted(caps.items()):
            ev = v.get("evidence", "?") if isinstance(v, dict) else str(v)
            summ = (v.get("summary", "") or "")[:50] if isinstance(v, dict) else ""
            lines.append(f"{k:30s} {ev:15s} {summ}")
        return "\n".join(lines)
    raise ValueError("能力清单 schema 不可识别（缺 capabilities 段）")


def audit_capabilities() -> str:
    """agent 工具正文：按契约候选序取第一份可解析台账，尾部标注契约身份。"""
    ref = ledger_ref()
    tried: list[str] = []
    for cand in LP.render_candidates():
        path: Path = cand["path"]
        if not path.is_file():
            tried.append(f"{path}: (不存在)")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            body = render_caps(payload)
        except Exception as exc:  # 坏文件继续试下一个候选，最终如实报出全部尝试
            tried.append(f"{path}: {type(exc).__name__}: {exc}")
            continue
        head_sha = (ref.get("sha256") or LP.sha256_file(path))[:12]
        contract = (f"[台账契约] status={ref.get('status')} path={ref.get('path_rel')} "
                    f"sha256={head_sha} schema={ref.get('schema_version')} "
                    f"未证实={ref.get('unverified')} OVERCLAIM={ref.get('overclaim')} "
                    f"reasons={','.join(ref.get('reasons') or []) or '-'}")
        return f"{body}\n[真值来源] {path}\n{contract}"
    return (UNKNOWN_HEADER + "\n"
            + ("\n".join(tried) or "(无候选文件存在)"))


def render(ref: dict | None = None) -> str:
    """给定台账引用时的纯渲染（降级也要可读，绝不抛异常）。"""
    ref = ref or ledger_ref()
    if not ref.get("capabilities"):
        return (f"[证据等级] UNKNOWN\n"
                f"能力台账 DEGRADED（reasons={','.join(ref.get('reasons') or [])}；"
                f"path={ref.get('path_rel') or ref.get('path')}）")
    return audit_capabilities()


def main() -> int:
    print(audit_capabilities())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())