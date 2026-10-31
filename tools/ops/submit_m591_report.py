#!/usr/bin/env python3
"""
提交 m591 报告到 TensorPlatform API
"""
import requests
import json
import base64
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

API_BASE = "http://127.0.0.1:8010/api/v1"
USERNAME = "eco_admin"
PASSWORD = "EcoTest!2026pass"

# ---------------------------------------------------------------------------
# 登录获取 token
# ---------------------------------------------------------------------------

def login() -> str:
    """登录获取 access token"""
    login_url = f"{API_BASE}/auth/login"
    resp = requests.post(login_url, json={
        "username": USERNAME,
        "password": PASSWORD
    }, timeout=10)
    
    if resp.status_code != 200:
        raise Exception(f"Login failed: {resp.status_code} {resp.text}")
    
    token = resp.json().get("access_token")
    if not token:
        raise Exception("No access_token in response")
    
    return token

# ---------------------------------------------------------------------------
# 准备报告数据
# ---------------------------------------------------------------------------

def prepare_report() -> dict:
    """准备报告数据"""
    
    # 读取报告内容
    report_path = Path("runs/m591/report.md")
    report_md = report_path.read_text()
    
    # 读取图表并转为 base64
    figures_dir = Path("runs/m591/figures")
    figures = []
    
    for fig_name in ["error_distribution.png", "discipline_comparison.png", "voting_consensus.png"]:
        fig_path = figures_dir / fig_name
        if fig_path.exists():
            with open(fig_path, "rb") as f:
                png_data = f.read()
                png_base64 = base64.b64encode(png_data).decode("utf-8")
                
                figures.append({
                    "name": fig_name,
                    "caption": f"m591 复现管线 - {fig_name.replace('.png', '').replace('_', ' ').title()}",
                    "png_base64": png_base64
                })
    
    # 构建报告 JSON
    report = {
        "title": "m591 复现管线批处理报告 - PC1 并行饱和战役",
        "summary": "成功处理 11 个积压复现请求（m534-540, m549/550, m577, m585），"
                   "2-of-3 表决机制，机器判据满足（≥5 案例完成表决），"
                   "通过率 18.2%（2/11），Strike 规则无误触。",
        "scenario_key": "reproduction_pipeline_m591",
        "mission_id": 643,
        "tags": ["m591", "reproduction", "batch-processing", "2-of-3-voting", "PC1"],
        "markdown": report_md,
        "figures": figures
    }
    
    return report

# ---------------------------------------------------------------------------
# 提交报告
# ---------------------------------------------------------------------------

def submit_report(token: str, report: dict) -> dict:
    """提交报告到 API"""
    
    submit_url = f"{API_BASE}/tensor/reports"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    resp = requests.post(submit_url, headers=headers, json=report, timeout=30)
    
    return {
        "status_code": resp.status_code,
        "response": resp.json() if resp.status_code == 200 else resp.text
    }

# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    """主程序"""
    print("=== 提交 m591 报告 ===")
    
    # 登录
    print("1. 登录获取 token...")
    token = login()
    print(f"   ✓ Token: {token[:20]}...")
    
    # 准备报告
    print("2. 准备报告数据...")
    report = prepare_report()
    print(f"   ✓ 报告标题: {report['title']}")
    print(f"   ✓ 图表数量: {len(report['figures'])}")
    
    # 提交报告
    print("3. 提交报告...")
    result = submit_report(token, report)
    print(f"   状态码: {result['status_code']}")
    
    if result['status_code'] == 200:
        print(f"   ✓ 提交成功")
        print(f"   响应: {json.dumps(result['response'], indent=2, ensure_ascii=False)[:500]}")
    else:
        print(f"   ✗ 提交失败")
        print(f"   错误: {result['response'][:500]}")
    
    return result['status_code'] == 200

if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
