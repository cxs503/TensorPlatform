#!/usr/bin/env python3
"""质量门执行工具（阻断式）—— QUALITY_BAR.md 五条硬规则机判。

PL-SEM-3 · owner 2026-10-25「需要严格质量把控，要不然会形成一堆垃圾」。

输入：
  --repo <eco库名>        例如 TensorLBM / TensorPlatform / TensorPhysics /
                          TensorDEM / TensorFVM
  --branch <分支>         待检分支
  [--pr <number>]         可选：发 PR 机器质量评论
  [--worktree <path>]     可选：仓库工作区路径（默认 /srv/tensor/shared/tensor_eco/<repo>）
  [--base-reds <path>]    可选：.base_reds 快照位置（默认 <wt>/.quality/base_reds.json）
  [--skip-r4]             可选：跳过 R4（CI 环境无 test suite 时使用）
  [--json]                可选：输出 JSON 到 stdout（默认 human-readable）

五规则逐条机判：
  R1 探针存在性：新增/改动 benchmarks/**/gates*.json 或判据时，检查
     tests/** 带 @mutation 标记的探针存在。
  R2 参考数据声明：validation_report*.json 存在性与 schema、
     reference_provenance 块（analytical|experiment+source|independent_impl）
     存在性——缺声明的新基准=FAIL。
  R3 物理项路径抽查：对 goal 声明的物理开关，grep 方程调用路径（非仅配置
     布尔）；无法静态判定=UNDETERMINED 登记（不假绿）。
  R4 基线红记账：跑套件于 main（.base_reds 快照缓存）与分支，新增红=FAIL；
     快照带 sha，main 前进时自动重算。
  R5 判决史 append-only：检查 verdict/判决文件未被删改（对照 git 历史，
     FAIL 条目仍在）。

输出：
  JSON 判定 {rule: {verdict, reason, evidence}} + overall。
  任一 FAIL → overall=FAIL；UNDETERMINED 不阻断但登记。

退出码：
  0 overall PASS
  1 overall FAIL
  2 工具异常（fail-open 登记人工位）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ECO = Path("/srv/tensor/shared/tensor_eco")
REVIEW_ENV = ECO / "agent_work" / "review.env"
GH_API = "https://api.github.com"
OWNER = "cxs503"
REPOS = ["TensorLBM", "TensorPlatform", "TensorPhysics", "TensorDEM", "TensorFVM"]

# validation_report schema 必需字段（R2）
REQUIRED_VALIDATION_FIELDS = [
    "task",
    "new_tests",
    "new_tests_passed",
]
# 参考数据声明块允许的类型
ALLOWED_PROVENANCE_TYPES = {"analytical", "experiment", "independent_impl"}
# 参考数据声明块必需字段
REQUIRED_PROVENANCE_FIELDS = ["type"]  # experiment 时额外需 source


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 300,
        check: bool = False) -> Tuple[int, str, str]:
    """执行命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"


def git_cmd(args: List[str], cwd: Path, timeout: int = 60) -> Tuple[int, str, str]:
    return run(["git"] + args, cwd=cwd, timeout=timeout)


def load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def gh_token() -> str:
    tok = os.environ.get("GH_PUSH_TOKEN", "")
    if not tok:
        p = Path("/tmp/gh_token")
        if p.exists():
            tok = p.read_text().strip()
    return tok


def load_review_env() -> Dict[str, str]:
    """读取 review.env 中的凭证。"""
    env = {}
    if not REVIEW_ENV.exists():
        return env
    for line in REVIEW_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


# ---------------------------------------------------------------------------
# 质量门判定结果
# ---------------------------------------------------------------------------

class RuleVerdict:
    """单条规则判定。"""
    PASS = "PASS"
    FAIL = "FAIL"
    UNDETERMINED = "UNDETERMINED"

    def __init__(self, rule: str, verdict: str, reason: str,
                 evidence: Optional[List[str]] = None):
        self.rule = rule
        self.verdict = verdict
        self.reason = reason
        self.evidence = evidence or []

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence": self.evidence,
        }


