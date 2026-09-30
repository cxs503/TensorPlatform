"""
Jupyter Notebook API
"""
from typing import List, Optional

from api.deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.notebook_service import get_code_executor, get_notebook_manager

router = APIRouter(prefix="/notebooks", tags=["Notebook开发环境"])


class CreateNotebookReq(BaseModel):
    name: str


class UpdateNotebookReq(BaseModel):
    content: dict


class ExecuteCodeReq(BaseModel):
    code: str


@router.post("")
def create_notebook(
    req: CreateNotebookReq, current_user: dict = Depends(get_current_user)
):
    """创建新的Notebook"""
    manager = get_notebook_manager()
    owner = (current_user or {}).get("username", "anonymous")

    notebook = manager.create_notebook(req.name, owner)
    notebook.add_cell("code", "# Welcome!\nprint('Hello, Open Foundation!')")
    manager._save_notebook_file(notebook)
    manager._save_notebooks()
    return {
        "id": notebook.id,
        "name": notebook.name,
        "owner": notebook.owner,
        "created_at": notebook.created_at,
    }


@router.get("")
def list_notebooks(current_user: dict = Depends(get_current_user)):
    """列出用户的Notebooks"""
    manager = get_notebook_manager()
    owner = (current_user or {}).get("username", "anonymous")
    notebooks = manager.list_notebooks(owner)
    return {"total": len(notebooks), "items": notebooks}


@router.get("/kernels")
def list_kernels():
    """列出可用的Jupyter内核"""
    return {"items": [
        {"name": "python3", "display_name": "Python 3", "language": "python"},
        {"name": "pyspark", "display_name": "PySpark", "language": "python"},
    ]}

@router.get("/templates")
def get_notebook_templates():
    """获取Notebook模板"""
    return {
        "templates": [
            {
                "id": "data_analysis",
                "name": "数据分析模板",
                "description": "包含数据加载、清洗、分析、可视化的完整流程",
            },
            {
                "id": "ml_training",
                "name": "机器学习训练模板",
                "description": "数据准备→模型训练→评估的端到端流程",
            },
            {
                "id": "model_evaluation",
                "name": "模型评估模板",
                "description": "全面的模型性能评估和可视化",
            },
            {
                "id": "feature_engineering",
                "name": "特征工程模板",
                "description": "特征创建、选择和转换示例",
            },
        ]
    }

@router.get("/{notebook_id}")
def get_notebook(notebook_id: str, current_user: dict = Depends(get_current_user)):
    """获取Notebook详情"""
    manager = get_notebook_manager()
    notebook = manager.get_notebook(notebook_id)

    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook不存在")

    # 加载完整内容
    content = manager.load_notebook_file(notebook_id)

    return {
        "id": notebook.id,
        "name": notebook.name,
        "owner": notebook.owner,
        "created_at": notebook.created_at,
        "updated_at": notebook.updated_at,
        "content": content,
        "cells_count": len(content.get("cells", [])),
    }


@router.put("/{notebook_id}")
def update_notebook(
    notebook_id: str,
    req: UpdateNotebookReq,
    current_user: dict = Depends(get_current_user),
):
    """更新Notebook内容"""
    manager = get_notebook_manager()
    notebook = manager.get_notebook(notebook_id)

    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook不存在")

    # 检查权限
    owner = (current_user or {}).get("username", "anonymous")
    if notebook.owner != owner:
        raise HTTPException(status_code=403, detail="无权限操作")

    success = manager.update_notebook(notebook_id, req.content)
    if success:
        return {"message": "Notebook已更新"}
    raise HTTPException(status_code=500, detail="更新失败")


@router.delete("/{notebook_id}")
def delete_notebook(notebook_id: str, current_user: dict = Depends(get_current_user)):
    """删除Notebook"""
    manager = get_notebook_manager()
    notebook = manager.get_notebook(notebook_id)

    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook不存在")

    # 检查权限
    owner = (current_user or {}).get("username", "anonymous")
    if notebook.owner != owner:
        raise HTTPException(status_code=403, detail="无权限操作")

    success = manager.delete_notebook(notebook_id)
    if success:
        return {"message": "Notebook已删除"}
    raise HTTPException(status_code=500, detail="删除失败")


