"""判定级联：把结构信号变成五级证据等级 + 理由码 + 证据链。

级联顺序与阈值**先于观测声明**（见 docs/CAPABILITY_AUDIT_GATE.md §3），改规则必须改文档
并升 ``RULESET_VERSION``；回归防护会把规则版本一起冻结进基线，规则变更需显式 ack。

级联（首个命中即定级）::

    1  绑定路径存在但文件缺失        → NOT_SUPPORTED  BINDING_TARGET_MISSING
    2  解析到测试/实现但无实现文件    → NOT_SUPPORTED  CODE_MISSING
    3  空壳占比不达标                → STUB           STUB_DOMINATED
       （有效行数不足只记 warning LOW_SLOC，不单独定级——见 find#1）
    4  未播种随机 / 造假标记 / 常量直返 → MOCK         MOCK_SIGNAL
    5  解析型(analytic/identity)基准越界 → MOCK        PHYSICS_INVARIANT_VIOLATED
    6  实验型基准误差 > 可接受门限     → WITHHELD       BENCHMARK_OVER_TOLERANCE
    7  无测试 / 无数值断言            → WITHHELD       TESTS_MISSING / TESTS_NON_NUMERIC
    8  无基准工件 / 基准无有效行       → WITHHELD       NO_BENCHMARK
    9  基准误差 > REAL 容差(3%)        → WITHHELD       BELOW_REAL_TOLERANCE
    10 全过且误差 ≤3%                 → REAL           NUMERIC_VERIFICATION_PASS
    11 其余（未绑定等）               → WITHHELD       EVIDENCE_NOT_BOUND / INSUFFICIENT

另有两条**降噪**纪律（阈值可配，默认开启）：

* 判 MOCK 需 ≥2 个独立信号族（``mock_min_independent_signals``）：单信号只记 ``warnings``；
* 仅由路径名模式匹配（无显式绑定）解析到的证据不得做正面反证：MOCK/STUB/NOT_SUPPORTED
  一律降为 WITHHELD 并留 ``pattern_refutation_suppressed`` 痕迹（``pattern_can_refute``
  可关闭该纪律，仅供口径对照）。
"""
from __future__ import annotations

RULESET_VERSION = "cag-rules-v2"

#: 解析型检查（解析解/恒等式/守恒律）——越界意味着输出不是该物理量 ⇒ MOCK
ANALYTIC_CHECK_CLASSES = frozenset({"analytic", "identity", "conservation"})


def _conf(level: str, reasons: list[str]) -> float:
    base = {"NOT_SUPPORTED": 0.95, "STUB": 0.85, "MOCK": 0.85,
            "WITHHELD": 0.6, "REAL": 0.9}[level]
    if "EVIDENCE_NOT_BOUND" in reasons:
        base = 0.5
    if "TESTS_NON_NUMERIC" in reasons or "BELOW_REAL_TOLERANCE" in reasons:
        base = 0.7
    return base


def _impl_aggregate(impl: list[dict]) -> dict:
    fn = sum(s["functions"] for _, s in impl)
    stub = sum(s["stub_functions"] for _, s in impl)
    consts = sum(s["constant_returns"] for _, s in impl)
    sloc = sum(s["sloc"] for _, s in impl)
    return {
        "files": len(impl),
        "functions": fn,
        "sloc": sloc,
        "stub_ratio": (stub / fn) if fn else 1.0,
        "constant_return_ratio": (consts / fn) if fn else 0.0,
        "random_calls": sum(s["random_calls"] for _, s in impl),
        "marker_hits": sum(s["marker_hits"] for _, s in impl),
        "parse_error": any(s["parse_error"] for _, s in impl),
    }


def _test_aggregate(tests: list[dict]) -> dict:
    names = [n for _, s in tests for n in s["test_names"]]
    return {
        "files": len(tests),
        "test_names": sorted(set(names)),
        "numeric_assertions": sum(s["numeric_assertions"] for _, s in tests),
        "skeleton_assertions": sum(s["skeleton_assertions"] for _, s in tests),
        "parse_error": any(s["parse_error"] for _, s in tests),
    }