class QualityGateResult:
    """质量门总判定。"""

    def __init__(self):
        self.rules: Dict[str, RuleVerdict] = {}
        self.tool_error: Optional[str] = None  # 工具异常（fail-open）

    def add(self, rv: RuleVerdict):
        self.rules[rv.rule] = rv

    @property
    def overall(self) -> str:
        if self.tool_error:
            return "UNDETERMINED"  # 工具异常=fail-open
        verdicts = [r.verdict for r in self.rules.values()]
        if "FAIL" in verdicts:
            return "FAIL"
        if "UNDETERMINED" in verdicts:
            return "PASS_WITH_UNDETERMINED"
        return "PASS"

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "tool_error": self.tool_error,
            "rules": {k: v.to_dict() for k, v in self.rules.items()},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


# ---------------------------------------------------------------------------
# R1 探针存在性
# ---------------------------------------------------------------------------

def check_r1_probe_existence(worktree: Path, branch: str) -> RuleVerdict:
    """R1 门必须能失败：新增/改动 gates*.json 或判据时，检查 @mutation 探针。

    逻辑：
    1. 找出分支相对 main 新增/改动的 benchmarks/**/gates*.json 或
       validation_report*.json。
    2. 若有改动，检查 tests/** 中是否有 @mutation 标记的探针文件。
    3. 无改动 → PASS（不涉及门变更）。
    4. 有改动但无探针 → FAIL。
    5. 有改动且有探针 → PASS。
    """
    # 获取分支相对 main 的变更文件
    rc, out, err = git_cmd(
        ["diff", "--name-only", "main...HEAD"],
        cwd=worktree)
    if rc != 0:
        return RuleVerdict(
            "R1", RuleVerdict.UNDETERMINED,
            f"无法获取分支变更列表: {err.strip()}",
        )

    changed_files = [f.strip() for f in out.splitlines() if f.strip()]

    # 检查是否有门/判据变更
    gate_changes = [
        f for f in changed_files
        if re.search(r"benchmarks/.*/gates.*\.json$", f)
        or re.search(r"validation_report.*\.json$", f)
        or re.search(r"benchmarks/.*/verdict.*\.json$", f)
    ]

    if not gate_changes:
        return RuleVerdict(
            "R1", RuleVerdict.PASS,
            "无门/判据变更，R1 不适用",
            evidence=["no gate/verdict changes detected"],
        )

    # 检查 tests/** 中是否有 @mutation 标记
    mutation_probes: List[str] = []
    tests_dir = worktree / "tests"
    if tests_dir.exists():
        for py_file in tests_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if "@mutation" in content:
                    mutation_probes.append(str(py_file.relative_to(worktree)))
            except OSError:
                continue

    # 也检查 tests/mutation/ 目录
    mutation_dir = worktree / "tests" / "mutation"
    if mutation_dir.exists():
        for py_file in mutation_dir.rglob("*.py"):
            rel = str(py_file.relative_to(worktree))
            if rel not in mutation_probes:
                mutation_probes.append(rel)

    if not mutation_probes:
        return RuleVerdict(
            "R1", RuleVerdict.FAIL,
            f"门/判据有变更（{len(gate_changes)} 文件）但无 @mutation 探针",
            evidence=gate_changes,
        )

    return RuleVerdict(
        "R1", RuleVerdict.PASS,
        f"门/判据变更有 {len(mutation_probes)} 个 @mutation 探针",
        evidence=gate_changes + [f"probes: {', '.join(mutation_probes[:5])}"],
    )


# ---------------------------------------------------------------------------
# R2 参考数据声明
# ---------------------------------------------------------------------------

