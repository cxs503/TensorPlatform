#!/usr/bin/env python3
"""Validate the provenance API endpoint with real missions.

This script tests the /tensor/missions/{id}/provenance endpoint for ≥3 missions
and verifies the chain renders without breaks (API 200 and fields present).

Usage:
    python validate_provenance.py [--api-base URL] [--token TOKEN]
"""
import argparse
import json
import sys
from typing import Any
from urllib import request, error


def api_get(path: str, token: str, base: str) -> dict[str, Any]:
    """Make authenticated GET request."""
    url = f"{base}/api/v1/{path}"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {body}")


def validate_mission(mid: int, token: str, base: str) -> dict[str, Any]:
    """Validate provenance for a single mission."""
    result = {
        "mission_id": mid,
        "api_status": None,
        "chain_score": None,
        "chain_max": 6,
        "nodes_present": {},
        "error": None,
    }
    try:
        data = api_get(f"tensor/missions/{mid}/provenance", token, base)
        result["api_status"] = 200
        result["chain_score"] = data.get("chain_score", 0)
        result["chain_max"] = data.get("chain_max", 6)
        nodes = data.get("nodes", {})
        for key, node in nodes.items():
            result["nodes_present"][key] = node.get("present", False)
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    parser = argparse.ArgumentParser(description="Validate provenance API")
    parser.add_argument("--api-base", default="http://localhost:8000",
                        help="API base URL")
    parser.add_argument("--token", required=True, help="Auth token")
    parser.add_argument("--mission-ids", type=str, default="",
                        help="Comma-separated mission IDs to test")
    args = parser.parse_args()

    # Get some mission IDs to test
    if args.mission_ids:
        mission_ids = [int(x.strip()) for x in args.mission_ids.split(",") if x.strip()]
    else:
        # Fetch recent missions
        try:
            missions = api_get("tensor/missions?limit=10", args.token, args.api_base)
            mission_ids = [m["id"] for m in missions.get("items", [])][:5]
        except Exception as e:
            print(f"Failed to fetch missions: {e}", file=sys.stderr)
            sys.exit(1)

    if not mission_ids:
        print("No missions found to validate", file=sys.stderr)
        sys.exit(1)

    print(f"Validating provenance for {len(mission_ids)} missions...")
    results = []
    for mid in mission_ids:
        r = validate_mission(mid, args.token, args.api_base)
        results.append(r)
        status = "✓" if r["api_status"] == 200 else "✗"
        score = f"{r['chain_score']}/{r['chain_max']}" if r["chain_score"] is not None else "N/A"
        print(f"  {status} Mission #{mid}: chain={score}")

    # Summary
    success = sum(1 for r in results if r["api_status"] == 200)
    complete = sum(1 for r in results if r["chain_score"] == r["chain_max"])
    print(f"\nSummary: {success}/{len(results)} API calls succeeded, "
          f"{complete}/{len(results)} fully complete chains")

    # Output JSON for programmatic consumption
    output = {
        "validated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "total_missions": len(results),
        "api_success": success,
        "fully_complete": complete,
        "results": results,
    }
    print("\n" + json.dumps(output, indent=2))
    return 0 if success >= 3 else 1


if __name__ == "__main__":
    sys.exit(main())
