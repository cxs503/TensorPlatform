#!/usr/bin/env python3
"""判据变异台账（criteria mutation ledger）：把「判据可证伪性」从单门抽样升级为
全台账 KPI + 门禁。

问题
----
平台的验收门（PAPER-TRACE 溯源门、REPORT-QUALITY 六要素门、REPORT-OWNERSHIP
归属/魔数门、CLIENT-PAYLOAD 工装自证门）各自都有单测，但**没有一把统一的尺子**
回答同一个问题：这些判据在被喂进「看起来合规、其实违规」的样本时，会不会亮红灯？
一条只会 ``return False``（恒放行）的判据与一条真判据在测试里完全同构（都绿），
这正是 P34-N7 要杀的失效模式：**判据不可证伪 = 门不存在**。

做法（变异驱动的可证伪性测量）
------------------------------
对台账里每条判据，按五类变异体（mutant）构造**应当被拒**的样本，逐条看判据是否
亮红灯（= kill）。kill rate 低不是「样本不够坏」，而是判据的盲区。
亮红灯判据返回 ``(True, 证据)``；放行返回 ``(False, 证据)``。

===========  ========================================================
类           变异语义
===========  ========================================================
M1 数值扰动  把判据依赖的数值按 ε∈{1%, 3%, 15%} 放大（单调性：ε↑ ⇒ kill 数非降）
M2 单位错位  同一个量换单位/换容器（m→mm、PNG→JPEG、字节→千字节），数值「看起来对」
M3 常数替身  把真实计算量替换成常数（常数永远自洽 = 恒真样本的最爱）
M4 样本伪装  结构合规、内容错位（列互换、空 caption、把别的段落搬进本段）
M5 基准替换  把基准/参考/身份换成另一份（别的船的基线、别的 token、别的单号）
===========  ========================================================

产物
----
* ``build_ledger()`` → 逐判据台账（每条 mutant 的 killed/未杀证据）；
* ``kpi(ledger)`` → 逐门 kill-rate、最弱 mutant 清单、不可证伪判据清单、
  M1 单调性违例数、覆盖面（门数 / 判据数 / 可实例化率）；
* ``pr_gate(...)`` → 新判据准入：**没有 killer test 的新判据一律拒**（exit 2）。

用法
----
    python3 tools/audit/criteria_mutation.py ledger --json-out artifacts/m5496/criteria_mutation_ledger.json
    python3 tools/audit/criteria_mutation.py kpi
    python3 tools/audit/criteria_mutation.py gate --new-cids RO-MISSION --run-killer-tests

killer test 的真伪由**破坏性验证**裁定：把该判据经环境变量
``CRITERIA_MUTATION_DISABLE`` 打成恒放行，对应的 killer test 必须转红。
绿着的「杀手」不算杀手。
"""

from __future__ import annotations

import argparse
import ast
import base64
import importlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(ROOT), str(ROOT / "tools" / "paper_gate")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: 环境变量：把指定判据打成恒放行（killer test 破坏性验证用；``*`` = 全打）。
DISABLE_ENV = "CRITERIA_MUTATION_DISABLE"
EPSILONS: tuple[float, ...] = (0.01, 0.03, 0.15)
CONTROL_GATE = "NEGATIVE-CONTROL"
KILL_RATE_FLOOR = 0.90           # 门禁线：单条判据 ≥90% 才算「可证伪"

MUTANT_CLASSES: dict[str, str] = {
    "M1": "数值扰动 ε∈{1%,3%,15%}",
    "M2": "单位/量纲/容器错位",
    "M3": "常数替身",
    "M4": "样本伪装",
    "M5": "基准替换",
}


def _disabled(cid: str) -> bool:
    ids = {x.strip() for x in os.environ.get(DISABLE_ENV, "").split(",") if x.strip()}
    return cid in ids or "*" in ids


@dataclass
class Criterion:
    """一条判据 = 一个可执行的「违规检测器」+ 一组应当被它拒绝的变异样本。"""

    cid: str
    gate: str
    statement: str                             # 判据内容（人读，取自真实门的判据定义）
    source: str                                # 实现出处 module:function
    check: Callable[[dict], tuple[bool, str]]  # sample -> (是否亮红灯, 证据)
    base: Callable[[], dict]                   # 基准样本（合规，判据必须放行）
    mutate: dict[str, Callable[[float | None], dict]]   # 类 -> (ε) -> 违规样本
    killer_tests: tuple[str, ...] = ()
    numeric_knob: bool = False                 # 是否适用 M1（无连续量的判据记 N/A）
    control: bool = False                      # 负控（恒真判据）
    canonical: str = "M3"                      # 该判据的旗舰违规样本（killer test 用）


# ══════════════════════════════════════════════════════════════════════════
# 门 1：PAPER-TRACE（稿件溯源门 tools/paper_gate/traceability_gate.py）
# ══════════════════════════════════════════════════════════════════════════
def _pg():
    return importlib.import_module("traceability_gate")


PG4_COMPUTED = 1.6666666666666667      # (0.0427-0.0420)/0.0420*100