@router.post("/{notebook_id}/delete")
def delete_notebook_post(notebook_id: str, current_user: dict = Depends(get_current_user)):
    """前端兼容: POST方式删除"""
    manager = get_notebook_manager()
    notebook = manager.get_notebook(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook不存在")
    owner = (current_user or {}).get("username", "anonymous")
    if notebook.owner != owner:
        raise HTTPException(status_code=403, detail="无权限操作")
    success = manager.delete_notebook(notebook_id)
    if success:
        return {"message": "Notebook已删除"}
    raise HTTPException(status_code=500, detail="删除失败")


@router.post("/{notebook_id}/cells")
def add_cell(
    notebook_id: str,
    cell_type: str = "code",
    current_user: dict = Depends(get_current_user),
):
    """添加单元格"""
    manager = get_notebook_manager()
    notebook = manager.get_notebook(notebook_id)

    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook不存在")

    cell_id = notebook.add_cell(cell_type)
    manager._save_notebook_file(notebook)
    manager._save_notebooks()

    return {"cell_id": cell_id, "message": "单元格已添加"}


@router.delete("/{notebook_id}/cells/{cell_index}")
def delete_cell(
    notebook_id: str, cell_index: int, current_user: dict = Depends(get_current_user)
):
    """删除单元格"""
    manager = get_notebook_manager()
    notebook = manager.get_notebook(notebook_id)

    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook不存在")

    notebook.delete_cell(cell_index)
    manager._save_notebook_file(notebook)
    manager._save_notebooks()

    return {"message": "单元格已删除"}


@router.post("/execute")
def execute_code(req: ExecuteCodeReq):
    """执行Python代码"""
    executor = get_code_executor()
    result = executor.execute_code(req.code)
    return result


@router.post("/{notebook_id}/cells/{cell_index}/execute")
def execute_cell(notebook_id: str, cell_index: int, req: ExecuteCodeReq):
    """执行Notebook单元格"""
    executor = get_code_executor()
    result = executor.execute_notebook_cell(notebook_id, cell_index, req.code)
    return result


@router.get("/templates")
def get_notebook_templates():
    """获取Notebook模板"""
    return {
        "templates": [
            {
                "id": "data_analysis",
                "name": "数据分析模板",
                "description": "包含数据加载、清洗、分析、可视化的完整流程",
            },
            {
                "id": "model_training",
                "name": "模型训练模板",
                "description": "包含数据预处理、模型训练、评估的完整流程",
            },
            {
                "id": "time_series",
                "name": "时序预测模板",
                "description": "包含时序数据处理、特征工程、预测的完整流程",
            },
        ]
    }


@router.get("/jupyter/status")
def jupyter_status():
    """Get Jupyter service status — 通过HTTP检测8888端口, 403=CSRF保护说明服务运行中"""
    import urllib.request, urllib.error, json
    try:
        req = urllib.request.Request("http://localhost:8888/jupyter/api/status")
        try:
            resp = urllib.request.urlopen(req, timeout=3)
            data = json.loads(resp.read().decode())
            return {"running": True, "url": "/jupyter/lab", "active_kernels": data.get("kernels", 0), "running_notebooks": data.get("connections", 0)}
        except urllib.error.HTTPError as e:
            if e.code == 403:
                return {"running": True, "url": "/jupyter/lab", "active_kernels": 0, "running_notebooks": 0}
    except:
        pass
    return {"running": False, "url": "/jupyter/lab"}

@router.post("/jupyter/start")
def jupyter_start():
    import subprocess, os, sys
    env = os.environ.copy()
    env["JUPYTER_RUNTIME_DIR"] = "/tmp/jupyter_runtime"
    env["JUPYTER_CONFIG_DIR"] = "/tmp/jupyter_config"
    os.makedirs("/tmp/jupyter_runtime", exist_ok=True)
    os.makedirs("/tmp/jupyter_config", exist_ok=True)
    try:
        subprocess.Popen([sys.executable, "-m", "jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--ServerApp.token=", "--ServerApp.allow_origin=*", "--ServerApp.base_url=/jupyter/", "--notebook-dir=/app/uploads/open-digital-foundation/backend/notebooks"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        return {"status": "started", "port": 8888}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.post("/jupyter/stop")
def jupyter_stop():
    import subprocess
    try:
        subprocess.run(["kill", "-9"] + [p for p in os.listdir("/proc") if p.isdigit() and b"jupyter" in open(f"/proc/{p}/cmdline","rb").read()], capture_output=True)
    except:
        pass
    return {"status": "stopped"}

@router.get("/jupyter/notebooks")
def jupyter_notebooks():
    import os
    nb_dir = "/app/uploads/open-digital-foundation/backend/notebooks"
    items = []
    if os.path.isdir(nb_dir):
        for f in sorted(os.listdir(nb_dir)):
            if f.endswith(".ipynb"):
                path = os.path.join(nb_dir, f)
                stat = os.stat(path)
                items.append({"name": f.replace(".ipynb", ""), "kernel": "python3", "modified": stat.st_mtime})
    return {"items": items}

@router.get("/jupyter/runs")
def jupyter_runs():
    return {"items": []}

