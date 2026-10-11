"""
工业知识图谱服务 — 将工艺经验转化为结构化知识
持久化至 PostgreSQL，内存字典作为缓存层。
"""

from datetime import datetime
from typing import Dict, List, Optional

from database.config import SessionLocal
from models.core import KnowledgeRule

# ========== 默认种子数据（首次启动时无 DB 记录时使用） ==========
_DEFAULT_RULES = {
    "生产过程温度控制": [
        {"rule": "终点温度偏高", "cause": "焦粉配比过高或料层偏薄", "action": "降低焦粉比例0.2%或增厚料层5mm", "confidence": 0.92},
        {"rule": "终点温度偏低", "cause": "焦粉不足或透气性差", "action": "增加焦粉0.3%或提高抽风负压2kPa", "confidence": 0.88},
        {"rule": "烟道温度梯度异常", "cause": "布料偏析导致横向温差", "action": "调整九辊布料器角度, 缩小横向偏差", "confidence": 0.85},
    ],
    "生产设备操作": [
        {"rule": "风压异常升高", "cause": "料柱透气性恶化或悬料", "action": "减风5-10%, 检查布料矩阵, 必要时坐料处理", "confidence": 0.90},
        {"rule": "铁水硅含量偏高", "cause": "炉温过高或焦炭负荷偏低", "action": "增加焦炭负荷0.02, 降低风温10°C", "confidence": 0.87},
        {"rule": "炉渣碱度波动", "cause": "原料成分波动或配料比变化", "action": "调整石灰石/白云石比例, 稳定碱度1.15-1.25", "confidence": 0.83},
    ],
    "除尘系统": [
        {"rule": "压差超限", "cause": "滤袋堵塞或清灰系统故障", "action": "启动强力清灰, 检查脉冲阀和压缩空气压力", "confidence": 0.95},
        {"rule": "粉尘排放超标", "cause": "滤袋破损或密封不良", "action": "停机检查滤袋, 更换破损滤袋, 检查密封条", "confidence": 0.93},
        {"rule": "风机能耗偏高", "cause": "系统阻力增大或风机效率下降", "action": "优化风门开度, 检查叶轮磨损, 调整变频频率", "confidence": 0.86},
    ],
    "能源调度": [
        {"rule": "峰时电价高", "cause": "当前处于电力高峰时段(8:00-11:00,18:00-21:00)", "action": "降低可调负荷, 启动储能放电, 优先使用自发电", "confidence": 0.96},
        {"rule": "煤气管网压力波动", "cause": "产消不平衡导致压力波动", "action": "调节煤气柜存储量, 调整发电机组负荷, 必要时启动放散", "confidence": 0.89},
        {"rule": "蒸汽管网热损失大", "cause": "保温层破损或疏水阀故障", "action": "检查管网保温, 更换故障疏水阀, 优化蒸汽输送压力", "confidence": 0.84},
    ],
}

# ========== 内存缓存 ==========
_knowledge_base: Dict[str, List[Dict]] = {}
_db_available = False


def _init_cache():
    """启动时从 PostgreSQL 加载规则到内存缓存；若 DB 不可用则回退到种子数据。"""
    global _knowledge_base, _db_available
    _knowledge_base = {}

    try:
        db = SessionLocal()
        try:
            rows = db.query(KnowledgeRule).order_by(KnowledgeRule.id).all()
            if rows:
                for row in rows:
                    entry = {"rule": row.rule, "cause": row.cause, "action": row.action, "confidence": row.confidence}
                    _knowledge_base.setdefault(row.domain, []).append(entry)
                _db_available = True
            else:
                # DB 无记录 → 写入种子数据
                _knowledge_base = {k: [dict(e) for e in v] for k, v in _DEFAULT_RULES.items()}
                _seed_db(db)
                _db_available = True
        finally:
            db.close()
    except Exception:
        # DB 不可达 → 纯缓存模式
        _knowledge_base = {k: [dict(e) for e in v] for k, v in _DEFAULT_RULES.items()}
        _db_available = False


def _seed_db(db):
    """将默认种子数据写入数据库。"""
    try:
        for domain, rules in _DEFAULT_RULES.items():
            for r in rules:
                db.add(KnowledgeRule(
                    domain=domain,
                    rule=r["rule"],
                    cause=r["cause"],
                    action=r["action"],
                    confidence=r["confidence"],
                ))
        db.commit()
    except Exception:
        db.rollback()


def _persist_to_db(domain: str, rule: str, cause: str, action: str, confidence: float):
    """将一条规则持久化到 PostgreSQL。"""
    if not _db_available:
        return
    try:
        db = SessionLocal()
        try:
            db.add(KnowledgeRule(
                domain=domain, rule=rule, cause=cause, action=action, confidence=confidence
            ))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    except Exception:
        pass


# ========== 公开 API（签名不变，向后兼容） ==========

def search_knowledge(domain: str = None, keyword: str = None) -> List[Dict]:
    """搜索工业知识"""
    results = []
    for domain_name, rules in _knowledge_base.items():
        if domain and domain not in domain_name:
            continue
        for entry in rules:
            if keyword:
                haystack = f"{entry['rule']} {entry['cause']} {entry['action']}"
                if keyword not in haystack:
                    continue
            results.append({"domain": domain_name, **entry})
    return results


def add_knowledge(domain: str, rule: str, cause: str, action: str, confidence: float = 0.8) -> Dict:
    """添加工业知识规则（同时持久化到 DB 并更新缓存）"""
    entry = {"rule": rule, "cause": cause, "action": action, "confidence": confidence}
    _knowledge_base.setdefault(domain, []).append(entry)
    _persist_to_db(domain, rule, cause, action, confidence)
    return {"status": "created", "domain": domain, **entry}


def get_domains() -> List[str]:
    """获取知识领域列表"""
    return list(_knowledge_base.keys())


def match_knowledge(current_values: Dict[str, float]) -> List[Dict]:
    """根据当前工况匹配知识规则(简易规则引擎)"""
    matches = []
    for domain, rules in _knowledge_base.items():
        for entry in rules:
            if "偏高" in entry["rule"] and current_values.get("temperature", 0) > 450:
                matches.append({"domain": domain, "match_reason": f'温度={current_values["temperature"]}°C > 450°C', **entry})
            if "偏低" in entry["rule"] and current_values.get("temperature", 999) < 300:
                matches.append({"domain": domain, "match_reason": f'温度={current_values["temperature"]}°C < 300°C', **entry})
            if "压差" in entry["rule"] and current_values.get("pressure_diff", 0) > 1.5:
                matches.append({"domain": domain, "match_reason": f'压差={current_values["pressure_diff"]}kPa > 1.5kPa', **entry})
    return matches


# ========== 模块加载时初始化缓存 ==========
_init_cache()
