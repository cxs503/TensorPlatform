#!/bin/bash
# Login
RESP=$(curl -s -X POST http://127.0.0.1:8010/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"eco_admin","password":"EcoTest!2026pass"}')
TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Get missions
curl -s -X GET "http://127.0.0.1:8010/api/v1/tensor/missions?limit=50" \
  -H "Authorization: Bearer $TOKEN" > agent_work/missions.json

echo "Missions fetched, size: $(wc -c < agent_work/missions.json) bytes"
