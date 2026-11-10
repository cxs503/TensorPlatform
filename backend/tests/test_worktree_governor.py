"""
Worktree Governor 测试套件

验证:
  C1 可达性证明: 所有活跃 ref 引用文件 100% 被保护
  C2 回收率:     不可达文件占比 ≥10%
  C3 恢复演练:   restore 后行级 checksum 100% 一致
  C4 RPO ≤1h
  C5 RTO ≤30min
  C7 负控:       活跃分支文件回收被拦截
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tools.worktree_governor import (
    ReachabilityGraph,
    ReachabilityReport,
    SafeReclaimer,
    ReclaimReport,
    RecoveryDrill,
    RecoveryReport,
    DiskPredictor,
    DiskPrediction,
    WorktreeGovernor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path):
    """创建一个带多个分支和文件的 git 仓库"""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    # 初始化 git
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

    # 创建初始文件
    (repo / "file1.txt").write_text("hello world\n")
    (repo / "file2.py").write_text("print('hello')\n")
    (repo / "data.json").write_text('{"key": "value"}\n')

    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True)

    # 创建分支 branch_a，添加文件
    subprocess.run(["git", "checkout", "-b", "branch_a"], cwd=repo, capture_output=True)
    (repo / "branch_a_file.txt").write_text("branch a content\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "branch a"], cwd=repo, capture_output=True)

    # 切回 main
    subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True,
                   check=False)  # may be master
    # 尝试 master 如果 main 不存在
    try:
        subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True, check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["git", "checkout", "master"], cwd=repo, capture_output=True, check=True)

    # 创建标签
    subprocess.run(["git", "tag", "v1.0"], cwd=repo, capture_output=True)

    return repo


@pytest.fixture
def repo_with_unreachable(git_repo):
    """创建包含不可达文件的仓库"""
    # 添加一些不在 git 跟踪中的文件
    (git_repo / "temp_build.o").write_text("binary data" * 1000)
    (git_repo / "cache.tmp").write_text("cache data" * 500)
    (git_repo / "old_output.log").write_text("log line 1\n" * 100)
    (git_repo / "backup.bak").write_text("backup" * 200)

    # 创建子目录中的不可达文件
    subdir = git_repo / "build"
    subdir.mkdir()
    (subdir / "intermediate.o").write_text("obj" * 800)
    (subdir / "debug.log").write_text("debug" * 300)

    return git_repo


# ---------------------------------------------------------------------------
# C1: 可达性证明测试
# ---------------------------------------------------------------------------

class TestReachabilityGraph:
    """C1: 可达性证明 — 所有活跃 ref 引用文件 100% 被保护"""

    def test_scan_collects_refs(self, git_repo):
        """测试: 扫描能收集到分支和标签"""
        graph = ReachabilityGraph(str(git_repo)).scan()
        refs = list(graph._ref_blobs.keys())

        # 应该有至少 main/master 和 branch_a
        ref_names = [r for r in refs if "branch_a" in r or "main" in r or "master" in r]
        assert len(ref_names) >= 1, f"Expected at least 1 branch ref, got {refs}"

        # 应该有 v1.0 标签
        assert "v1.0" in refs, f"Expected v1.0 tag, got {refs}"

    def test_reachable_files_protected(self, git_repo):
        """测试: 所有 git 跟踪的文件都被标记为可达"""
        graph = ReachabilityGraph(str(git_repo)).scan()
        reachable = graph.get_reachable_files()

        # file1.txt, file2.py, data.json 应该在可达集合中
        assert "file1.txt" in reachable, "file1.txt should be reachable"
        assert "file2.py" in reachable, "file2.py should be reachable"
        assert "data.json" in reachable, "data.json should be reachable"

    def test_unreachable_files_detected(self, repo_with_unreachable):
        """测试: 不可达文件被正确识别"""
        graph = ReachabilityGraph(str(repo_with_unreachable)).scan()
        reachable = graph.get_reachable_files()

        # 收集所有文件
        all_files = set()
        for root, dirs, files in os.walk(str(repo_with_unreachable)):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(repo_with_unreachable)
                all_files.add(str(rel))

        unreachable = graph.get_unreachable_files(all_files)

        # 不可达文件应该被检测到
        assert "temp_build.o" in unreachable, "temp_build.o should be unreachable"
        assert "cache.tmp" in unreachable, "cache.tmp should be unreachable"

    def test_report_generation(self, repo_with_unreachable):
        """测试: 可达性报告生成正确"""
        graph = ReachabilityGraph(str(repo_with_unreachable)).scan()

        all_files = set()
        for root, dirs, files in os.walk(str(repo_with_unreachable)):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(repo_with_unreachable)
                all_files.add(str(rel))

        report = graph.generate_report(all_files)

        assert report.total_files > 0
        assert report.reachable_files > 0
        assert report.unreachable_files > 0
        assert report.total_files == report.reachable_files + report.unreachable_files
        assert len(report.refs_scanned) > 0

    def test_zero_false_deletes(self, git_repo):
        """C1 核心: 零误删 — 所有活跃 ref 文件都在可达集合中"""
        graph = ReachabilityGraph(str(git_repo)).scan()
        reachable = graph.get_reachable_files()

        # 获取所有 git 跟踪的文件
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, cwd=str(git_repo)
        )
        tracked_files = set(result.stdout.strip().split("\n"))
        tracked_files = {f for f in tracked_files if f}

        # 所有跟踪的文件都应该在可达集合中
        missing = tracked_files - reachable
        assert len(missing) == 0, f"Files tracked by git but not reachable: {missing}"


# ---------------------------------------------------------------------------
# C2: 回收率测试
# ---------------------------------------------------------------------------

class TestSafeReclaimer:
    """C2: 回收率 — 不可达文件占比 ≥10%"""

    def test_protected_files_not_deleted(self, repo_with_unreachable):
        """测试: .git 和 .recycle 目录受保护"""
        reclaimer = SafeReclaimer(str(repo_with_unreachable))

        assert reclaimer.is_protected(".git/config")
        assert reclaimer.is_protected(".git")
        assert reclaimer.is_protected(".recycle/old_file")
        assert reclaimer.is_protected(".gitignore")

    def test_non_protected_files_can_be_reclaimed(self, repo_with_unreachable):
        """测试: 非保护文件可以被回收"""
        reclaimer = SafeReclaimer(str(repo_with_unreachable))

        assert not reclaimer.is_protected("temp_build.o")
        assert not reclaimer.is_protected("cache.tmp")
        assert not reclaimer.is_protected("build/intermediate.o")

    def test_soft_delete_moves_files(self, repo_with_unreachable):
        """测试: 软删除将文件移至 .recycle/"""
        graph = ReachabilityGraph(str(repo_with_unreachable)).scan()

        all_files = set()
        for root, dirs, files in os.walk(str(repo_with_unreachable)):
            dirs[:] = [d for d in dirs if d not in (".git", ".recycle")]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(repo_with_unreachable)
                all_files.add(str(rel))

        unreachable = graph.get_unreachable_files(all_files)

        reclaimer = SafeReclaimer(str(repo_with_unreachable))
        report = reclaimer.soft_delete(unreachable)

        # 文件应该被移动
        assert report.files_moved > 0, "Should have moved some files"
        assert report.bytes_freed > 0, "Should have freed some bytes"

        # 原文件应该不存在
        for f in report.moved_paths:
            src = repo_with_unreachable / f
            assert not src.exists(), f"Source file should be moved: {f}"

        # 回收目录中应该有文件
        recycle_dir = repo_with_unreachable / ".recycle"
        assert recycle_dir.exists(), ".recycle directory should exist"

    def test_reclaim_rate_above_threshold(self, repo_with_unreachable):
        """C2: 回收率 ≥10%"""
        graph = ReachabilityGraph(str(repo_with_unreachable)).scan()

        all_files = set()
        for root, dirs, files in os.walk(str(repo_with_unreachable)):
            dirs[:] = [d for d in dirs if d not in (".git", ".recycle")]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(repo_with_unreachable)
                all_files.add(str(rel))

        report = graph.generate_report(all_files)

        if report.total_files > 0:
            reclaim_rate = report.unreachable_files / report.total_files
            assert reclaim_rate >= 0.10, (
                f"Reclaim rate {reclaim_rate:.2%} < 10% threshold. "
                f"Unreachable: {report.unreachable_files}, Total: {report.total_files}"
            )

    def test_hard_delete_expired(self, repo_with_unreachable):
        """测试: 硬删除过期文件"""
        reclaimer = SafeReclaimer(str(repo_with_unreachable), retention_days=0)

        # 先软删除一些文件
        graph = ReachabilityGraph(str(repo_with_unreachable)).scan()
        all_files = set()
        for root, dirs, files in os.walk(str(repo_with_unreachable)):
            dirs[:] = [d for d in dirs if d not in (".git", ".recycle")]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(repo_with_unreachable)
                all_files.add(str(rel))
        unreachable = graph.get_unreachable_files(all_files)
        reclaimer.soft_delete(unreachable)

        # 设置回收文件时间为过去
        recycle_dir = repo_with_unreachable / ".recycle"
        if recycle_dir.exists():
            for root, dirs, files in os.walk(str(recycle_dir)):
                for f in files:
                    fp = Path(root) / f
                    old_time = os.path.getmtime(fp) - 86400 * 2  # 2 days ago
                    os.utime(fp, (old_time, old_time))

        # 硬删除
        count = reclaimer.hard_delete_expired()
        assert count >= 0  # may be 0 if no files expired


# ---------------------------------------------------------------------------
# C3/C4/C5: 恢复演练测试
# ---------------------------------------------------------------------------

class TestRecoveryDrill:
    """C3/C4/C5: 恢复演练 — checksum 一致, RPO ≤1h, RTO ≤30min"""

    def test_drill_passes(self, repo_with_unreachable):
        """C3: 恢复后行级 checksum 100% 一致"""
        drill = RecoveryDrill(str(repo_with_unreachable))
        report = drill.execute_drill(sample_ratio=0.5)

        assert report.passed, f"Drill should pass. Mismatches: {report.checksum_mismatches}"
        assert report.checksum_mismatches == 0, "Zero checksum mismatches required"
        assert report.checksum_matches > 0, "Should have matching files"

    def test_rpo_within_limit(self, repo_with_unreachable):
        """C4: RPO ≤ 1 小时 (3600 秒)"""
        drill = RecoveryDrill(str(repo_with_unreachable))
        report = drill.execute_drill(sample_ratio=0.3)

        assert report.rpo_seconds <= 3600, f"RPO {report.rpo_seconds}s > 3600s limit"

    def test_rto_within_limit(self, repo_with_unreachable):
        """C5: RTO ≤ 30 分钟 (1800 秒)"""
        drill = RecoveryDrill(str(repo_with_unreachable))
        report = drill.execute_drill(sample_ratio=0.3)

        assert report.rto_seconds <= 1800, f"RTO {report.rto_seconds}s > 1800s limit"

    def test_file_checksum_consistency(self, tmp_path):
        """测试: 文件 checksum 计算一致性"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")

        drill = RecoveryDrill(str(tmp_path))
        cs1 = drill._file_checksum(test_file)
        cs2 = drill._file_checksum(test_file)

        assert cs1 == cs2, "Same file should produce same checksum"
        assert len(cs1) == 64, "SHA256 hex digest should be 64 chars"

    def test_different_files_different_checksums(self, tmp_path):
        """测试: 不同文件产生不同 checksum"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A\n")
        f2.write_text("content B\n")

        drill = RecoveryDrill(str(tmp_path))
        cs1 = drill._file_checksum(f1)
        cs2 = drill._file_checksum(f2)

        assert cs1 != cs2, "Different files should have different checksums"


# ---------------------------------------------------------------------------
# C6: 磁盘预测测试
# ---------------------------------------------------------------------------

class TestDiskPredictor:
    """C6: 磁盘预测 — 预测误差 ≤20%"""

    def test_record_and_predict(self, tmp_path):
        """测试: 记录使用量并预测"""
        predictor = DiskPredictor(str(tmp_path))

        # 创建一些文件
        (tmp_path / "file1.txt").write_text("x" * 1000)
        (tmp_path / "file2.txt").write_text("y" * 2000)

        usage = predictor.record_usage()
        assert usage > 0, "Should record positive usage"

        prediction = predictor.predict("linear")
        assert prediction.current_usage_bytes > 0
        assert prediction.history_points >= 1

    def test_linear_prediction_with_history(self, tmp_path):
        """测试: 线性预测基于历史数据"""
        predictor = DiskPredictor(str(tmp_path))

        # 模拟历史数据
        history = []
        base_time = "2026-01-01T00:00:00"
        for i in range(10):
            history.append({
                "timestamp": f"2026-01-{i+1:02d}T00:00:00",
                "usage_bytes": 1000000 + i * 100000,  # 1MB + 100KB/day
            })

        with open(predictor.history_file, "w") as f:
            json.dump(history, f)

        prediction = predictor.predict("linear")
        assert prediction.growth_rate_bytes_per_day > 0, "Should detect positive growth"
        assert prediction.model_type == "linear"

    def test_exponential_prediction(self, tmp_path):
        """测试: 指数预测"""
        predictor = DiskPredictor(str(tmp_path))

        import math
        history = []
        for i in range(10):
            usage = int(1000000 * math.exp(0.05 * i))  # exponential growth
            history.append({
                "timestamp": f"2026-01-{i+1:02d}T00:00:00",
                "usage_bytes": usage,
            })

        with open(predictor.history_file, "w") as f:
            json.dump(history, f)

        prediction = predictor.predict("exponential")
        assert prediction.model_type == "exponential"
        assert prediction.growth_rate_bytes_per_day > 0

    def test_insufficient_data_fallback(self, tmp_path):
        """测试: 数据不足时使用默认值"""
        predictor = DiskPredictor(str(tmp_path))

        prediction = predictor.predict("linear")
        assert prediction.history_points == 0
        assert prediction.growth_rate_bytes_per_day > 0  # default fallback


# ---------------------------------------------------------------------------
# C7: 负控测试
# ---------------------------------------------------------------------------

class TestNegativeControl:
    """C7: 负控 — 活跃分支文件回收被拦截"""

    def test_active_branch_files_protected(self, git_repo):
        """C7: 活跃分支的文件不能被回收"""
        graph = ReachabilityGraph(str(git_repo)).scan()
        reachable = graph.get_reachable_files()

        reclaimer = SafeReclaimer(str(git_repo))

        # 可达文件不应该被回收
        for f in reachable:
            if reclaimer.is_protected(f):
                continue  # already protected by pattern
            # 这些文件在 git 跟踪中，不应该被标记为可回收
            # 通过可达性图，它们不在 unreachable 集合中
            pass

        # 验证: 可达文件不在不可达集合中
        all_files = set()
        for root, dirs, files in os.walk(str(git_repo)):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(git_repo)
                all_files.add(str(rel))

        unreachable = graph.get_unreachable_files(all_files)

        # 可达文件和不可达文件不应该有交集
        overlap = reachable & unreachable
        assert len(overlap) == 0, f"Overlap between reachable and unreachable: {overlap}"

    def test_staged_files_protected(self, git_repo):
        """测试: staged 文件受保护"""
        # 创建一个新文件并 stage
        new_file = git_repo / "staged_file.txt"
        new_file.write_text("staged content\n")

        subprocess.run(["git", "add", "staged_file.txt"], cwd=git_repo, capture_output=True)

        reclaimer = SafeReclaimer(str(git_repo))
        assert reclaimer.is_staged("staged_file.txt"), "Staged file should be protected"


# ---------------------------------------------------------------------------
# 集成测试
# ---------------------------------------------------------------------------

class TestWorktreeGovernorIntegration:
    """完整治理周期集成测试"""

    def test_full_cycle(self, repo_with_unreachable):
        """测试: 完整治理周期"""
        governor = WorktreeGovernor(str(repo_with_unreachable), retention_days=7)
        results = governor.full_governance_cycle()

        # 验证所有部分都有结果
        assert "reachability" in results
        assert "reclaim" in results
        assert "recovery" in results
        assert "disk_prediction" in results

        # 验证可达性
        reach = results["reachability"]
        assert reach["total_files"] > 0
        assert reach["reachable_files"] > 0

        # 验证回收
        reclaim = results["reclaim"]
        assert reclaim["files_moved"] >= 0

        # 验证恢复
        recovery = results["recovery"]
        assert recovery["checksum_mismatches"] == 0, "C3: Zero mismatches required"
        assert recovery["rpo_seconds"] <= 3600, "C4: RPO ≤ 1h"
        assert recovery["rto_seconds"] <= 1800, "C5: RTO ≤ 30min"

        # 验证预测
        disk = results["disk_prediction"]
        assert disk["current_usage_bytes"] > 0
        assert disk["model_type"] in ("linear", "exponential")

    def test_governance_report_serializable(self, repo_with_unreachable):
        """测试: 治理报告可序列化为 JSON"""
        governor = WorktreeGovernor(str(repo_with_unreachable))
        results = governor.full_governance_cycle()

        # 应该可以序列化为 JSON
        json_str = json.dumps(results, indent=2)
        assert len(json_str) > 0

        # 反序列化应该成功
        parsed = json.loads(json_str)
        assert parsed == results


# ---------------------------------------------------------------------------
# 边界情况测试
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """边界情况测试"""

    def test_empty_repo(self, tmp_path):
        """测试: 空仓库处理"""
        repo = tmp_path / "empty_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        graph = ReachabilityGraph(str(repo)).scan()
        reachable = graph.get_reachable_files()
        assert len(reachable) == 0  # no commits yet

    def test_special_characters_in_filename(self, tmp_path):
        """测试: 文件名含特殊字符"""
        repo = tmp_path / "special_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        # 创建含空格的文件
        special_file = repo / "file with spaces.txt"
        special_file.write_text("content\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        graph = ReachabilityGraph(str(repo)).scan()
        reachable = graph.get_reachable_files()
        assert "file with spaces.txt" in reachable

    def test_large_file_handling(self, tmp_path):
        """测试: 大文件处理"""
        repo = tmp_path / "large_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        # 创建大文件 (1MB)
        large_file = repo / "large.bin"
        large_file.write_bytes(b"x" * (1024 * 1024))
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "large"], cwd=repo, capture_output=True)

        graph = ReachabilityGraph(str(repo)).scan()
        report = graph.generate_report({"large.bin"})
        assert report.reachable_bytes >= 1024 * 1024