def _pg4_table(stated_err: float, ref: float, val: float) -> str:
    return ("# 结果\n\n| 量 | 参考 | 数值 | 误差% |\n|---|---|---|---|\n"
            f"| C_T | {ref:.6f} | {val:.6f} | {stated_err:.6f} |\n")


def _pg4_check(sample: dict) -> tuple[bool, str]:
    if _disabled("PT-PG4"):
        return False, "disabled"
    pg = _pg()
    hits = 0
    for header, rows, line_no in pg.iter_tables(sample["markdown"]):
        f, _ = pg.check_table_arithmetic(header, rows, line_no, 0.05, 0.005)
        hits += len(f)
    return hits > 0, f"{hits} 处误差列与值/参考不平"


def _pg5_inline(stated: float, a: float = 0.0427, b: float = 0.0420) -> str:
    return f"# 结果\n\n升力系数 {a:.4f} vs {b:.4f}，误差 {stated:.4f}%。\n"


def _pg5_check(sample: dict) -> tuple[bool, str]:
    if _disabled("PT-PG5"):
        return False, "disabled"
    pg = _pg()
    f, _ = pg.check_inline_errors(sample["markdown"], 0.05, 0.005)
    return bool(f), f"{len(f)} 处行内误差复算不上"


def _pg7_md(pointer: str) -> str:
    return ("# 结果\n\n| 量 | 参考 | 数值 | 误差% |\n|---|---|---|---|\n"
            "| C_T | 0.0420 | 0.0427 | 1.6667 |\n\n"
            f"数据出处：{pointer}\n")


def _pg7_check(sample: dict) -> tuple[bool, str]:
    if _disabled("PT-PG7"):
        return False, "disabled"
    pg = _pg()
    out = pg.check_manuscript(sample["markdown"], artifact_roots=[ROOT])
    findings = [f for f in out["findings"] if str(f.get("code", "")).startswith("PG7")]
    return bool(findings), f"{len(findings)} 处缺工件级出处"


