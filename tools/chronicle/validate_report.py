#!/usr/bin/env python3
"""
T3 Anti-Fabrication Validator for research reports.
Checks that all numeric claims in the report are backed by extracted metrics.

Usage:
    python3 validate_report.py --report report.md --metrics metrics.json
"""
import argparse
import json
import re
import sys
from pathlib import Path


def extract_numbers_from_report(report_text):
    """Extract all numeric claims from the report."""
    claims = []
    
    # Pattern: "N missions" or "N tasks" etc.
    for match in re.finditer(r'(\d[\d,]*)\s+(missions|tasks|workers|nodes|campaigns|repositories|repos)', report_text, re.IGNORECASE):
        claims.append({
            'value': int(match.group(1).replace(',', '')),
            'unit': match.group(2).lower(),
            'context': match.group(0),
            'line': report_text[:match.start()].count('\n') + 1
        })
    
    # Pattern: "N%" percentages
    for match in re.finditer(r'(\d+\.?\d*)\s*%', report_text):
        claims.append({
            'value': float(match.group(1)),
            'unit': 'percent',
            'context': match.group(0),
            'line': report_text[:match.start()].count('\n') + 1
        })
    
    # Pattern: time durations like "26.1 minutes" or "1568 seconds"
    for match in re.finditer(r'(\d+\.?\d*)\s*(seconds?|minutes?|hours?)', report_text, re.IGNORECASE):
        claims.append({
            'value': float(match.group(1)),
            'unit': match.group(2).lower().rstrip('s'),
            'context': match.group(0),
            'line': report_text[:match.start()].count('\n') + 1
        })
    
    return claims


def validate_claims(claims, metrics):
    """Validate numeric claims against metrics data."""
    results = []
    
    for claim in claims:
        validated = False
        source = None
        
        # Check missions count
        if claim['unit'] in ('mission', 'missions'):
            if metrics.get('missions', {}).get('total_missions') == claim['value']:
                validated = True
                source = 'missions.total_missions'
            elif metrics.get('missions', {}).get('succeeded') == claim['value']:
                validated = True
                source = 'missions.succeeded'
        
        # Check worker count
        elif claim['unit'] in ('worker', 'workers'):
            if metrics.get('workers', {}).get('total_workers') == claim['value']:
                validated = True
                source = 'workers.total_workers'
        
        # Check repository count
        elif claim['unit'] in ('repo', 'repos', 'repositories'):
            repo_count = len(metrics.get('by_repository', []))
            if repo_count == claim['value']:
                validated = True
                source = f'len(by_repository)={repo_count}'
        
        # Check campaign count
        elif claim['unit'] in ('campaign', 'campaigns'):
            camp_count = len(metrics.get('campaigns', []))
            if camp_count == claim['value']:
                validated = True
                source = f'len(campaigns)={camp_count}'
        
        # Check percentages (success rate)
        elif claim['unit'] == 'percent':
            total = metrics.get('missions', {}).get('total_missions', 0)
            succeeded = metrics.get('missions', {}).get('succeeded', 0)
            if total > 0:
                success_rate = (succeeded / total) * 100
                if abs(success_rate - claim['value']) < 0.5:
                    validated = True
                    source = f'missions.succeeded/total={success_rate:.1f}%'
        
        results.append({
            'claim': claim,
            'validated': validated,
            'source': source
        })
    
    return results


def main():
    parser = argparse.ArgumentParser(description='T3 Anti-Fabrication Validator')
    parser.add_argument('--report', required=True, help='Report markdown file')
    parser.add_argument('--metrics', required=True, help='Metrics JSON file')
    parser.add_argument('--strict', action='store_true',
                       help='Fail if any claim is unvalidated')
    args = parser.parse_args()
    
    report_text = Path(args.report).read_text()
    metrics = json.loads(Path(args.metrics).read_text())
    
    claims = extract_numbers_from_report(report_text)
    results = validate_claims(claims, metrics)
    
    validated = sum(1 for r in results if r['validated'])
    total = len(results)
    
    print(f"T3 Validation: {validated}/{total} claims validated")
    
    for r in results:
        status = '✓' if r['validated'] else '✗'
        source = r['source'] or 'UNVALIDATED'
        print(f"  {status} Line {r['claim']['line']}: {r['claim']['context']} → {source}")
    
    if args.strict and validated < total:
        print(f"\nFAILED: {total - validated} claims not validated")
        return 1
    
    print(f"\nPASSED: All {validated} claims validated against metrics")
    return 0


if __name__ == '__main__':
    sys.exit(main())