def judge(cap: dict, resolution: dict, impl: list[dict], tests: list[dict],
          benchmark: dict | None, thresholds: dict) -> dict:
    """返回 ``{level, reasons, confidence, signals, evidence}``。"""
    reasons: list[str] = []
    warns: list[str] = []
    ia = _impl_aggregate(impl)
    ta = _test_aggregate(tests)
    real_tol = float(thresholds["real_tolerance_pct"])
    hard_tol = float(thresholds["acceptable_tolerance_pct"])

    def result(level: str) -> dict:
        suppressed = None
        if (resolution.get("source") == "PATTERN"
                and not thresholds.get("pattern_can_refute", False)
                and level in {"NOT_SUPPORTED", "STUB", "MOCK"}):
            # 模式匹配是"机器猜的关联"，不是人审过的证据面：只允许它把能力留在 WITHHELD，
            # 不允许凭未绑定关联做出正面反证（否则短词噪音会把无关文件判成 MOCK）。
            suppressed = {"would_be_level": level, "signals": list(reasons)}
            reasons.append("PATTERN_ONLY_REFUTATION_SUPPRESSED")
            level = "WITHHELD"
        return {"capability": cap["id"], "level": level, "reasons": reasons,
                "pattern_refutation_suppressed": suppressed,
                "confidence": _conf(level, reasons), "signals": {"impl": ia, "tests": ta},
                "warnings": list(warns),
                "evidence": _chain(impl, tests, benchmark, reasons),
                "ruleset_version": RULESET_VERSION}

    # 1/2 存在性
    if resolution.get("missing") and not (resolution.get("code") or resolution.get("tests")):
        reasons.append("BINDING_TARGET_MISSING")
        return result("NOT_SUPPORTED")
    if not resolution.get("code") and not resolution.get("tests"):
        reasons.append("EVIDENCE_NOT_BOUND")
        return result("WITHHELD")
    if not resolution.get("code"):
        reasons.append("CODE_MISSING")
        return result("NOT_SUPPORTED")

    # 3 空壳
    if ia["functions"] == 0 or ia["stub_ratio"] >= float(thresholds["stub_ratio_max"]):
        reasons.append("STUB_DOMINATED")
        return result("STUB")
    if ia["sloc"] < int(thresholds["sloc_min"]):
        # find#1：小文件不等于空壳（5 行的造假实现也是 MOCK，不是 STUB）。
        # 尺寸只作弱信号：记录 warning，不单独定级；空壳仍由 stub_ratio 判定。
        warns.append("LOW_SLOC")

    # 4 造假信号：需 **≥2 个独立信号族** 才判 MOCK（单信号只记 WARN 并继续级联）
    mock_signals: list[str] = []
    if ia["random_calls"] > 0:
        mock_signals.append("UNSEEDED_RANDOM")
    if ia["marker_hits"] >= int(thresholds["mock_marker_hits_min"]):
        mock_signals.append("HARDCODE_MARKERS")
    if ia["constant_return_ratio"] >= float(thresholds["constant_return_ratio_max"]):
        mock_signals.append("CONSTANT_RETURN")
    if len(mock_signals) >= int(thresholds.get("mock_min_independent_signals", 2)):
        reasons.extend(mock_signals)
        return result("MOCK")
    warns.extend(mock_signals)

    # 5/6 基准
    bench_class = (benchmark or {}).get("check_class", "")
    analytic = bench_class in ANALYTIC_CHECK_CLASSES
    bench_status = (benchmark or {}).get("status")
    worst = (benchmark or {}).get("worst_error_pct")
    if bench_status in {"OVER_TOLERANCE", "DECLARED_FAIL"}:
        if worst is not None and worst > hard_tol:
            if analytic:
                reasons.append("PHYSICS_INVARIANT_VIOLATED")
                return result("MOCK")
            reasons.append("BENCHMARK_OVER_TOLERANCE")
            return result("WITHHELD")
        if bench_status == "DECLARED_FAIL":
            if analytic:
                reasons.append("PHYSICS_INVARIANT_VIOLATED")
                return result("MOCK")
            reasons.append("BENCHMARK_OVER_TOLERANCE")
            return result("WITHHELD")

    # 7 测试
    if not ta["test_names"]:
        reasons.append("TESTS_MISSING")
        return result("WITHHELD")
    if ta["numeric_assertions"] < int(thresholds["min_numeric_assertions"]):
        reasons.append("TESTS_NON_NUMERIC")
        return result("WITHHELD")

    # 8 基准缺失
    if not benchmark or not benchmark.get("rows"):
        reasons.append("NO_BENCHMARK")
        return result("WITHHELD")

    # 9/10 数值容差
    if worst is None:
        reasons.append("NO_BENCHMARK")
        return result("WITHHELD")
    if worst > real_tol:
        reasons.append("BELOW_REAL_TOLERANCE")
        return result("WITHHELD")
    reasons.append("NUMERIC_VERIFICATION_PASS")
    return result("REAL")


def _chain(impl: list[dict], tests: list[dict], benchmark: dict | None,
           reasons: list[str]) -> dict:
    """证据链：文件路径 + 测试名 + 基准值/误差，全部带 sha256 或工件哈希。"""
    return {
        "impl_files": [{"path": rel, "sha256": s.get("_sha256"), "sloc": s["sloc"],
                        "functions": s["functions"], "stub_functions": s["stub_functions"]}
                       for rel, s in impl][:12],
        "test_files": [{"path": rel, "sha256": s.get("_sha256"),
                        "test_names": s["test_names"][:8],
                        "numeric_assertions": s["numeric_assertions"]}
                       for rel, s in tests][:12],
        "test_names": sorted({n for _, s in tests for n in s["test_names"]})[:12],
        "benchmark": ({k: benchmark.get(k) for k in
                       ("path", "kind", "check_class", "metric", "status", "worst_error_pct",
                        "tolerance_pct", "sha256", "declared_verdict")} | {"rows": (benchmark.get("rows") or [])[:5]}
                      if benchmark else None),
        "reason_codes": list(reasons),
    }


#: 说明"为什么没有实现路径"的理由码（有其一即视为证据链已交代）
_ABSENCE_REASONS = frozenset({"EVIDENCE_NOT_BOUND", "BINDING_TARGET_MISSING", "CODE_MISSING"})


def chain_sufficient(verdict: dict) -> bool:
    """C4 判据：判定必须可回溯到具体证据或明确交代缺失。

    * 任何等级：≥1 个实现文件路径，或有明确的"未绑定/目标缺失/实现缺失"理由码；
    * REAL：还必须同时有测试名与基准值（路径+测试名+基准值三件套）。
    """
    ev = verdict["evidence"]
    has_impl = bool(ev["impl_files"]) or bool(_ABSENCE_REASONS & set(verdict["reasons"]))
    if verdict["level"] == "REAL":
        bench = ev.get("benchmark") or {}
        return has_impl and bool(ev["test_names"]) and bool(bench.get("rows"))
    return has_impl


def chain_full(verdict: dict) -> bool:
    """更强口径（报告用，不作门）：实现路径 + 测试名 + 基准行 三件套齐全。"""
    ev = verdict["evidence"]
    bench = ev.get("benchmark") or {}
    return bool(ev["impl_files"]) and bool(ev["test_names"]) and bool(bench.get("rows"))