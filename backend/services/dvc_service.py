"""
DVC 数据版本控制服务
数据集/模型版本追踪，支持 Git 后端
"""
import os
import subprocess
import json
from pathlib import Path
from datetime import datetime


class DVCDataVersion:
    """DVC 数据版本管理"""

    DATA_DIR = "/app/uploads/data"
    MODELS_DIR = "/app/uploads/models"

    def __init__(self, repo_dir="/app"):
        self.repo_dir = Path(repo_dir)
        self._dvc_dir = self.repo_dir / ".dvc"

    def init(self):
        """初始化 DVC"""
        self._dvc_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["dvc", "init", "--subdir"],
            cwd=self.repo_dir,
            capture_output=True, text=True,
        )
        return {"status": "initialized" if result.returncode == 0 else "error",
                "message": result.stderr[:200]}

    def track_data(self, file_path: str, description: str = ""):
        """追踪数据文件版本"""
        full_path = Path(file_path)
        if not full_path.exists():
            return {"error": f"File not found: {file_path}"}

        result = subprocess.run(
            ["dvc", "add", str(full_path)],
            cwd=full_path.parent,
            capture_output=True, text=True,
        )

        # Read generated .dvc file
        dvc_file = Path(str(full_path) + ".dvc")
        metadata = {}
        if dvc_file.exists():
            import yaml
            with open(dvc_file) as f:
                metadata = yaml.safe_load(f)

        return {
            "file": str(full_path),
            "tracked": result.returncode == 0,
            "dvc_file": str(dvc_file) if dvc_file.exists() else None,
            "md5": metadata.get("outs", [{}])[0].get("md5", "unknown") if metadata else "unknown",
            "size": full_path.stat().st_size,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def list_tracked(self):
        """列出所有版本追踪的文件"""
        dvc_files = list(Path(self.DATA_DIR).glob("*.dvc")) + \
                    list(Path(self.MODELS_DIR).glob("*.dvc"))
        return [
            {
                "name": f.stem,
                "dvc_file": str(f),
                "size": f.stat().st_size,
            }
            for f in dvc_files
        ]

    def checkout(self, file_path: str):
        """检出文件历史版本"""
        result = subprocess.run(
            ["dvc", "checkout", file_path],
            cwd=self.repo_dir,
            capture_output=True, text=True,
        )
        return {
            "file": file_path,
            "status": "checked_out" if result.returncode == 0 else "error",
            "message": result.stderr[:200],
        }

    def get_status(self):
        """获取 DVC 状态"""
        return {
            "dvc_dir": str(self._dvc_dir),
            "exists": self._dvc_dir.exists(),
            "tracked_files": len(self.list_tracked()),
        }
