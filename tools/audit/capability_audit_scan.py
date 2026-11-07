#!/usr/bin/env python3
"""能力审计扫描器与证据等级量化器（IN21-A1 的库内核）。

本模块只做三件事，全部确定性、可单测：

1. ``scan_worktree`` 自动遍历工作树（``backend/``、``src/``、``tests/``、``docs/``，
   其中路径含 ``benchmarks/verified/`` 或 ``verification/`` 的单独归类为
   「已验证工件」），按 21 项能力的画像表提取证据；每条证据带文件名、行号、
   命中行、文件 sha256，可机器复核。
2. ``probe_solver_libs`` 只读探测各 Tensor* 求解器库入口（AST 解析，**不 import、
   不执行**），统计公开入口数、``benchmarks/verified`` 文件数、测试文件数。
3. ``component_scores`` / ``total_score`` / ``classify`` 把证据量化成 6 个分项分
   （code / test / verify / doc / reference / solver，各归一到 [0,1]），加权求和，
   再走显式规则级联判 REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED。

验证面的关键设计（为什么不用「文档里出现 PASS 就算通过」）
----------------------------------------------------------
关键词级「通过/误差」匹配在 20 万行文档的仓里必然假阳性（一句话里 \"FAIL 0 项\"
和 \"FAIL 8 项\" 都命中）。本模块改为**数值化判据**：

* ``numeric_error_pct`` 从正文解析所有 ``误差/error/deviation/偏差 … N%`` 的
  百分数，取最小者；
* ``pass`` 要求「有数值 + 最小误差 ≤ 3% + 正文无判决性失败词」（失败词正则与
  ``capability_ledger_upgrade.FAILURE_QUOTE`` 同义）；
* 通过判据再分两级：**tier-A** = 命中 ``verification/``/``verified/`` 路径的
  已验证工件（物理域）或带数值断言的测试（非物理域）；**tier-B** = 其它文档
  的一般性数值通过。REAL 要求 ``tier-A ≥ 1``——只有分数没有可复算工件的一律
  停在 WITHHELD。

纪律：阈值是常量并随产物落盘（``WEIGHTS``/``TOTAL_REAL``/``VERIFY_REAL``/
``STUB_RATIO_STUB``/``TOTAL_WITHHELD``），改阈值必须同步改
``tests/test_capability_audit_cli.py``。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path

# ── 等级与权重 ──────────────────────────────────────────────────────────────
LEVELS = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}

WEIGHTS = {
    "code": 0.22,       # 实现面：有非空壳代码路径
    "test": 0.20,       # 测试面：带数值断言的测试覆盖
    "verify": 0.26,     # 验证面：tier-A/B 数值化通过判据
    "doc": 0.12,        # 文档面：物理表述/参数表/基准比较/局限 四要素
    "reference": 0.12,  # 基准面：正典算例覆盖且未被 WITHHELD
    "solver": 0.08,     # 求解器面：Tensor* 库入口可只读探测
}

SATURATION = {"test": 3, "verify": 3, "code": 2, "doc": 2}

# 判级阈值
TOTAL_REAL = 0.78          # 总分门槛
VERIFY_REAL = 0.66         # REAL 还要求验证面 ≥ 2/3（两份 tier-A 工件即达标）
STUB_RATIO_STUB = 0.50     # 空壳文件占比 ≥ 50% → STUB
TOTAL_WITHHELD = 0.30      # 低于此且无矛盾证据 → NOT_SUPPORTED
ERROR_PASS_PCT = 3.0       # 数值判据通过线（%）

# 强标记：代码自述 MOCK / 随机数充当物理量 —— 单独命中即可降级
STRONG_MOCK_MARKERS = (
    "[MOCK]", "MOCK:", "np.random.rand", "random.uniform",
    "random.random", "mock_response", "dummy_value",
)
# 弱标记：常见于注释/负控说明，单独命中不降级（仅计入明细）
WEAK_MOCK_MARKERS = ("hardcoded", "hard-coded", "hardcode", "fake_")
MOCK_MARKERS = STRONG_MOCK_MARKERS + WEAK_MOCK_MARKERS
STUB_MARKERS = (
    "NotImplementedError", "TODO: implement", "# stub", "placeholder",
)
FAILURE_QUOTE = re.compile(
    r"是否\s*<3%\s*[:：]\s*否|不可接受|超.{0,4}15%|\bFAIL\b|error.*>.*15%|unacceptable"
)
NUMERIC_ERROR = re.compile(
    r"(?:误差|偏差|error|deviation|discrepancy)[^0-9\n%]{0,40}?(-?\d+(?:\.\d+)?)\s*%", re.I
)
# 负判决词：文档本身把该能力裁决为 MOCK/STUB/不支持/证据不足 —— 这类文档不得充作
# 通过判据（否则「审计报告里顺带出现的 0.0% 误差」会被误当成验证通过）。
NEGATIVE_VERDICT = re.compile(
    r"裁决[^\n]{0,20}(MOCK|STUB|NOT_SUPPORTED)"
    r"|应为\s*MOCK|证据等级[^\n]{0,10}(MOCK|STUB)"
    r"|等级[^\n]{0,6}(MOCK|STUB)|honest[- ]?FAIL",
    re.I,
)
NUMERIC_ASSERT = re.compile(
    r"assert .*(?:allclose|rtol|atol|approx|abs\(|\d+\.\d+)", re.I
)
VERIFIED_PATH = re.compile(r"verification|verified|benchmarks?/", re.I)
DOC_FIELDS = {
    "physics": ("物理", "physics", "governing", "方程", "equation", "边界条件"),
    "parameters": ("参数表", "parameter", "Re=", "Fr=", "Cb=", "| ---"),
    "benchmark": ("基准", "benchmark", "参考值", "reference", "误差", "error"),
    "limitations": ("局限", "limitation", "适用", "applicable", "适用范围"),
}
# 物理域：验证面必须由 verification/verified 工件支撑；非物理域允许测试充当 tier-A
PHYSICS_DOMAINS = {"阻力", "稳性", "推进", "耐波", "几何", "AI", "方法"}

EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", "artifacts", "runs", "frontend",
    "frontend-vue", "frontend-console", "frontend-factory", "grafana",
}
SCAN_EXT = {".py", ".md", ".json"}
# 自输出排除：审计工具自己的产物/报告/用例不得充作被审能力的证据，否则
# 「报告里出现上一轮分数表」→ 下一轮分数变化 → 报告再变，形成自引用回路。
EXCLUDE_SELF = re.compile(r"capability_audit", re.I)
MAX_FILE_BYTES = 400_000

SOLVER_LIBS = ("TensorLBM", "TensorFEM", "TensorFVM", "TensorDEM",
               "TensorBEM", "TensorSPH", "TensorDG", "TensorCAD")

# ── 21 项能力画像 ───────────────────────────────────────────────────────────
PROFILES: dict[str, dict] = {
    "calc.resistance": {
        "domain": "阻力",
        "keywords": ("resistance", "阻力", "holtrop", "ITTC", "Cf", "Rt="),
        "reference_domain": "resistance",
        "solver_libs": ("TensorLBM", "TensorFVM"),
    },
    "calc.stability": {
        "domain": "稳性",
        "keywords": ("stability", "稳性", "GZ", "metacentric", "KB", "KM"),
        "reference_domain": "stability",
        "solver_libs": ("TensorFEM",),
    },
    "propeller.open_water": {
        "domain": "推进",
        "keywords": ("open_water", "open-water", "敞水", "KQ", "KT"),
        "reference_domain": "propulsion",
        "solver_libs": ("TensorBEM",),
    },
    "geometry.generate": {
        "domain": "几何",
        "keywords": ("geometry", "几何", "hull", "型线"),
        "reference_domain": "geometry",
        "solver_libs": ("TensorCAD",),
    },
    "geometry.hydro_curves": {
        "domain": "几何",
        "keywords": ("hydrostat", "静水力", "bonjean", "hydro_curve"),
        "reference_domain": "geometry",
        "solver_libs": ("TensorCAD",),
    },
    "geometry.list_models": {
        "domain": "几何",
        "keywords": ("list_models", "stdmodels", "标准船型"),
        "reference_domain": "geometry",
        "solver_libs": ("TensorCAD",),
    },
    "geometry.verify_official": {
        "domain": "几何",
        "keywords": ("verify_official", "official", "Cb", "方型系数"),
        "reference_domain": "geometry",
        "solver_libs": ("TensorCAD",),
    },
    "shiphull.generate": {
        "domain": "几何",
        "keywords": ("shiphull", "hull_form", "船型生成", "offset table"),
        "reference_domain": "geometry",
        "solver_libs": ("TensorCAD",),
    },
    "stdmodels.lookup": {
        "domain": "数据",
        "keywords": ("stdmodels", "KVLCC2", "KCS", "DTMB", "SUBOFF"),
        "reference_domain": "geometry",
        "solver_libs": (),
    },
    "gov.stats": {
        "domain": "平台",
        "keywords": ("gov_stats", "governance", "审计统计", "/tensor/stats"),
        "reference_domain": None,
        "solver_libs": (),
    },
    "knowledge.docs": {
        "domain": "平台",
        "keywords": ("knowledge", "/knowledge", "知识库"),
        "reference_domain": None,
        "solver_libs": (),
    },
    "dev.gate_5415": {
        "domain": "开发",
        "keywords": ("5415", "gate_5415", "dtmb"),
        "reference_domain": "resistance",
        "solver_libs": ("TensorCAD",),
    },
    "dev.git": {
        "domain": "开发",
        "keywords": ("git_status", "/dev/git", "git log --oneline"),
        "reference_domain": None,
        "solver_libs": (),
    },
    "dev.logs": {
        "domain": "开发",
        "keywords": ("/dev/logs", "log_tail"),
        "reference_domain": None,
        "solver_libs": (),
    },
    "dev.nginx_audit": {
        "domain": "开发",
        "keywords": ("nginx",),
        "reference_domain": None,
        "solver_libs": (),
    },
    "dev.probe": {
        "domain": "开发",
        "keywords": ("probe", "探针", "healthcheck"),
        "reference_domain": None,
        "solver_libs": (),
    },
    "dev.restart": {
        "domain": "开发",
        "keywords": ("restart", "systemctl restart", "重启服务"),
        "reference_domain": None,
        "solver_libs": (),
    },
    "dev.services": {
        "domain": "开发",
        "keywords": ("supervisorctl status", "服务状态", "systemctl status"),
        "reference_domain": None,
        "solver_libs": (),
    },
    "ai.predict": {
        "domain": "AI",
        "keywords": ("predict", "surrogate", "代理模型", "neural"),
        "reference_domain": None,
        "solver_libs": ("TensorPhysics",),
    },
    "methods.computation": {
        "domain": "方法",
        "keywords": ("solver registry", "求解器注册", "methods/computation"),
        "reference_domain": None,
        "solver_libs": ("TensorLBM", "TensorFEM", "TensorFVM"),
    },
    "misc.stubs": {
        "domain": "杂项",
        "keywords": ("stub", "占位端点", "example_endpoint"),
        "reference_domain": None,
        "solver_libs": (),
    },
}


# ── 相关性门（relevance gate）────────────────────────────────────────────────
# 仅靠「关键词出现在文件里」在 20 万行文档的仓里必然假阳性（"predict"/"stub"/
# "probe" 随处可见，会把 ai.predict / misc.stubs 误判成 REAL）。因此每个能力
# 额外声明**判别性词（rare terms）**：文件必须命中其中至少一个才允许贡献证据。
# 判别词取能力自身标识符 / 端点路径 / 领域专有名词，宁窄不宽。
RARE_TERMS: dict[str, tuple] = {
    "calc.resistance": ("holtrop", "ITTC 1957", "剩余阻力", "resistance_calc"),
    "calc.stability": ("稳性", "GZ", "metacentric", "静水力曲线"),
    "propeller.open_water": ("open_water", "敞水", "KQ", "KT"),
    "geometry.generate": ("geometry.generate", "geometry_generate", "型线生成", "hull_generation"),
    "geometry.hydro_curves": ("hydrostat", "静水力"),
    "geometry.list_models": ("list_models", "stdmodels"),
    "geometry.verify_official": ("verify_official", "方型系数", "Cb 校验"),
    "shiphull.generate": ("shiphull", "hull_form"),
    "stdmodels.lookup": ("stdmodels",),
    "gov.stats": ("gov_stats", "/tensor/stats", "governance"),
    "knowledge.docs": ("knowledge.docs", "/knowledge/", "knowledge_base"),
    "dev.gate_5415": ("5415",),
    "dev.git": ("/dev/git", "git_status"),
    "dev.logs": ("/dev/logs", "log_tail"),
    "dev.nginx_audit": ("nginx",),
    "dev.probe": ("/dev/probe", "dev_probe", "probe_endpoint"),
    "dev.restart": ("/dev/restart", "dev_restart", "systemctl restart"),
    "dev.services": ("/dev/services", "dev_services", "systemctl status"),
    "ai.predict": ("surrogate", "代理模型"),
    "methods.computation": ("methods/computation", "solver registry", "求解器注册"),
    "misc.stubs": ("example_endpoint", "占位端点", "misc.stubs"),
}
RARE_TERMS_NOTE = ("相关性门：文件须命中该能力的 rare term 之一才计证据；"
                   "判别词取能力标识符/端点路径/领域专有名词，避免通用词假阳性。")


# ── 工具函数 ────────────────────────────────────────────────────────────────
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def line_sha256(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def norm_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def numeric_error_pct(text: str):
    """解析正文里所有「误差/error … N%」，返回 (最小百分数, 命中数)。"""
    vals = [abs(float(m)) for m in NUMERIC_ERROR.findall(text)]
    return (min(vals) if vals else None), len(vals)


def judge_pass(text: str) -> dict:
    """数值化判据：有数值 + 最小误差 ≤ 3% + 无失败词/负判决词。"""
    low, n = numeric_error_pct(text)
    fail_q = bool(FAILURE_QUOTE.search(text))
    neg = bool(NEGATIVE_VERDICT.search(text))
    fail = fail_q or neg
    ok = low is not None and low <= ERROR_PASS_PCT and not fail
    return {"has_numeric": low is not None, "min_error_pct": low, "n_numeric": n,
            "fail_quote": fail_q, "negative_verdict": neg, "fail": fail, "pass": ok}


# ── 遍历与扫描 ──────────────────────────────────────────────────────────────
def iter_scan_files(root: Path, subdirs, max_files: int = 20000):
    """有界遍历：限定子目录、扩展名、单文件大小。"""
    seen = 0
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        if base.is_file():
            cands = [base]
        else:
            cands = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
                for fn in filenames:
                    if Path(fn).suffix in SCAN_EXT:
                        cands.append(Path(dirpath) / fn)
        for p in sorted(cands):
            if EXCLUDE_SELF.search(norm_path(p, root)):
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            seen += 1
            if seen > max_files:
                return
            yield p


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def classify_file(rel: str) -> str:
    if "benchmarks/verified/" in rel or VERIFIED_PATH.search(rel):
        return "verified"
    if rel.startswith("tests/") or "/tests/" in rel:
        return "test"
    if rel.endswith(".md") or rel.startswith("docs/"):
        return "doc"
    return "code"


def scan_worktree(root: Path, profiles: dict | None = None,
                  subdirs=("backend", "src", "tests", "docs")) -> dict:
    """一次遍历，为每个能力收集 code/test/verified/doc 四类证据。"""
    profiles = profiles or PROFILES
    caps = {cid: {"code": [], "test": [], "verified": [], "doc": [],
                  "markers": {"mock": 0, "stub": 0}} for cid in profiles}
    pats = {cid: re.compile("|".join(re.escape(k) for k in prof["keywords"]), re.I)
            for cid, prof in profiles.items()}
    gates = {cid: re.compile("|".join(re.escape(k) for k in RARE_TERMS.get(cid, prof["keywords"])), re.I)
             for cid, prof in profiles.items()}

    for path in iter_scan_files(root, subdirs):
        text = _read(path)
        if not text:
            continue
        rel = norm_path(path, root)
        kind = classify_file(rel)
        lines = text.splitlines()
        matched = []
        for cid, pat in pats.items():
            if not (gates[cid].search(text) or gates[cid].search(rel)):
                # 相关性门：正文或路径均未命中判别词 → 不计证据
                continue
            m = pat.search(text)
            if m:
                line_no = text.count("\n", 0, m.start()) + 1
                quote = lines[line_no - 1].strip()[:160] if line_no - 1 < len(lines) else ""
            elif pat.search(rel):
                line_no, quote = 1, (lines[0].strip()[:160] if lines else rel)
            else:
                continue
            matched.append((cid, line_no, quote))
        if not matched:
            continue
        sha = sha256_file(path)
        verdict = None if kind == "test" else judge_pass(text)
        mock = [mk for mk in MOCK_MARKERS if mk in text] if kind == "code" else []
        stub = [mk for mk in STUB_MARKERS if mk in text] if kind == "code" else []
        fields = None
        if kind == "doc":
            low = text.lower()
            fields = sorted(f for f, marks in DOC_FIELDS.items()
                            if any(mk.lower() in low for mk in marks))
        head = text[:400]
        for cid, line_no, quote in matched:
            subject = bool(gates[cid].search(Path(rel).stem)) or bool(gates[cid].search(head))
            rec = {"path": rel, "line": line_no, "quote": quote,
                   "sha256": sha, "kind": kind, "subject_hit": subject}
            if kind == "test":
                rec["numeric_asserts"] = len(NUMERIC_ASSERT.findall(text))
                caps[cid]["test"].append(rec)
            elif kind == "code":
                rec["mock_markers"] = mock
                rec["stub_markers"] = stub
                caps[cid]["code"].append(rec)
                caps[cid]["markers"]["mock"] += len(mock)
                caps[cid]["markers"]["stub"] += len(stub)
            else:
                rec.update(verdict)
                if kind == "doc":
                    rec["doc_fields"] = fields
                caps[cid]["verified" if kind == "verified" else "doc"].append(rec)
    return caps


def probe_solver_libs(solver_root: Path, libs=SOLVER_LIBS) -> dict:
    """只读探测 Tensor* 库入口：AST 解析公开 def/class，不 import、不执行。"""
    out = {}
    for lib in libs:
        base = solver_root / lib
        rec = {"lib": lib, "path": str(base), "state": "ABSENT",
               "entry_points": [], "entry_point_count": 0,
               "verified_benchmarks": 0, "test_files": 0, "src_files": 0,
               "reason": ""}
        if not base.exists():
            rec["reason"] = f"{base} not present"
            out[lib] = rec
            continue
        src_files = [p for p in iter_scan_files(base, ("src",)) if p.suffix == ".py"]
        test_files = [p for p in iter_scan_files(base, ("tests",)) if p.suffix == ".py"]
        verified = [p for p in iter_scan_files(base, ("benchmarks",))
                    if "verified" in norm_path(p, base).lower()]
        entries = []
        for p in src_files:
            try:
                tree = ast.parse(_read(p) or "")
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if not node.name.startswith("_"):
                        entries.append(f"{norm_path(p, base)}::{node.name}")
        rec.update({
            "state": "PRESENT",
            "entry_points": entries[:40],
            "entry_point_count": len(entries),
            "verified_benchmarks": len(verified),
            "test_files": len(test_files),
            "src_files": len(src_files),
        })
        if not entries:
            rec["state"] = "PRESENT_NO_ENTRY"
            rec["reason"] = "no top-level public def/class in src/"
        out[lib] = rec
    return out


# ── 基准正典对拍 ────────────────────────────────────────────────────────────
def load_reference(path: Path) -> dict:
    if not path.exists():
        return {"cases": [], "version": None, "available": False, "path": str(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    if isinstance(cases, dict):
        cases = list(cases.values())
    return {"cases": cases, "version": data.get("version"), "available": True,
            "path": str(path)}


def reference_coverage(reference: dict, domain) -> dict:
    """按域统计正典算例覆盖；rt_status=WITHHELD 的算例不计入判决面。"""
    if not domain or not reference.get("available"):
        return {"applicable": False, "domain": domain, "cases": [],
                "usable": 0, "withheld": 0, "score": None,
                "reason": "no reference domain mapped" if domain is None
                          else "reference file missing"}
    keys = {"resistance": ("suboff", "dtmb", "kvlcc2"), "geometry": ("dtmb", "kvlcc2")}
    keyset = keys.get(domain, (domain,))
    hits = []
    for c in reference["cases"]:
        blob = json.dumps(c, ensure_ascii=False).lower()
        if domain.lower() in blob or any(k in blob for k in keyset):
            hits.append({"id": c.get("case_id") or c.get("id") or "?",
                         "rt_status": str(c.get("rt_status", "OK")).upper()})
    usable = sum(1 for c in hits if c["rt_status"] not in ("WITHHELD", "NONE", "NULL"))
    withheld = len(hits) - usable
    score = 0.0 if not hits else clamp(usable / len(hits))
    return {"applicable": True, "domain": domain, "cases": hits,
            "usable": usable, "withheld": withheld, "score": round(score, 4),
            "reason": f"{usable} usable / {len(hits)} matched (withheld={withheld})"}


# ── 证据等级量化 ────────────────────────────────────────────────────────────
def verification_tiers(ev: dict, domain: str) -> dict:
    """把验证证据分成 tier-A/tier-B，并统计判决性失败文档。"""
    physics = domain in PHYSICS_DOMAINS
    tier_a, tier_b, fail_docs = [], [], []
    for kind in ("verified", "doc"):
        for rec in ev[kind]:
            if not rec.get("subject_hit"):  # 主题门：正文顺带提及不算该能力的验证证据
                continue
            if rec.get("fail_quote"):
                fail_docs.append(rec)
                continue
            if not rec.get("pass"):
                continue
            if kind == "verified" or VERIFIED_PATH.search(rec.get("path", "")):
                tier_a.append(rec)
            else:
                tier_b.append(rec)
    if not physics:  # 非物理域：带数值断言的测试也算硬工件
        tier_a = tier_a + [r for r in ev["test"] if r.get("numeric_asserts", 0) > 0]
    return {"tier_a": tier_a, "tier_b": tier_b, "fail_docs": fail_docs,
            "physics_domain": physics,
            "conflict": bool(tier_a or tier_b) and bool(fail_docs)}


def component_scores(ev: dict, ref: dict, solver: dict | None, domain: str = "") -> dict:
    """6 个分项分 + 明细；不适用分项 score=None（归一化时剔除）。"""
    code_files = ev["code"]
    mock_files = sum(1 for r in code_files if r.get("mock_markers"))
    stub_files = sum(1 for r in code_files if r.get("stub_markers"))
    s_code = None
    if code_files:
        breadth = clamp(len(code_files) / SATURATION["code"])
        cleanliness = 1.0 - clamp((mock_files + stub_files) / len(code_files))
        s_code = round(clamp(0.5 * breadth + 0.5 * cleanliness), 4)

    tests = ev["test"]
    test_num = sum(1 for r in tests if r.get("numeric_asserts", 0) > 0)
    s_test = round(clamp(test_num / SATURATION["test"]), 4) if tests else None

    tiers = verification_tiers(ev, domain)
    pos = len(tiers["tier_a"]) + 0.5 * len(tiers["tier_b"])
    s_verify = round(clamp(pos / SATURATION["verify"]), 4)

    fields = set()
    for r in ev["doc"]:
        fields.update(r.get("doc_fields") or [])
    s_doc = round(clamp(len(fields) / 4.0), 4) if ev["doc"] else None

    s_ref = ref.get("score") if ref.get("applicable") else None

    s_solver = None
    if solver:
        present = [s for s in solver.values() if s["state"].startswith("PRESENT")]
        if present:
            s_solver = 1.0 if any(s.get("verified_benchmarks", 0) > 0 for s in present) else 0.5
        else:
            s_solver = 0.0

    return {
        "code": {"score": s_code,
                 "detail": {"files": len(code_files), "mock_files": mock_files,
                            "strong_mock_files": sum(
                                1 for r in code_files
                                if any(mk in (r.get("quote") or "") for mk in STRONG_MOCK_MARKERS)
                                or any(mk in "".join(r.get("mock_markers") or [])
                                       for mk in STRONG_MOCK_MARKERS)),
                            "strong_mock_hits": sum(
                                1 for r in code_files
                                if any(mk in (r.get("mock_markers") or [])
                                       for mk in STRONG_MOCK_MARKERS)),
                            "stub_files": stub_files,
                            "mock_marker_hits": ev["markers"]["mock"],
                            "stub_marker_hits": ev["markers"]["stub"]}},
        "test": {"score": s_test,
                 "detail": {"files": len(tests), "numeric_assert_files": test_num}},
        "verify": {"score": s_verify,
                   "detail": {"tier_a": len(tiers["tier_a"]),
                              "negative_verdict_docs": len(tiers["fail_docs"]),
                              "tier_b": len(tiers["tier_b"]),
                              "tier_a_paths": [r["path"] for r in tiers["tier_a"][:5]],
                              "tier_b_paths": [r["path"] for r in tiers["tier_b"][:5]],
                              "fail_docs": len(tiers["fail_docs"]),
                              "verification_conflict": tiers["conflict"],
                              "physics_domain": tiers["physics_domain"],
                              "best_error_pct": min(
                                  [r["min_error_pct"] for r in tiers["tier_a"] + tiers["tier_b"]
                                   if r.get("min_error_pct") is not None], default=None)}},
        "doc": {"score": s_doc,
                "detail": {"files": len(ev["doc"]), "fields_detected": sorted(fields)}},
        "reference": {"score": s_ref, "detail": dict(ref)},
        "solver": {"score": s_solver,
                   "detail": {lib: {"state": r["state"],
                                    "entry_points": r.get("entry_point_count", 0),
                                    "verified_benchmarks": r.get("verified_benchmarks", 0)}
                              for lib, r in (solver or {}).items()}},
    }


def normalize_weights(components: dict, base: dict | None = None) -> dict:
    """剔除不适用分项后对剩余权重重新归一（落盘便于复核，残差补到最大项）。"""
    base = base or WEIGHTS
    applicable = {k: base[k] for k, v in components.items() if v["score"] is not None}
    total = sum(applicable.values())
    if not total:
        return {}
    out = {k: round(w / total, 6) for k, w in applicable.items()}
    residual = round(1.0 - sum(out.values()), 6)
    if residual:
        biggest = max(out, key=out.get)
        out[biggest] = round(out[biggest] + residual, 6)
    return out


def total_score(components: dict, weights: dict) -> float:
    return round(sum(components[k]["score"] * w for k, w in weights.items()), 6)


# ── 判级规则级联 ────────────────────────────────────────────────────────────
def ledger_signals(ledger_rec: dict | None) -> dict:
    """从台账证据里抽「最新矛盾」与「最新支撑」两条信号。"""
    out = {"contradict": None, "support": None, "evidence_count": 0}
    if not ledger_rec:
        return out
    evid = ledger_rec.get("evidence") or []
    out["evidence_count"] = len(evid)
    cands = sorted(((str(e.get("as_of") or ""), e) for e in evid), key=lambda x: x[0])
    for _, e in cands:
        stance, lvl = e.get("stance"), e.get("asserted_level")
        rec = {"level": lvl, "as_of": e.get("as_of"), "ref": e.get("report_id"),
               "quote": e.get("quote_match")}
        if stance in ("contradicts", "fails") and lvl in LEVELS:
            out["contradict"] = rec
        elif stance in ("supports_real", "supports") and lvl in LEVELS:
            out["support"] = rec
    return out


def classify(components: dict, total: float, sig: dict) -> dict:
    """显式规则级联：返回 {level, rule, triggered[], reason}。"""
    trig = []
    code = components["code"]
    vdet = components["verify"]["detail"]
    verify = components["verify"]["score"]
    tier_a = vdet["tier_a"]
    files = code["detail"]["files"]
    stub_ratio = (code["detail"]["stub_files"] / files) if files else 0.0
    contradict, support = sig.get("contradict"), sig.get("support")

    def done(level, rule, why):
        trig.append({"rule": rule, "why": why})
        return {"level": level, "rule": rule, "triggered": trig, "reason": why}

    # R0 三面全无证据
    if code["score"] is None and components["test"]["score"] is None \
            and components["doc"]["score"] is None:
        return done("NOT_SUPPORTED", "R0_ABSENT", "code/test/doc 三面均无命中")

    # R1 空壳主导
    if stub_ratio >= STUB_RATIO_STUB and verify < 0.5:
        return done("STUB", "R1_STUB",
                    f"空壳文件占比 {stub_ratio:.2f} ≥ {STUB_RATIO_STUB} 且验证面 {verify:.2f} < 0.5")

    # R2 台账最新证据判负、且无更新的支撑
    if contradict and (not support or str(contradict["as_of"] or "") >= str(support["as_of"] or "")):
        lvl = "WITHHELD" if contradict["level"] == "REAL" else contradict["level"]
        return done(lvl, "R2_CONTRADICTED",
                    f"最新台账证据 {contradict['ref']} 判 {contradict['level']}"
                    f"（{contradict['as_of']}）且无更新的支撑证据；"
                    f"经 curation 的人工裁决优先于自动关键词扫描（verify={verify:.2f}）")

    # R3 代码自述 MOCK
    if code["detail"]["strong_mock_files"] > 0 and verify < VERIFY_REAL:
        return done("MOCK", "R3_MOCK_MARKER",
                    f"{code['detail']['mock_files']} 个代码文件含 MOCK 标记，验证面 {verify:.2f}")

    # R3b 验证文档互相矛盾且无 tier-A 硬工件 → 证据不足
    if vdet["verification_conflict"] and tier_a == 0:
        return done("WITHHELD", "R3B_CONFLICT",
                    f"tier-B 通过 {vdet['tier_b']} 份、判决失败 {vdet['fail_docs']} 份互斥，"
                    "无 tier-A 硬工件")

    # R3c 只有负判决、无任何正面判据 → 证据不足（不凭「别人判过 MOCK」就判 MOCK）
    if vdet["fail_docs"] > 0 and vdet["tier_a"] == 0 and vdet["tier_b"] == 0:
        return done("WITHHELD", "R3C_NEGATIVE_ONLY",
                    f"{vdet['fail_docs']} 份同主题文档判负且无任何数值通过判据 → 证据不足，待人工裁决")

    # R4 REAL 双门槛 + 必须有一份 tier-A 硬工件
    neg_clear = vdet.get("negative_verdict_docs", 0) == 0 or bool(
        support and (not contradict
                     or str(support["as_of"] or "") > str(contradict["as_of"] or "")))
    if total >= TOTAL_REAL and verify >= VERIFY_REAL and tier_a >= 1 and neg_clear:
        return done("REAL", "R4_REAL",
                    f"total={total:.3f} ≥ {TOTAL_REAL}、verify={verify:.2f} ≥ {VERIFY_REAL}、"
                    f"tier-A 工件 {tier_a} 份")

    # R4a 有同主题负判决且无更新的支撑 → 封顶 WITHHELD
    if total >= TOTAL_REAL and verify >= VERIFY_REAL and tier_a >= 1 and not neg_clear:
        return done("WITHHELD", "R4A_NEGATIVE_VERDICT",
                    f"total={total:.3f}、verify={verify:.2f}、tier-A={tier_a} 均过线，但存在 "
                    f"{vdet.get('negative_verdict_docs')} 份同主题负判决文档且无更新的支撑证据")

    # R4b 分数够但没有硬工件 → 停在 WITHHELD（不许凭分数升 REAL）
    if total >= TOTAL_REAL and verify >= VERIFY_REAL and tier_a == 0:
        return done("WITHHELD", "R4B_NO_HARD_ARTIFACT",
                    f"total={total:.3f}、verify={verify:.2f} 均过线但 tier-A=0，缺可复算工件")

    # R5 证据面过薄
    if total < TOTAL_WITHHELD:
        return done("NOT_SUPPORTED", "R5_LOW_SCORE", f"total={total:.3f} < {TOTAL_WITHHELD}")

    # R6 兜底
    return done("WITHHELD", "R6_WITHHELD",
                f"total={total:.3f}, verify={verify:.2f} 未达 REAL 双门槛或 tier-A=0")


def divergence(claimed: str, graded: str) -> str:
    """声明 vs 量化结果的对拍归类。"""
    if claimed == graded:
        return "OK"
    return "OVERCLAIM" if LEVEL_RANK.get(claimed, 0) > LEVEL_RANK.get(graded, 0) else "UNDERCLAIM"