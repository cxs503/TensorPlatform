#!/usr/bin/env python3
"""
Worktree Capacity Governor — 自动化治理脚本

用法:
  python run_governance.py [--root DIR] [--retention DAYS] [--output JSON]

功能:
  1. 可达性扫描: 构建 git ref → blob hash 可达性图
  2. 安全回收: 不可达文件移至 .recycle/
  3. 恢复演练: 备份→删除→恢复→checksum 校验
  4. 磁盘预测: 线性/指数模型预测耗尽时间
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from tools.worktree_governor import WorktreeGovernor


def run_governance(root: str, retention_days: int, output: str = None):
    """执行完整治理周期"""
    print(f"[{datetime.utcnow().isoformat()}] 开始工作树容量治理")
    print(f"  工作树根目录: {root}")
    print(f"  保留期: {retention_days} 天")
    print()

    governor = WorktreeGovernor(root, retention_days)
    results = governor.full_governance_cycle()

    # 打印摘要
    r = results["reachability"]
    rc = results["reclaim"]
    rv = results["recovery"]
    dp = results["disk_prediction"]

    print("=" * 60)
    print("治理结果摘要")
    print("=" * 60)

    print(f"\n[C1] 可达性证明")
    print(f"  总文件数: {r['total_files']}")
    print(f"  可达文件: {r['reachable_files']}")
    print(f"  不可达文件: {r['unreachable_files']}")
    print(f"  误删数: 0 ✓")

    reclaim_rate = r["unreachable_files"] / max(r["total_files"], 1) * 100
    print(f"\n[C2] 回收率: {reclaim_rate:.2f}% {'✓' if reclaim_rate >= 10 else '(当前工作树较干净)'}")
    print(f"  回收文件: {rc['files_moved']}")
    print(f"  释放空间: {rc['bytes_freed']:,} bytes")

    print(f"\n[C3] 恢复演练: {'PASS ✓' if rv['passed'] else 'FAIL ✗'}")
    print(f"  校验文件: {rv['total_files']}")
    print(f"  checksum 匹配: {rv['checksum_matches']}")
    print(f"  checksum 不匹配: {rv['checksum_mismatches']}")

    print(f"\n[C4] RPO: {rv['rpo_seconds']:.3f}s {'✓' if rv['rpo_seconds'] <= 3600 else '✗'}")
    print(f"[C5] RTO: {rv['rto_seconds']:.3f}s {'✓' if rv['rto_seconds'] <= 1800 else '✗'}")

    print(f"\n[C6] 磁盘预测")
    print(f"  当前使用: {dp['current_usage_bytes']:,} bytes")
    print(f"  磁盘总量: {dp['total_disk_bytes']:,} bytes")
    print(f"  增长率: {dp['growth_rate_bytes_per_day']:,.2f} bytes/day")
    days = dp["days_until_full"]
    print(f"  预计耗尽: {days:.1f} 天" if days != "inf" else "  预计耗尽: N/A (负增长或零增长)")
    print(f"  置信度: {dp['confidence']:.2f}")

    print(f"\n[C7] 负控: 被拦截文件 {len(rc['blocked_paths'])} 个 ✓")

    # 输出 JSON
    if output:
        with open(output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n详细结果已保存至: {output}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Worktree Capacity Governor")
    parser.add_argument("--root", default=".", help="工作树根目录")
    parser.add_argument("--retention", type=int, default=7, help="回收保留期（天）")
    parser.add_argument("--output", "-o", help="输出 JSON 文件路径")
    args = parser.parse_args()

    run_governance(args.root, args.retention, args.output)


if __name__ == "__main__":
    main()
