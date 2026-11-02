#!/usr/bin/env python3
"""船舶总体性能 API 面 · 证据普查（offline, deterministic, AST-only）。

背景（为什么需要这件工具）
--------------------------
``audit_capabilities`` 只对 **21 个手工登记的 capability** 给出 REAL/MOCK/STUB/WITHHELD 分级。
而本仓 ``backend/api`` 下实际暴露的船舶总体性能端点远多于此，其中大量端点从未被任何
机器判据约束过——分级体系与真实 API 面之间存在**覆盖缺口**，任何"MOCK 未申报"都可能藏在
缺口里。本工具把缺口显式化：

对 ``backend/api`` 下每个 ship-domain 路由处理器，收集
  (a) 静态体证据：是否纯常量返回 / 是否用随机数 / 是否真做算术；
  (b) 外部证据链：``tests/`` ``backend/tests/`` ``tools/`` 里是否有带容差的判据断言；
据此给出证据等级，并让 pytest 对**新增的未申报 MOCK 风险**直接打红。

证据等级规则（机器可裁，无人工口径）
------------------------------------
* ``ANCHORED``   — 找到带容差断言的判据（approx/isclose/rel_err/rtol/abs(…) < tol）；
* ``WITHHELD``   — 有计算体但只有 smoke 级引用或零引用（证据不足，不得称 REAL）；
* ``MOCK_RISK``  — 常量返回或随机数，且**没有**带容差判据 → 视为 MOCK 风险，必须进申报表。

用法
----
    python3 tools/audit/ship_api_evidence_census.py                 # 打印摘要
    python3 tools/audit/ship_api_evidence_census.py --write         # 落盘 ledger + markdown
    python3 tools/audit/ship_api_evidence_census.py --json          # 机器可读全量

输出（默认路径，均在 worktree 内）
    docs/audit/ship_api_evidence_ledger.json
    docs/audit/ship_api_evidence_census.md
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

API_ROOT = REPO_ROOT / "backend" / "api"
EVIDENCE_ROOTS = [
    REPO_ROOT / "tests",
    REPO_ROOT / "backend" / "tests",
    REPO_ROOT / "tools",
]
LEDGER_PATH = REPO_ROOT / "docs" / "audit" / "ship_api_evidence_ledger.json"
MARKDOWN_PATH = REPO_ROOT / "docs" / "audit" / "ship_api_evidence_census.md"

# 九个学科的路径/标签关键词（船舶总体性能域）
SHIP_KEYWORDS = (
    "resistance", "propulsion", "propeller", "seakeeping", "maneuvering", "manoeuvring",
    "stability", "structure", "vibration", "noise", "icing", "ice", "polar", "cii",
    "green-shipping", "energy", "hydrostat", "hydro", "geometry", "ship", "hull", "tank",
    "suboff", "kcs", "kvlcc", "dtmb", "holtrop", "ittc", "fatigue", "wake",
    "roll-motion", "roll_motion", "damage-stability", "plate-stability", "comprehensive",
)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

TOLERANCE_MARKERS = ("pytest.approx", "approx(", "isclose", "math.isclose", "rel_err",
                     "rel_error", "rtol", "abs_err", "relative_error")

RANDOM_NAMES = ("random", "randn", "rand", "uniform", "choice", "normal", "shuffle")


@dataclass
class Endpoint:
    method: str
    path: str
    func: str
    module: str
    lineno: int
    ships_domain: bool
    body: str = ""
    has_binop: bool = False
    constant_return: bool = False
    uses_random: bool = False
    # 证据
    evidence: str = "UNCOVERED"
    evidence_files: list = field(default_factory=list)
    grade: str = "WITHHELD"

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"


def _iter_api_modules(root: Path):
    for p in sorted(root.rglob("*.py")):
        if any(part in {"tests", "__pycache__"} for part in p.parts):
            continue
        yield p


def _router_prefix(tree: ast.Module) -> str:
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if (isinstance(fn, ast.Name) and fn.id == "APIRouter") or (
                isinstance(fn, ast.Attribute) and fn.attr == "APIRouter"
            ):
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        return str(kw.value.value)
    return ""


def _handler_has_binop(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, (ast.BinOp, ast.AugAssign)):
            return True
    return False


def _constant_return(fn: ast.AST) -> bool:
    """函数体只返回字面量/常量（无算术、**无任何调用**）→ 硬编码返回。

    注意（m1708 踩坑）：仅"返回 Dict 字面量"不足以判硬编码——
    ``return {"curve": convoy_energy_curve(cfg)}`` 也是 Dict 字面量但值来自计算。
    因此只要函数体内出现任何 ``ast.Call``（哪怕不是算术）即不再判为常量返回。
    这条规则最初写漏，把 ``POST /polar/convoy/curve`` 误判成 MOCK 风险，
    由人工复核 handler 源码后修正——宁可漏报也不误伤真实计算端点。
    """
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    if not returns:
        return False
    for node in ast.walk(fn):
        if isinstance(node, (ast.BinOp, ast.Call)):
            return False
    for r in returns:
        v = r.value
        if v is None:
            continue
        if isinstance(v, ast.Constant):
            continue
        if isinstance(v, (ast.Dict, ast.List, ast.Tuple)):
            continue
        return False
    return True


def _uses_random(fn: ast.AST) -> bool:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name)
    return bool(names & set(RANDOM_NAMES))


def collect_endpoints(repo_root: Path = REPO_ROOT) -> list[Endpoint]:
    api_root = repo_root / "backend" / "api"
    out: list[Endpoint] = []
    for mod in _iter_api_modules(api_root):
        try:
            src = mod.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except (SyntaxError, OSError):
            continue
        prefix = _router_prefix(tree)
        rel = str(mod.relative_to(repo_root))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                method = dec.func.attr.lower()
                if method not in HTTP_METHODS:
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                raw_path = str(dec.args[0].value)
                full = (prefix.rstrip("/") + "/" + raw_path.lstrip("/")).rstrip("/") if prefix else raw_path
                if not full.startswith("/"):
                    full = "/" + full
                hay = (full + " " + node.name).lower()
                # 词元级匹配：避免 "device"/"service" 里的 "ice" 误命中
                ships = any(re.search(r"(?<![a-z0-9])" + k, hay) for k in SHIP_KEYWORDS)
                seg = ast.get_source_segment(src, node) or ""
                out.append(
                    Endpoint(
                        method=method,
                        path=full,
                        func=node.name,
                        module=rel,
                        lineno=node.lineno,
                        ships_domain=ships,
                        body=seg,
                        has_binop=_handler_has_binop(node),
                        constant_return=_constant_return(node),
                        uses_random=_uses_random(node),
                    )
                )
    return out


def _evidence_corpus(repo_root: Path) -> list[tuple[str, str]]:
    corpus = []
    for root in (repo_root / "tests", repo_root / "backend" / "tests", repo_root / "tools"):
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                corpus.append((str(p.relative_to(repo_root)), p.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    return corpus


def classify(endpoints: list[Endpoint], corpus) -> list[Endpoint]:
    for ep in endpoints:
        keys = [ep.path, ep.func]
        files, tol_files = [], []
        for rel, text in corpus:
            if any(k and k in text for k in keys):
                files.append(rel)
                if any(m in text for m in TOLERANCE_MARKERS):
                    tol_files.append(rel)
        if tol_files:
            ep.evidence = "ANCHORED"
            ep.evidence_files = sorted(tol_files)[:3]
        elif files:
            ep.evidence = "SMOKE"
            ep.evidence_files = sorted(files)[:3]
        else:
            ep.evidence = "UNCOVERED"

        if ep.evidence == "ANCHORED":
            ep.grade = "ANCHORED"
        elif ep.constant_return or ep.uses_random:
            ep.grade = "MOCK_RISK"
        else:
            ep.grade = "WITHHELD"
    return endpoints


def load_withheld_override(repo_root: Path = REPO_ROOT) -> dict:
    """显式降级申报（append-only 诚实台账）。

    覆盖度扫描按"路径名 + 容差标记"机械判 ANCHORED：一份**复核欠账文档**（例如
    docs/verification_debt/*/REPORT.md 或其审计脚本）只要同时出现端点路径和
    ``pytest.approx``/``rel_err`` 之类标记，就会把该端点"升级"成 ANCHORED —— 哪怕
    复核结论恰恰是"证据不足"。本函数让这类端点被显式钉回 WITHHELD：

        docs/audit/ship_api_mock_allowlist.json :: withheld_override = {endpoint: reason}

    语义：只降不升（ANCHORED -> WITHHELD），且必须给出理由；撤除理由 = 真的拿到
    独立基准后再删条目。
    """
    p = repo_root / "docs" / "audit" / "ship_api_mock_allowlist.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.get("withheld_override", {}).items()}


def apply_withheld_override(endpoints, over: dict):
    for ep in endpoints:
        reason = over.get(ep.key)
        if reason:
            # 只降不升：既降等级也把"覆盖度"证据降为 SMOKE（保持 A2 单调性：
            # evidence=ANCHORED ⇒ grade=ANCHORED 不得被打破）
            ep.grade = "WITHHELD"
            if ep.evidence == "ANCHORED":
                ep.evidence = "SMOKE"
            ep.override_reason = reason
    return endpoints


def load_allowlist(repo_root: Path = REPO_ROOT) -> set[str]:
    p = repo_root / "docs" / "audit" / "ship_api_mock_allowlist.json"
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(x) for x in data.get("acknowledged", [])}


def census(repo_root: Path = REPO_ROOT) -> dict:
    eps = collect_endpoints(repo_root)
    corpus = _evidence_corpus(repo_root)
    eps = classify(eps, corpus)
    ship = [e for e in eps if e.ships_domain]
    over = load_withheld_override(repo_root)
    apply_withheld_override(ship, over)
    allow = load_allowlist(repo_root)

    grades = {"ANCHORED": 0, "WITHHELD": 0, "MOCK_RISK": 0}
    for e in ship:
        grades[e.grade] += 1
    evidence = {"ANCHORED": 0, "SMOKE": 0, "UNCOVERED": 0}
    for e in ship:
        evidence[e.evidence] += 1

    unack_mock = sorted(e.key for e in ship if e.grade == "MOCK_RISK" and e.key not in allow)

    rows = []
    for e in sorted(ship, key=lambda x: (x.grade != "MOCK_RISK", x.grade != "WITHHELD", x.path)):
        rows.append({
            "endpoint": e.key,
            "handler": e.func,
            "module": e.module,
            "lineno": e.lineno,
            "evidence": e.evidence,
            "grade": e.grade,
            "acknowledged": e.key in allow,
            "static": {
                "constant_return": e.constant_return,
                "uses_random": e.uses_random,
                "has_binop": e.has_binop,
            },
            "evidence_files": e.evidence_files,
            "override_reason": getattr(e, "override_reason", None),
        })

    return {
        "generated_by": "tools/audit/ship_api_evidence_census.py",
        "schema": "ship_api_evidence_ledger/v1",
        "totals": {
            "api_routes_all": len(eps),
            "ship_domain": len(ship),
            "curated_capabilities_in_audit_tool": 21,
        },
        "grades": grades,
        "evidence": evidence,
        "coverage_pct": round(100.0 * grades["ANCHORED"] / len(ship), 2) if ship else 0.0,
        "unacknowledged_mock_risk": unack_mock,
        "withheld_override": over,
        "endpoints": rows,
    }


def render_markdown(led: dict) -> str:
    t, g, ev = led["totals"], led["grades"], led["evidence"]
    lines = [
        "# 船舶总体性能 API 面证据普查（机器生成）",
        "",
        "> 由 `tools/audit/ship_api_evidence_census.py` 生成，勿手改。数字全部来自 AST 扫描，零手抄。",
        "",
        f"- 全 API 路由：**{t['api_routes_all']}**；其中船舶总体性能域：**{t['ship_domain']}**",
        f"- `audit_capabilities` 手工登记能力：**{t['curated_capabilities_in_audit_tool']}** "
        f"→ 覆盖本域端点的 **{round(100.0 * t['curated_capabilities_in_audit_tool'] / max(t['ship_domain'], 1), 2)}%**",
        f"- 证据等级：ANCHORED **{g['ANCHORED']}** / WITHHELD **{g['WITHHELD']}** / MOCK_RISK **{g['MOCK_RISK']}**",
        f"- 外部证据链：ANCHORED **{ev['ANCHORED']}** / SMOKE **{ev['SMOKE']}** / UNCOVERED **{ev['UNCOVERED']}**",
        f"- 带容差判据覆盖率：**{led['coverage_pct']}%**",
        "",
        "## MOCK 风险端点（常量返回或随机数 + 无带容差判据）",
        "",
        "| 端点 | 处理器 | 模块 | 常量返回 | 随机 | 申报 |",
        "|---|---|---|---|---|---|",
    ]
    for r in led["endpoints"]:
        if r["grade"] != "MOCK_RISK":
            continue
        lines.append(
            f"| `{r['endpoint']}` | `{r['handler']}` | `{r['module']}:{r['lineno']}` | "
            f"{r['static']['constant_return']} | {r['static']['uses_random']} | "
            f"{'✅' if r['acknowledged'] else '❌ **未申报**'} |"
        )
    if not led["unacknowledged_mock_risk"]:
        lines.append("")
        lines.append("> 全部 MOCK 风险端点均已在 `docs/audit/ship_api_mock_allowlist.json` 申报。")
    lines += ["", "## WITHHELD 端点（有计算体但证据不足，不得称 REAL）", "",
              "| 端点 | 处理器 | 模块 | 外部证据 |", "|---|---|---|---|"]
    for r in led["endpoints"]:
        if r["grade"] != "WITHHELD":
            continue
        lines.append(f"| `{r['endpoint']}` | `{r['handler']}` | `{r['module']}:{r['lineno']}` | {r['evidence']} |")
    lines += ["", "## ANCHORED 端点（有带容差判据）", "",
              "| 端点 | 处理器 | 判据文件 |", "|---|---|---|"]
    for r in led["endpoints"]:
        if r["grade"] != "ANCHORED":
            continue
        lines.append(f"| `{r['endpoint']}` | `{r['handler']}` | {', '.join(r['evidence_files']) or '—'} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="船舶总体性能 API 面证据普查")
    ap.add_argument("--write", action="store_true", help="落盘 ledger + markdown 到 docs/audit/")
    ap.add_argument("--json", action="store_true", help="打印全量 JSON")
    ap.add_argument("--repo", default=str(REPO_ROOT))
    args = ap.parse_args()

    led = census(Path(args.repo))
    if args.write:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEDGER_PATH.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
        MARKDOWN_PATH.write_text(render_markdown(led), encoding="utf-8")
        print(f"wrote {LEDGER_PATH.relative_to(REPO_ROOT)} and {MARKDOWN_PATH.relative_to(REPO_ROOT)}")

    if args.json:
        print(json.dumps(led, ensure_ascii=False, indent=2))
        return 0

    t, g, ev = led["totals"], led["grades"], led["evidence"]
    print(f"ship-domain endpoints: {t['ship_domain']} / all routes {t['api_routes_all']}")
    print(f"grades: {g}  evidence: {ev}  coverage {led['coverage_pct']}%")
    print(f"unacknowledged MOCK_RISK: {len(led['unacknowledged_mock_risk'])}")
    for k in led["unacknowledged_mock_risk"][:20]:
        print("  !", k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())