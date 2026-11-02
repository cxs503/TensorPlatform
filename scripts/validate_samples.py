#!/usr/bin/env python3
"""
validate_samples.py — 用 JSON Schema 验证提取的样本，输出验证报告。

用法:
    python3 scripts/validate_samples.py [--schema PATH] [--samples PATH]
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone

# Try to import jsonschema; if not available, do basic validation
try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

WORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(WORK_DIR, "docs", "sample.schema.json")
DEFAULT_SAMPLES = os.path.join(WORK_DIR, "artifacts", "samples_batch1.jsonl")


def validate_basic(sample, idx):
    """Basic validation without jsonschema library."""
    errors = []
    required = ["sample_id", "mission_id", "case_id", "ship_type",
                "physics_domain", "geometry_params", "operating_condition",
                "result_metrics", "validation_status"]
    for field in required:
        if field not in sample:
            errors.append(f"Missing required field: {field}")

    # Check types
    if "sample_id" in sample and not isinstance(sample["sample_id"], str):
        errors.append("sample_id must be string")
    if "mission_id" in sample and not isinstance(sample["mission_id"], int):
        errors.append("mission_id must be integer")
    if "case_id" in sample and not isinstance(sample["case_id"], str):
        errors.append("case_id must be string")

    # Check enums
    valid_ship_types = ["container", "tanker", "bulk_carrier", "passenger", "naval",
                        "submarine", "fishing", "tug", "barge", "yacht",
                        "research_vessel", "icebreaker", "generic"]
    if "ship_type" in sample and sample["ship_type"] not in valid_ship_types:
        errors.append(f"Invalid ship_type: {sample['ship_type']}")

    valid_domains = ["resistance", "propulsion", "seakeeping", "manoeuvring",
                     "stability", "structure", "vibration_noise", "green_navigation",
                     "ice_navigation"]
    if "physics_domain" in sample and sample["physics_domain"] not in valid_domains:
        errors.append(f"Invalid physics_domain: {sample['physics_domain']}")

    valid_statuses = ["PASS", "FAIL", "HONEST_FAIL", "UNKNOWN", "NOT_VALIDATED"]
    if "validation_status" in sample and sample["validation_status"] not in valid_statuses:
        errors.append(f"Invalid validation_status: {sample['validation_status']}")

    # Check geometry_params required fields
    if "geometry_params" in sample:
        gp = sample["geometry_params"]
        for gf in ["Cb", "L_pp", "B"]:
            if gf not in gp:
                errors.append(f"geometry_params missing required: {gf}")
            elif not isinstance(gp[gf], (int, float)):
                errors.append(f"geometry_params.{gf} must be number")
            elif gp[gf] < 0:
                errors.append(f"geometry_params.{gf} must be >= 0")
        if "Cb" in gp and (gp["Cb"] < 0 or gp["Cb"] > 1):
            errors.append(f"geometry_params.Cb must be in [0, 1]")

    return errors


def validate_with_jsonschema(sample, schema, idx):
    """Validate using jsonschema library."""
    errors = []
    try:
        jsonschema.validate(instance=sample, schema=schema)
    except jsonschema.ValidationError as e:
        errors.append(f"Schema violation: {e.message}")
    except jsonschema.SchemaError as e:
        errors.append(f"Schema error: {e.message}")
    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate samples against JSON Schema")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA, help="Path to JSON Schema")
    parser.add_argument("--samples", default=DEFAULT_SAMPLES, help="Path to JSONL samples")
    parser.add_argument("--output", default=None, help="Path to output report (default: stdout)")
    args = parser.parse_args()

    # Load schema
    if not os.path.exists(args.schema):
        print(f"ERROR: Schema not found: {args.schema}", file=sys.stderr)
        sys.exit(1)
    with open(args.schema) as f:
        schema = json.load(f)
    print(f"Schema loaded: {args.schema}")

    # Load samples
    if not os.path.exists(args.samples):
        print(f"ERROR: Samples not found: {args.samples}", file=sys.stderr)
        sys.exit(1)

    samples = []
    with open(args.samples) as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    print(f"Samples loaded: {len(samples)} from {args.samples}")

    # Validate
    results = []
    all_pass = True
    for idx, sample in enumerate(samples):
        if HAS_JSONSCHEMA:
            errors = validate_with_jsonschema(sample, schema, idx)
        else:
            errors = validate_basic(sample, idx)

        if errors:
            all_pass = False
            results.append({
                "index": idx,
                "sample_id": sample.get("sample_id", f"unknown_{idx}"),
                "status": "FAIL",
                "errors": errors
            })
        else:
            results.append({
                "index": idx,
                "sample_id": sample.get("sample_id", f"unknown_{idx}"),
                "status": "PASS",
                "errors": []
            })

    # Summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")

    report = {
        "validation_date": datetime.now(timezone.utc).isoformat(),
        "schema": args.schema,
        "samples_file": args.samples,
        "total_samples": len(samples),
        "passed": passed,
        "failed": failed,
        "all_pass": all_pass,
        "jsonschema_available": HAS_JSONSCHEMA,
        "results": results
    }

    # Print summary
    print(f"\n{'='*60}")
    print(f"VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"Schema: {args.schema}")
    print(f"Samples: {args.samples}")
    print(f"Total: {len(samples)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"jsonschema: {'available' if HAS_JSONSCHEMA else 'not available (basic validation)'}")
    print(f"Overall: {'PASS' if all_pass else 'FAIL'}")
    print(f"{'='*60}")

    if failed > 0:
        print(f"\nFailed samples:")
        for r in results:
            if r["status"] == "FAIL":
                print(f"  [{r['index']}] {r['sample_id']}: {r['errors']}")

    # Write report
    report_path = args.output or os.path.join(WORK_DIR, "artifacts", "validation_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport written: {report_path}")

    # Exit code
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
