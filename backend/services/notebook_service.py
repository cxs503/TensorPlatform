"""
Jupyter Notebook服务
支持在线代码编写、执行和管理
"""
import json
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Notebook存储目录
NOTEBOOK_DIR = Path("/app/uploads/open-digital-foundation/backend/notebooks")
NOTEBOOK_DIR.mkdir(parents=True, exist_ok=True)


class Notebook:
    """Jupyter Notebook"""

    def __init__(self, notebook_id: str, name: str, owner: str):
        self.id = notebook_id
        self.name = name
        self.owner = owner
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.content = self._create_empty_notebook()
        self.status = "idle"  # idle, running, error

    def _create_empty_notebook(self) -> dict:
        """创建空Notebook"""
        return {
            "cells": [],
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {"name": "python", "version": "3.9.0"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }

    def add_cell(self, cell_type: str = "code", source: str = "") -> str:
        """添加单元格"""
        cell_id = str(uuid.uuid4())
        cell = {
            "cell_type": cell_type,
            "metadata": {},
            "source": source,
            "outputs": [],
            "execution_count": None,
        }
        self.content["cells"].append(cell)
        self.updated_at = datetime.now().isoformat()
        return cell_id

    def update_cell(self, cell_index: int, source: str):
        """更新单元格"""
        if 0 <= cell_index < len(self.content["cells"]):
            self.content["cells"][cell_index]["source"] = source
            self.updated_at = datetime.now().isoformat()

    def delete_cell(self, cell_index: int):
        """删除单元格"""
        if 0 <= cell_index < len(self.content["cells"]):
            self.content["cells"].pop(cell_index)
            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "cells_count": len(self.content["cells"]),
        }


class NotebookManager:
    """Notebook管理器"""

    def __init__(self):
        self.notebooks: Dict[str, Notebook] = {}
        self._load_notebooks()

    def _load_notebooks(self):
        """从文件加载notebooks — 先清空旧数据保证一致性"""
        self.notebooks.clear()
        index_file = NOTEBOOK_DIR / "index.json"
        if index_file.exists():
            with open(index_file, "r") as f:
                data = json.load(f)
                for nb_data in data:
                    nb = Notebook(nb_data["id"], nb_data["name"], nb_data["owner"])
                    nb.created_at = nb_data.get("created_at", nb.created_at)
                    nb.updated_at = nb_data.get("updated_at", nb.updated_at)
                    self.notebooks[nb.id] = nb

    def _save_notebooks(self):
        """保存notebooks索引"""
        data = [nb.to_dict() for nb in self.notebooks.values()]
        index_file = NOTEBOOK_DIR / "index.json"
        with open(index_file, "w") as f:
            json.dump(data, f, indent=2)

    def create_notebook(self, name: str, owner: str) -> Notebook:
        """创建Notebook"""
        notebook_id = f"nb_{uuid.uuid4().hex[:8]}"
        notebook = Notebook(notebook_id, name, owner)
        self.notebooks[notebook_id] = notebook

        # 保存文件
        self._save_notebook_file(notebook)

        self._save_notebooks()
        return notebook

    def get_notebook(self, notebook_id: str) -> Optional[Notebook]:
        """获取Notebook — 先从文件重载保证多worker一致"""
        self._load_notebooks()
        return self.notebooks.get(notebook_id)

    def list_notebooks(self, owner: str = None) -> List[Dict]:
        """列出Notebooks — 每次从文件重新加载，避免多worker不一致"""
        self._load_notebooks()
        notebooks = list(self.notebooks.values())
        if owner:
            notebooks = [nb for nb in notebooks if nb.owner == owner]
        return [nb.to_dict() for nb in notebooks]

    def update_notebook(self, notebook_id: str, content: dict) -> bool:
        """更新Notebook内容 — 先从文件重载保证多worker一致"""
        self._load_notebooks()
        notebook = self.notebooks.get(notebook_id)
        if not notebook:
            return False

        notebook.content = content
        notebook.updated_at = datetime.now().isoformat()

        # 保存到文件
        self._save_notebook_file(notebook)
        self._save_notebooks()

        return True

    def delete_notebook(self, notebook_id: str) -> bool:
        """删除Notebook — 先从文件重载保证多worker一致"""
        self._load_notebooks()
        if notebook_id in self.notebooks:
            # 删除文件
            notebook_file = NOTEBOOK_DIR / f"{notebook_id}.ipynb"
            if notebook_file.exists():
                notebook_file.unlink()

            del self.notebooks[notebook_id]
            self._save_notebooks()
            return True
        return False

    def _save_notebook_file(self, notebook: Notebook):
        """保存Notebook文件"""
        notebook_file = NOTEBOOK_DIR / f"{notebook.id}.ipynb"
        with open(notebook_file, "w") as f:
            json.dump(notebook.content, f, indent=2)

    def load_notebook_file(self, notebook_id: str) -> dict:
        """加载Notebook文件"""
        notebook_file = NOTEBOOK_DIR / f"{notebook_id}.ipynb"
        if notebook_file.exists():
            with open(notebook_file, "r") as f:
                return json.load(f)
        return {}


# 全局实例
_notebook_manager: Optional[NotebookManager] = None


def get_notebook_manager() -> NotebookManager:
    """获取Notebook管理器"""
    global _notebook_manager
    if _notebook_manager is None:
        _notebook_manager = NotebookManager()
    return _notebook_manager


# 代码执行器
class CodeExecutor:
    """Python代码执行器"""

    def __init__(self):
        self.results: Dict[str, Dict] = {}

    def execute_code(
        self, code: str, notebook_id: str = None, cell_index: int = None
    ) -> Dict:
        """执行Python代码"""
        import io
        import sys

        # 捕获输出
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        result = {"success": False, "output": "", "error": "", "execution_count": 1}

        try:
            # 执行代码
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture

            try:
                exec(code, {})
                result["success"] = True
                result["output"] = stdout_capture.getvalue()
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

        except Exception as e:
            result["error"] = str(e)
            result["output"] = stdout_capture.getvalue()

        return result

    def execute_notebook_cell(
        self, notebook_id: str, cell_index: int, code: str
    ) -> Dict:
        """执行Notebook单元格"""
        return self.execute_code(code, notebook_id, cell_index)


# 全局执行器
_code_executor: Optional[CodeExecutor] = None


def get_code_executor() -> CodeExecutor:
    """获取代码执行器"""
    global _code_executor
    if _code_executor is None:
        _code_executor = CodeExecutor()
    return _code_executor
