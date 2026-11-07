import json
import random
import hashlib

random.seed(42)

# Seeds based on B2/B3/B5 real leakage patterns
SEEDS = [
    "底孔排水不畅", "胸墙裂缝渗水", "闸室底板脱空",
    "止水橡皮老化", "启闭机卡阻", "闸墩裂缝",
    "闸门振动", "流道空蚀", "消力池冲刷"
]

# 6 Mutation Operators
def op_synonym(text):
    synonyms = {
        "底孔": "底部孔口", "排水": "泄水", "不畅": "堵塞",
        "裂缝": "裂纹", "渗水": "漏水", "底板": "底部",
        "脱空": "悬空", "止水": "密封", "老化": "损坏",
        "启闭机": "闸门启闭装置", "卡阻": "卡死", "振动": "震动"
    }
    res = text
    for k, v in synonyms.items():
        res = res.replace(k, v)
    return res

def op_symbolic(text):
    symbols = ["[", "]", "(", ")", "{", "}", "<", ">"]
    res = list(text)
    for i in range(len(res)):
        if random.random() < 0.1:
            res.insert(i, random.choice(symbols))
    return "".join(res)

def op_cross_lingual(text):
    en_map = {
        "底孔": "bottom_orifice", "排水": "drainage", "不畅": "blockage",
        "裂缝": "crack", "渗水": "seepage", "底板": "floor",
        "脱空": "void", "止水": "waterstop", "老化": "aging",
        "启闭机": "hoist", "卡阻": "jam", "振动": "vibration"
    }
    res = text
    for k, v in en_map.items():
        res = res.replace(k, v)
    return res

def op_table_code(text):
    return f"```csv\nid,description\n1,{text}\n```"

def op_comment_url(text):
    return f"<!-- https://example.com/leak/{hashlib.md5(text.encode()).hexdigest()} -->\n{text}"

def op_long_range(text):
    context = "根据2023年10月15日的巡检记录，该区域曾出现异常。经过长达三个月的监测，最终确认问题根源在于："
    return f"{context}\n\n{text}"

OPERATORS = [op_synonym, op_symbolic, op_cross_lingual, op_table_code, op_comment_url, op_long_range]

corpus = []
for i, op in enumerate(OPERATORS):
    # Generate positive samples
    for j in range(30):
        seed = SEEDS[j % len(SEEDS)]
        content = op(seed)
        corpus.append({
            "id": f"pos_{i}_{j}",
            "type": f"type_{i+1}",
            "label": 1,
            "content": content,
            "operator": f"op_{i+1}"
        })
    # Generate negative samples
    for j in range(15):
        seed = SEEDS[(j + 3) % len(SEEDS)]
        content = op(seed)
        corpus.append({
            "id": f"neg_{i}_{j}",
            "type": f"type_{i+1}",
            "label": 0,
            "content": content,
            "operator": f"op_{i+1}"
        })

with open("corpus.json", "w") as f:
    json.dump(corpus, f, ensure_ascii=False, indent=2)

print(f"Generated {len(corpus)} samples.")
