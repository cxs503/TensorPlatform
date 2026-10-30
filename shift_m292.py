#!/usr/bin/env python3
"""Shift supervisor script for m292 - fetch and analyze missions."""
import json
import os
import requests

API_BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "EcoTest!2026pass")

def login():
    r = requests.post(f"{API_BASE}/auth/login", json={"username": USER, "password": PASS})
    r.raise_for_status()
    return r.json()["access_token"]

def get_missions(token, limit=50):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API_BASE}/tensor/missions", headers=headers, params={"limit": limit})
    r.raise_for_status()
    return r.json()

def create_mission(token, mission_data):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(f"{API_BASE}/tensor/missions", headers=headers, json=mission_data)
    r.raise_for_status()
    return r.json()

def create_report(token, report_data):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(f"{API_BASE}/tensor/reports", headers=headers, json=report_data)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    
    token = login()
    print(f"Logged in as {USER}", file=sys.stderr)
    
    if action == "fetch":
        missions = get_missions(token)
        print(json.dumps(missions, indent=2, ensure_ascii=False))
    elif action == "dispatch":
        mission_data = json.load(sys.stdin)
        result = create_mission(token, mission_data)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif action == "report":
        report_data = json.load(sys.stdin)
        result = create_report(token, report_data)
        print(json.dumps(result, indent=2, ensure_ascii=False))
