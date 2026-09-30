"""
JupyterLab 管理 API
"""
import json
import os
import subprocess
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
import urllib.request
from pydantic import BaseModel

router = APIRouter(prefix="/jupyter", tags=["JupyterLab"])

# 笔记本目录
NOTEBOOK_DIR = "/app/uploads/notebooks"


class CreateNotebookReq(BaseModel):
    name: str
    kernel: str = "python3"


class TemplateReq(BaseModel):
    template: str


@router.get("/status")
def get_jupyter_status():
    """获取JupyterLab服务状态"""
    try:
        # Use HTTP check instead of systemctl
        req = urllib.request.Request("http://localhost:8888/jupyter/api/status")
        resp = urllib.request.urlopen(req, timeout=5)
        running = resp.status == 200

        notebooks_count = 0
        if os.path.exists(NOTEBOOK_DIR):
            notebooks_count = len(
                [f for f in os.listdir(NOTEBOOK_DIR) if f.endswith(".ipynb")]
            )

        return {
            "running": running,
            "url": "/jupyter/" if running else None,
            "active_kernels": 0,
            "running_notebooks": 0,
            "notebooks_count": notebooks_count,
            "status": "running" if running else "stopped",
        }
    except Exception as e:
        return {"running": False, "error": str(e)}


@router.post("/start")
def start_jupyter():
    """启动JupyterLab服务"""
    try:
        # Check if already running
        try:
            req = urllib.request.Request("http://localhost:8888/jupyter/api/status")
            urllib.request.urlopen(req, timeout=3)
            return {"message": "JupyterLab已在运行", "status": "running"}
        except:
            pass
        
        # Ensure runtime directories exist
        os.makedirs("/home/foundation/.local/share/jupyter/runtime", exist_ok=True)
        os.makedirs("/tmp/jupyter_runtime", exist_ok=True)
        
        # Start jupyter in background
        subprocess.Popen(
            ["/opt/venv/bin/jupyter", "lab",
             "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root",
             "--ServerApp.token=''", "--ServerApp.password=''",
             "--ServerApp.allow_origin='*'",
             "--ServerApp.base_url=/jupyter/",
             "--notebook-dir=/app/uploads"],
            env={**os.environ, "JUPYTER_RUNTIME_DIR": "/tmp/jupyter_runtime"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return {"message": "JupyterLab服务启动中", "status": "starting"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动失败: {str(e)}")


@router.post("/stop")
def stop_jupyter():
    """停止JupyterLab服务"""
    try:
        subprocess.run(["pkill", "-f", "jupyter-lab"], capture_output=True)
        return {"message": "JupyterLab服务已停止", "status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"停止失败: {str(e)}")


@router.get("/notebooks")
def list_notebooks():
    """列出所有笔记本"""
    notebooks = []
    if os.path.exists(NOTEBOOK_DIR):
        for f in os.listdir(NOTEBOOK_DIR):
            if f.endswith(".ipynb"):
                path = os.path.join(NOTEBOOK_DIR, f)
                stat = os.stat(path)
                notebooks.append(
                    {
                        "name": f,
                        "path": f,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "size": stat.st_size,
                        "kernel": "python3",
                    }
                )
    return {"total": len(notebooks), "items": notebooks}


@router.post("/notebooks")
def create_notebook(req: CreateNotebookReq):
    """创建新笔记本"""
    name = req.name
    if not name.endswith(".ipynb"):
        name += ".ipynb"

    path = os.path.join(NOTEBOOK_DIR, name)
    if os.path.exists(path):
        raise HTTPException(status_code=400, detail="笔记本已存在")

    # 创建空白笔记本
    notebook_content = {
        "cells": [],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": req.kernel,
            },
            "language_info": {"name": "python", "version": "3.9.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }

    os.makedirs(NOTEBOOK_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(notebook_content, f)

    return {"name": name, "path": name, "message": "笔记本创建成功"}


@router.post("/create-from-template")
def create_from_template(req: TemplateReq):
    """从模板创建笔记本"""
    templates = {
        "data-analysis": {
            "name": "data_analysis.ipynb",
            "cells": [
                {"cell_type": "markdown", "source": ["# 数据分析\n", "\n", "导入必要的库"]},
                {
                    "cell_type": "code",
                    "source": [
                        "import pandas as pd\n",
                        "import numpy as np\n",
                        "import matplotlib.pyplot as plt\n",
                        "%matplotlib inline",
                    ],
                },
            ],
        },
        "model-training": {
            "name": "model_training.ipynb",
            "cells": [
                {"cell_type": "markdown", "source": ["# 模型训练\n", "\n", "导入必要的库"]},
                {
                    "cell_type": "code",
                    "source": [
                        "import pandas as pd\n",
                        "from sklearn.model_selection import train_test_split\n",
                        "from sklearn.ensemble import RandomForestClassifier",
                    ],
                },
            ],
        },
        "visualization": {
            "name": "visualization.ipynb",
            "cells": [
                {"cell_type": "markdown", "source": ["# 数据可视化\n", "\n", "导入必要的库"]},
                {
                    "cell_type": "code",
                    "source": [
                        "import matplotlib.pyplot as plt\n",
                        "import seaborn as sns\n",
                        "%matplotlib inline",
                    ],
                },
            ],
        },
        "blank": {"name": "untitled.ipynb", "cells": []},
    }

    template = templates.get(req.template, templates["blank"])
    name = template["name"]

    # 确保名称唯一
    base, ext = os.path.splitext(name)
    counter = 1
    path = os.path.join(NOTEBOOK_DIR, name)
    while os.path.exists(path):
        name = f"{base}_{counter}{ext}"
        path = os.path.join(NOTEBOOK_DIR, name)
        counter += 1

    # 创建笔记本
    notebook_content = {
        "cells": [
            {
                "cell_type": c["cell_type"],
                "source": c["source"],
                "metadata": {},
                "execution_count": None,
                "outputs": [],
            }
            for c in template["cells"]
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            }
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }

    os.makedirs(NOTEBOOK_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(notebook_content, f)

    return {"notebook": {"name": name, "path": name}, "message": "笔记本创建成功"}


@router.get("/runs")
def list_recent_runs():
    """获取最近运行记录"""
    return {"total": 0, "items": []}
