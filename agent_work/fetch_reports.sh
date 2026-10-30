#!/bin/bash
RESP=$(curl -s -X POST http://127.0.0.1:8010/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"eco_admin","password":"EcoTest!2026pass"}')
TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Get reports
curl -s -X GET "http://127.0.0.1:8010/api/v1/tensor/reports?limit=100" \
  -H "Authorization: Bearer $TOKEN" > agent_work/reports.json

echo "Reports fetched, size: $(wc -c < agent_work/reports.json) bytes"
