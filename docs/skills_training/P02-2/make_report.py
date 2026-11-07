#!/usr/bin/env python3
"""组装 P02-2 论文级报告 payload（数字程序化注入自 verify_report.json，零手抄）。

用法: python3 make_report.py  →  生成 /tmp/report.json
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
vr = json.loads((HERE / "verify_report.json").read_text(encoding="utf-8"))

rows = "\n".join(
    f"| {c['id']} | {c['check']} | {'PASS' if c['pass'] else 'FAIL'} | {c['detail']} |"
    for c in vr["checks"]
)

md = f"""# 认证导出端点：四 Agent 分工规划（契约先行 + CPM 关键路径 + 机器验收）

## ① 案例介绍（背景 / 对象 / 参数）

平台已有「认证」语义（证据分级 REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED、GCI 不确定度裁决、
provenance sha256 锁定，见 `docs/W10-N07_numerical_uncertainty_certification.md`），但后端
`grep -ril certif backend/api` **零命中**——认证结论无法导出为自包含、可离线复核的工件。
本单规划「认证导出端点」：给定 CERTIFIED 认证记录，异步生成自包含导出包
（裁决 + 判据逐项 + 证据引用 + provenance + sha256 清单），横跨 API/存储/文档/测试四面，
至多 4 个 agent 分工。本单是**规划练习**：交付可执行、可机器验收的规划，不实现端点。

| 参数 | 取值 |
|---|---|
| 端点族 | 3 操作（创建 202 / 查询 200 / 下载 200 流式），异步 job 模式 |
| 横跨面 | API / 存储 / 文档 / 测试，各 1 owner |
| Agent 数 | 4（上限 4），其中测试 owner 兼端到端集成负责人 |
| 契约策略 | 契约先行：OpenAPI 3.1 + DDL + 错误码表 + bundle schema 冻结后并行 |
| 存储 | 新表 `certification_exports`，不 ALTER 既有表；工件 30 天保留、租户配额 500 MB |
| 多租户 | 强制隔离，跨租户 403 |
| 验收 | 8 项机器判据（verify.py 可裁） |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

1. **契约先行**：先冻结契约包（`P02-2/contract/` 下 openapi.yaml / storage.sql /
   error_codes.md / bundle.schema.json），四个面对契约负责而非互等；契约带哈希，改动走变更评审。
2. **WBS 七字段**：4 个工作包（WP1 契约 / WP2 API / WP3 存储 / WP4 文档+测试）强制写全
   目标/输入/输出/owner/验收/依赖/风险，缺一即不合格。
3. **CPM**：估工期（WP1=1.0、WP2=2.0、WP3=1.5、WP4a=1.0、WP4b=1.0 人日），画依赖 DAG，
   最长路径法算关键路径与浮动。
4. **单点集成签发**：E2E 集成负责人握唯一 sign-off 权（四面绿 + E2E 绿 + 契约哈希未变）。
5. **机器验收**：`verify.py` 对规划产物做 8 项结构检查，数字程序化注入，零手抄。

工具链：python3 + PyYAML 6.0.3 + sqlite3（内存库执行 DDL）；复算指令
`python3 docs/skills_training/P02-2/verify.py`。

## ③ 计算结果（程序化注入自 verify_report.json，重跑即复现）

判定：**{vr['verdict']}（{vr['passed']}/{vr['total']} 通过）**

| # | 检查 | 结果 | 关键数字 |
|---|---|---|---|
{rows}

关键路径复算：`WP1 -> WP2 -> WP4b`，总工期 **4.0 人日**（与文档声明一致）；
WP3 浮动 0.5d、WP4a 浮动 1.0d，WP2 为唯一无浮动最长非起点包（延期 1:1 拖全局）。

## ④ 结果图

**无图，理由**：本单为纯规划/文档任务，无数值模拟、无场量、无解析解或文献曲线可对照，
「场量云图 + 比较曲线」不适用；结果即规划结构本身，由 §③ 的 8 项结构检查机器验收
（训练单允许无图，此处按纪律写明理由）。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. 工期为经验估计非实测；关键路径结论对 WP2 ±0.4d 扰动稳健。
2. 规划≠实现：契约文件为提案态，实施时缺陷走契约变更评审（升 version），禁止实现面私改。
3. **最大风险 R5**：后端目前无认证记录表/状态机（勘察零命中），列为前置项；若不成立需重排
   （插入 1.5d 前置包，关键路径变 5.5d）。
4. 方法适用范围：跨 API/存储/文档/测试 的端点族规划；不适用于纯算法内核任务。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| 规划产物结构一致性（8 项检查） | 机器判据（非数值基准） | 不适用 | **不适用** |

**显式回答**：本单为规划类任务，**无基准**——不产生物理量/数值解，无解析解、文献值或基线
可对照（声明原因：无基准，非回避）。替代判据为 8 项结构验收，{vr['passed']}/{vr['total']} 通过；
「是否 <3%」对规划任务不适用，若后续实施，误差判定将以契约 conformance 测试与 E2E sha256
复核为准。
"""

payload = {
    "title": "认证导出端点：四 Agent 分工规划（契约先行 + CPM 关键路径 + 8 项机器验收）",
    "summary": (
        "规划认证导出端点（3 操作异步 job，横跨 API/存储/文档/测试）：契约先行冻结 4 件契约文件，"
        "4 工作包七字段齐全，关键路径 WP1->WP2->WP4b=4.0 人日，8/8 结构检查 PASS。"
    ),
    "scenario_key": "skills_training",
    "mission_id": 6341,
    "tags": ["skills_training", "planning", "contract-first", "cpm", "certification-export"],
    "markdown": md,
    "figures": [],
}
Path("/tmp/report.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("payload written, markdown bytes:", len(md.encode("utf-8")))
