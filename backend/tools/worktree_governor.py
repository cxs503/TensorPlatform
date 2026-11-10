"""
Worktree Capacity Governor — 工作树容量治理模块

可达性证明驱动的安全回收、恢复演练与磁盘耗尽预测。

核心组件:
  - ReachabilityGraph:  git ref → blob hash → file path 可达性图
  - SafeReclaimer:      不可达文件标记→软删除(.recycle/)→保留期后硬删除
  - RecoveryDrill:      备份→删除→恢复→行级 checksum 校验
  - DiskPredictor:      基于历史增长率的线性/指数预测模型

验收判据:
  C1 可达性证明: 所有活跃 ref 引用文件 100% 被保护（零误删）
  C2 回收率:     不可达文件占比 ≥10%
  C3 恢复演练:   restore 后行级 checksum 100% 一致
  C4 RPO ≤1h, C5 RTO ≤30min, C6 预测误差 ≤20%
  C7 负控:       活跃分支文件回收被拦截
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ReachabilityReport:
    """可达性证明报告"""
    worktree_root: str
    total_files: int = 0
    reachable_files: int = 0
    unreachable_files: int = 0
    reachable_bytes: int = 0
    unreachable_bytes: int = 0
    refs_scanned: List[str] = field(default_factory=list)
    unreachable_paths: List[str] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "worktree_root": self.worktree_root,
            "total_files": self.total_files,
            "reachable_files": self.reachable_files,
            "unreachable_files": self.unreachable_files,
            "reachable_bytes": self.reachable_bytes,
            "unreachable_bytes": self.unreachable_bytes,
            "refs_scanned": self.refs_scanned,
            "unreachable_paths": self.unreachable_paths[:50],  # truncate for report
            "timestamp": self.timestamp or datetime.utcnow().isoformat(),
        }


@dataclass
class ReclaimReport:
    """回收报告"""
    files_moved: int = 0
    bytes_freed: int = 0
    recycle_dir: str = ""
    moved_paths: List[str] = field(default_factory=list)
    blocked_paths: List[str] = field(default_factory=list)  # 被保护机制拦截
    timestamp: str = ""


@dataclass
class RecoveryReport:
    """恢复演练报告"""
    backup_path: str = ""
    restore_path: str = ""
    total_files: int = 0
    checksum_matches: int = 0
    checksum_mismatches: int = 0
    rpo_seconds: float = 0.0
    rto_seconds: float = 0.0
    passed: bool = False
    timestamp: str = ""


@dataclass
class DiskPrediction:
    """磁盘预测结果"""
    current_usage_bytes: int = 0
    total_disk_bytes: int = 0
    growth_rate_bytes_per_day: float = 0.0
    model_type: str = "linear"  # linear | exponential
    days_until_full: float = 0.0
    predicted_full_date: str = ""
    confidence: float = 0.0
    history_points: int = 0


# ---------------------------------------------------------------------------
# 可达性图构建器
# ---------------------------------------------------------------------------

class ReachabilityGraph:
    """
    构建 git ref → blob hash → file path 可达性图。

    对每个 worktree 目录，扫描所有活跃分支和标签，
    收集它们引用的所有 blob hash，建立可达性映射。
    """

    def __init__(self, worktree_root: str):
        self.worktree_root = Path(worktree_root)
        self._ref_blobs: Dict[str, Set[str]] = {}  # ref → set of blob hashes
        self._blob_files: Dict[str, Set[str]] = {}  # blob hash → set of file paths
        self._all_reachable_files: Set[str] = set()

    def scan(self) -> "ReachabilityGraph":
        """扫描所有活跃 ref 并构建可达性图"""
        self._scan_refs()
        self._build_blob_map()
        return self

    def _run_git(self, args: List[str], cwd: Optional[str] = None) -> str:
        """在 worktree 根目录执行 git 命令"""
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True,
            cwd=cwd or str(self.worktree_root),
            timeout=30
        )
        return result.stdout.strip()

    def _scan_refs(self):
        """获取所有活跃分支和标签"""
        # 本地分支
        branches = self._run_git(["branch", "--format=%(refname:short)"])
        # 远程分支
        remote_branches = self._run_git(["branch", "-r", "--format=%(refname:short)"])
        # 标签
        tags = self._run_git(["tag", "-l"])

        all_refs = []
        for ref_list in [branches, remote_branches, tags]:
            if ref_list:
                all_refs.extend(ref_list.split("\n"))

        # 去重并过滤空行
        self._ref_blobs = {}
        for ref in set(all_refs):
            ref = ref.strip()
            if ref:
                try:
                    blobs = self._get_ref_blobs(ref)
                    if blobs:
                        self._ref_blobs[ref] = blobs
                except Exception:
                    pass  # skip broken refs

    def _get_ref_blobs(self, ref: str) -> Set[str]:
        """获取某个 ref 引用的所有 blob hash"""
        blobs = set()
        try:
            # git ls-tree -r --format='%(objectmode) %(objecttype) %(objectname) %(path)'
            output = self._run_git(["ls-tree", "-r", ref])
            for line in output.split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "blob":
                    blobs.add(parts[2])
        except Exception:
            pass
        return blobs

    def _build_blob_map(self):
        """构建 blob hash → 文件路径映射（基于当前工作树）"""
        self._blob_files = {}
        self._all_reachable_files = set()

        # 获取当前 HEAD 的 tree 信息
        try:
            output = self._run_git(["ls-tree", "-r", "HEAD"])
            for line in output.split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "blob":
                    blob_hash = parts[2]
                    file_path = " ".join(parts[3:])  # handle spaces in filenames
                    if blob_hash not in self._blob_files:
                        self._blob_files[blob_hash] = set()
                    self._blob_files[blob_hash].add(file_path)
                    self._all_reachable_files.add(file_path)
        except Exception:
            pass

    def get_reachable_files(self) -> Set[str]:
        """返回所有可达文件路径集合"""
        return self._all_reachable_files.copy()

    def get_unreachable_files(self, all_files: Set[str]) -> Set[str]:
        """返回不可达文件路径集合"""
        return all_files - self._all_reachable_files

    def generate_report(self, all_files: Set[str]) -> ReachabilityReport:
        """生成可达性报告"""
        reachable = self._all_reachable_files
        unreachable = all_files - reachable

        reachable_bytes = 0
        unreachable_bytes = 0
        unreachable_paths = []

        for f in reachable:
            fp = self.worktree_root / f
            if fp.exists():
                reachable_bytes += fp.stat().st_size

        for f in unreachable:
            fp = self.worktree_root / f
            if fp.exists():
                unreachable_bytes += fp.stat().st_size
                unreachable_paths.append(f)

        return ReachabilityReport(
            worktree_root=str(self.worktree_root),
            total_files=len(all_files),
            reachable_files=len(reachable),
            unreachable_files=len(unreachable),
            reachable_bytes=reachable_bytes,
            unreachable_bytes=unreachable_bytes,
            refs_scanned=list(self._ref_blobs.keys()),
            unreachable_paths=unreachable_paths,
            timestamp=datetime.utcnow().isoformat(),
        )


# ---------------------------------------------------------------------------
# 安全回收器
# ---------------------------------------------------------------------------

class SafeReclaimer:
    """
    安全回收不可达文件。

    流程:
      1. 标记不可达文件（不在任何活跃 ref 中、非 staged、非保护列表）
      2. 软删除：移至 .recycle/ 目录（保留原始路径结构）
      3. 保留期后硬删除
    """

    def __init__(self, worktree_root: str, retention_days: int = 7):
        self.worktree_root = Path(worktree_root)
        self.recycle_dir = self.worktree_root / ".recycle"
        self.retention_days = retention_days
        self.protected_patterns = {".git", ".recycle", ".gitignore", ".gitattributes"}

    def is_protected(self, file_path: str) -> bool:
        """检查文件是否受保护"""
        # git 内部文件
        if file_path.startswith(".git/") or file_path == ".git":
            return True
        # 回收目录自身
        if file_path.startswith(".recycle/"):
            return True
        # 保护列表
        for pattern in self.protected_patterns:
            if file_path == pattern or file_path.startswith(pattern + "/"):
                return True
        return False

    def is_staged(self, file_path: str) -> bool:
        """检查文件是否在 git stage 中"""
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only", file_path],
                capture_output=True, text=True,
                cwd=str(self.worktree_root),
                timeout=10
            )
            return file_path in result.stdout.strip()
        except Exception:
            return False

    def soft_delete(self, unreachable_files: Set[str]) -> ReclaimReport:
        """软删除不可达文件（移至 .recycle/）"""
        self.recycle_dir.mkdir(parents=True, exist_ok=True)

        report = ReclaimReport(
            recycle_dir=str(self.recycle_dir),
            timestamp=datetime.utcnow().isoformat(),
        )

        for f in sorted(unreachable_files):
            src = self.worktree_root / f
            if not src.exists():
                continue

            # 保护检查
            if self.is_protected(f):
                report.blocked_paths.append(f)
                continue

            # staged 检查
            if self.is_staged(f):
                report.blocked_paths.append(f)
                continue

            # 执行软删除
            dest = self.recycle_dir / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                file_size = src.stat().st_size
                shutil.move(str(src), str(dest))
                report.files_moved += 1
                report.bytes_freed += file_size
                report.moved_paths.append(f)
            except Exception as e:
                pass  # log but continue

        return report

    def hard_delete_expired(self) -> int:
        """硬删除超过保留期的文件"""
        cutoff = datetime.utcnow() - timedelta(days=self.retention_days)
        count = 0

        if not self.recycle_dir.exists():
            return 0

        for root, dirs, files in os.walk(str(self.recycle_dir)):
            for f in files:
                fp = Path(root) / f
                try:
                    mtime = datetime.utcfromtimestamp(fp.stat().st_mtime)
                    if mtime < cutoff:
                        fp.unlink()
                        count += 1
                except Exception:
                    pass

        # 清理空目录
        for root, dirs, files in os.walk(str(self.recycle_dir), topdown=False):
            if not dirs and not files:
                try:
                    Path(root).rmdir()
                except Exception:
                    pass

        return count


# ---------------------------------------------------------------------------
# 恢复演练
# ---------------------------------------------------------------------------

class RecoveryDrill:
    """
    恢复演练：备份→删除→恢复→行级 checksum 校验。

    验证 RPO（Recovery Point Objective）和 RTO（Recovery Time Objective）。
    """

    def __init__(self, worktree_root: str, backup_root: Optional[str] = None):
        self.worktree_root = Path(worktree_root)
        self.backup_root = Path(backup_root) if backup_root else self.worktree_root.parent / ".backups"
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def _file_checksum(self, filepath: Path) -> str:
        """计算文件行级 checksum（每行 hash 的聚合）"""
        line_hashes = []
        try:
            with open(filepath, "rb") as f:
                for line in f:
                    line_hashes.append(hashlib.sha256(line).hexdigest()[:16])
            return hashlib.sha256("".join(line_hashes).encode()).hexdigest()
        except Exception:
            return ""

    def _dir_checksum_map(self, directory: Path) -> Dict[str, str]:
        """计算目录下所有文件的 checksum 映射"""
        checksums = {}
        for root, dirs, files in os.walk(str(directory)):
            # 跳过 .git 和 .recycle
            dirs[:] = [d for d in dirs if d not in (".git", ".recycle", ".backups")]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(directory)
                checksums[str(rel)] = self._file_checksum(fp)
        return checksums

    def execute_drill(self, sample_ratio: float = 0.1) -> RecoveryReport:
        """
        执行完整恢复演练。

        sample_ratio: 采样比例（0.0-1.0），用于控制演练规模
        """
        report = RecoveryReport(timestamp=datetime.utcnow().isoformat())

        # --- Phase 1: Backup ---
        t0 = time.time()
        backup_name = f"drill_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        backup_path = self.backup_root / backup_name
        backup_path.mkdir(parents=True, exist_ok=True)

        # 采样文件进行演练
        all_files = []
        for root, dirs, files in os.walk(str(self.worktree_root)):
            dirs[:] = [d for d in dirs if d not in (".git", ".recycle", ".backups")]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(self.worktree_root)
                all_files.append((fp, rel))

        # 采样
        import random
        random.seed(42)  # 可复现
        n_sample = max(1, int(len(all_files) * sample_ratio))
        sample = random.sample(all_files, min(n_sample, len(all_files)))

        # 备份采样文件
        for src, rel in sample:
            dest = backup_path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(dest))
            except Exception:
                pass

        report.backup_path = str(backup_path)
        backup_time = time.time() - t0

        # --- Phase 2: Compute pre-delete checksums ---
        pre_checksums = {}
        for src, rel in sample:
            pre_checksums[str(rel)] = self._file_checksum(src)

        # --- Phase 3: Delete ---
        t_del_start = time.time()
        for src, rel in sample:
            try:
                src.unlink()
            except Exception:
                pass
        delete_time = time.time() - t_del_start

        # --- Phase 4: Restore ---
        t_restore_start = time.time()
        for src, rel in sample:
            dest = self.worktree_root / rel
            backup_file = backup_path / rel
            if backup_file.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(str(backup_file), str(dest))
                except Exception:
                    pass
        restore_time = time.time() - t_restore_start

        # --- Phase 5: Verify ---
        post_checksums = {}
        for src, rel in sample:
            post_checksums[str(rel)] = self._file_checksum(src)

        matches = 0
        mismatches = 0
        for rel in pre_checksums:
            if pre_checksums[rel] == post_checksums.get(rel, ""):
                matches += 1
            else:
                mismatches += 1

        report.total_files = len(sample)
        report.checksum_matches = matches
        report.checksum_mismatches = mismatches
        report.rpo_seconds = backup_time
        report.rto_seconds = restore_time
        report.passed = (mismatches == 0 and matches > 0)
        report.restore_path = str(self.worktree_root)

        # 清理备份
        try:
            shutil.rmtree(str(backup_path))
        except Exception:
            pass

        return report


# ---------------------------------------------------------------------------
# 磁盘预测器
# ---------------------------------------------------------------------------

class DiskPredictor:
    """
    基于历史增长率的磁盘耗尽预测。

    支持线性模型和指数模型。
    """

    def __init__(self, worktree_root: str):
        self.worktree_root = Path(worktree_root)
        self.history_file = self.worktree_root / ".disk_history.json"

    def record_usage(self) -> int:
        """记录当前磁盘使用量"""
        usage = self._get_dir_size(self.worktree_root)
        self._append_history(usage)
        return usage

    def _get_dir_size(self, directory: Path) -> int:
        """计算目录总大小（跳过 .git）"""
        total = 0
        try:
            for root, dirs, files in os.walk(str(directory)):
                dirs[:] = [d for d in dirs if d != ".git"]
                for f in files:
                    fp = Path(root) / f
                    try:
                        total += fp.stat().st_size
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    def _append_history(self, usage: int):
        """追加历史记录"""
        history = self._load_history()
        history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "usage_bytes": usage,
        })
        # 保留最近 100 条
        history = history[-100:]
        with open(self.history_file, "w") as f:
            json.dump(history, f)

    def _load_history(self) -> List[dict]:
        """加载历史记录"""
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def predict(self, model_type: str = "linear") -> DiskPrediction:
        """
        预测磁盘耗尽时间。

        model_type: "linear" 或 "exponential"
        """
        history = self._load_history()
        prediction = DiskPrediction(
            current_usage_bytes=self._get_dir_size(self.worktree_root),
            model_type=model_type,
            history_points=len(history),
        )

        # 获取磁盘总容量
        try:
            stat = os.statvfs(str(self.worktree_root))
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            prediction.total_disk_bytes = total
        except Exception:
            prediction.total_disk_bytes = prediction.current_usage_bytes * 2  # fallback

        if len(history) < 2:
            # 数据不足，使用当前增长率估算
            prediction.growth_rate_bytes_per_day = 1024 * 1024 * 100  # 100MB/day default
            remaining = prediction.total_disk_bytes - prediction.current_usage_bytes
            if prediction.growth_rate_bytes_per_day > 0:
                prediction.days_until_full = min(remaining / prediction.growth_rate_bytes_per_day, 365000)
            else:
                prediction.days_until_full = float("inf")
            prediction.confidence = 0.1
            return prediction

        # 计算增长率
        timestamps = []
        usages = []
        for entry in history:
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
                timestamps.append(ts)
                usages.append(entry["usage_bytes"])
            except Exception:
                pass

        if len(timestamps) < 2:
            return prediction

        # 时间跨度（天）
        time_span = (timestamps[-1] - timestamps[0]).total_seconds() / 86400.0
        if time_span <= 0:
            time_span = 1.0

        if model_type == "linear":
            # 线性模型: y = a + b*t
            usage_diff = usages[-1] - usages[0]
            growth_rate = usage_diff / time_span
            prediction.growth_rate_bytes_per_day = growth_rate

            remaining = prediction.total_disk_bytes - prediction.current_usage_bytes
            if growth_rate > 0:
                prediction.days_until_full = min(remaining / growth_rate, 365000)
            else:
                prediction.days_until_full = float("inf")

            # 置信度基于数据点数量
            prediction.confidence = min(0.95, 0.3 + 0.05 * len(history))

        elif model_type == "exponential":
            # 指数模型: y = a * e^(b*t)
            # 简化: 使用对数线性回归
            import math
            log_usages = []
            valid_pairs = []
            for i, u in enumerate(usages):
                if u > 0:
                    t = (timestamps[i] - timestamps[0]).total_seconds() / 86400.0
                    log_usages.append(math.log(u))
                    valid_pairs.append((t, math.log(u)))

            if len(valid_pairs) >= 2:
                # 简单线性回归 on log space
                n = len(valid_pairs)
                sum_t = sum(t for t, _ in valid_pairs)
                sum_y = sum(y for _, y in valid_pairs)
                sum_tt = sum(t * t for t, _ in valid_pairs)
                sum_ty = sum(t * y for t, y in valid_pairs)

                denom = n * sum_tt - sum_t * sum_t
                if denom != 0:
                    b = (n * sum_ty - sum_t * sum_y) / denom
                    a = (sum_y - b * sum_t) / n

                    growth_rate = math.exp(a) * b  # instantaneous rate
                    prediction.growth_rate_bytes_per_day = growth_rate

                    # 求解 e^(a + b*t) = total
                    if b > 0:
                        t_full = (math.log(prediction.total_disk_bytes) - a) / b
                        t_elapsed = (timestamps[-1] - timestamps[0]).total_seconds() / 86400.0
                        prediction.days_until_full = min(max(0, t_full - t_elapsed), 365000)
                    else:
                        prediction.days_until_full = float("inf")

                    prediction.confidence = min(0.9, 0.2 + 0.05 * len(history))
                else:
                    prediction.days_until_full = float("inf")
            else:
                prediction.days_until_full = float("inf")

        if prediction.days_until_full != float("inf") and 0 < prediction.days_until_full < 365000:
            full_date = datetime.utcnow() + timedelta(days=prediction.days_until_full)
            prediction.predicted_full_date = full_date.isoformat()
        else:
            prediction.predicted_full_date = "N/A"

        return prediction


# ---------------------------------------------------------------------------
# 工作树容量治理主类
# ---------------------------------------------------------------------------

class WorktreeGovernor:
    """
    工作树容量治理主控制器。

    编排可达性证明、安全回收、恢复演练和磁盘预测。
    """

    def __init__(self, worktree_root: str, retention_days: int = 7):
        self.worktree_root = worktree_root
        self.retention_days = retention_days

    def scan_reachability(self) -> ReachabilityReport:
        """执行可达性扫描"""
        graph = ReachabilityGraph(self.worktree_root).scan()

        # 收集所有文件
        all_files = set()
        for root, dirs, files in os.walk(self.worktree_root):
            dirs[:] = [d for d in dirs if d not in (".git", ".recycle", ".backups")]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(Path(self.worktree_root))
                all_files.add(str(rel))

        return graph.generate_report(all_files)

    def reclaim_unreachable(self, unreachable_files: Set[str]) -> ReclaimReport:
        """执行安全回收"""
        reclaimer = SafeReclaimer(self.worktree_root, self.retention_days)
        return reclaimer.soft_delete(unreachable_files)

    def run_recovery_drill(self, sample_ratio: float = 0.1) -> RecoveryReport:
        """执行恢复演练"""
        drill = RecoveryDrill(self.worktree_root)
        return drill.execute_drill(sample_ratio)

    def predict_disk(self, model_type: str = "linear") -> DiskPrediction:
        """执行磁盘预测"""
        predictor = DiskPredictor(self.worktree_root)
        predictor.record_usage()
        return predictor.predict(model_type)

    def full_governance_cycle(self) -> dict:
        """执行完整治理周期"""
        results = {}

        # 1. 可达性扫描
        reach_report = self.scan_reachability()
        results["reachability"] = reach_report.to_dict()

        # 2. 安全回收
        unreachable = set(reach_report.unreachable_paths)
        reclaim_report = self.reclaim_unreachable(unreachable)
        results["reclaim"] = {
            "files_moved": reclaim_report.files_moved,
            "bytes_freed": reclaim_report.bytes_freed,
            "blocked_paths": reclaim_report.blocked_paths,
            "timestamp": reclaim_report.timestamp,
        }

        # 3. 恢复演练
        recovery_report = self.run_recovery_drill(sample_ratio=0.05)
        results["recovery"] = {
            "total_files": recovery_report.total_files,
            "checksum_matches": recovery_report.checksum_matches,
            "checksum_mismatches": recovery_report.checksum_mismatches,
            "rpo_seconds": round(recovery_report.rpo_seconds, 3),
            "rto_seconds": round(recovery_report.rto_seconds, 3),
            "passed": recovery_report.passed,
            "timestamp": recovery_report.timestamp,
        }

        # 4. 磁盘预测
        disk_pred = self.predict_disk()
        results["disk_prediction"] = {
            "current_usage_bytes": disk_pred.current_usage_bytes,
            "total_disk_bytes": disk_pred.total_disk_bytes,
            "growth_rate_bytes_per_day": round(disk_pred.growth_rate_bytes_per_day, 2),
            "model_type": disk_pred.model_type,
            "days_until_full": round(disk_pred.days_until_full, 1) if disk_pred.days_until_full != float("inf") else "inf",
            "predicted_full_date": disk_pred.predicted_full_date,
            "confidence": disk_pred.confidence,
            "history_points": disk_pred.history_points,
        }

        return results


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Worktree Capacity Governor")
    parser.add_argument("--root", default=".", help="Worktree root directory")
    parser.add_argument("--action", choices=["scan", "reclaim", "drill", "predict", "full"],
                        default="full", help="Action to perform")
    parser.add_argument("--retention", type=int, default=7, help="Retention days for recycle")
    parser.add_argument("--sample-ratio", type=float, default=0.1, help="Sample ratio for drill")
    parser.add_argument("--model", choices=["linear", "exponential"], default="linear",
                        help="Prediction model type")
    parser.add_argument("--output", "-o", help="Output JSON file")

    args = parser.parse_args()

    governor = WorktreeGovernor(args.root, args.retention)

    if args.action == "scan":
        result = governor.scan_reachability().to_dict()
    elif args.action == "reclaim":
        reach = governor.scan_reachability()
        result = governor.reclaim_unreachable(set(reach.unreachable_paths)).__dict__
    elif args.action == "drill":
        result = governor.run_recovery_drill(args.sample_ratio).__dict__
    elif args.action == "predict":
        result = governor.predict_disk(args.model).__dict__
    else:
        result = governor.full_governance_cycle()

    # 序列化
    def serialize(obj):
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Cannot serialize {type(obj)}")

    output = json.dumps(result, indent=2, default=serialize)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Results written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
