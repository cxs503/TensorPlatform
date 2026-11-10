"""生成器：capability_ledger.json —— 能力台账。

复算逻辑：能力计数与分类统计。
"""

OUTPUT_PATH = "artifacts/capability_ledger/capability_ledger.json"


def generate() -> dict:
    """生成 capability_ledger.json 的数值内容。"""
    # 模拟能力台账
    capabilities = [
        {"id": "resistance_ittc57", "domain": "resistance", "level": "REAL", "confidence": 0.95},
        {"id": "propulsion_wageningen", "domain": "propulsion", "level": "MOCK", "confidence": 0.3},
        {"id": "seakeeping_rao", "domain": "seakeeping", "level": "STUB", "confidence": 0.1},
        {"id": "maneuvering_mmg", "domain": "maneuvering", "level": "REAL", "confidence": 0.85},
        {"id": "stability_intact", "domain": "stability", "level": "REAL", "confidence": 0.90},
    ]
    
    # 统计
    by_level = {}
    by_domain = {}
    for cap in capabilities:
        level = cap["level"]
        domain = cap["domain"]
        by_level[level] = by_level.get(level, 0) + 1
        by_domain[domain] = by_domain.get(domain, 0) + 1
    
    return {
        "total_capabilities": len(capabilities),
        "by_level": by_level,
        "by_domain": by_domain,
        "capabilities": capabilities,
        "summary": {
            "real_count": by_level.get("REAL", 0),
            "mock_count": by_level.get("MOCK", 0),
            "stub_count": by_level.get("STUB", 0),
            "avg_confidence": sum(c["confidence"] for c in capabilities) / len(capabilities)
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
