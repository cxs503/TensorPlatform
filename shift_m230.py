#!/usr/bin/env python3
"""Shift m230: SDAA sweep analysis and dispatch."""
import json
import os
import sys
import requests

API_BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "EcoTest!2026pass")

def login():
    r = requests.post(f"{API_BASE}/auth/login", json={"username": USER, "password": PASS})
    print(f"Login status: {r.status_code}")
    data = r.json()
    print(f"Login response keys: {list(data.keys())}")
    token = data.get("access_token") or data.get("token") or data.get("data", {}).get("access_token")
    if not token:
        print(f"Full response: {json.dumps(data, indent=2)[:500]}")
    return token

def get_missions(token, limit=50):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API_BASE}/tensor/missions", headers=headers, params={"limit": limit})
    print(f"Missions status: {r.status_code}")
    if r.status_code == 200:
        return r.json()
    print(f"Error: {r.text[:500]}")
    return None

def get_reports(token, limit=50):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API_BASE}/tensor/reports", headers=headers, params={"limit": limit})
    print(f"Reports status: {r.status_code}")
    if r.status_code == 200:
        return r.json()
    print(f"Error: {r.text[:500]}")
    return None

def create_mission(token, goal, repo="TensorLBM", runner="native"):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "goal": goal,
        "repo": repo,
        "runner": runner,
        "origin": "agent",
        "base_ref": "main"
    }
    r = requests.post(f"{API_BASE}/tensor/missions", headers=headers, json=body)
    print(f"Create mission status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    return r.json() if r.status_code == 200 else None

def create_report(token, title, summary, scenario_key, mission_id, tags, markdown, figures=None):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "title": title,
        "summary": summary,
        "scenario_key": scenario_key,
        "mission_id": mission_id,
        "tags": tags,
        "markdown": markdown,
        "figures": figures or []
    }
    r = requests.post(f"{API_BASE}/tensor/reports", headers=headers, json=body)
    print(f"Create report status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    return r.json() if r.status_code == 200 else None

if __name__ == "__main__":
    token = login()
    if not token:
        print("Login failed, exiting")
        sys.exit(1)
    
    print(f"\nToken obtained: {token[:20]}...")
    
    missions = get_missions(token)
    if missions:
        print(f"\nTotal missions: {missions.get('total', 'N/A')}")
        items = missions.get('items', [])
        print(f"Returned items: {len(items)}")
        
        with open("/srv/tensor/agent/work/m230/missions_data.json", "w") as f:
            json.dump(missions, f, indent=2, default=str)
        print("Saved missions data")
    
    reports = get_reports(token)
    if reports:
        print(f"\nTotal reports: {reports.get('total', 'N/A')}")
        items = reports.get('items', [])
        print(f"Returned items: {len(items)}")
        
        with open("/srv/tensor/agent/work/m230/reports_data.json", "w") as f:
            json.dump(reports, f, indent=2, default=str)
        print("Saved reports data")
