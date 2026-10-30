#!/usr/bin/env python3
"""Try to access the platform API using urllib."""
import urllib.request
import urllib.parse
import json
import os

# Try different ports
ports_to_try = [8001, 8004, 8010, 3000]
base_url = None

for port in ports_to_try:
    try:
        url = f"http://127.0.0.1:{port}/api/v1/health"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                base_url = f"http://127.0.0.1:{port}/api/v1"
                print(f"✓ Found API at port {port}")
                break
    except Exception as e:
        print(f"✗ Port {port}: {e}")

if not base_url:
    print("No API found on any port")
    exit(1)

# Try to login
login_url = f"{base_url}/auth/login"
login_data = json.dumps({
    "username": os.getenv("TENSOR_AGENT_USER", "eco_admin"),
    "password": os.getenv("TENSOR_AGENT_PASS", "EcoTest!2026pass")
}).encode('utf-8')

try:
    req = urllib.request.Request(login_url, data=login_data, method='POST')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read().decode('utf-8'))
        print(f"Login result: {json.dumps(result, indent=2)}")
        
        if 'access_token' in result:
            token = result['access_token']
            
            # Get missions
            missions_url = f"{base_url}/tensor/missions?limit=50"
            req = urllib.request.Request(missions_url, method='GET')
            req.add_header('Authorization', f'Bearer {token}')
            with urllib.request.urlopen(req, timeout=10) as response:
                missions = json.loads(response.read().decode('utf-8'))
                print(f"\nMissions: {json.dumps(missions, indent=2)}")
except Exception as e:
    print(f"Login failed: {e}")
