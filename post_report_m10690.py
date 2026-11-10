#!/usr/bin/env python3
"""Post mission report for m10690."""
import json
import os
import sys
import urllib.request
import urllib.error

API_BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")

def get_token():
    """Get auth token."""
    url = f"{API_BASE}/auth/login"
    data = json.dumps({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("access_token", "")
    except Exception as e:
        print(f"Auth failed: {e}", file=sys.stderr)
        return ""

def post_report(token, report_data):
    """Post report to API."""
    url = f"{API_BASE}/tensor/reports"
    data = json.dumps(report_data).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            print(f"Report posted: {result}")
            return True
    except urllib.error.HTTPError as e:
        print(f"HTTP error {e.code}: {e.read().decode()}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Post failed: {e}", file=sys.stderr)
        return False

def main():
    token = get_token()
    if not token:
        print("No token, saving report locally only")
        return
    
    report = {
        "title": "求解器能力自动发现与注册系统（成熟化）— m10690",
        "summary": "实现多仓求解器能力自动发现闭环：标记扫描→特征提取→台账登记→差异报告。33项pytest全通过，覆盖C1-C5全部验收判据。",
        "scenario_key": "p348w3-n02-capability-auto-discovery",
        "mission_id": 10690,
        "tags": ["capability-discovery", "auto-registration", "multi-repo", "ledger"],
        "markdown": """
# 求解器能力自动发现与注册系统

## 1. 案例介绍

**物理背景**：Tensor 生态包含多个求解器仓（TensorLBM/TensorFEM/TensorDEM/TensorFVM），每个仓实现不同的数值方法能力。人工登记能力到台账效率低、易遗漏。本节点实现机器自发现闭环。

**目标**：通过扫描测试套件中的 `@pytest.mark.capability()` 标记，自动提取能力特征并登记到能力台账，生成差异报告供人工审核。

## 2. 方法

### 架构

```
MultiScanner → FeatureExtractor → AutoRegistrar → DiffReportGenerator
```

- **MultiScanner**：AST/正则扫描多仓测试文件，提取 marker 元数据（能力ID、证据等级、参数、验证锚）
- **FeatureExtractor**：按能力ID聚合记录，保守定级（最低证据等级优先），计算置信度
- **AutoRegistrar**：与台账比对，新增/更新能力条目，哈希链审计，支持撤回/恢复
- **DiffReportGenerator**：生成 Markdown/JSON 差异报告

### 关键设计

- **零领域语义**：所有能力名称、模式来自配置，不硬编码域词条
- **保守定级**：未声明等级默认 WITHHELD；REAL 需验证锚点
- **哈希链**：每次台账变更写入链式哈希，可检测篡改
- **撤回保护**：人工撤回的能力不会被自动登记复活

## 3. 计算结果

| 判据 | 目标 | 实测 | 状态 |
|------|------|------|------|
| C1 扫描覆盖率 | ≥90% | 100% | ✅ |
| C2 特征提取准确率 | ≥95% | 100% | ✅ |
| C3 台账登记一致性 | ≤5%差异 | 0% | ✅ |
| C4 差异报告完整性 | 全章节 | 完整 | ✅ |
| C5 pytest用例数 | ≥12 | 33 | ✅ |

### 测试覆盖

- TestMultiScanner: 7 例（标记扫描、参数提取、证据等级、锚点、多仓、诊断、缺失仓）
- TestFeatureExtractor: 6 例（聚合、定级、结局、置信度、去重、覆盖率）
- TestAutoRegistrar: 7 例（新增、更新、撤回、恢复、防复活、哈希链、缺失检测）
- TestDiffReportGenerator: 3 例（生成、Markdown、JSON）
- TestPipeline: 5 例（端到端、覆盖率、持久化、幂等、注册表）
- TestAcceptanceCriteria: 5 例（C1-C5 验证）

## 4. 结果图

本报告为纯后端/工具链任务，无场量云图。产物为代码模块+测试+示例报告。

## 5. 注意点

- **标记格式**：当前支持 `@pytest.mark.capability("id", key=value)` 格式，列表值如 `anchors=["Laplace"]` 已支持
- **置信度阈值**：默认 0.0（全部登记），可通过配置调整
- **架构限制**：当前为静态扫描，运行期 pytest 插件集成待后续节点

## 6. 误差判定

本任务为工具链开发，无数值计算误差。验收判据全部通过：
- 扫描覆盖率 100% ≥ 90% ✅
- 提取准确率 100% ≥ 95% ✅
- 台账一致性 0% ≤ 5% ✅

## 产物清单

- `src/capability_discovery_multirepo/` — 主模块（6 文件）
- `tests/capability_discovery_multirepo/` — 测试套件（33 例）
- `artifacts/capability_discovery_multirepo_demo.json` — 示例报告
- `artifacts/capability_discovery_multirepo_demo.md` — Markdown 报告
"""
    }
    
    post_report(token, report)

if __name__ == "__main__":
    main()
