"""
模型版本管理与A/B Testing服务
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 模型存储目录
MODEL_DIR = Path("/app/uploads/models")
try:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    try:
        MODEL_DIR = Path("/app/uploads/data")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        MODEL_DIR = Path("/tmp/foundation_models")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)


class ModelVersion:
    """模型版本"""

    def __init__(
        self,
        model_id: str,
        version: str,
        description: str = "",
        metrics: Dict[str, float] = None,
        tags: List[str] = None,
    ):
        self.model_id = model_id
        self.version = version
        self.description = description
        self.metrics = metrics or {}
        self.tags = tags or []
        self.created_at = datetime.now().isoformat()
        self.is_current = False


class ModelVersionManager:
    """模型版本管理器"""

    def __init__(self):
        self.versions: Dict[str, List[ModelVersion]] = {}
        self._load_versions()

    def _load_versions(self):
        """从文件加载版本"""
        registry_file = MODEL_DIR / "versions.json"
        if registry_file.exists():
            with open(registry_file, "r") as f:
                data = json.load(f)
                for model_id, versions in data.items():
                    self.versions[model_id] = [
                        ModelVersion(
                            v["model_id"],
                            v["version"],
                            v.get("description", ""),
                            v.get("metrics", {}),
                            v.get("tags", []),
                        )
                        for v in versions
                    ]

    def _save_versions(self):
        """保存版本到文件"""
        data = {}
        for model_id, versions in self.versions.items():
            data[model_id] = [
                {
                    "model_id": v.model_id,
                    "version": v.version,
                    "description": v.description,
                    "metrics": v.metrics,
                    "tags": v.tags,
                    "created_at": v.created_at,
                    "is_current": v.is_current,
                }
                for v in versions
            ]
        registry_file = MODEL_DIR / "versions.json"
        with open(registry_file, "w") as f:
            json.dump(data, f, indent=2)

    def create_version(
        self,
        model_id: str,
        description: str = "",
        metrics: Dict[str, float] = None,
        tags: List[str] = None,
    ) -> ModelVersion:
        """创建新版本"""
        if model_id not in self.versions:
            self.versions[model_id] = []

        # 生成版本号
        existing = self.versions[model_id]
        if existing:
            last_version = existing[-1].version
            major, minor = last_version.split(".")
            version = f"{major}.{int(minor) + 1}"
        else:
            version = "1.0"

        version_obj = ModelVersion(model_id, version, description, metrics, tags)
        version_obj.is_current = True

        # 更新旧版本
        for v in existing:
            v.is_current = False

        existing.append(version_obj)
        self._save_versions()

        return version_obj

    def get_versions(self, model_id: str) -> List[ModelVersion]:
        """获取所有版本"""
        return self.versions.get(model_id, [])

    def get_version(self, model_id: str, version: str) -> Optional[ModelVersion]:
        """获取指定版本"""
        versions = self.versions.get(model_id, [])
        for v in versions:
            if v.version == version:
                return v
        return None

    def get_current_version(self, model_id: str) -> Optional[ModelVersion]:
        """获取当前版本"""
        versions = self.versions.get(model_id, [])
        for v in versions:
            if v.is_current:
                return v
        return versions[-1] if versions else None

    def set_current_version(self, model_id: str, version: str) -> bool:
        """设置当前版本"""
        versions = self.versions.get(model_id, [])
        for v in versions:
            v.is_current = v.version == version
        self._save_versions()
        return True

    def rollback(self, model_id: str, version: str) -> bool:
        """回滚到指定版本"""
        return self.set_current_version(model_id, version)

    def compare_versions(
        self, model_id: str, version1: str, version2: str
    ) -> Dict[str, Any]:
        """对比两个版本"""
        v1 = self.get_version(model_id, version1)
        v2 = self.get_version(model_id, version2)

        if not v1 or not v2:
            raise ValueError("Version not found")

        comparison = {
            "version1": version1,
            "version2": version2,
            "metrics_comparison": {},
        }

        # 对比指标
        all_metrics = set(v1.metrics.keys()) | set(v2.metrics.keys())
        for metric in all_metrics:
            val1 = v1.metrics.get(metric, 0)
            val2 = v2.metrics.get(metric, 0)
            diff = val2 - val1
            diff_pct = (diff / val1 * 100) if val1 != 0 else 0
            comparison["metrics_comparison"][metric] = {
                "version1": val1,
                "version2": val2,
                "diff": diff,
                "diff_percent": round(diff_pct, 2),
            }

        return comparison


class ABTest:
    """A/B测试"""

    def __init__(
        self,
        name: str,
        model_id: str,
        versions: List[str],
        traffic_split: Dict[str, int],
    ):
        self.test_id = str(uuid.uuid4())[:8]
        self.name = name
        self.model_id = model_id
        self.versions = versions
        self.traffic_split = traffic_split
        self.status = "running"
        self.created_at = datetime.now().isoformat()
        self.results = {}

    def get_version(self, traffic_percentage: int) -> str:
        """根据流量百分比获取版本"""
        cumulative = 0
        for version, split in self.traffic_split.items():
            cumulative += split
            if traffic_percentage <= cumulative:
                return version
        return self.versions[-1]


class ABTestingManager:
    """A/B测试管理器"""

    def __init__(self):
        self.tests: Dict[str, ABTest] = {}
        self._load_tests()

    def _load_tests(self):
        """从文件加载测试"""
        tests_file = MODEL_DIR / "ab_tests.json"
        if tests_file.exists():
            with open(tests_file, "r") as f:
                data = json.load(f)
                for test_id, test_data in data.items():
                    test = ABTest(
                        test_data["name"],
                        test_data["model_id"],
                        test_data["versions"],
                        test_data["traffic_split"],
                    )
                    test.test_id = test_id
                    test.status = test_data.get("status", "running")
                    test.results = test_data.get("results", {})
                    self.tests[test_id] = test

    def _save_tests(self):
        """保存测试到文件"""
        data = {
            test_id: {
                "name": test.name,
                "model_id": test.model_id,
                "versions": test.versions,
                "traffic_split": test.traffic_split,
                "status": test.status,
                "results": test.results,
            }
            for test_id, test in self.tests.items()
        }
        tests_file = MODEL_DIR / "ab_tests.json"
        with open(tests_file, "w") as f:
            json.dump(data, f, indent=2)

    def create_test(
        self,
        name: str,
        model_id: str,
        versions: List[str],
        traffic_split: Dict[str, int],
    ) -> ABTest:
        """创建A/B测试"""
        # 验证流量分配总和为100
        if sum(traffic_split.values()) != 100:
            raise ValueError("Traffic split must sum to 100")

        test = ABTest(name, model_id, versions, traffic_split)
        self.tests[test.test_id] = test
        self._save_tests()

        return test

    def get_test(self, test_id: str) -> Optional[ABTest]:
        """获取测试"""
        return self.tests.get(test_id)

    def list_tests(self, status: Optional[str] = None) -> List[ABTest]:
        """列出测试"""
        tests = list(self.tests.values())
        if status:
            tests = [t for t in tests if t.status == status]
        return tests

    def record_result(self, test_id: str, version: str, result: Dict[str, Any]):
        """记录测试结果"""
        test = self.tests.get(test_id)
        if not test:
            return

        if version not in test.results:
            test.results[version] = {
                "total": 0,
                "success": 0,
                "failure": 0,
                "latency_sum": 0,
            }

        test.results[version]["total"] += 1
        if result.get("success"):
            test.results[version]["success"] += 1
        else:
            test.results[version]["failure"] += 1
        test.results[version]["latency_sum"] += result.get("latency", 0)

        self._save_tests()

    def get_test_results(self, test_id: str) -> Dict[str, Any]:
        """获取测试结果"""
        test = self.tests.get(test_id)
        if not test:
            return {}

        results = {}
        for version, data in test.results.items():
            results[version] = {
                "total": data["total"],
                "success_rate": data["success"] / data["total"]
                if data["total"] > 0
                else 0,
                "failure_rate": data["failure"] / data["total"]
                if data["total"] > 0
                else 0,
                "avg_latency": data["latency_sum"] / data["total"]
                if data["total"] > 0
                else 0,
            }

        return results

    def stop_test(self, test_id: str) -> bool:
        """停止测试"""
        test = self.tests.get(test_id)
        if test:
            test.status = "stopped"
            self._save_tests()
            return True
        return False


# 全局实例
_version_manager: Optional[ModelVersionManager] = None
_ab_testing_manager: Optional[ABTestingManager] = None


def get_version_manager() -> ModelVersionManager:
    """获取版本管理器"""
    global _version_manager
    if _version_manager is None:
        _version_manager = ModelVersionManager()
    return _version_manager


def get_ab_testing_manager() -> ABTestingManager:
    """获取A/B测试管理器"""
    global _ab_testing_manager
    if _ab_testing_manager is None:
        _ab_testing_manager = ABTestingManager()
    return _ab_testing_manager
