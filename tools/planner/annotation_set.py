"""
黄金标注集（Golden Annotation Set）

来源：
- 2026-10-31 全案（≥15 条直录）
- 历史 succeeded/failed/僵尸单采样

结构：每条标注 = {
  candidate: {...},
  ground_truth: {verdict, target_objective, rationale},
  source: "2026-10-31" | "history_succeeded" | "history_failed" | "zombie",
  split: "train" | "held_out"  (held_out 占 1/3，不许训练调参偷看)
}
"""

import json
import random
from pathlib import Path
from typing import List, Dict

# ============================================================
# 2026-10-31 全案直录（15 条）
# ============================================================
CASES_1031 = [
    # --- 僵尸返工单（12 个 openhands，该拒：目标已被覆盖）---
    {
        "id": "oh-zombie-01",
        "title": "openhands: 阻力模块重构",
        "description": "openhands 返工阻力模块，但 m541 已覆盖该目标",
        "source": "github_issue",
        "labels": ["openhands", "resistance"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "目标已被 m541 覆盖，僵尸返工"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-02",
        "title": "openhands: 推进效率优化",
        "description": "openhands 返工推进效率，m542 已覆盖",
        "source": "github_issue",
        "labels": ["openhands", "propulsion"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "目标已被 m542 覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-03",
        "title": "openhands: 耐波性 RAO 计算",
        "description": "openhands 重复做 RAO，m543 已完成",
        "source": "github_issue",
        "labels": ["openhands", "seakeeping"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m543 已完成 RAO 计算"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-04",
        "title": "openhands: 操纵性 K/T 指数",
        "description": "openhands 重复 K/T 指数计算",
        "source": "github_issue",
        "labels": ["openhands", "maneuvering"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m544 已覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-05",
        "title": "openhands: 稳性校核",
        "description": "openhands 返工稳性校核，m545 已覆盖",
        "source": "github_issue",
        "labels": ["openhands", "stability"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m545 已覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-06",
        "title": "openhands: 结构强度分析",
        "description": "openhands 重复结构分析",
        "source": "github_issue",
        "labels": ["openhands", "structure"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m546 已覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-07",
        "title": "openhands: 振动噪声评估",
        "description": "openhands 重复振动噪声",
        "source": "github_issue",
        "labels": ["openhands", "vibration"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m547 已覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-08",
        "title": "openhands: 绿色航行 EEDI",
        "description": "openhands 重复 EEDI 计算",
        "source": "github_issue",
        "labels": ["openhands", "green"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m548 已覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-09",
        "title": "openhands: 冰区航行载荷",
        "description": "openhands 重复冰区载荷",
        "source": "github_issue",
        "labels": ["openhands", "ice"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m549 已覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-10",
        "title": "openhands: 平台 API 接线",
        "description": "openhands 重复 API 接线工作",
        "source": "github_issue",
        "labels": ["openhands", "api"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "m550 已覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-11",
        "title": "openhands: 前端可视化改进",
        "description": "openhands 重复前端改进",
        "source": "github_issue",
        "labels": ["openhands", "frontend"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "已有覆盖"},
        "source_case": "2026-10-31",
    },
    {
        "id": "oh-zombie-12",
        "title": "openhands: 测试覆盖率提升",
        "description": "openhands 重复测试工作",
        "source": "github_issue",
        "labels": ["openhands", "testing"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "已有覆盖"},
        "source_case": "2026-10-31",
    },
    # --- 报告 34/35/36 三份全同重复（该机械修不派 LLM）---
    {
        "id": "report-dup-34",
        "title": "报告 34/35/36 三份全同重复",
        "description": "报告 34、35、36 内容完全相同，需去重",
        "source": "auto_generated",
        "labels": ["report", "dedup"],
        "ground_truth": {"verdict": "mechanical", "target_objective": "data-quality",
                         "rationale": "三份报告 MD5 相同，脚本去重即可"},
        "source_case": "2026-10-31",
    },
    # --- 报告 8 MD5 typo（不值 140 次工具调用）---
    {
        "id": "report8-md5-typo",
        "title": "报告 8 MD5 typo",
        "description": "报告 8 中 MD5 值有拼写错误，需修正",
        "source": "auto_generated",
        "labels": ["report", "typo"],
        "ground_truth": {"verdict": "mechanical", "target_objective": "data-quality",
                         "rationale": "MD5 typo，sed 批量替换即可，不值 140 次工具调用"},
        "source_case": "2026-10-31",
    },
    # --- EX-1 m541-550 系（好单范本）---
    {
        "id": "ex1-m541",
        "title": "EX-1: Suboff 阻力验证",
        "description": "EX-1 m541: SUBOFF 裸艇体阻力验证，对标实验数据",
        "source": "manual",
        "labels": ["EX-1", "validation", "resistance"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-resistance-accuracy",
                         "rationale": "好单范本：目标明确、有对标数据、机器可判"},
        "source_case": "2026-10-31",
    },
    # --- m533 教训（任务书判据好但交付造假）---
    {
        "id": "m533-fake-delivery",
        "title": "m533: 任务书判据好但交付造假",
        "description": "m533 任务书判据设计好，但 agent 交付数据造假，需复算验证",
        "source": "manual",
        "labels": ["lesson-learned", "verification"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-trust-verification",
                         "rationale": "判据必须配复算，需增加独立验证步骤"},
        "source_case": "2026-10-31",
    },
    # --- m583 治理假声明 ---
    {
        "id": "m583-false-claim",
        "title": "m583: 治理假声明（自查全绿外查全假）",
        "description": "m583 agent 自查全绿但外查全假，需独立验证机制",
        "source": "manual",
        "labels": ["governance", "verification"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-governance-integrity",
                         "rationale": "需建立独立外查机制，不能只信自查"},
        "source_case": "2026-10-31",
    },
]

# ============================================================
# 历史采样（15 条，凑够 ≥30 条）
# ============================================================
CASES_HISTORY = [
    # --- succeeded 采样（5 条）---
    {
        "id": "hist-succ-01",
        "title": "推进模块效率曲线计算",
        "description": "新增推进模块效率曲线计算功能，对标模型试验",
        "source": "manual",
        "labels": ["propulsion", "feature"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-propulsion",
                         "rationale": "新功能，有明确对标目标"},
        "source_case": "history_succeeded",
    },
    {
        "id": "hist-succ-02",
        "title": "耐波性模块添加不规则波",
        "description": "耐波性模块添加不规则波谱分析功能",
        "source": "manual",
        "labels": ["seakeeping", "feature"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-seakeeping",
                         "rationale": "功能扩展，有验收标准"},
        "source_case": "history_succeeded",
    },
    {
        "id": "hist-succ-03",
        "title": "操纵性模块 K/T 指数校准",
        "description": "校准操纵性 K/T 指数计算，对标 Mariner 公式",
        "source": "manual",
        "labels": ["maneuvering", "calibration"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-maneuvering",
                         "rationale": "校准任务，有参考公式"},
        "source_case": "history_succeeded",
    },
    {
        "id": "hist-succ-04",
        "title": "稳性模块增加风载荷",
        "description": "稳性校核增加风载荷计算",
        "source": "manual",
        "labels": ["stability", "feature"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-stability",
                         "rationale": "功能完善"},
        "source_case": "history_succeeded",
    },
    {
        "id": "hist-succ-05",
        "title": "结构模块添加梁单元",
        "description": "结构分析模块添加 Euler-Bernoulli 梁单元",
        "source": "manual",
        "labels": ["structure", "feature"],
        "ground_truth": {"verdict": "dispatch", "target_objective": "kpi-structure",
                         "rationale": "单元类型扩展"},
        "source_case": "history_succeeded",
    },
    # --- failed 采样（5 条）---
    {
        "id": "hist-fail-01",
        "title": "阻力模块湍流模型切换",
        "description": "切换阻力模块湍流模型（k-epsilon → k-omega），导致发散",
        "source": "manual",
        "labels": ["resistance", "bug"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "高风险改动，近期失败过"},
        "source_case": "history_failed",
    },
    {
        "id": "hist-fail-02",
        "title": "推进模块空泡模型集成",
        "description": "集成空泡模型到推进模块，收敛困难",
        "source": "manual",
        "labels": ["propulsion", "bug"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "技术风险高，近期失败"},
        "source_case": "history_failed",
    },
    {
        "id": "hist-fail-03",
        "title": "耐波性模块频域到时域转换",
        "description": "频域到时域转换算法不稳定",
        "source": "manual",
        "labels": ["seakeeping", "bug"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "算法不稳定，需重新设计"},
        "source_case": "history_failed",
    },
    {
        "id": "hist-fail-04",
        "title": "操纵性模块 MMG 模型参数辨识",
        "description": "MMG 模型参数辨识不收敛",
        "source": "manual",
        "labels": ["maneuvering", "bug"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "参数辨识问题，需更多数据"},
        "source_case": "history_failed",
    },
    {
        "id": "hist-fail-05",
        "title": "稳性模块大倾角稳性计算",
        "description": "大倾角稳性计算精度不足",
        "source": "manual",
        "labels": ["stability", "bug"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "精度问题，需改进算法"},
        "source_case": "history_failed",
    },
    # --- 僵尸单采样（5 条）---
    {
        "id": "zombie-sample-01",
        "title": "返工：阻力系数计算修正",
        "description": "返工阻力系数计算，但 m541 已覆盖",
        "source": "github_issue",
        "labels": ["resistance", "rework"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "返工单，目标已被覆盖"},
        "source_case": "zombie",
    },
    {
        "id": "zombie-sample-02",
        "title": "返工：推进效率优化",
        "description": "返工推进效率，已有覆盖",
        "source": "github_issue",
        "labels": ["propulsion", "rework"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "返工单，目标已被覆盖"},
        "source_case": "zombie",
    },
    {
        "id": "zombie-sample-03",
        "title": "返工：耐波性 RAO 修正",
        "description": "返工 RAO 计算，已有覆盖",
        "source": "github_issue",
        "labels": ["seakeeping", "rework"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "返工单，目标已被覆盖"},
        "source_case": "zombie",
    },
    {
        "id": "zombie-sample-04",
        "title": "返工：操纵性导数修正",
        "description": "返工操纵性导数，已有覆盖",
        "source": "github_issue",
        "labels": ["maneuvering", "rework"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "返工单，目标已被覆盖"},
        "source_case": "zombie",
    },
    {
        "id": "zombie-sample-05",
        "title": "返工：稳性校核流程",
        "description": "返工稳性校核，已有覆盖",
        "source": "github_issue",
        "labels": ["stability", "rework"],
        "ground_truth": {"verdict": "reject", "target_objective": None,
                         "rationale": "返工单，目标已被覆盖"},
        "source_case": "zombie",
    },
]


def build_annotation_set(seed: int = 42) -> Dict:
    """
    构建标注集，自动划分 train/held_out
    
    held_out 占 1/3，不许训练调参偷看
    """
    all_cases = CASES_1031 + CASES_HISTORY
    
    # 按 source_case 分层抽样，确保 held_out 中各类型都有
    random.seed(seed)
    
    train_set = []
    held_out_set = []
    
    # 分组
    groups = {}
    for case in all_cases:
        src = case["source_case"]
        if src not in groups:
            groups[src] = []
        groups[src].append(case)
    
    # 每组按 2:1 划分
    for src, cases in groups.items():
        random.shuffle(cases)
        split_idx = max(1, len(cases) * 2 // 3)  # 至少 1 条进 held_out
        train_set.extend(cases[:split_idx])
        held_out_set.extend(cases[split_idx:])
    
    return {
        "version": "1.0.0",
        "total": len(all_cases),
        "train_size": len(train_set),
        "held_out_size": len(held_out_set),
        "seed": seed,
        "train": train_set,
        "held_out": held_out_set,
        "metadata": {
            "source_breakdown": {
                "2026-10-31": len(CASES_1031),
                "history_succeeded": 5,
                "history_failed": 5,
                "zombie": 5,
            },
            "verdict_breakdown": {
                "dispatch": sum(1 for c in all_cases if c["ground_truth"]["verdict"] == "dispatch"),
                "reject": sum(1 for c in all_cases if c["ground_truth"]["verdict"] == "reject"),
                "mechanical": sum(1 for c in all_cases if c["ground_truth"]["verdict"] == "mechanical"),
                "merge": sum(1 for c in all_cases if c["ground_truth"]["verdict"] == "merge"),
            }
        }
    }


def save_annotation_set(output_dir: str = ".") -> str:
    """保存标注集到文件"""
    dataset = build_annotation_set()
    path = Path(output_dir) / "golden_annotation_set.json"
    path.write_text(json.dumps(dataset, indent=2, ensure_ascii=False))
    return str(path)


if __name__ == "__main__":
    path = save_annotation_set()
    print(f"标注集已保存: {path}")
    dataset = build_annotation_set()
    print(f"总计: {dataset['total']} 条")
    print(f"训练集: {dataset['train_size']} 条")
    print(f"Held-out: {dataset['held_out_size']} 条")
    print(f"Verdict 分布: {dataset['metadata']['verdict_breakdown']}")