def check_r2_reference_provenance(worktree: Path, branch: str) -> RuleVerdict:
    """R2 参考数据必须可溯源。

    逻辑：
    1. 查找 validation_report*.json 文件。
    2. 检查是否存在 reference_provenance 块。
    3. 检查 reference_provenance.type 是否为允许值。
    4. experiment 类型必须有 source 字段。
    5. 无 validation_report → UNDETERMINED（可能不涉及基准）。
    6. 有 validation_report 但无 reference_provenance → FAIL。
    """
    # 查找 validation_report*.json
    reports: List[Path] = []
    for pattern in ["validation_report*.json", "**/validation_report*.json"]:
        reports.extend(worktree.rglob(pattern.replace("**/", "")))
        if "**" in pattern:
            reports.extend(worktree.rglob(pattern.split("/")[-1]))

    # 去重
    reports = list(set(reports))

    if not reports:
        return RuleVerdict(
            "R2", RuleVerdict.UNDETERMINED,
            "无 validation_report*.json，R2 不适用（可能不涉及基准）",
        )

    # 检查每个 report 的 reference_provenance
    missing_provenance: List[str] = []
    invalid_provenance: List[str] = []
    valid_count = 0

    for report_path in reports:
        data = load_json(report_path)
        if data is None:
            missing_provenance.append(str(report_path.relative_to(worktree)))
            continue

        # 检查必需字段
        missing_fields = [f for f in REQUIRED_VALIDATION_FIELDS if f not in data]
        if missing_fields:
            invalid_provenance.append(
                f"{report_path.relative_to(worktree)}: 缺少字段 {missing_fields}")
            continue

        # 检查 reference_provenance
        prov = data.get("reference_provenance")
        if prov is None:
            # 检查是否有 reference_data / provenance 等变体
            prov = data.get("reference_data") or data.get("provenance")

        if prov is None:
            missing_provenance.append(str(report_path.relative_to(worktree)))
            continue

        # 检查 type
        prov_type = prov.get("type") if isinstance(prov, dict) else None
        if prov_type not in ALLOWED_PROVENANCE_TYPES:
            invalid_provenance.append(
                f"{report_path.relative_to(worktree)}: type={prov_type} 不在允许值 "
                f"{ALLOWED_PROVENANCE_TYPES}")
            continue

        # experiment 类型必须有 source
        if prov_type == "experiment":
            source = prov.get("source") if isinstance(prov, dict) else None
            if not source:
                invalid_provenance.append(
                    f"{report_path.relative_to(worktree)}: experiment 类型缺少 source")
                continue

        valid_count += 1

    if missing_provenance:
        return RuleVerdict(
            "R2", RuleVerdict.FAIL,
            f"{len(missing_provenance)} 个 validation_report 缺少 reference_provenance 声明",
            evidence=missing_provenance,
        )

    if invalid_provenance:
        return RuleVerdict(
            "R2", RuleVerdict.FAIL,
            f"{len(invalid_provenance)} 个 validation_report 的 reference_provenance 无效",
            evidence=invalid_provenance,
        )

    return RuleVerdict(
        "R2", RuleVerdict.PASS,
        f"{valid_count} 个 validation_report 的 reference_provenance 有效",
        evidence=[str(r.relative_to(worktree)) for r in reports],
    )


# ---------------------------------------------------------------------------
# R3 物理项路径抽查
# ---------------------------------------------------------------------------

