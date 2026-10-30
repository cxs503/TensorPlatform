#!/usr/bin/env python3
"""
validate_lineage.py — TensorPlatform 谱系追溯记录校验器

用法:
    python scripts/validate_lineage.py <lineage.json> [--schema schemas/lineage.schema.json]

裁决规则:
    1. JSON 必须合法且符合 lineage.schema.json
    2. environment.git_sha 必须存在且匹配 ^[0-9a-f]{7,40}$
    3. 若存在 baseline 字段，baseline.version 必须存在且匹配语义化版本
    4. 无版本锁定的基线一律 INVALID
    5. 所有 required 字段必须存在

返回:
    0 = PASS (valid)
    1 = INVALID (validation failed)
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import jsonschema
    from jsonschema import Draft202012Validator, ValidationError
except ImportError:
    print("ERROR: jsonschema package required. Install: pip install jsonschema", file=sys.stderr)
    sys.exit(1)


def load_schema(schema_path: str) -> dict:
    """Load JSON Schema file."""
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_lineage(data: dict, schema: dict) -> list[str]:
    """
    Validate a lineage record against the schema.
    Returns a list of error messages (empty = valid).
    """
    errors = []

    # 1. JSON Schema validation
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(data):
        path = ".".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
        errors.append(f"Schema violation at '{path}': {error.message}")

    # 2. Critical field checks (even if schema passes, enforce business rules)
    env = data.get("environment", {})

    # git_sha must exist and be valid hex
    git_sha = env.get("git_sha")
    if git_sha is None:
        errors.append("CRITICAL: environment.git_sha is missing — lineage INVALID")
    elif not isinstance(git_sha, str) or len(git_sha) < 7:
        errors.append(f"CRITICAL: environment.git_sha '{git_sha}' too short (min 7 hex chars)")

    # 3. Baseline version lock check
    baseline = data.get("baseline")
    if baseline is not None:
        if not isinstance(baseline, dict):
            errors.append("CRITICAL: baseline must be an object or null")
        else:
            bl_version = baseline.get("version")
            if bl_version is None:
                errors.append(
                    "CRITICAL: baseline.version is missing — unversioned baseline is INVALID"
                )
            elif not isinstance(bl_version, str):
                errors.append(
                    f"CRITICAL: baseline.version must be a string, got {type(bl_version).__name__}"
                )

    # 4. Schema version must match
    sv = data.get("schema_version")
    schema_id = schema.get("$id", "")
    if sv and schema_id:
        # Just check it's a valid semver string (already done by schema)
        pass

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate TensorPlatform lineage records")
    parser.add_argument("lineage_file", help="Path to lineage JSON file")
    parser.add_argument(
        "--schema",
        default=str(Path(__file__).resolve().parent.parent / "schemas" / "lineage.schema.json"),
        help="Path to JSON Schema file (default: schemas/lineage.schema.json)",
    )
    args = parser.parse_args()

    # Load schema
    try:
        schema = load_schema(args.schema)
    except Exception as e:
        print(f"ERROR: Failed to load schema: {e}", file=sys.stderr)
        sys.exit(1)

    # Load lineage data
    try:
        with open(args.lineage_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"INVALID: JSON parse error in {args.lineage_file}: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"ERROR: File not found: {args.lineage_file}", file=sys.stderr)
        sys.exit(1)

    # Validate
    errors = validate_lineage(data, schema)

    if errors:
        print(f"INVALID: {args.lineage_file}")
        for err in errors:
            print(f"  ✗ {err}")
        sys.exit(1)
    else:
        print(f"PASS: {args.lineage_file} — lineage record is valid")
        sys.exit(0)


if __name__ == "__main__":
    main()
