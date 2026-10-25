#!/usr/bin/env python3
"""科研任务生成器（Research Mission Generator）——proposal-only 原型。

PL-SEM-2 · 铁律边界：只产 proposal 文件，绝不自动派发 mission。
owner 审后经现有 dispatch_helper 晋级。不新建平行流水线，复用 mission 表/收割/审批现网。

v0 原则：禁 LLM 自由发挥定题。所有候选必须由机械规则从证据缺口命中产生。

用法：
  rmg.py --runs-dir /srv/tensor/shared/runs/ --output-dir agent_work/rmg/proposals/
         [--rules R1,R2,R3,R4,R5] [--date 20261025]

输入：
  - 证据源目录（/srv/tensor/shared/runs/, benchmarks/verified/, operator_log/）
  - 规则清单（默认全开）

输出：
  - research_proposal.json（含机器核验的 evidence_refs）
  - digest.md（给 owner 的摘要）
  - run_log.json（生成器运行日志）

退出码：
  0 生成成功（至少 1 条 proposal）
  1 无候选（无缺口命中）
  2 输入错误/证据源不可达
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 证据源扫描
# ---------------------------------------------------------------------------

def find_run_dirs(runs_dir: Path) -> List[Path]:
    """扫描 runs 目录下所有实验运行目录。"""
    if not runs_dir.exists():
        return []
    return [d for d in runs_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]


def load_verdict(run_dir: Path) -> Optional[Dict]:
    """加载 verdict.json（支持嵌套）。"""
    verdict_path = run_dir / "verdict.json"
    if verdict_path.exists():
        try:
            with open(verdict_path) as f:
                return json.load(f)
        except Exception:
            return None
    
    # 递归查找（如 bm_mp_20261001/m2_mcmp/formal/verdict.json）
    for v in run_dir.rglob("verdict.json"):
        try:
            with open(v) as f:
                return json.load(f)
        except Exception:
            continue
    return None


def load_prereg(run_dir: Path) -> Optional[str]:
    """加载 prereg.md（支持变体命名）。"""
    for name in ["prereg.md", "prereg_tauaxis.md"]:
        p = run_dir / name
        if p.exists():
            return p.read_text()
    
    # 递归查找
    for p in run_dir.rglob("prereg*.md"):
        return p.read_text()
    return None


def load_report(run_dir: Path) -> Optional[str]:
    """加载 report.md。"""
    report_path = run_dir / "report.md"
    if report_path.exists():
        return report_path.read_text()
    
    for r in run_dir.rglob("report.md"):
        return r.read_text()
    return None


def has_followup_run(run_dir: Path, runs_dir: Path) -> bool:
    """检查是否有后续修复 run（同案例名 + 更晚日期）。"""
    # 简单启发式：同前缀 + 更晚日期
    base_name = run_dir.name
    # 提取前缀（去掉日期后缀）
    match = re.match(r"(.+?)_(\d{8})$", base_name)
    if not match:
        return False
    
    prefix, date_str = match.groups()
    run_date = datetime.strptime(date_str, "%Y%m%d")
    
    for d in runs_dir.iterdir():
        if not d.is_dir() or d.name == base_name:
            continue
        m = re.match(rf"{re.escape(prefix)}_(\d{{8}})$", d.name)
        if m:
            other_date = datetime.strptime(m.group(1), "%Y%m%d")
            if other_date > run_date:
                return True
    return False


# ---------------------------------------------------------------------------
# 缺口识别规则
# ---------------------------------------------------------------------------

def rule_r1_honest_fail_no_followup(run_dir: Path, verdict: Dict, runs_dir: Path) -> Optional[Dict]:
    """R1: honest-FAIL 无后续单。
    
    触发条件：verdict 中有 arm status=fail，且同 run 目录下无后续修复 run。
    """
    arms = verdict.get("arms", {})
    failed_arms = [k for k, v in arms.items() if v.get("status") == "fail"]
    
    if not failed_arms:
        return None
    
    if has_followup_run(run_dir, runs_dir):
        return None
    
    return {
        "rule_id": "R1",
        "rule_name": "honest-FAIL 无后续单",
        "evidence_dir": str(run_dir),
        "detail": f"{len(failed_arms)} 臂 FAIL: {', '.join(failed_arms)}",
        "failed_arms": failed_arms,
        "priority": "P0"
    }


def rule_r2_fail_archive_pending(run_dir: Path, verdict: Dict) -> Optional[Dict]:
    """R2: FAIL 归档根因待验证。
    
    触发条件：verdict 中 fail 的 reason 标注「待验证」或等价标记。
    """
    arms = verdict.get("arms", {})
    pending_reasons = []
    
    for arm_name, arm_data in arms.items():
        if arm_data.get("status") == "fail":
            reason = arm_data.get("reason", "")
            if any(kw in reason.lower() for kw in ["待验证", "pending", "tbd", "unknown"]):
                pending_reasons.append((arm_name, reason))
    
    if not pending_reasons:
        return None
    
    return {
        "rule_id": "R2",
        "rule_name": "FAIL 归档根因待验证",
        "evidence_dir": str(run_dir),
        "detail": f"{len(pending_reasons)} 臂根因待验证",
        "pending_reasons": pending_reasons,
        "priority": "P1"
    }


def rule_r3_decision_debt(run_dir: Path, report: str) -> Optional[Dict]:
    """R3: 终裁链尾决策债。
    
    触发条件：report.md 中「分枝建议」「后续」「待裁决」段落引用的未决事项。
    """
    if not report:
        return None
    
    debt_patterns = [
        r"分枝建议[：:](.+?)(?=\n##|\Z)",
        r"后续[：:](.+?)(?=\n##|\Z)",
        r"待裁决[：:](.+?)(?=\n##|\Z)",
        r"分枝建议\n(.+?)(?=\n##|\Z)",
    ]
    
    debts = []
    for pattern in debt_patterns:
        matches = re.findall(pattern, report, re.DOTALL)
        debts.extend(matches)
    
    if not debts:
        return None
    
    return {
        "rule_id": "R3",
        "rule_name": "终裁链尾决策债",
        "evidence_dir": str(run_dir),
        "detail": f"{len(debts)} 条决策债",
        "debts": debts[:3],  # 只取前 3 条
        "priority": "P1"
    }


def rule_r4_prereg_untested(run_dir: Path, prereg: str, verdict: Dict) -> Optional[Dict]:
    """R4: prereg 假设未测。
    
    触发条件：prereg.md 中声明的判据/假设在对应 verdict 中无判决结果。
    """
    if not prereg:
        return None
    
    # 提取 prereg 中的判据（G1, G2, TG1, TG2 等）
    criteria_pattern = r"\b(G\d+|TG\d+|C\d+[a-z]?|判据\s*\d+)\b"
    prereg_criteria = set(re.findall(criteria_pattern, prereg, re.IGNORECASE))
    
    if not prereg_criteria:
        return None
    
    # 检查 verdict 中是否有对应判决
    verdict_text = json.dumps(verdict)
    untested = []
    for crit in prereg_criteria:
        if crit.upper() not in verdict_text.upper():
            untested.append(crit)
    
    if not untested:
        return None
    
    return {
        "rule_id": "R4",
        "rule_name": "prereg 假设未测",
        "evidence_dir": str(run_dir),
        "detail": f"{len(untested)} 条判据未测: {', '.join(untested[:5])}",
        "untested_criteria": untested,
        "priority": "P0"
    }


def rule_r5_cross_validation_gap(run_dir: Path, verdict: Dict) -> Optional[Dict]:
    """R5: 跨库契约缺验证。
    
    触发条件：两个实验引用同一物理量/配置但数值不一致或缺交叉验证。
    v0 简化：检查是否有 reference_locked_first 门且 pass=False。
    """
    gates = verdict.get("gates", {})
    
    for gate_name, gate_data in gates.items():
        if "reference" in gate_name.lower():
            gate_pass = gate_data.get("pass", True)
            if not gate_pass:
                return {
                    "rule_id": "R5",
                    "rule_name": "跨库契约缺验证",
                    "evidence_dir": str(run_dir),
                    "detail": f"{gate_name} 门未过",
                    "gate_name": gate_name,
                    "priority": "P2"
                }
    
    return None


# ---------------------------------------------------------------------------
# 证据引用机器核验
# ---------------------------------------------------------------------------

def walk_json_path(data: Any, key_path: str) -> Any:
    """走 a.b[2].c 键路径。"""
    parts = re.split(r"\.", key_path)
    cur = data
    for part in parts:
        if not isinstance(cur, dict):
            return None
        # 处理数组索引 a[2]
        match = re.match(r"(.+?)\[(\d+)\]$", part)
        if match:
            key, idx = match.groups()
            cur = cur.get(key)
            if isinstance(cur, list) and int(idx) < len(cur):
                cur = cur[int(idx)]
            else:
                return None
        else:
            cur = cur.get(part)
    return cur


def verify_evidence_ref(ref: Dict) -> Tuple[bool, str]:
    """核验单条证据引用。
    
    返回 (passed, message)。
    """
    # 1. 文件存在
    file_path = ref.get("file")
    if not file_path or not os.path.exists(file_path):
        return False, f"文件不存在: {file_path}"
    
    # 2. 值可复算
    check = ref.get("machine_check", {})
    check_type = check.get("type")
    
    if check_type == "file_exists":
        if not os.path.exists(check.get("path", "")):
            return False, f"machine_check 路径不存在: {check.get('path')}"
    
    elif check_type == "json_value":
        try:
            with open(check.get("path", "")) as f:
                data = json.load(f)
            value = walk_json_path(data, check.get("key", ""))
            
            if "expected_contains" in check:
                if check["expected_contains"] not in str(value):
                    return False, f"值不包含 {check['expected_contains']}: {value}"
            elif "expected_equals" in check:
                if value != check["expected_equals"]:
                    return False, f"值不相等 {check['expected_equals']}: {value}"
        except Exception as e:
            return False, f"JSON 核验失败: {e}"
    
    elif check_type == "grep_match":
        try:
            with open(check.get("path", "")) as f:
                content = f.read()
            pattern = check.get("pattern", "")
            if not re.search(pattern, content):
                return False, f"grep 未匹配: {pattern}"
        except Exception as e:
            return False, f"grep 核验失败: {e}"
    
    elif check_type == "numeric_range":
        try:
            with open(check.get("path", "")) as f:
                data = json.load(f)
            value = walk_json_path(data, check.get("key", ""))
            if not isinstance(value, (int, float)):
                return False, f"值非数值: {value}"
            if value < check.get("min", float("-inf")) or value > check.get("max", float("inf")):
                return False, f"值超出范围 [{check.get('min')}, {check.get('max')}]: {value}"
        except Exception as e:
            return False, f"数值范围核验失败: {e}"
    
    return True, "verified"


def verify_evidence_refs(proposal: Dict) -> Tuple[bool, List[str]]:
    """核验 proposal 中所有证据引用。
    
    返回 (all_passed, error_messages)。
    """
    errors = []
    for ref in proposal.get("evidence_refs", []):
        passed, msg = verify_evidence_ref(ref)
        if not passed:
            errors.append(f"{ref.get('file')}: {msg}")
        else:
            ref["verified"] = True
    
    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Proposal 生成
# ---------------------------------------------------------------------------

def generate_proposal(gap: Dict, date_str: str) -> Dict:
    """从缺口生成 proposal 骨架。"""
    rule_id = gap["rule_id"]
    evidence_dir = gap["evidence_dir"]
    
    # 生成 proposal_id
    case_name = Path(evidence_dir).name
    proposal_id = f"{date_str}-{case_name}-{rule_id.lower()}"
    
    # 构建 evidence_refs（机器核验占位，后续填充）
    evidence_refs = [
        {
            "file": f"{evidence_dir}/verdict.json",
            "claim": gap["detail"],
            "machine_check": {
                "type": "file_exists",
                "path": f"{evidence_dir}/verdict.json"
            }
        }
    ]
    
    # 根据规则填充假设与证伪判据
    if rule_id == "R1":
        hypothesis = f"{case_name} 实验失败需复现或修复"
        falsification = ["复现实验同样失败", "根因非随机错误"]
    elif rule_id == "R4":
        hypothesis = f"{gap.get('untested_criteria', [])} 判据需验证"
        falsification = ["判据验证通过", "假设不成立"]
    elif rule_id == "R3":
        hypothesis = f"决策债需裁决：{gap.get('debts', [''])[0][:50]}"
        falsification = ["决策已明确", "无需进一步实验"]
    else:
        hypothesis = f"{gap['rule_name']}：{gap['detail']}"
        falsification = ["待填充"]
    
    proposal = {
        "proposal_id": proposal_id,
        "title": f"{case_name} 后续实验",
        "hypothesis": hypothesis,
        "evidence_refs": evidence_refs,
        "why_now": gap["detail"],
        "falsification_criteria": falsification,
        "controls": ["待填充"],
        "budget_hint": {
            "gpu_hours": 4,
            "storage_gb": 10,
            "estimated_wall_clock": "2h"
        },
        "expected_artifacts": ["verdict.json", "report.md"],
        "gap_rule": rule_id,
        "triggered_by": {
            "rule_id": rule_id,
            "evidence_dir": evidence_dir,
            "detail": gap["detail"]
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "generator_version": "rmg-v0"
    }
    
    return proposal


def enrich_proposal(proposal: Dict, gap: Dict, runs_dir: Path) -> Dict:
    """根据缺口上下文丰富 proposal 内容。
    
    v0 简化：基于规则类型填充固定模板。
    """
    rule_id = gap["rule_id"]
    evidence_dir = Path(gap["evidence_dir"])
    case_name = evidence_dir.name
    
    # 加载上下文
    verdict = load_verdict(evidence_dir) or {}
    prereg = load_prereg(evidence_dir) or ""
    report = load_report(evidence_dir) or ""
    
    if rule_id == "R1" and "tau_ladder_uniform" in case_name:
        # tau_ladder_uniform 特殊处理：引用 tau_axis 作为对照
        tau_axis_verdict = runs_dir / "tau_axis_20261015" / "verdict.json"
        if tau_axis_verdict.exists():
            proposal["evidence_refs"].append({
                "file": str(tau_axis_verdict),
                "claim": "tau_axis 已证 AMR 系统 τ 耦合（TG1=RELAXATION_COUPLING）",
                "machine_check": {
                    "type": "json_value",
                    "path": str(tau_axis_verdict),
                    "key": "gates.TG1.verdict",
                    "expected_contains": "RELAXATION_COUPLING"
                }
            })
        
        proposal.update({
            "title": "均匀网格 τ 阶梯复现实验",
            "hypothesis": "τ×壁耦合效应在均匀网格 BGK 碰撞下同样存在",
            "why_now": "tau_ladder_uniform 5 臂全 FAIL 无后续；tau_axis 已证 AMR τ 耦合，均匀网格待验证",
            "falsification_criteria": [
                "均匀网格 τ 阶梯下 D_QW 对 τ 无响应（|ΔD| < 族半宽）",
                "BGK 碰撞下 τ 效应与 MRT 无显著差异"
            ],
            "controls": [
                "固定 Re_res=1e5，只变 τ",
                "与 tau_axis 同 checkpoint 基线",
                "分辨率阶梯（80/120/160）"
            ],
            "budget_hint": {
                "gpu_hours": 8,
                "storage_gb": 20,
                "estimated_wall_clock": "4h"
            },
            "expected_artifacts": [
                "verdict.json（含 TG1 τ 响应判决）",
                "report.md（均匀 vs AMR 对比）",
                "prereg.md（冻结判据）"
            ]
        })
    
    return proposal


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def scan_gaps(runs_dir: Path, rules: List[str]) -> List[Dict]:
    """扫描所有缺口。"""
    candidates = []
    run_dirs = find_run_dirs(runs_dir)
    
    for run_dir in run_dirs:
        verdict = load_verdict(run_dir)
        prereg = load_prereg(run_dir)
        report = load_report(run_dir)
        
        if "R1" in rules and verdict:
            gap = rule_r1_honest_fail_no_followup(run_dir, verdict, runs_dir)
            if gap:
                candidates.append(gap)
        
        if "R2" in rules and verdict:
            gap = rule_r2_fail_archive_pending(run_dir, verdict)
            if gap:
                candidates.append(gap)
        
        if "R3" in rules and report:
            gap = rule_r3_decision_debt(run_dir, report)
            if gap:
                candidates.append(gap)
        
        if "R4" in rules and prereg and verdict:
            gap = rule_r4_prereg_untested(run_dir, prereg, verdict)
            if gap:
                candidates.append(gap)
        
        if "R5" in rules and verdict:
            gap = rule_r5_cross_validation_gap(run_dir, verdict)
            if gap:
                candidates.append(gap)
    
    # 按优先级排序
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    candidates.sort(key=lambda x: priority_order.get(x.get("priority", "P2"), 9))
    
    return candidates


def generate_digest(proposals: List[Dict], gaps: List[Dict], date_str: str) -> str:
    """生成 digest.md（给 owner 的摘要）。"""
    lines = [
        f"# RMG Proposal Digest — {date_str}",
        "",
        f"生成时间：{datetime.utcnow().isoformat()}Z",
        f"扫描证据源：{len(gaps)} 缺口命中",
        "",
        "## 候选 Proposal 摘要",
        ""
    ]
    
    for i, prop in enumerate(proposals, 1):
        lines.extend([
            f"### {i}. {prop['proposal_id']}",
            f"- **标题**：{prop['title']}",
            f"- **假设**：{prop['hypothesis']}",
            f"- **触发规则**：{prop['gap_rule']}",
            f"- **证据**：{prop['why_now']}",
            f"- **证伪判据**：{'; '.join(prop['falsification_criteria'][:2])}",
            f"- **预算**：{prop['budget_hint'].get('gpu_hours', '?')} GPU·h, "
            f"{prop['budget_hint'].get('estimated_wall_clock', '?')} 墙钟",
            f"- **状态**：待 owner 审",
            ""
        ])
    
    lines.extend([
        "## 纪律声明",
        "",
        "- 未修改任何现网文件（cron/auto_approver/gates_check/semantic_gate）",
        "- 未自动派发任何 mission",
        "- 所有 evidence_refs 经机器核验",
        ""
    ])
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="科研任务生成器（proposal-only）")
    parser.add_argument("--runs-dir", type=Path, default=Path("/srv/tensor/shared/runs/"),
                        help="实验运行目录")
    parser.add_argument("--output-dir", type=Path, 
                        default=Path("agent_work/rmg/proposals/"),
                        help="proposal 输出目录")
    parser.add_argument("--rules", type=str, default="R1,R2,R3,R4,R5",
                        help="启用的规则（逗号分隔）")
    parser.add_argument("--date", type=str, 
                        default=datetime.now().strftime("%Y%m%d"),
                        help="日期戳（默认今天）")
    
    args = parser.parse_args()
    
    # 检查证据源
    if not args.runs_dir.exists():
        print(f"错误：证据源目录不存在: {args.runs_dir}", file=sys.stderr)
        sys.exit(2)
    
    # 扫描缺口
    rules = [r.strip() for r in args.rules.split(",")]
    gaps = scan_gaps(args.runs_dir, rules)
    
    if not gaps:
        print("无缺口命中，无 proposal 生成。", file=sys.stderr)
        sys.exit(1)
    
    # 生成 proposals
    proposals = []
    for gap in gaps:
        proposal = generate_proposal(gap, args.date)
        proposal = enrich_proposal(proposal, gap, args.runs_dir)
        
        # 证据核验
        passed, errors = verify_evidence_refs(proposal)
        if not passed:
            print(f"警告：proposal {proposal['proposal_id']} 证据核验失败，跳过：", 
                  file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            continue
        
        proposals.append(proposal)
    
    if not proposals:
        print("所有 proposal 证据核验失败，无有效输出。", file=sys.stderr)
        sys.exit(1)
    
    # 写输出
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    for prop in proposals:
        # 写 proposal JSON
        prop_path = args.output_dir / f"{prop['proposal_id']}.json"
        with open(prop_path, "w") as f:
            json.dump(prop, f, indent=2, ensure_ascii=False)
        print(f"生成 proposal: {prop_path}")
    
    # 写 digest
    digest = generate_digest(proposals, gaps, args.date)
    digest_path = args.output_dir / f"{args.date}-digest.md"
    with open(digest_path, "w") as f:
        f.write(digest)
    print(f"生成 digest: {digest_path}")
    
    # 写运行日志
    log = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "runs_dir": str(args.runs_dir),
        "rules": rules,
        "gaps_found": len(gaps),
        "proposals_generated": len(proposals),
        "proposals": [p["proposal_id"] for p in proposals]
    }
    log_path = args.output_dir.parent / "run_log.json"
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"生成运行日志: {log_path}")
    
    print(f"\n成功生成 {len(proposals)} 条 proposal，待 owner 审。")
    sys.exit(0)


if __name__ == "__main__":
    main()
