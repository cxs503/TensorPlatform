#!/usr/bin/env python3
"""语义门（Semantic Gate）——验证 goal 语义是否被内容满足。

PL-SEM 线 · owner 2026-10-25 指令「缺真正理解意义的评估闭环，不能以形式闭环
作为验收标准，需要内容真正符合」的落地实现。

与形式门（gates_check.py）并存：形式门验内部自洽，语义门验目标语义。

用法：
  semantic_gate.py --goal <goal_text> --worktree <path> [--criteria <yaml>]
                   [--output <report.json>]

输入：
  - goal 文本（或包含 semantic-criteria 块的 goal 文件）
  - worktree 路径（工件与源码所在目录）

输出：
  - 逐判据：符合/不符/无法判定 + 证据引用
  - 总判定：内容符合/部分符合/不符合
  - 差距摘要

退出码：
  0 全部符合
  1 部分符合或不符合
  2 输入错误/工件缺失
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 默认配置（可被外部 YAML/JSON 覆盖）
# ---------------------------------------------------------------------------

DEFAULT_SYNTHETIC_MARKERS = [
    "synthetic",
    "placeholder",
    "seed=",
    "generate_synthetic",
    "mock_",
    "dummy_",
    "fake_",
    "random_state",
]

DEFAULT_PHYSICS_KEYWORDS = {
    "gravity": {
        "config_keys": ["gravity_enabled", "gravity", "g"],
        "disable_patterns": [r"gravity\s*=\s*None", r"gravity\s*=\s*0"],
    },
    "two_phase": {
        "config_keys": ["two_phase", "phase_field", "volume_fraction"],
        "disable_patterns": ["single_phase"],
    },
    "contact": {
        "config_keys": ["contact_model", "contact_enabled"],
        "disable_patterns": ["no_contact", r"contact\s*=\s*None"],
    },
    "fracture": {
        "config_keys": ["fracture_enabled", "cohesive_model"],
        "disable_patterns": ["no_fracture"],
    },
}

DEFAULT_SCALE_DIMENSIONS = {
    "physical_duration": {
        "key_path": "total_physical_time_s",
        "unit": "s",
    },
    "grid_resolution": {
        "key_path": "n_elements",
        "unit": "count",
    },
    "particle_count": {
        "key_path": "n_particles",
        "unit": "count",
    },
    "bond_count": {
        "key_path": "n_bonds_broken",
        "unit": "count",
    },
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def walk_json_key(doc: Any, key_path: str) -> Tuple[bool, Any]:
    """走 a.b[2].c 键路径；返回 (found, value)。"""
    parts = re.split(r"\.", key_path)
    cur = doc
    for part in parts:
        if not isinstance(cur, dict):
            return False, None
        # 处理数组索引 a[0]
        m = re.match(r"^(.+?)\[(\d+)\]$", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if key not in cur:
                return False, None
            cur = cur[key]
            if not isinstance(cur, list) or idx >= len(cur):
                return False, None
            cur = cur[idx]
        else:
            if part not in cur:
                return False, None
            cur = cur[part]
    return True, cur


def scan_files_for_patterns(
    worktree: Path,
    file_globs: List[str],
    patterns: List[str],
) -> List[Dict[str, Any]]:
    """扫描文件匹配模式，返回证据列表。"""
    evidence = []
    for glob_pat in file_globs:
        for fpath in worktree.glob(glob_pat):
            if not fpath.is_file():
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for line_no, line in enumerate(content.splitlines(), start=1):
                for pat in patterns:
                    if re.search(pat, line, re.IGNORECASE):
                        evidence.append({
                            "file": str(fpath.relative_to(worktree)),
                            "line": line_no,
                            "match": line.strip(),
                            "pattern": pat,
                        })
    return evidence


def extract_numeric_from_worktree(
    worktree: Path,
    file_glob: str,
    key_path: str,
) -> Tuple[bool, Optional[float], Optional[str]]:
    """从工件提取数值，返回 (found, value, source_file)。"""
    for fpath in worktree.glob(file_glob):
        if not fpath.is_file():
            continue
        try:
            if fpath.suffix == ".json":
                doc = json.loads(fpath.read_text(encoding="utf-8"))
            else:
                continue
        except Exception:
            continue
        found, val = walk_json_key(doc, key_path)
        if found and isinstance(val, (int, float)):
            return True, float(val), str(fpath.relative_to(worktree))
    return False, None, None


# ---------------------------------------------------------------------------
# 判据检查器
# ---------------------------------------------------------------------------

class SemanticGate:
    """语义门主类。"""

    def __init__(
        self,
        worktree: Path,
        synthetic_markers: Optional[List[str]] = None,
        physics_keywords: Optional[Dict] = None,
        scale_dimensions: Optional[Dict] = None,
    ):
        self.worktree = worktree
        self.synthetic_markers = synthetic_markers or DEFAULT_SYNTHETIC_MARKERS
        self.physics_keywords = physics_keywords or DEFAULT_PHYSICS_KEYWORDS
        self.scale_dimensions = scale_dimensions or DEFAULT_SCALE_DIMENSIONS

    def check_provenance(
        self,
        criterion: Dict[str, Any],
    ) -> Dict[str, Any]:
        """溯源判据：检查关键输入是否来自真实源。"""
        result = {
            "id": criterion.get("id", "provenance_check"),
            "category": "provenance",
            "verdict": "undetermined",
            "evidence": [],
            "gap": None,
        }

        assertion = criterion.get("assertion", {})
        scan_targets = assertion.get("scan_targets", ["**/*.py", "**/*.json"])
        markers = assertion.get("markers", self.synthetic_markers)

        # 扫描合成标记
        matches = scan_files_for_patterns(self.worktree, scan_targets, markers)
        if matches:
            result["verdict"] = "non_compliant"
            result["evidence"] = matches[:10]  # 限制证据数量
            result["gap"] = f"检测到合成标记：{matches[0]['match']}"
        else:
            result["verdict"] = "compliant"

        return result

    def check_physics_switch(
        self,
        criterion: Dict[str, Any],
    ) -> Dict[str, Any]:
        """物理开关判据：检查 goal 声明的物理是否开启。"""
        result = {
            "id": criterion.get("id", "physics_switch_check"),
            "category": "physics_switch",
            "verdict": "undetermined",
            "evidence": [],
            "gap": None,
        }

        assertion = criterion.get("assertion", {})
        physics_type = assertion.get("physics_type", "")
        config_files = assertion.get("config_files", ["**/*.json"])
        key_path = assertion.get("key_path", "")
        expected = assertion.get("expected", True)

        # 从配置提取实际值
        found = False
        actual = None
        source = None
        for glob_pat in config_files:
            for fpath in self.worktree.glob(glob_pat):
                if not fpath.is_file() or fpath.suffix != ".json":
                    continue
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if key_path:
                    f, v = walk_json_key(doc, key_path)
                    if f:
                        found, actual, source = True, v, str(fpath.relative_to(self.worktree))
                        break
            if found:
                break

        # 若配置无此键，grep 源码检查禁用模式
        if not found:
            grep_fallback = assertion.get("grep_fallback", {})
            if grep_fallback:
                files = grep_fallback.get("files", ["**/*.py"])
                patterns = grep_fallback.get("patterns", [])
                match_means = grep_fallback.get("match_means", "fail")
                matches = scan_files_for_patterns(self.worktree, files, patterns)
                if matches and match_means == "fail":
                    result["verdict"] = "non_compliant"
                    result["evidence"] = matches[:5]
                    result["gap"] = f"物理效应被禁用：{matches[0]['match']}"
                    return result

        # 比较实际值与期望值
        if found:
            result["evidence"].append({
                "file": source,
                "key_path": key_path,
                "actual": actual,
                "expected": expected,
            })
            if actual == expected:
                result["verdict"] = "compliant"
            else:
                result["verdict"] = "non_compliant"
                result["gap"] = f"{key_path}={actual}，期望 {expected}"

        return result

    def check_scale_realism(
        self,
        criterion: Dict[str, Any],
    ) -> Dict[str, Any]:
        """规模现实性判据：检查物理量级是否达到下限。"""
        result = {
            "id": criterion.get("id", "scale_realism_check"),
            "category": "scale_realism",
            "verdict": "undetermined",
            "evidence": [],
            "gap": None,
        }

        assertion = criterion.get("assertion", {})
        source = assertion.get("source", {})
        file_glob = source.get("file", "**/*.json")
        key_path = source.get("key_path", "")
        lower_bound = assertion.get("lower_bound", 0.0)
        unit = assertion.get("unit", "")

        # 提取实际数值
        found, actual, src_file = extract_numeric_from_worktree(
            self.worktree, file_glob, key_path
        )

        if not found:
            result["verdict"] = "undetermined"
            result["gap"] = f"无法从工件提取 {key_path}"
            return result

        result["evidence"].append({
            "file": src_file,
            "key_path": key_path,
            "actual": actual,
            "lower_bound": lower_bound,
            "unit": unit,
        })

        if actual >= lower_bound:
            result["verdict"] = "compliant"
        else:
            result["verdict"] = "non_compliant"
            result["gap"] = f"{key_path}={actual}{unit}，下限 {lower_bound}{unit}"

        return result

    def evaluate(
        self,
        goal_text: str,
        criteria: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """评估全部判据，返回总判定。"""
        results = []
        for crit in criteria:
            category = crit.get("category", "")
            if category == "provenance":
                res = self.check_provenance(crit)
            elif category == "physics_switch":
                res = self.check_physics_switch(crit)
            elif category == "scale_realism":
                res = self.check_scale_realism(crit)
            else:
                res = {
                    "id": crit.get("id", "unknown"),
                    "category": category,
                    "verdict": "undetermined",
                    "evidence": [],
                    "gap": f"未知判据类别：{category}",
                }
            results.append(res)

        # 总判定
        verdicts = [r["verdict"] for r in results]
        non_compliant_count = verdicts.count("non_compliant")
        undetermined_count = verdicts.count("undetermined")

        # 溯源不符 → 不符合
        provenance_fail = any(
            r["verdict"] == "non_compliant" and r["category"] == "provenance"
            for r in results
        )
        if provenance_fail or non_compliant_count >= 2:
            overall = "non_compliant"
        elif non_compliant_count > 0 or undetermined_count > 0:
            overall = "partial_compliant"
        else:
            overall = "compliant"

        # 差距摘要
        gap_summary = {
            "provenance_gaps": sum(
                1 for r in results
                if r["category"] == "provenance" and r["verdict"] == "non_compliant"
            ),
            "physics_switch_gaps": sum(
                1 for r in results
                if r["category"] == "physics_switch" and r["verdict"] == "non_compliant"
            ),
            "scale_realism_gaps": sum(
                1 for r in results
                if r["category"] == "scale_realism" and r["verdict"] == "non_compliant"
            ),
        }

        return {
            "goal_text": goal_text,
            "overall_verdict": overall,
            "criteria_results": results,
            "gap_summary": gap_summary,
        }


# ---------------------------------------------------------------------------
# SI-4 锚案例专用判据（硬编码，用于单测固化）
# ---------------------------------------------------------------------------

def si4_anchor_criteria() -> List[Dict[str, Any]]:
    """SI-4 全链演示的语义判据（锚案例）。"""
    return [
        {
            "id": "si4_provenance_lbm_flow",
            "category": "provenance",
            "description": "LBM 流场必须来自真实模拟，非合成函数",
            "assertion": {
                "type": "no_synthetic_marker",
                "scan_targets": [
                    "scenarios/**/*.py",
                    "outputs/**/*.json",
                ],
                "markers": [
                    "generate_synthetic",
                    "synthetic",
                    "seed=",
                ],
            },
        },
        {
            "id": "si4_physics_gravity_enabled",
            "category": "physics_switch",
            "description": "goal 声明含破冰演化，重力必须开启",
            "assertion": {
                "type": "config_key_true",
                "physics_type": "gravity",
                "config_files": ["outputs/**/full_chain_summary.json"],
                "key_path": "gravity_enabled",
                "expected": True,
                "grep_fallback": {
                    "files": ["scenarios/**/*.py"],
                    "patterns": [r"gravity\s*=\s*None"],
                    "match_means": "fail",
                },
            },
        },
        {
            "id": "si4_scale_physical_duration",
            "category": "scale_realism",
            "description": "物理时长至少 1 秒（破冰演化量级）",
            "assertion": {
                "type": "numeric_lower_bound",
                "source": {
                    "file": "outputs/**/full_chain_summary.json",
                    "key_path": "total_physical_time_s",
                },
                "lower_bound": 1.0,
                "unit": "s",
            },
        },
        {
            "id": "si4_scale_grid_resolution",
            "category": "scale_realism",
            "description": "网格单元数至少 100（SUBOFF 量级）",
            "assertion": {
                "type": "numeric_lower_bound",
                "source": {
                    "file": "outputs/**/full_chain_summary.json",
                    "key_path": "n_elements",
                },
                "lower_bound": 100,
                "unit": "count",
            },
        },
        {
            "id": "si4_scale_particle_count",
            "category": "scale_realism",
            "description": "颗粒数至少 100（破冰碎片量级）",
            "assertion": {
                "type": "numeric_lower_bound",
                "source": {
                    "file": "outputs/**/dem_snapshot_*.json",
                    "key_path": "n_particles",
                },
                "lower_bound": 100,
                "unit": "count",
            },
        },
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="语义门：验证 goal 语义是否被内容满足")
    parser.add_argument("--goal", required=True, help="goal 文本")
    parser.add_argument("--worktree", required=True, help="worktree 路径")
    parser.add_argument("--criteria", help="判据 YAML/JSON 文件（可选）")
    parser.add_argument("--output", help="输出报告路径（可选）")
    parser.add_argument("--anchor", choices=["si4"], help="使用锚案例判据")
    args = parser.parse_args()

    worktree = Path(args.worktree)
    if not worktree.is_dir():
        print(f"错误：worktree 不存在：{worktree}", file=sys.stderr)
        return 2

    gate = SemanticGate(worktree)

    # 加载判据
    if args.anchor == "si4":
        criteria = si4_anchor_criteria()
    elif args.criteria:
        crit_path = Path(args.criteria)
        if not crit_path.is_file():
            print(f"错误：判据文件不存在：{crit_path}", file=sys.stderr)
            return 2
        with open(crit_path, encoding="utf-8") as f:
            if crit_path.suffix in (".yaml", ".yml"):
                try:
                    import yaml
                    data = yaml.safe_load(f)
                except ImportError:
                    print("错误：需要 PyYAML 解析 YAML 文件", file=sys.stderr)
                    return 2
            else:
                data = json.load(f)
        criteria = data.get("semantic-criteria", [])
    else:
        print("错误：必须指定 --criteria 或 --anchor", file=sys.stderr)
        return 2

    # 评估
    report = gate.evaluate(args.goal, criteria)

    # 输出
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(report_json + "\n", encoding="utf-8")
        print(f"报告已写入：{args.output}")
    else:
        print(report_json)

    # 退出码
    if report["overall_verdict"] == "compliant":
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
