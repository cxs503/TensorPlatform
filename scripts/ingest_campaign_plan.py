#!/usr/bin/env python3
"""Ingest master_plan.json into campaign_plans tables via API.

Usage:
  python scripts/ingest_campaign_plan.py [--api-base URL] [--plan-file PATH]

Defaults:
  --api-base  http://localhost:8001/api/v1
  --plan-file /srv/tensor/agent/work/m611/artifacts/master_plan.json
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error


def main():
    parser = argparse.ArgumentParser(description="Ingest master_plan.json into campaign_plans")
    parser.add_argument("--api-base", default=os.getenv("API_BASE", "http://localhost:8001/api/v1"))
    parser.add_argument("--plan-file", default="/srv/tensor/agent/work/m611/artifacts/master_plan.json")
    parser.add_argument("--token", default=os.getenv("TOKEN", ""))
    args = parser.parse_args()

    with open(args.plan_file, "r") as f:
        plan_json = json.load(f)

    url = f"{args.api_base}/tensor/campaign-plans/ingest"
    payload = json.dumps({"plan_json": plan_json}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if args.token:
        req.add_header("Authorization", f"Bearer {args.token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
