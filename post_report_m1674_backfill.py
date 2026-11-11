#!/usr/bin/env python3
"""Backfill report for mission 1674 (resistance four-path audit)."""
import json, os, sys, urllib.request, base64

api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

# 1. Login
login_data = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login_data,
                             headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["access_token"]
    print(f"Login OK, token={token[:20]}...")
except Exception as e:
    print(f"Login failed: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Read report markdown
with open("/root/tp-agent/work/m14396/docs/report_m1674.md") as f:
    md = f.read()

# 3. Try to include the figure as base64
figures = []
import subprocess
try:
    result = subprocess.run(
        ["git", "show", "96d105afa:artifacts/m1674/fig1_paths_and_fix_effect.png"],
        capture_output=True, cwd="/root/tp-agent/work/m14396", timeout=10
    )
    if result.returncode == 0 and len(result.stdout) > 0:
        fig_b64 = base64.b64encode(result.stdout).decode()
        figures.append({
            "name": "fig1_paths_and_fix_effect",
            "caption": "三条 HM 路径的 R_T(Fn) 对比（左）与三处转写缺陷修复对 R_T 的逐点影响（右）。修前 A 路径在高 Fn 段塌陷，修后与 C/D 贴合。",
            "png_base64": fig_b64
        })
        print(f"Figure extracted: {len(result.stdout)} bytes")
    else:
        print("Figure not available in git, skipping")
except Exception as e:
    print(f"Figure extraction failed: {e}")

# 4. Build report payload
report = {
    "title": "M1674 补报：阻力四路并行实现的跨路径一致性审计与三处转写缺陷修复（c7 分支对调/c15 缺带/iE 误用/Cm 误用）",
    "summary": "补报 mission 1674（commit 96d105afa）。审计四条独立阻力路径（A=Holtrop-Mennen, B=SUBOFF 端点, C=digital-twin HM, D=polar HM）的跨路径一致性，发现并修复三处转写缺陷：(1) c7 细长/肥大分支对调导致 KCS 12m/s 阻力偏低 43.7%；(2) c15 缺失第三带；(3) polar iE 用 L/T 而非 LR/B；(4) digital-twin 湿表面积用 sqrt(Cb) 而非 sqrt(Cm)。修后三条 HM 路径在设计 Froude 范围一致至 3-11%；端点 B 仍差 19-71%（SUBOFF 拟合本质）。81 项测试通过，15 行回归守卫钉住。",
    "scenario_key": "report-backfill",
    "mission_id": 1674,
    "tags": ["补报", "resistance", "holtrop-mennen", "audit", "conformance", "c7-fix"],
    "markdown": md,
    "figures": figures
}

body = json.dumps(report).encode()
print(f"Payload size: {len(body)} bytes, figures: {len(figures)}")

# 5. POST report
req2 = urllib.request.Request(f"{api_base}/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"Bearer {token}"})
try:
    resp2 = urllib.request.urlopen(req2, timeout=15)
    result = json.loads(resp2.read())
    print(f"Report posted successfully: {result}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    if hasattr(e, 'read'):
        print(f"Error body: {e.read().decode()[:500]}", file=sys.stderr)
    sys.exit(1)
