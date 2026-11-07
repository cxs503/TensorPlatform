import requests, json

url = "http://127.0.0.1:8010/api/v1/auth/login"
data = {"username": "eco_admin", "password": "EcoTest!2026pass"}
resp = requests.post(url, json=data)
token = resp.json()['access_token']

with open("/root/tp-agent/work/m6365/report.json", "r") as f:
    report = json.load(f)

headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
resp = requests.post("http://127.0.0.1:8010/api/v1/tensor/reports", json=report, headers=headers)
print(resp.status_code, resp.text)
