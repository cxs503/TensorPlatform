"""生成器：planner/golden_annotation_set.json —— 黄金标注集。

复算逻辑：标注质量统计。
"""

OUTPUT_PATH = "artifacts/planner/golden_annotation_set.json"


def generate() -> dict:
    """生成 planner/golden_annotation_set.json 的数值内容。"""
    # 模拟标注集
    annotations = [
        {"id": "G001", "category": "resistance", "confidence": 0.95, "agreement": 0.92},
        {"id": "G002", "category": "propulsion", "confidence": 0.88, "agreement": 0.85},
        {"id": "G003", "category": "seakeeping", "confidence": 0.92, "agreement": 0.90},
        {"id": "G004", "category": "maneuvering", "confidence": 0.85, "agreement": 0.82},
        {"id": "G005", "category": "stability", "confidence": 0.90, "agreement": 0.88},
    ]
    
    return {
        "dataset": "golden_annotations",
        "n_annotations": len(annotations),
        "annotations": annotations,
        "summary": {
            "avg_confidence": sum(a["confidence"] for a in annotations) / len(annotations),
            "avg_agreement": sum(a["agreement"] for a in annotations) / len(annotations),
            "categories": list(set(a["category"] for a in annotations))
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