def check_r3_physics_path(worktree: Path, branch: str) -> RuleVerdict:
    """R3 硬缺陷一票否决：物理项路径抽查。

    逻辑：
    1. 查找 goal 声明或 validation_report 中的物理开关声明。
    2. 对每个物理开关，grep 源码中的方程调用路径（非仅配置布尔）。
    3. 无法静态判定 → UNDETERMINED（不假绿）。

    物理开关示例：
    - gravity_enabled=true → 检查是否有重力项的方程调用（如 rho*g, body_force）
    - two_phase=true → 检查是否有相场/VOF 方程
    """
    # 查找物理开关声明
    physics_switches: Dict[str, bool] = {}

    # 从 validation_report 中提取
    for report_path in worktree.rglob("validation_report*.json"):
        data = load_json(report_path)
        if data and "physics_switches" in data:
            physics_switches.update(data["physics_switches"])

    # 从 goal 文件中提取（如果有）
    goal_files = list(worktree.rglob("goal*.txt")) + list(worktree.rglob("goal*.md"))
    for goal_file in goal_files:
        try:
            content = goal_file.read_text(encoding="utf-8", errors="ignore")
            # 简单启发式：查找 physics_switches 块
            if "physics_switches" in content:
                # 提取 JSON 块
                match = re.search(
                    r"physics_switches\s*[:=]\s*(\{[^}]+\})", content, re.DOTALL)
                if match:
                    try:
                        switches = json.loads(match.group(1))
                        physics_switches.update(switches)
                    except json.JSONDecodeError:
                        pass
        except OSError:
            continue

    if not physics_switches:
        return RuleVerdict(
            "R3", RuleVerdict.UNDETERMINED,
            "无物理开关声明，R3 不适用",
        )

    # 对每个启用的物理开关，检查方程调用路径
    evidence: List[str] = []
    undetermined: List[str] = []
    verified: List[str] = []

    # 物理项 → 方程关键词映射
    PHYSICS_EQUATION_KEYWORDS = {
        "gravity": ["rho*g", "body_force", "gravity", "g_vec", "gravitational"],
        "two_phase": ["phase_field", "volume_fraction", "order_parameter", "cahn-hilliard"],
        "contact": ["contact_force", "contact_model", "hertz", "linear_spring"],
        "fracture": ["bond_break", "cohesive", "fracture_energy", "damage"],
        "turbulence": ["les", "smagorinsky", "k_epsilon", "reynolds_stress"],
    }

    enabled_switches = [name for name, enabled in physics_switches.items() if enabled]
    
    if not enabled_switches:
        return RuleVerdict(
            "R3", RuleVerdict.UNDETERMINED,
            "无启用的物理开关，R3 不适用",
        )

    for switch_name in enabled_switches:
        # 查找对应的方程关键词
        keywords = PHYSICS_EQUATION_KEYWORDS.get(switch_name, [switch_name])

        # 在工作区源码中 grep（用 rg 或 grep，不依赖 git 追踪）
        found = False
        for keyword in keywords:
            rc, out, _ = run(
                ["grep", "-r", "-l", "-i", keyword,
                 "--include=*.py", "--include=*.cpp", "--include=*.h"],
                cwd=worktree, timeout=30)
            if rc == 0 and out.strip():
                found = True
                # 取第一个匹配文件
                first_file = out.strip().splitlines()[0]
                evidence.append(f"{switch_name}: 找到 '{keyword}' 在 {first_file}")
                break

        if found:
            verified.append(switch_name)
        else:
            undetermined.append(switch_name)

    if undetermined:
        return RuleVerdict(
            "R3", RuleVerdict.UNDETERMINED,
            f"{len(undetermined)} 个物理开关无法静态判定方程路径: {undetermined}",
            evidence=evidence + [f"undetermined: {', '.join(undetermined)}"],
        )

    return RuleVerdict(
        "R3", RuleVerdict.PASS,
        f"{len(verified)} 个物理开关的方程路径已验证",
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# R4 基线红记账
# ---------------------------------------------------------------------------

def check_r4_baseline_reds(worktree: Path, branch: str,
                           base_reds_path: Path, skip: bool = False) -> RuleVerdict:
    """R4 基线红记账：新增红=FAIL。

    逻辑：
    1. 获取 main 分支当前 SHA。
    2. 加载 .base_reds 快照（含 sha + red_tests 列表）。
    3. 若快照不存在或 sha 不匹配 → 跑 main 套件生成新快照。
    4. 跑分支套件，对比红测试列表。
    5. 新增红 → FAIL；红减少或不变 → PASS。

    快照格式：
    {
      "sha": "<main_sha>",
      "timestamp": "<iso8601>",
      "red_tests": ["test_a::test_foo", "test_b::test_bar"]
    }
    """
    if skip:
        return RuleVerdict(
            "R4", RuleVerdict.UNDETERMINED,
            "R4 已跳过（--skip-r4）",
        )

    # 获取 main SHA
    rc, main_sha, _ = git_cmd(["rev-parse", "main"], cwd=worktree)
    if rc != 0:
        return RuleVerdict(
            "R4", RuleVerdict.UNDETERMINED,
            "无法获取 main 分支 SHA",
        )
    main_sha = main_sha.strip()

    # 加载基线快照
    baseline = load_json(base_reds_path)
    need_recompute = (
        baseline is None
        or baseline.get("sha") != main_sha
        or "red_tests" not in baseline
    )

    if need_recompute:
        # 跑 main 套件
        rc, stdout, stderr = run(
            ["python", "-m", "pytest", "--tb=no", "-q"],
            cwd=worktree, timeout=600)
        if rc < 0:
            return RuleVerdict(
                "R4", RuleVerdict.UNDETERMINED,
                f"main 套件执行异常: {stderr}",
            )

        # 解析红测试
        baseline_reds = parse_pytest_failures(stdout + stderr)

        # 保存快照
        save_json(base_reds_path, {
            "sha": main_sha,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "red_tests": baseline_reds,
        })
        baseline = {"red_tests": baseline_reds}

    # 跑分支套件
    rc, stdout, stderr = run(
        ["python", "-m", "pytest", "--tb=no", "-q"],
        cwd=worktree, timeout=600)
    if rc < 0:
        return RuleVerdict(
            "R4", RuleVerdict.UNDETERMINED,
            f"分支套件执行异常: {stderr}",
        )

    branch_reds = parse_pytest_failures(stdout + stderr)

    # 对比
    baseline_set = set(baseline["red_tests"])
    branch_set = set(branch_reds)
    new_reds = branch_set - baseline_set
    fixed_reds = baseline_set - branch_set

    if new_reds:
        return RuleVerdict(
            "R4", RuleVerdict.FAIL,
            f"新增 {len(new_reds)} 个红测试（基线 {len(baseline_set)} → 分支 {len(branch_set)}）",
            evidence=[f"new_reds: {', '.join(sorted(new_reds)[:10])}"],
        )

    return RuleVerdict(
        "R4", RuleVerdict.PASS,
        f"无新增红测试（基线 {len(baseline_set)} → 分支 {len(branch_set)}，修复 {len(fixed_reds)}）",
        evidence=[f"fixed: {', '.join(sorted(fixed_reds)[:5])}"] if fixed_reds else [],
    )


def parse_pytest_failures(output: str) -> List[str]:
    """从 pytest 输出解析失败测试列表。

    格式示例：
    FAILED tests/test_foo.py::test_bar - AssertionError
    """
    failures = []
    for line in output.splitlines():
        match = re.match(r"FAILED\s+(\S+)", line)
        if match:
            failures.append(match.group(1))
    return failures


# ---------------------------------------------------------------------------
# R5 判决史 append-only
# ---------------------------------------------------------------------------

def check_r5_verdict_history(worktree: Path, branch: str) -> RuleVerdict:
    """R5 失败保留：FAIL 判决不可被覆盖/删除。

    逻辑：
    1. 获取分支相对 main 的变更文件。
    2. 检查是否有 verdict*.json 或判决文件被删除或修改。
    3. 若有删除 → FAIL。
    4. 若有修改 → 检查是否删除了 FAIL 条目（需对比内容）。
    5. 仅新增或无变更 → PASS。
    """
    # 获取变更文件
    rc, out, err = git_cmd(
        ["diff", "--name-status", "main...HEAD"],
        cwd=worktree)
    if rc != 0:
        return RuleVerdict(
            "R5", RuleVerdict.UNDETERMINED,
            f"无法获取分支变更列表: {err.strip()}",
        )

    # 解析变更
    verdict_deletions: List[str] = []
    verdict_modifications: List[str] = []

    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()
        filepath = parts[1].strip()

        # 检查是否为判决文件
        is_verdict = (
            re.search(r"verdict.*\.json$", filepath)
            or re.search(r"benchmarks/.*/gates.*\.json$", filepath)
            or re.search(r"benchmarks/.*/verdict.*\.json$", filepath)
        )

        if not is_verdict:
            continue

        if status.startswith("D"):
            verdict_deletions.append(filepath)
        elif status.startswith("M"):
            verdict_modifications.append(filepath)

    if verdict_deletions:
        return RuleVerdict(
            "R5", RuleVerdict.FAIL,
            f"{len(verdict_deletions)} 个判决文件被删除（违反 append-only）",
            evidence=verdict_deletions,
        )

    # 检查修改是否删除了 FAIL 条目
    fail_removals: List[str] = []
    for filepath in verdict_modifications:
        # 对比 main 和 HEAD 的内容
        rc_old, old_content, _ = git_cmd(
            ["show", f"main:{filepath}"], cwd=worktree)
        rc_new, new_content, _ = git_cmd(
            ["show", f"HEAD:{filepath}"], cwd=worktree)

        if rc_old != 0 or rc_new != 0:
            continue

        # 解析 JSON
        try:
            old_data = json.loads(old_content)
            new_data = json.loads(new_content)
        except json.JSONDecodeError:
            continue

        # 检查 verdict 字段
        old_verdict = old_data.get("verdict", "")
        new_verdict = new_data.get("verdict", "")

        if old_verdict == "FAIL" and new_verdict != "FAIL":
            fail_removals.append(f"{filepath}: verdict {old_verdict} → {new_verdict}")

        # 检查 gates 中的 FAIL
        old_gates = old_data.get("gates", [])
        new_gates = new_data.get("gates", [])
        if isinstance(old_gates, list) and isinstance(new_gates, list):
            old_fail_count = sum(1 for g in old_gates if not g.get("pass", True))
            new_fail_count = sum(1 for g in new_gates if not g.get("pass", True))
            if old_fail_count > new_fail_count:
                fail_removals.append(
                    f"{filepath}: gates FAIL 数 {old_fail_count} → {new_fail_count}")

    if fail_removals:
        return RuleVerdict(
            "R5", RuleVerdict.FAIL,
            f"{len(fail_removals)} 个判决文件的 FAIL 条目被移除（违反 append-only）",
            evidence=fail_removals,
        )

    if verdict_modifications:
        return RuleVerdict(
            "R5", RuleVerdict.PASS,
            f"{len(verdict_modifications)} 个判决文件有修改但未删除 FAIL 条目",
            evidence=verdict_modifications,
        )

    return RuleVerdict(
        "R5", RuleVerdict.PASS,
        "无判决文件变更",
    )


# ---------------------------------------------------------------------------
# PR 评论
# ---------------------------------------------------------------------------

def post_pr_comment(repo: str, pr_number: int, result: QualityGateResult) -> bool:
    """发 PR 机器质量评论。

    复用 review_bridge 的评论通道凭证（GH_PUSH_TOKEN）。
    """
    tok = gh_token()
    if not tok:
        print("警告: 无 GitHub 令牌，无法发 PR 评论", file=sys.stderr)
        return False

    # 构建评论内容
    body_lines = [
        "<!-- quality-gate-bot -->",
        "# 质量门判定（QUALITY_BAR.md）",
        "",
        f"**总判定**: `{result.overall}`",
        "",
        "## 逐条规则判定",
        "",
    ]

    for rule_name in ["R1", "R2", "R3", "R4", "R5"]:
        rv = result.rules.get(rule_name)
        if rv:
            emoji = {"PASS": "✅", "FAIL": "❌", "UNDETERMINED": "⚠️"}.get(rv.verdict, "❓")
            body_lines.append(f"### {rule_name} {emoji} {rv.verdict}")
            body_lines.append(f"**理由**: {rv.reason}")
            if rv.evidence:
                body_lines.append("**证据**:")
                for ev in rv.evidence[:5]:
                    body_lines.append(f"- `{ev}`")
            body_lines.append("")

    if result.tool_error:
        body_lines.append(f"## ⚠️ 工具异常（fail-open）")
        body_lines.append(f"```\n{result.tool_error}\n```")
        body_lines.append("")

    body_lines.append("---")
    body_lines.append("*此评论由 quality_gate.py 自动生成，owner 合并前必读。*")

    body = "\n".join(body_lines)

    # 发评论
    import urllib.request
    import urllib.error

    url = f"{GH_API}/repos/{OWNER}/{repo}/issues/{pr_number}/comments"
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as exc:
        print(f"PR 评论失败: HTTP {exc.code}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"PR 评论异常: {type(exc).__name__}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_quality_gate(repo: str, branch: str, worktree: Path,
                     base_reds_path: Path, skip_r4: bool = False,
                     pr_number: Optional[int] = None) -> QualityGateResult:
    """执行质量门五规则检查。"""
    result = QualityGateResult()

    try:
        # R1 探针存在性
        result.add(check_r1_probe_existence(worktree, branch))

        # R2 参考数据声明
        result.add(check_r2_reference_provenance(worktree, branch))

        # R3 物理项路径抽查
        result.add(check_r3_physics_path(worktree, branch))

        # R4 基线红记账
        result.add(check_r4_baseline_reds(worktree, branch, base_reds_path, skip_r4))

        # R5 判决史 append-only
        result.add(check_r5_verdict_history(worktree, branch))

    except Exception as exc:
        # 工具异常 = fail-open 登记人工位
        result.tool_error = f"{type(exc).__name__}: {exc}"

    # PR 评论
    if pr_number is not None:
        post_pr_comment(repo, pr_number, result)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="质量门执行工具（QUALITY_BAR.md 五条硬规则机判）")
    parser.add_argument("--repo", required=True, choices=REPOS,
                        help="eco 库名")
    parser.add_argument("--branch", required=True,
                        help="待检分支")
    parser.add_argument("--pr", type=int, default=None,
                        help="PR 编号（可选，发机器质量评论）")
    parser.add_argument("--worktree", type=Path, default=None,
                        help="仓库工作区路径（默认 /srv/tensor/shared/tensor_eco/<repo>）")
    parser.add_argument("--base-reds", type=Path, default=None,
                        help=".base_reds 快照位置")
    parser.add_argument("--skip-r4", action="store_true",
                        help="跳过 R4（CI 环境无 test suite 时使用）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 到 stdout")

    args = parser.parse_args()

    # 默认路径
    worktree = args.worktree or (ECO / args.repo)
    base_reds_path = args.base_reds or (worktree / ".quality" / "base_reds.json")

    if not worktree.exists():
        print(f"错误: 工作区不存在: {worktree}", file=sys.stderr)
        sys.exit(2)

    # 执行质量门
    result = run_quality_gate(
        repo=args.repo,
        branch=args.branch,
        worktree=worktree,
        base_reds_path=base_reds_path,
        skip_r4=args.skip_r4,
        pr_number=args.pr,
    )

    # 输出
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"质量门判定: {result.overall}")
        print()
        for rule_name in ["R1", "R2", "R3", "R4", "R5"]:
            rv = result.rules.get(rule_name)
            if rv:
                emoji = {"PASS": "✅", "FAIL": "❌", "UNDETERMINED": "⚠️"}.get(rv.verdict, "❓")
                print(f"{emoji} {rule_name}: {rv.verdict}")
                print(f"   {rv.reason}")
                if rv.evidence:
                    for ev in rv.evidence[:3]:
                        print(f"   - {ev}")
                print()

        if result.tool_error:
            print(f"⚠️ 工具异常（fail-open）: {result.tool_error}")
            print()

    # 退出码
    if result.overall == "FAIL":
        sys.exit(1)
    elif result.overall == "UNDETERMINED":
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
