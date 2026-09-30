"""
能源服务Agent引擎 — 基于智谱GLM API + Function Calling
"""
import json, os, requests
from typing import List, Dict, Any, Optional
from datetime import datetime

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"

# ── 工具定义 ──
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_device_efficiency",
            "description": "查询设备能效状态。支持风机/水泵/空压机/加热炉等设备类型。返回能效指标和优化建议。",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_type": {"type": "string", "enum": ["风机","水泵","空压机","加热炉","锅炉","制冷机"], "description": "设备类型"},
                    "device_id": {"type": "string", "description": "设备编号，不填则返回该类型所有设备"}
                }
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "query_energy_consumption",
            "description": "查询厂级能源消耗数据。返回电力/燃气/蒸汽/压缩空气等能源介质的消耗量和趋势。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today","week","month"], "description": "查询周期"},
                    "media_type": {"type": "string", "enum": ["electricity","gas","steam","compressed_air","all"], "description": "能源介质类型"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_model_prediction",
            "description": "使用已训练的AI模型进行预测。支持温度预测/能耗预测/故障预测等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string", "enum": ["temperature","energy","fault","carbon"], "description": "预测场景"},
                    "features": {"type": "object", "description": "输入特征值"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_energy_schedule",
            "description": "优化能源调度方案。根据电价/负荷/储能状态生成最优用能计划。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "enum": ["cost","carbon","peak_shaving"], "description": "优化目标: 成本最低/碳排最低/削峰"},
                    "hours": {"type": "integer", "description": "优化时间范围(小时), 默认24"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_fault",
            "description": "设备故障诊断。根据设备运行参数判断故障类型和严重程度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_type": {"type": "string", "description": "设备类型"},
                    "symptoms": {"type": "string", "description": "故障现象描述"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_carbon_report",
            "description": "获取碳排放报告。返回当前碳排放量/趋势/对标数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["total","scope1","scope2"], "description": "排放范围"},
                    "period": {"type": "string", "enum": ["today","month","year"], "description": "时间范围"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_platform_stats",
            "description": "获取平台总体统计数据：数据集数量、模型数量、训练任务数等。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge",
            "description": "查询工业知识图谱。获取生产过程/生产设备/除尘/能源调度等工艺知识规则。",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": ["生产过程温度控制","生产设备操作","除尘系统","能源调度"], "description": "知识领域"}
                }
            }
        }
    }
]

SYSTEM_PROMPT = """你是开放数字化底座的能源服务助手(ODF Agent)。
你的职责是帮助用户管理和优化工业能源使用。

能力范围:
- 查询设备能效状态，给出优化建议
- 分析能源消耗数据，识别异常
- 使用AI模型进行预测（能耗/温度/故障）
- 优化能源调度方案（峰谷/储能）
- 诊断设备故障，推荐处置方案
- 生成碳排放报告
- 回答工业知识问题（基于知识图谱）

回复风格:
- 专业但易懂，用数据和事实说话
- 给出具体可操作的建议
- 涉及金额/成本时明确标注货币单位
- 如果无法回答，诚实说明并建议替代方案

当前平台: 33个数据集 / 21个模型 / 32次训练完成 / 26个公开评测数据集"""
