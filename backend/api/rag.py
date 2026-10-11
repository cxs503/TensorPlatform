"""RAG知识库API — 向量检索增强Agent回答"""
import os, json, pickle, numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from database.config import get_db

router = APIRouter(prefix="/rag", tags=["RAG知识库"])

# ── 简易向量存储 ──
VECTOR_DB = "/app/data/rag_store.pkl"
embeddings_cache = {}  # doc_id → vector
documents = []          # list of {id, text, source, metadata}

def _cosine_sim(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8)

def _simple_embed(text: str, dim: int = 384) -> list:
    """简易文本向量化（字符级ngram哈希）"""
    text = text.lower()
    vec = np.zeros(dim)
    for i, ch in enumerate(text):
        vec[hash(ch) % dim] += 1
        if i < len(text) - 1:
            vec[hash(text[i:i+2]) % dim] += 0.5
        if i < len(text) - 2:
            vec[hash(text[i:i+3]) % dim] += 0.3
    norm = np.linalg.norm(vec)
    return (vec / (norm + 1e-8)).tolist()

def _load_store():
    global documents
    if os.path.exists(VECTOR_DB):
        with open(VECTOR_DB, "rb") as f:
            data = pickle.load(f)
            documents = data.get("docs", [])
    return documents

def _save_store():
    os.makedirs(os.path.dirname(VECTOR_DB), exist_ok=True)
    with open(VECTOR_DB, "wb") as f:
        pickle.dump({"docs": documents, "version": 1}, f)

# ── Pydantic Models ──
class IndexDoc(BaseModel):
    text: str
    source: str = "manual"
    metadata: dict = {}

class QueryReq(BaseModel):
    query: str
    top_k: int = 5

class BatchIndex(BaseModel):
    documents: list[IndexDoc]

# ── API Endpoints ──
@router.get("/status")
def rag_status():
    _load_store()
    return {"status": "active", "total_docs": len(documents), "engine": "numpy-cosine"}

@router.post("/index")
def index_document(doc: IndexDoc):
    """索引单个文档"""
    global documents
    _load_store()
    doc_id = f"doc_{len(documents)}_{hash(doc.text) % 10000}"
    vec = _simple_embed(doc.text)
    documents.append({"id": doc_id, "text": doc.text, "source": doc.source, "metadata": doc.metadata, "vector": vec})
    _save_store()
    return {"status": "indexed", "doc_id": doc_id, "total": len(documents)}

@router.post("/index/batch")
def index_batch(req: BatchIndex):
    """批量索引文档"""
    global documents
    _load_store()
    added = 0
    for doc in req.documents:
        doc_id = f"doc_{len(documents)}_{hash(doc.text) % 10000}"
        documents.append({"id": doc_id, "text": doc.text, "source": doc.source, "metadata": doc.metadata, "vector": _simple_embed(doc.text)})
        added += 1
    _save_store()
    return {"status": "indexed", "added": added, "total": len(documents)}

@router.post("/search")
def search_rag(req: QueryReq):
    """语义搜索"""
    _load_store()
    if not documents:
        return {"results": [], "query": req.query}
    
    q_vec = np.array(_simple_embed(req.query))
    scored = []
    for doc in documents:
        sim = _cosine_sim(q_vec, np.array(doc["vector"]))
        scored.append((sim, doc))
    
    scored.sort(key=lambda x: -x[0])
    top = scored[:req.top_k]
    
    results = []
    for sim, doc in top:
        results.append({
            "score": round(float(sim), 4),
            "text": doc["text"][:500],
            "source": doc["source"],
            "metadata": doc.get("metadata", {})
        })
    
    return {"results": results, "query": req.query}

@router.post("/auto-index")
def auto_index_platform(db=Depends(get_db)):
    """自动索引平台知识：数据集+模型+文档"""
    global documents
    _load_store()
    added = 0
    
    # 索引数据集
    try:
        datasets = db.execute(text("SELECT id, name, description, row_count, columns_info FROM datasets ORDER BY id")).fetchall()
        for ds in datasets:
            ds_text = f"数据集: {ds[1]}. 描述: {ds[2] or '无'}. 行数: {ds[3] or 0}. 列: {ds[4] or '无'}"
            doc_id = f"ds_{ds[0]}"
            if not any(d["id"] == doc_id for d in documents):
                documents.append({"id": doc_id, "text": ds_text, "source": "auto/datasets", "metadata": {"dataset_id": ds[0], "name": ds[1]}, "vector": _simple_embed(ds_text)})
                added += 1
    except Exception as e:
        print(f"Auto-index datasets error: {e}")
    
    # 索引模型
    try:
        models = db.execute(text("SELECT id, name, model_type, description, metrics FROM models ORDER BY id")).fetchall()
        for m in models:
            m_text = f"模型: {m[1]}. 类型: {m[2]}. 描述: {m[3] or '无'}. 指标: {m[4] or '无'}"
            doc_id = f"model_{m[0]}"
            if not any(d["id"] == doc_id for d in documents):
                documents.append({"id": doc_id, "text": m_text, "source": "auto/models", "metadata": {"model_id": m[0], "name": m[1]}, "vector": _simple_embed(m_text)})
                added += 1
    except Exception as e:
        print(f"Auto-index models error: {e}")
    
    # 平台功能介绍
    platform_docs = [
        "开放数字化底座是工业能效AI平台，支持数据治理、模型训练、MLOps全流程。",
        "数据中台包括数据集管理、数据质量、数据血缘追踪、数据脱敏、数据标准管理等功能。",
        "模型工厂支持CV训练（分类/检测/分割/生成）、NLP训练、音频训练（ASR/TTS）、强化学习训练。",
        "AutoML功能支持自动模型选择和超参数优化，基于Optuna贝叶斯优化。",
        "平台集成MLflow进行实验追踪和模型版本管理。",
        "特征存储支持离线特征和在线特征服务，基于PostgreSQL和Redis。",
        "模型部署支持一键部署为REST API推理服务，支持ONNX格式导出。",
        "数据血缘追踪支持全链路数据关系可视化，基于Neo4j图数据库。",
        "AI智能体支持自然语言查询数据集、训练模型、评估模型、生成图表等多模态交互。",
        "平台支持分布式训练，可配置Ray集群进行大规模模型训练。",
        "能碳管理支持碳排放核算、光伏发电预测、储能优化调度、负荷预测等。",
        "数据质量六维评估：完整性、唯一性、一致性、准确性、时效性、有效性。",
        "LLMOps支持大语言模型的微调、部署、监控和Prompt管理。",
        "模型解释使用SHAP和LIME进行特征归因分析，支持部分依赖图。",
        "推理服务面板支持实时监控推理请求量、延迟、错误率等指标。",
        "AB测试支持多模型版本的流量分配和效果对比。",
        "知识图谱基于Neo4j存储工业设备、工艺参数、产品之间的关系。",
        "数据脱敏支持遮盖、替换、泛化、扰动等多种策略，保障数据安全。",
    ]
    for i, pdoc in enumerate(platform_docs):
        doc_id = f"platform_{i}"
        if not any(d["id"] == doc_id for d in documents):
            documents.append({"id": doc_id, "text": pdoc, "source": "auto/platform", "metadata": {}, "vector": _simple_embed(pdoc)})
            added += 1
    
    _save_store()
    return {"status": "indexed", "added": added, "total": len(documents)}