def _paper_trace_criteria() -> list[Criterion]:
    return [
        Criterion(
            cid="PT-PG4", gate="PAPER-TRACE",
            statement="PG4 表内误差列必须由「数值/参考」复算得出（容差 0.05pp + 0.5%）",
            source="tools/paper_gate/traceability_gate.py:check_table_arithmetic",
            check=_pg4_check,
            base=lambda: {"markdown": _pg4_table(PG4_COMPUTED, 0.0420, 0.0427)},
            mutate={
                "M1": lambda eps: {"markdown": _pg4_table(PG4_COMPUTED * (1 + eps), 0.0420, 0.0427)},
                "M2": lambda eps: {"markdown": _pg4_table(PG4_COMPUTED, 42.0, 0.0427)},   # 参考换成 mm
                "M3": lambda eps: {"markdown": _pg4_table(PG4_COMPUTED, 0.0427, 0.0427)},  # 常数替身
                "M4": lambda eps: {"markdown": _pg4_table(PG4_COMPUTED, 0.0427, 0.0420)},  # 值/参考互换
                "M5": lambda eps: {"markdown": _pg4_table(PG4_COMPUTED, 0.0500, 0.0427)},  # 别的基线
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[PT-PG4]",),
            numeric_knob=True,
        ),
        Criterion(
            cid="PT-PG5", gate="PAPER-TRACE",
            statement="PG5 正文「A vs B，误差 Z%」必须能复算（H2/H3 行内数字）",
            source="tools/paper_gate/traceability_gate.py:check_inline_errors",
            check=_pg5_check,
            base=lambda: {"markdown": _pg5_inline(PG4_COMPUTED)},
            mutate={
                "M1": lambda eps: {"markdown": _pg5_inline(PG4_COMPUTED * (1 + eps))},
                "M2": lambda eps: {"markdown": _pg5_inline(1.6667, 42.7, 42.0)},          # ×1000 量纲
                "M3": lambda eps: {"markdown": _pg5_inline(0.0)},                         # 「误差 0%」
                "M4": lambda eps: {"markdown": _pg5_inline(PG4_COMPUTED, 0.0420, 0.0427)},  # 伪装成升序
                "M5": lambda eps: {"markdown": _pg5_inline(PG4_COMPUTED, 0.0427, 0.0500)},  # 别的基线
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[PT-PG5]",),
            numeric_knob=True,
        ),
        Criterion(
            cid="PT-PG7", gate="PAPER-TRACE",
            statement="PG7 带数字的结果表必须给出工件级出处（path#pointer）",
            source="tools/paper_gate/traceability_gate.py:artifact_provenance",
            check=_pg7_check,
            base=lambda: {"markdown": _pg7_md("`artifacts/m5496/criteria_mutation_ledger.json`")},
            mutate={
                "M2": lambda eps: {"markdown": _pg7_md("`artifacts/m5496/ledger.JSON#L1`")},
                "M3": lambda eps: {"markdown": _pg7_md("（常数：见上表）")},
                "M4": lambda eps: {"markdown": _pg7_md("`见附件`")},
                "M5": lambda eps: {"markdown": _pg7_md("`artifacts/m1994/audit_v1.json#kpi`")},
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[PT-PG7]",),
            numeric_knob=False,
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════
# 门 2：REPORT-QUALITY（论文级六要素门 backend/services/report_gate.py）
# ══════════════════════════════════════════════════════════════════════════
PHYSICS_LINE = "物理背景：KCS 集装箱船静水阻力工况，几何 L = 100 m, B = 20 m, T = 8 m, Cb = 0.6。"
VALIDATION_LINE = "验证：与 ITTC-1957 基线对标，误差 1.67%。"
COMPLIANT_MD = (
    "# 结果\n"
    + PHYSICS_LINE + "\n"
    "方法：pytest 复算 + 变异驱动，工具链 tools/audit/criteria_mutation.py。\n"
    + VALIDATION_LINE + "\n"
    "误差判定：关键量 vs ITTC-1957 基线误差 1.67%，是否 <3%：是。\n"
    "局限：单点排水量，未覆盖浅水。\n"
    "数据出处：`artifacts/m5496/criteria_mutation_ledger.json`。\n"
)
COMPLIANT_FIGS = ({"name": "fig1_killrate.png", "caption": "逐门 kill rate 对比"},)


def _rq_issues(sample: dict) -> list[str]:
    rq = importlib.import_module("services.report_gate")
    out = rq.check_report_quality(sample["markdown"], sample.get("figures") or [])
    return list(out.get("issues") or [])


def _rq_criterion(cid: str, issue: str, statement: str, mutate: dict,
                  canonical: str = "M3") -> Criterion:
    def _check(sample: dict) -> tuple[bool, str]:
        if _disabled(cid):
            return False, "disabled"
        issues = _rq_issues(sample)
        # 门的 issues 是「code: 人读信息」形式，按 code 前缀匹配（不用宽正则）
        codes = [i.split(":", 1)[0].strip() for i in issues]
        return issue in codes, f"codes={codes}"

    return Criterion(
        cid=cid, gate="REPORT-QUALITY", statement=statement,
        source="backend/services/report_gate.py:check_report_quality",
        check=_check,
        base=lambda: {"markdown": COMPLIANT_MD, "figures": list(COMPLIANT_FIGS)},
        mutate=mutate,
        killer_tests=(f"tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[{cid}]",),
        numeric_knob=False,
        canonical=canonical,
    )


def _report_quality_criteria() -> list[Criterion]:
    return [
        _rq_criterion(
            "RQ-PHYSICS", "missing_physics_statement",
            "六要素①：物理/案例介绍必须给问题背景 **且** 几何或参数",
            {
                "M2": lambda eps: {"markdown": COMPLIANT_MD.replace(
                    "L = 100 m, B = 20 m, T = 8 m", "L = 100, B = 20, T = 8"),
                    "figures": list(COMPLIANT_FIGS)},
                "M3": lambda eps: {"markdown": "参数表\n" + COMPLIANT_MD.split("。", 1)[1],
                                   "figures": list(COMPLIANT_FIGS)},
                "M4": lambda eps: {"markdown": COMPLIANT_MD.replace(PHYSICS_LINE, VALIDATION_LINE),
                                   "figures": list(COMPLIANT_FIGS)},
                "M5": lambda eps: {"markdown": COMPLIANT_MD.replace(
                    "L = 100 m, B = 20 m, T = 8 m, Cb = 0.6",
                    "L = 230 m, B = 32 m, T = 11 m, Cb = 0.82"),
                    "figures": list(COMPLIANT_FIGS)},
            },
        ),
        _rq_criterion(
            "RQ-VALIDATION", "missing_validation",
            "六要素③：必须有对标/验证入口（基准、文献、解析解之一）",
            {
                "M2": lambda eps: {"markdown": COMPLIANT_MD.replace("误差 1.67%", "误差 16.7‰"),
                                   "figures": list(COMPLIANT_FIGS)},
                "M3": lambda eps: {"markdown": COMPLIANT_MD.replace(
                    VALIDATION_LINE, "与常数 1.67 一致。"), "figures": list(COMPLIANT_FIGS)},
                "M4": lambda eps: {"markdown": COMPLIANT_MD.replace(
                    VALIDATION_LINE, "已核对一致。"), "figures": list(COMPLIANT_FIGS)},
                "M5": lambda eps: {"markdown": COMPLIANT_MD.replace(
                    "与 ITTC-1957 基线对标", "与《基线 v0》对标"), "figures": list(COMPLIANT_FIGS)},
            },
            canonical="M4",
        ),
        _rq_criterion(
            "RQ-FIGURES", "missing_figures",
            "六要素④：必须有真图（带 caption）或显式无图理由",
            {
                "M2": lambda eps: {"markdown": COMPLIANT_MD, "figures": []},
                "M3": lambda eps: {"markdown": COMPLIANT_MD,
                                   "figures": [{"name": "fig1_killrate.png", "caption": ""}]},
                "M4": lambda eps: {"markdown": COMPLIANT_MD.replace("局限：", "见上图。局限："),
                                   "figures": []},
                "M5": lambda eps: {"markdown": COMPLIANT_MD.replace("数据出处", "参考图 1。数据出处"),
                                   "figures": []},
            },
            canonical="M2",
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════
# 门 3：REPORT-OWNERSHIP（本单新增：归属门 + 配图魔数门）
# ══════════════════════════════════════════════════════════════════════════
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"


def _png_1x1() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = struct.pack(">I", len(data)) + tag + data
        return body + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b""))


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _ro():
    return importlib.import_module("services.report_ownership")


def _own_sample(user_id: int = 7, mission_id: int = 5496, owner: Any = 7,
                png: bytes | None = None, claimed_by: str | None = None) -> dict:
    mission = {"id": mission_id, "organization_id": 1, "status": "running",
               "claimed_by": claimed_by, "claimed_user_id": owner}
    return {"user": {"id": user_id, "username": f"u{user_id}", "role": "ENGINEER",
                     "organization_id": 1},
            "mission": mission, "mission_id": mission_id, "org_id": 1,
            "png": png if png is not None else _png_1x1()}


def _own_check(sample: dict) -> tuple[bool, str]:
    ro = _ro()
    v = ro.ownership_verdict(sample["user"], sample["mission"],
                             mission_id=sample["mission_id"], org_id=sample["org_id"])
    return (not v["ok"]), f"code={v['code']}"


def _png_check(expect: str) -> Callable[[dict], tuple[bool, str]]:
    def _check(sample: dict) -> tuple[bool, str]:
        ro = _ro()
        try:
            ro.validate_png_payload("fig.png", _b64(sample["png"]))
            return False, "accepted"
        except ro.FigureRejected as exc:
            return (exc.code == expect), f"code={exc.code}"

    return _check


def _ownership_criteria() -> list[Criterion]:
    png = _png_1x1()
    truncated = png[:-12]

    def _guard(cid: str, fn):
        def _c(sample: dict) -> tuple[bool, str]:
            if _disabled(cid):
                return False, "disabled"
            return fn(sample)
        return _c

    return [
        Criterion(
            cid="RO-MISSION", gate="REPORT-OWNERSHIP",
            statement="POST /tensor/reports 的 mission_id 必须属于提交者（claimed_user_id/claimed_by）",
            source="backend/services/report_ownership.py:ownership_verdict",
            check=_guard("RO-MISSION", _own_check),
            base=lambda: _own_sample(),
            mutate={
                "M1": lambda eps: _own_sample(mission_id=int(5496 * (1 + eps)), owner=8),
                "M2": lambda eps: _own_sample(owner="7.0"),        # 身份量纲错位
                "M3": lambda eps: _own_sample(owner=1),            # 常数替身：服务账号
                "M4": lambda eps: _own_sample(owner=8, claimed_by="u7"),   # 伪装
                "M5": lambda eps: _own_sample(user_id=8, owner=7),  # 基准替换：换 token
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[RO-MISSION]",),
            numeric_knob=True,
        ),
        Criterion(
            cid="RO-PNG-MAGIC", gate="REPORT-OWNERSHIP",
            statement="配图必须是 PNG（八字节魔数 89 50 4E 47 0D 0A 1A 0A），其它容器 422",
            source="backend/services/report_ownership.py:validate_png_payload",
            check=_guard("RO-PNG-MAGIC", _png_check("REPORT_FIGURE_NOT_PNG")),
            base=lambda: _own_sample(png=png),
            mutate={
                "M2": lambda eps: _own_sample(png=JPEG),
                "M3": lambda eps: _own_sample(png=b"\x00" * 64),
                "M4": lambda eps: _own_sample(png=GIF),
                "M5": lambda eps: _own_sample(png=WEBP),
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[RO-PNG-MAGIC]",),
            numeric_knob=False,
        ),
        Criterion(
            cid="RO-PNG-IEND", gate="REPORT-OWNERSHIP",
            statement="PNG 必须以 IEND 段收尾；截断/半截上传 422 REPORT_FIGURE_TRUNCATED",
            source="backend/services/report_ownership.py:validate_png_payload",
            check=_guard("RO-PNG-IEND", _png_check("REPORT_FIGURE_TRUNCATED")),
            base=lambda: _own_sample(png=png),
            mutate={
                "M2": lambda eps: _own_sample(png=png[:-2] + b"\xff\xd9"),      # 用 JPEG 尾巴收尾
                "M3": lambda eps: _own_sample(png=truncated[:16] + b"\x00" * (len(png) - 16)),
                "M4": lambda eps: _own_sample(png=truncated),                   # 砍掉 IEND
                "M5": lambda eps: _own_sample(png=png[:len(png) // 2]),         # 半截上传
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[RO-PNG-IEND]",),
            numeric_knob=False,
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════
# 门 4：CLIENT-PAYLOAD（客户端工装 tools/report_payload.py 的自证判据）
# ══════════════════════════════════════════════════════════════════════════
_SCRATCH: Path | None = None
DRIFT_PAYLOAD = {"kpi": {"kill_rate": 0.92}, "n_mutants": 77}


def scratch_dir() -> Path:
    """变异样本落盘用的临时目录（台账运行时统一注入，单测时惰性创建）。"""
    global _SCRATCH
    if _SCRATCH is None:
        _SCRATCH = Path(tempfile.mkdtemp(prefix="critmut-lazy-"))
    return _SCRATCH


def _rp():
    return importlib.import_module("tools.report_payload")


def _name_check(sample: dict) -> tuple[bool, str]:
    try:
        _rp().require_exclusive_name(sample["path"], sample["mission_id"])
        return False, "accepted"
    except _rp().PayloadError as exc:
        return True, type(exc).__name__


def _drift_check(sample: dict) -> tuple[bool, str]:
    rp = _rp()
    path = Path(sample["path"])
    path.unlink(missing_ok=True)
    proof = rp.write_report_payload(path, sample["mission_id"],
                                    {"mission_id": sample["mission_id"], **sample["payload"]})
    if sample.get("tamper") is not None:
        path.write_text(json.dumps(sample["tamper"], ensure_ascii=False), encoding="utf-8")
    try:
        rp.preflight(path, sample["mission_id"], proof)
        return False, "accepted"
    except rp.PayloadError as exc:
        return True, type(exc).__name__


def _client_criteria() -> list[Criterion]:
    def _guard(cid: str, fn):
        def _c(sample: dict) -> tuple[bool, str]:
            if _disabled(cid):
                return False, "disabled"
            return fn(sample)
        return _c

    def _path(name: str) -> str:
        return str(scratch_dir() / name)

    def _tamper(**over) -> dict:
        out = {"mission_id": 5496, **DRIFT_PAYLOAD}
        out.update(over)
        return out

    return [
        Criterion(
            cid="PL-NAME", gate="CLIENT-PAYLOAD",
            statement="报告文件名必须含 mission_id 令牌（否则无法反查归属、不同单互相覆盖）",
            source="tools/report_payload.py:require_exclusive_name",
            check=_guard("PL-NAME", _name_check),
            base=lambda: {"path": _path("report_m5496.json"), "mission_id": 5496},
            mutate={
                "M1": lambda eps: {"path": _path(f"report_m{int(5496 * (1 + eps))}.json"), "mission_id": 5496},
                "M2": lambda eps: {"path": _path("report_m05496.json"), "mission_id": 5496},
                "M3": lambda eps: {"path": _path("report.json"), "mission_id": 5496},
                "M4": lambda eps: {"path": _path("report_m6001.json"), "mission_id": 5496},
                "M5": lambda eps: {"path": _path("report_m1994.json"), "mission_id": 5496},
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[PL-NAME]",),
            numeric_knob=True,
        ),
        Criterion(
            cid="PL-DRIFT", gate="CLIENT-PAYLOAD",
            statement="提交前本地复算：磁盘字节 sha256 必须等于写盘自证（漂移即拒）",
            source="tools/report_payload.py:preflight",
            check=_guard("PL-DRIFT", _drift_check),
            base=lambda: {"path": _path("drift_m5496.json"), "mission_id": 5496,
                          "payload": dict(DRIFT_PAYLOAD), "tamper": None},
            mutate={
                "M1": lambda eps: {"path": _path("drift_m5496.json"), "mission_id": 5496,
                                   "payload": dict(DRIFT_PAYLOAD),
                                   "tamper": json.loads(json.dumps(
                                       _tamper(kpi={"kill_rate": 0.92 * (1 + eps)})))},
                "M2": lambda eps: {"path": _path("drift_m5496.json"), "mission_id": 5496,
                                   "payload": dict(DRIFT_PAYLOAD),
                                   "tamper": _tamper(kpi={"kill_rate": 920.0}, n_mutants=77000)},
                "M3": lambda eps: {"path": _path("drift_m5496.json"), "mission_id": 5496,
                                   "payload": dict(DRIFT_PAYLOAD),
                                   "tamper": _tamper(kpi={"kill_rate": 1.0}, n_mutants=1)},
                "M4": lambda eps: {"path": _path("drift_m5496.json"), "mission_id": 5496,
                                   "payload": dict(DRIFT_PAYLOAD),
                                   "tamper": {"n_mutants": 77, "kpi": {"kill_rate": 0.92},
                                              "mission_id": 5496}},   # 同内容重排 = 不同字节
                "M5": lambda eps: {"path": _path("drift_m5496.json"), "mission_id": 5496,
                                   "payload": dict(DRIFT_PAYLOAD),
                                   "tamper": _tamper(mission_id=6001)},
            },
            killer_tests=("tests/test_criteria_mutation.py::test_criterion_kills_canonical_mutant[PL-DRIFT]",),
            numeric_knob=True,
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════
# 负控：恒真判据（kill rate 必须是 0，且必须被标成「不可证伪」）
# ══════════════════════════════════════════════════════════════════════════
def _control_criterion() -> Criterion:
    def _always_pass(sample: dict) -> tuple[bool, str]:
        return False, "tautology: 恒放行"

    return Criterion(
        cid="NEG-TAUTOLOGY", gate=CONTROL_GATE,
        statement="负控：恒真判据（必须 0 kill；用来证明台账本身不是恒真）",
        source="tools/audit/criteria_mutation.py:_control_criterion",
        check=_always_pass,
        base=lambda: {"markdown": COMPLIANT_MD, "figures": list(COMPLIANT_FIGS)},
        mutate={
            "M1": lambda eps: {"markdown": COMPLIANT_MD.replace(
                "1.67%", f"{1.67 * (1 + eps):.4f}%"), "figures": list(COMPLIANT_FIGS)},
            "M2": lambda eps: {"markdown": COMPLIANT_MD.replace("100 m", "100000 mm"),
                               "figures": list(COMPLIANT_FIGS)},
            "M3": lambda eps: {"markdown": "参数表\n", "figures": []},
            "M4": lambda eps: {"markdown": COMPLIANT_MD, "figures": []},
            "M5": lambda eps: {"markdown": COMPLIANT_MD.replace("ITTC-1957", "ITTC-1978"),
                               "figures": list(COMPLIANT_FIGS)},
        },
        killer_tests=(),
        numeric_knob=True,
        control=True,
    )


def all_criteria() -> list[Criterion]:
    return (_paper_trace_criteria() + _report_quality_criteria()
            + _ownership_criteria() + _client_criteria() + [_control_criterion()])


def get_criterion(cid: str) -> Criterion:
    for c in all_criteria():
        if c.cid == cid:
            return c
    raise KeyError(cid)


# ══════════════════════════════════════════════════════════════════════════
# 台账 / KPI
# ══════════════════════════════════════════════════════════════════════════
def _mutant_grid(crit: Criterion) -> list[tuple[str, float | None]]:
    grid: list[tuple[str, float | None]] = []
    if crit.numeric_knob and "M1" in crit.mutate:
        grid += [("M1", eps) for eps in EPSILONS]
    for cls in ("M2", "M3", "M4", "M5"):
        if cls in crit.mutate:
            grid.append((cls, None))
    return grid


def evaluate_criterion(crit: Criterion) -> dict[str, Any]:
    """跑一条判据：基准必须放行（否则判据本身假阳性），逐 mutant 看是否亮红灯。"""
    entry: dict[str, Any] = {
        "cid": crit.cid, "gate": crit.gate, "statement": crit.statement,
        "source": crit.source, "numeric_knob": crit.numeric_knob,
        "control": crit.control, "killer_tests": list(crit.killer_tests),
        "mutants": [], "base_clean": None, "base_detail": None,
        "instantiable": False, "error": None,
    }
    try:
        base_flagged, base_detail = crit.check(crit.base())
        entry["base_clean"] = (not base_flagged)
        entry["base_detail"] = base_detail
    except Exception as exc:
        entry["error"] = f"base:{type(exc).__name__}:{exc}"
        entry.update({"killed": 0, "total": len(_mutant_grid(crit)),
                      "kill_rate": 0.0, "killed_classes": 0,
                      "classes_total": len({c for c, _ in _mutant_grid(crit)}),
                      "survivors": []})
        return entry

    ran = 0
    for cls, eps in _mutant_grid(crit):
        label = f"{cls}@{int(round(eps * 100))}%" if eps is not None else cls
        try:
            sample = crit.mutate[cls](eps)
            flagged, detail = crit.check(sample)
            ran += 1
            entry["mutants"].append({"class": cls, "label": label, "eps": eps,
                                     "killed": bool(flagged), "detail": detail})
        except Exception as exc:
            entry["mutants"].append({"class": cls, "label": label, "eps": eps,
                                     "killed": False,
                                     "detail": f"ERROR:{type(exc).__name__}:{exc}",
                                     "error": True})
    entry["instantiable"] = ran > 0 and entry["error"] is None
    killed = sum(1 for m in entry["mutants"] if m["killed"])
    entry["killed"] = killed
    entry["total"] = len(entry["mutants"])
    entry["kill_rate"] = round(killed / entry["total"], 4) if entry["total"] else 0.0
    entry["killed_classes"] = len({m["class"] for m in entry["mutants"] if m["killed"]})
    entry["classes_total"] = len({m["class"] for m in entry["mutants"]})
    entry["survivors"] = [m for m in entry["mutants"] if not m["killed"]]
    return entry


def build_ledger(*, with_ast_audit: dict[str, Any] | None = None,
                 scratch_parent: Path | None = None) -> dict[str, Any]:
    global _SCRATCH
    parent = scratch_parent or (ROOT / "artifacts")
    parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="critmut-", dir=str(parent))
    _SCRATCH = Path(tmp)
    try:
        entries = [evaluate_criterion(c) for c in all_criteria()]
    finally:
        _SCRATCH = None
        try:
            for p in Path(tmp).glob("*"):
                p.unlink(missing_ok=True)
            Path(tmp).rmdir()
        except OSError:
            pass
    ledger = {
        "tool": "tools/audit/criteria_mutation.py",
        "mutant_classes": MUTANT_CLASSES,
        "epsilon_grid": list(EPSILONS),
        "kill_rate_floor": KILL_RATE_FLOOR,
        "criteria": entries,
    }
    if with_ast_audit is not None:
        ledger["paper_trace_ast_audit"] = with_ast_audit
    ledger["kpi"] = kpi(ledger)
    return ledger


def kpi(ledger: dict[str, Any]) -> dict[str, Any]:
    """KPI：逐门 kill-rate / 最弱 mutant / 不可证伪判据 / M1 单调性 / 覆盖面。"""
    crits = ledger["criteria"]
    real = [c for c in crits if not c.get("control")]
    control = [c for c in crits if c.get("control")]

    gates: dict[str, dict[str, Any]] = {}
    for c in crits:
        g = gates.setdefault(c["gate"], {"criteria": [], "mutants": 0, "killed": 0,
                                         "control": bool(c.get("control"))})
        g["criteria"].append(c["cid"])
        g["mutants"] += c["total"]
        g["killed"] += c["killed"]
    for g in gates.values():
        g["kill_rate"] = round(g["killed"] / g["mutants"], 4) if g["mutants"] else 0.0
        g["n_criteria"] = len(g["criteria"])

    weakest = sorted(
        ({"cid": c["cid"], "gate": c["gate"], "label": m["label"], "class": m["class"],
          "detail": m["detail"]}
         for c in real for m in c["mutants"] if not m["killed"]),
        key=lambda d: (d["gate"], d["cid"], d["label"]))

    unfalsifiable = [{"cid": c["cid"], "gate": c["gate"],
                      "killed": c["killed"], "total": c["total"],
                      "killed_classes": c["killed_classes"],
                      "classes_total": c["classes_total"],
                      "verdict": "UNFALSIFIABLE"}
                     for c in crits if c["total"] and c["killed"] == 0]

    # M1 单调性：ε 从 1% → 3% → 15%，kill 数必须非降
    m1_counts = {f"{int(round(e * 100))}%": 0 for e in EPSILONS}
    for c in crits:
        for m in c["mutants"]:
            if m["class"] == "M1" and m["eps"] is not None and m["killed"]:
                m1_counts[f"{int(round(m['eps'] * 100))}%"] += 1
    seq = [m1_counts[k] for k in sorted(m1_counts, key=lambda k: int(k.rstrip("%")))]
    violations = [{"at": f"{sorted(m1_counts, key=lambda k: int(k.rstrip(chr(37))))[i]}",
                   "prev": seq[i - 1], "cur": seq[i]}
                  for i in range(1, len(seq)) if seq[i] < seq[i - 1]]

    instantiable = [c for c in crits if c["instantiable"] and c["base_clean"]]
    accepted_gates = sorted(g for g, v in gates.items() if not v["control"])
    return {
        "n_gates": len(accepted_gates),
        "gates": accepted_gates,
        "n_criteria": len(crits),
        "n_criteria_accepted_gates": len(real),
        "n_instantiable": len(instantiable),
        "instantiation_rate": round(len(instantiable) / len(crits), 4) if crits else 0.0,
        "n_mutants": sum(c["total"] for c in crits),
        "n_killed": sum(c["killed"] for c in crits),
        "overall_kill_rate": (round(sum(c["killed"] for c in crits)
                                    / sum(c["total"] for c in crits), 4)
                              if any(c["total"] for c in crits) else 0.0),
        "accepted_gate_kill_rate": (round(sum(c["killed"] for c in real)
                                          / sum(c["total"] for c in real), 4)
                                    if any(c["total"] for c in real) else 0.0),
        "per_gate": gates,
        "weakest_mutants": weakest,
        "n_weakest": len(weakest),
        "unfalsifiable_criteria": unfalsifiable,
        "m1_monotonic": {"counts": m1_counts, "sequence": seq,
                         "violations": violations, "n_violations": len(violations)},
        "control": {
            "cids": [c["cid"] for c in control],
            "killed": sum(c["killed"] for c in control),
            "total": sum(c["total"] for c in control),
            "killed_classes": sum(c["killed_classes"] for c in control),
            "classes_total": sum(c["classes_total"] for c in control),
            "flagged_unfalsifiable": [c["cid"] for c in control
                                      if c["cid"] in [u["cid"] for u in unfalsifiable]],
        },
        "criteria_below_floor": [{"cid": c["cid"], "gate": c["gate"],
                                  "kill_rate": c["kill_rate"]}
                                 for c in real if c["kill_rate"] < KILL_RATE_FLOOR],
    }


# ══════════════════════════════════════════════════════════════════════════
# 门禁：新判据准入（没有 killer test → 拒）
# ══════════════════════════════════════════════════════════════════════════
def _test_functions(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test")}


def killer_test_exists(node: str, root: Path = ROOT) -> bool:
    """``path::test_name[param]`` 形式的 node id 是否真实存在。"""
    file_part, _, test_part = node.partition("::")
    path = root / file_part
    if not path.is_file():
        return False
    name = test_part.split("[", 1)[0]
    return name in _test_functions(path)


def killer_test_bites(node: str, cid: str, root: Path = ROOT,
                      timeout: int = 180) -> dict[str, Any]:
    """破坏性验证：把判据打成恒放行后，该 killer test 必须**转红**。

    绿着的杀手 = 假杀手（它没在测这条判据）。
    """
    env = dict(os.environ)
    env[DISABLE_ENV] = cid
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, "-m", "pytest", node, "-q", "--noconftest",
           "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(cmd, cwd=str(root), env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"node": node, "cid": cid, "bites": False, "reason": "TIMEOUT"}
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    return {"node": node, "cid": cid, "bites": proc.returncode != 0,
            "exit": proc.returncode, "reason": "DISABLED_MUST_FAIL" if proc.returncode != 0
            else "KILLER_STAYED_GREEN", "tail": tail[0][:200]}


def load_criterion_spec(path: str | Path) -> list[Criterion]:
    """从 JSON 读「PR 里新增判据」的登记表（CI 准入用）。

    登记表只声明元数据与 killer test node id；判据实现本体在 PR 的代码里。
    这里用惰性 check（恒放行）占位——准入只看**杀手测试是否真咬**，不看实现体。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("new_criteria") if isinstance(data, dict) else data
    out: list[Criterion] = []
    for item in items or []:
        killers = item.get("killer_tests") or []
        if isinstance(killers, str):
            killers = [k.strip() for k in killers.split(",") if k.strip()]
        out.append(Criterion(
            cid=str(item["cid"]), gate=str(item.get("gate", "UNSPECIFIED")),
            statement=str(item.get("statement", "(来自 PR spec)")),
            source=str(item.get("source", "(来自 PR spec)")),
            check=lambda sample: (False, "spec-placeholder"),
            base=lambda: {}, mutate={"M3": lambda eps: {}},
            killer_tests=tuple(killers),
        ))
    return out


def pr_gate(new_cids: list[str] | None = None, *, root: Path = ROOT,
            run_killer_tests: bool = False,
            baseline_cids: list[str] | None = None) -> dict[str, Any]:
    """新判据准入：无 killer test / killer test 不存在 / 杀手不咬 → 拒（exit 2）。"""
    crits = all_criteria()
    known = set(baseline_cids or [])
    selected = [c for c in crits
                if (c.cid in new_cids if new_cids else c.cid not in known)]
    rejected: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for c in selected:
        if not c.killer_tests:
            rejected.append({"cid": c.cid, "reason": "NO_KILLER_TEST",
                             "detail": "新判据未登记任何 killer test（无人能证明它会亮红灯）"})
            continue
        missing = [t for t in c.killer_tests if not killer_test_exists(t, root)]
        if missing:
            rejected.append({"cid": c.cid, "reason": "KILLER_TEST_NOT_FOUND",
                             "missing": missing})
            continue
        for node in c.killer_tests:
            if run_killer_tests:
                out = killer_test_bites(node, c.cid, root)
                checks.append(out)
                if not out["bites"]:
                    rejected.append({"cid": c.cid, "reason": "KILLER_TEST_TOOTHLESS",
                                     "node": node, "tail": out.get("tail")})
    return {"selected": [c.cid for c in selected], "rejected": rejected,
            "accepted": not rejected, "exit_code": 0 if not rejected else 2,
            "killer_checks": checks}


def _load_ast_audit(paths: list[str]) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        key = p.name
        out[key] = {k: data.get(k) for k in
                    ("module", "tests", "total", "killed", "survived", "kill_rate")}
        out[key]["baseline_green"] = data.get("baseline_green")
        out[key]["by_operator"] = data.get("by_operator") or {}
        out[key]["survivors"] = len(data.get("survivors") or [])
        out[key]["survivor_names"] = [s.get("name") for s in (data.get("survivors") or [])]
    return out or None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="判据变异台账 / KPI / 准入门禁")
    sub = ap.add_subparsers(dest="cmd")
    p_led = sub.add_parser("ledger")
    p_led.add_argument("--json-out", default=None)
    p_led.add_argument("--ast-audit", action="append", default=[])
    sub.add_parser("kpi")
    p_gate = sub.add_parser("gate")
    p_gate.add_argument("--new-cids", default="")
    p_gate.add_argument("--baseline-cids", default="")
    p_gate.add_argument("--spec", default=None,
                        help="待准入的新判据描述 JSON（PR 里新增判据的登记表）")
    p_gate.add_argument("--run-killer-tests", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "gate":
        new = [x.strip() for x in a.new_cids.split(",") if x.strip()]
        base = [x.strip() for x in a.baseline_cids.split(",") if x.strip()]
        if a.spec:
            extra = load_criterion_spec(a.spec)
            global all_criteria
            builtin = all_criteria
            all_criteria = lambda: builtin() + extra
            for c in extra:
                if c.cid not in new:
                    new.append(c.cid)
        out = pr_gate(new or None, run_killer_tests=a.run_killer_tests, baseline_cids=base)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return out["exit_code"]

    ledger = build_ledger(with_ast_audit=_load_ast_audit(a.ast_audit)
                          if getattr(a, "ast_audit", None) else None)
    if a.cmd == "ledger" and getattr(a, "json_out", None):
        p = Path(a.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    k = ledger["kpi"]
    print(json.dumps({key: k[key] for key in
                      ("n_gates", "n_criteria", "n_instantiable", "instantiation_rate",
                       "n_mutants", "n_killed", "overall_kill_rate",
                       "accepted_gate_kill_rate", "n_weakest", "m1_monotonic",
                       "control", "unfalsifiable_criteria")},
                     ensure_ascii=False, indent=2))
    print("per-gate:", json.dumps({g: {"n": v["n_criteria"], "kill_rate": v["kill_rate"]}
                                   for g, v in k["per_gate"].items()},
                                  ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())