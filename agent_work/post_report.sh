#!/bin/bash
RESP=$(curl -s -X POST http://127.0.0.1:8010/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"eco_admin","password":"EcoTest!2026pass"}')
TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# POST report
curl -s -X POST "http://127.0.0.1:8010/api/v1/tensor/reports" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d @agent_work/report_m317.json

echo ""
echo "Report POST complete"
