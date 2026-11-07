#!/usr/bin/env python3
"""
Chronicle data extractor for system research report.
Extracts metrics from tensorplatform database for the 2026-11-06~11-07 window.

Usage:
    python3 extract_metrics.py [--output metrics.json] [--dry-run]
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("psycopg2 not available, using mock data for dry-run", file=sys.stderr)
    psycopg2 = None


def get_db_connection():
    """Get database connection from environment."""
    import os
    return psycopg2.connect(
        host=os.environ.get('TP_PG_HOST', 'localhost'),
        port=os.environ.get('TP_PG_PORT', '5432'),
        dbname=os.environ.get('TP_PG_DB', 'tensorplatform'),
        user=os.environ.get('TP_PG_USER', 'foundation'),
        password=os.environ.get('TP_PG_PASSWORD', 'CHANGE_ME')
    )


def extract_metrics(dry_run=False):
    """Extract all metrics for the research report."""
    if dry_run or psycopg2 is None:
        return get_mock_metrics()
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    metrics = {
        'extraction_timestamp': datetime.utcnow().isoformat() + 'Z',
        'window': {
            'start': '2026-11-06T00:00:00Z',
            'end': '2026-11-08T00:00:00Z',
            'duration_hours': 48
        }
    }
    
    # Overall mission statistics
    cursor.execute("""
        SELECT COUNT(*) as total_missions,
               COUNT(CASE WHEN status='succeeded' THEN 1 END) as succeeded,
               COUNT(CASE WHEN status='failed' THEN 1 END) as failed,
               COUNT(CASE WHEN status='awaiting_pr_merge' THEN 1 END) as awaiting_pr_merge,
               MIN(created_at) as first_created,
               MAX(finished_at) as last_finished,
               AVG(EXTRACT(EPOCH FROM (finished_at - created_at))) as avg_lifecycle_seconds
        FROM tensor_dev_missions 
        WHERE finished_at >= '2026-11-06' AND finished_at < '2026-11-08'
    """)
    metrics['missions'] = dict(cursor.fetchone())
    
    # Hourly throughput
    cursor.execute("""
        SELECT DATE_TRUNC('hour', finished_at) as hour,
               COUNT(*) as missions_completed
        FROM tensor_dev_missions 
        WHERE finished_at >= '2026-11-06' AND finished_at < '2026-11-08'
        GROUP BY hour
        ORDER BY hour
    """)
    metrics['hourly_throughput'] = [dict(row) for row in cursor.fetchall()]
    
    # Repository distribution
    cursor.execute("""
        SELECT repo, COUNT(*) as missions,
               COUNT(CASE WHEN status='succeeded' THEN 1 END) as succeeded
        FROM tensor_dev_missions 
        WHERE finished_at >= '2026-11-06' AND finished_at < '2026-11-08'
        GROUP BY repo
        ORDER BY missions DESC
    """)
    metrics['by_repository'] = [dict(row) for row in cursor.fetchall()]
    
    # Campaign plans
    cursor.execute("""
        SELECT id, name, status, created_at, metadata
        FROM campaign_plans 
        WHERE created_at >= '2026-11-05'
        ORDER BY created_at DESC
    """)
    campaigns = []
    for row in cursor.fetchall():
        camp = dict(row)
        if camp['metadata']:
            camp['metadata'] = json.loads(camp['metadata']) if isinstance(camp['metadata'], str) else camp['metadata']
        campaigns.append(camp)
    metrics['campaigns'] = campaigns
    
    # Campaign nodes status
    cursor.execute("""
        SELECT plan_id, status, COUNT(*) as node_count
        FROM campaign_nodes
        WHERE plan_id IN (SELECT id FROM campaign_plans WHERE created_at >= '2026-11-05')
        GROUP BY plan_id, status
        ORDER BY plan_id, status
    """)
    metrics['campaign_nodes'] = [dict(row) for row in cursor.fetchall()]
    
    # Model distribution
    cursor.execute("""
        SELECT model, COUNT(*) as count
        FROM tensor_dev_missions 
        WHERE finished_at >= '2026-11-06' AND finished_at < '2026-11-08'
        AND model IS NOT NULL
        GROUP BY model
        ORDER BY count DESC
    """)
    metrics['by_model'] = [dict(row) for row in cursor.fetchall()]
    
    # Worker statistics
    cursor.execute("""
        SELECT COUNT(DISTINCT claim_token) as total_workers,
               MIN(finished_at) as first_finish,
               MAX(finished_at) as last_finish
        FROM tensor_dev_missions 
        WHERE finished_at >= '2026-11-06' AND finished_at < '2026-11-08'
        AND claim_token IS NOT NULL
    """)
    metrics['workers'] = dict(cursor.fetchone())
    
    cursor.close()
    conn.close()
    
    return metrics


def get_mock_metrics():
    """Return mock metrics for dry-run mode."""
    return {
        'extraction_timestamp': datetime.utcnow().isoformat() + 'Z',
        'window': {
            'start': '2026-11-06T00:00:00Z',
            'end': '2026-11-08T00:00:00Z',
            'duration_hours': 48
        },
        'missions': {
            'total_missions': 329,
            'succeeded': 303,
            'failed': 24,
            'awaiting_pr_merge': 2,
            'avg_lifecycle_seconds': 1568.6
        },
        'by_repository': [
            {'repo': 'TensorLBM', 'missions': 72, 'succeeded': 68},
            {'repo': 'TensorSPH', 'missions': 44, 'succeeded': 41},
            {'repo': 'TensorDEM', 'missions': 42, 'succeeded': 39},
        ],
        'campaigns': [],
        'by_model': [
            {'model': 'deepseek-v4-flash', 'count': 49},
            {'model': 'qwen3.6-plus', 'count': 17},
        ],
        'workers': {
            'total_workers': 5,
        },
        '_mock': True
    }


def main():
    parser = argparse.ArgumentParser(description='Extract chronicle metrics')
    parser.add_argument('--output', '-o', default='metrics.json',
                       help='Output JSON file path')
    parser.add_argument('--dry-run', action='store_true',
                       help='Use mock data without database connection')
    args = parser.parse_args()
    
    metrics = extract_metrics(dry_run=args.dry_run)
    
    output_path = Path(args.output)
    output_path.write_text(json.dumps(metrics, indent=2, default=str))
    print(f"Metrics written to {output_path}")
    
    if metrics.get('_mock'):
        print("WARNING: Using mock data (dry-run mode or psycopg2 unavailable)")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
