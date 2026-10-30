#!/usr/bin/env python3
"""Query missions for SDAA sweep line analysis."""
import os
import json
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation"
)

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # Find SDAA sweep missions
    result = conn.execute(text("""
        SELECT id, goal, status, repo, runner, created_at, finished_at, result
        FROM tensor_dev_missions 
        WHERE goal ILIKE '%SDAA%' OR goal ILIKE '%扫荡%'
        ORDER BY id DESC
        LIMIT 50
    """))
    
    missions = []
    for row in result:
        missions.append({
            "id": row[0],
            "goal": row[1],
            "status": row[2],
            "repo": row[3],
            "runner": row[4],
            "created_at": str(row[5]) if row[5] else None,
            "finished_at": str(row[6]) if row[6] else None,
            "result": row[7] if row[7] else {}
        })
    
    print(json.dumps(missions, indent=2, ensure_ascii=False, default=str))
