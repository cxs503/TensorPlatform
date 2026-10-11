"""
AI学院 API — 平台嵌入式学习（无需Jupyter）
"""
import json, os, re, sys, io
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/academy", tags=["AI学院"])

ACADEMY_DIR = Path("/app/academy")
FULL_ACADEMY_DIR = Path("/app/academy_readmes")

# Course metadata
COURSES = [
    {"id": "01", "title": "平台入门与AI全貌", "icon": "🎯", "domain": "AI基础", "summary": "理解AI工业应用全链路", "file": "01-intro"},
    {"id": "02", "title": "回归预测—生产过程温度", "icon": "📈", "domain": "AI基础", "summary": "RF/XGBoost/LightGBM, R²=0.92", "file": "02-regression"},
    {"id": "03", "title": "分类算法—故障诊断", "icon": "🏷️", "domain": "AI基础", "summary": "LR/SVM/RF/XGBoost多模型对比", "file": "03-classification"},
    {"id": "04", "title": "聚类分析—用电模式", "icon": "🔮", "domain": "AI基础", "summary": "K-Means/DBSCAN/层次聚类", "file": "04-clustering"},
    {"id": "05", "title": "时序预测—能源负荷", "icon": "⏰", "domain": "AI基础", "summary": "ARIMA/Prophet/LSTM三位一体", "file": "05-timeseries"},
    {"id": "06", "title": "异常检测—设备预警", "icon": "⚠️", "domain": "AI基础", "summary": "IsolationForest/AutoEncoder/规则引擎", "file": "06-anomaly"},
    {"id": "07", "title": "计算机视觉—缺陷检测", "icon": "👁️", "domain": "深度学习", "summary": "ResNet/YOLOv8/Grad-CAM", "file": "07-optimization"},
    {"id": "08", "title": "深度学习基础—PyTorch", "icon": "🧠", "domain": "深度学习", "summary": "从零实现MLP+反向传播+自动微分", "file": "08-deep-learning"},
    {"id": "09", "title": "LLM+RAG—工业问答", "icon": "🤖", "domain": "大模型", "summary": "RAG架构/向量检索/智谱GLM-4", "file": "09-llm-rag"},
    {"id": "10", "title": "MLOps—端到端部署", "icon": "🚀", "domain": "工程化", "summary": "MLflow/Docker/CI/CD/漂移检测", "file": "15-mlops"},
    {"id": "11", "title": "GNN—知识图谱推理", "icon": "🕸️", "domain": "深度学习", "summary": "PyG/GCN/GAT/链路预测", "file": "11-gnn"},
    {"id": "12", "title": "Transformer—从零实现", "icon": "🔀", "domain": "深度学习", "summary": "Self-Attention/多头/多模态融合", "file": "12-transformer"},
    {"id": "13", "title": "可解释AI—SHAP/XAI", "icon": "🔍", "domain": "可信AI", "summary": "Shapley值/LIME/部分依赖图", "file": "13-xai"},
    {"id": "14", "title": "AutoML—贝叶斯优化", "icon": "⚡", "domain": "工程化", "summary": "Optuna/贝叶斯优化/HyperBand", "file": "14-optimization"},
    {"id": "16", "title": "NLP—工业文本处理", "icon": "📝", "domain": "AI应用", "summary": "TF-IDF/Word2Vec/BERT/NER", "file": "16-nlp"},
    {"id": "17", "title": "语音AI—声学监测", "icon": "🎤", "domain": "AI应用", "summary": "librosa/MFCC/异常声分类", "file": "17-audio"},
    {"id": "18", "title": "模型压缩与边缘部署", "icon": "📱", "domain": "工程化", "summary": "ONNX/INT8量化/蒸馏/剪枝", "file": "18-edge"},
    {"id": "19", "title": "推荐系统—方案推荐", "icon": "🎯", "domain": "AI应用", "summary": "SVD矩阵分解/DeepFM", "file": "19-recsys"},
    {"id": "20", "title": "因果推断—决策AI", "icon": "🔬", "domain": "决策AI", "summary": "A/B测试/PSM/DoWhy/反事实", "file": "20-causal"},
]


def parse_readme_to_blocks(content: str) -> list:
    """Parse README.md into learning blocks (markdown + code sections)"""
    blocks = []
    # Split by ## headers
    sections = re.split(r'\n(?=## )', content)
    
    for section in sections:
        # Split markdown and code
        parts = re.split(r'```(?:python|bash|dockerfile)?\n', section)
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            
            # Remove closing ```
            if '```' in part:
                part = part.split('```')[0].strip()
            
            if i == 0:
                if part:
                    blocks.append({"type": "markdown", "content": part})
            else:
                if part:
                    blocks.append({"type": "code", "content": part, "language": "python"})
    
    return blocks


@router.get("/courses")
def list_courses():
    """列出所有19门课程"""
    return {"total": len(COURSES), "courses": COURSES}


@router.get("/courses/{course_id}")
def get_course(course_id: str):
    """获取课程完整内容（解析后的blocks）"""
    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")
    
    # Try to load from full academy dir first
    readme_path = FULL_ACADEMY_DIR / course["file"] / "README.md"
    if not readme_path.exists():
        raise HTTPException(status_code=404, detail="课程内容文件不存在")
    
    with open(readme_path, 'r') as f:
        content = f.read()
    
    blocks = parse_readme_to_blocks(content)
    
    return {
        "id": course["id"],
        "title": course["title"],
        "icon": course["icon"],
        "domain": course["domain"],
        "summary": course["summary"],
        "blocks": blocks,
        "block_count": len(blocks),
        "jupyter_url": f"/jupyter/lab/tree/academy/{course['file']}.ipynb"
    }


class ExecuteCodeReq(BaseModel):
    code: str


@router.post("/execute")
def execute_code(req: ExecuteCodeReq):
    """在平台后端执行Python代码，返回结果"""
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    
    result = {"success": False, "output": "", "error": ""}
    
    try:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        
        # Restricted globals for safety
        safe_globals = {
            "__builtins__": __builtins__,
            "print": print,
            "range": range, "len": len, "int": int, "float": float, "str": str,
            "list": list, "dict": dict, "set": set, "tuple": tuple,
            "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
            "True": True, "False": False, "None": None,
        }
        
        try:
            exec(req.code, safe_globals)
            result["success"] = True
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        
        result["output"] = stdout_capture.getvalue()
        if not result["output"] and result["error"]:
            result["output"] = stderr_capture.getvalue()
            
    except Exception as e:
        result["error"] = str(e)
    
    return result
