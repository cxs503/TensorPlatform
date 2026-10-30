#!/usr/bin/env python3
"""Access the platform API to get missions."""
import urllib.request
import urllib.parse
import json
import os

base_url = "http://127.0.0.1:8004/api/v1"

# Login
login_url = f"{base_url}/login"
login_data = json.dumps({
    "username": os.getenv("TENSOR_AGENT_USER", "eco_admin"),
    "password": os.getenv("TENSOR_AGENT_PASS", "EcoTest!2026pass")
}).encode('utf-8')

try:
    req = urllib.request.Request(login_url, data=login_data, method='POST')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read().decode('utf-8'))
        print(f"Login successful!")
        
        if 'access_token' in result:
            token = result['access_token']
            
            # Get missions
            missions_url = f"{base_url}/tensor/missions?limit=50"
            req = urllib.request.Request(missions_url, method='GET')
            req.add_header('Authorization', f'Bearer {token}')
            with urllib.request.urlopen(req, timeout=10) as response:
                missions = json.loads(response.read().decode('utf-8'))
                
                # Save to file
                with open('missions_data.json', 'w', encoding='utf-8') as f:
                    json.dump(missions, f, indent=2, ensure_ascii=False, default=str)
                
                print(f"Retrieved {len(missions) if isinstance(missions, list) else 'data'} missions")
                print(f"Data saved to missions_data.json")
                
                # Filter SDAA missions
                if isinstance(missions, list):
                    sdaa_missions = [m for m in missions if 'SDAA' in m.get('goal', '') or '扫荡' in m.get('goal', '')]
                    print(f"\nSDAA sweep missions: {len(sdaa_missions)}")
                    for m in sdaa_missions[:10]:
                        print(f"  m{m['id']}: {m['status']} - {m['goal'][:80]}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
