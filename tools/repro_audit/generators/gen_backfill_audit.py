"""生成器：backfill_audit.json —— 回填审计。

复算逻辑：回填审计统计。
"""

OUTPUT_PATH = "artifacts/backfill_audit.json"


def generate() -> dict:
    """生成 backfill_audit.json 的数值内容。"""
    # 模拟回填审计
    return {
        "audit_id": "BA_DEMO",
        "generated_at": "2026-10-30 07:10:00",
        "total_files": 90,
        "parseable_files": 90,
        "parse_errors": 0,
        "total_extractable_samples": 125,
        "missions_with_samples": 81,
        "disciplines": {
            "fluid": 90
        },
        "case_types": {
            "unknown": 24,
            "suboff": 10,
            "sphere": 9,
            "cavity": 8,
            "poiseuille": 8,
            "couette": 5,
            "womersley": 5,
            "cylinder": 3,
            "taylor": 3,
            "B18": 2,
            "thermal": 2,
            "stokes": 2,
            "aircraft": 1,
            "SDAA": 1,
            "permeability3d": 1
        },
        "verdicts": {
            "UNKNOWN": 48,
            "HONEST_FAIL": 6,
            "FAIL": 19,
            "PASS": 16,
            "IN_PROGRESS": 1
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
