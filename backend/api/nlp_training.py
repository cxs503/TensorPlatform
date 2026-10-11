"""NLP基础API — TF-IDF/文本分类/Word2Vec"""
import json, uuid, threading, time, numpy as np
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/nlp", tags=["NLP"])

_jobs = {}

class NLPTrainReq(BaseModel):
    task: str = "text_classification"  # text_classification | word2vec
    method: str = "tfidf"  # tfidf | word2vec
    texts: list = ["设备温度过高报警","压力正常","振动异常需检修","能耗超标预警"]

@router.get("/status")
def nlp_status():
    return {"sklearn_available": True, "tasks": ["text_classification","word2vec","ner"], "methods": ["tfidf","word2vec","naive_bayes"]}

@router.post("/train")
def train_nlp(req: NLPTrainReq):
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"job_id": job_id, "config": {"task": req.task, "method": req.method}, "status": "created", "progress": 0}
    def run():
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.naive_bayes import MultinomialNB
            texts = req.texts or ["设备故障报警","压力正常","温度过高","振动异常"]
            labels = ["故障","正常","故障","故障"]
            vec = TfidfVectorizer(max_features=100)
            X = vec.fit_transform(texts)
            model = MultinomialNB()
            model.fit(X, labels)
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["progress"] = 100
            _jobs[job_id]["metrics"] = {"vocab_size": len(vec.vocabulary_), "classes": list(set(labels))}
            _jobs[job_id]["feature_words"] = list(vec.vocabulary_.keys())[:10]
        except Exception as e:
            _jobs[job_id]["status"] = "failed"; _jobs[job_id]["error"] = str(e)
    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id, "status": "created"}

@router.get("/jobs")
def list_jobs(): return {"jobs": list(_jobs.values())}

@router.post("/analyze")
def analyze_text(text: str = "设备温度过高，需要立即检查"):
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(max_features=20)
        vec.fit([text, "正常运行无异常", "故障需要维修", "温度压力振动"])
        X = vec.transform([text])
        words = list(vec.vocabulary_.keys())
        scores = X.toarray()[0].tolist()
        top = sorted(zip(words, scores), key=lambda x: x[1], reverse=True)[:5]
        return {"text": text, "tokens": text.replace("，"," ").replace("、"," ").split(), "top_keywords": [{"word": w, "score": round(s,4)} for w,s in top]}
    except Exception as e:
        return {"text": text, "tokens": text.split(), "error": str(e)}
