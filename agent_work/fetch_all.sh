#!/bin/bash
RESP=$(curl -s -X POST http://127.0.0.1:8010/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"eco_admin","password":"EcoTest!2026pass"}')
TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Get all missions with pagination
curl -s -X GET "http://127.0.0.1:8010/api/v1/tensor/missions?limit=200&offset=0" \
  -H "Authorization: Bearer $TOKEN" > agent_work/missions_p1.json

curl -s -X GET "http://127.0.0.1:8010/api/v1/tensor/missions?limit=200&offset=200" \
  -H "Authorization: Bearer $TOKEN" > agent_work/missions_p2.json

echo "Page 1 size: $(wc -c < agent_work/missions_p1.json)"
echo "Page 2 size: $(wc -c < agent_work/missions_p2.json)"
