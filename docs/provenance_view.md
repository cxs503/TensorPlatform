# 谱系追溯视图 (Provenance View)

## 概述

谱系追溯视图是 TensorPlatform Console 的新增功能，用于可视化展示开发任务（mission）的完整生命周期链路。给定一个 `mission_id`，系统渲染从派发到复现的全链条，帮助开发者和管理者快速理解任务的来龙去脉。

## 功能特性

### 链路节点

谱系追溯视图渲染以下 6 个核心节点：

1. **派发 (Dispatch)** — 任务创建信息，包括目标、仓库、基线分支、Runner、状态等
2. **Worktree** — 工作目录和关联的 Agent 会话
3. **分支 (Branch)** — Git 分支名称（通常为 `agent/m{mission_id}`）和 HEAD commit
4. **Pull Request** — PR URL、编号和 CI checks 状态
5. **报告 (Reports)** — 关联的科研进展报告（通过 `mission_id` 关联）
6. **复现 (Runs)** — 注册的 TensorPhysics 运行记录

### 辅助节点

- **验收 (Approval)** — 人审裁定状态和意见
- **子任务 (Children)** — 编排派发的子任务列表

### 视觉设计

- **链路流程图** — 顶部横向流程图，节点亮色表示数据齐全，灰色表示断点
- **详情卡片** — 左右两栏布局，每个节点独立卡片展示详细数据
- **链路完整度** — 显示 `chain_score / chain_max`（满分 6 分）

## API 端点

### GET /api/v1/tensor/missions/{mission_id}/provenance

返回指定任务的完整谱系链路。

**响应示例：**

```json
{
  "mission_id": 641,
  "chain_score": 4,
  "chain_max": 6,
  "nodes": {
    "dispatch": {
      "present": true,
      "data": {
        "mission_id": 641,
        "goal": "...",
        "repo": "TensorPlatform",
        "base_ref": "main",
        "status": "succeeded",
        "created_at": "2026-10-31T10:00:00Z"
      }
    },
    "worktree": {
      "present": true,
      "data": {
        "path": "/work/m641",
        "session_id": 12345
      }
    },
    "branch": {
      "present": true,
      "data": {
        "name": "agent/m641",
        "head_commit": "abc123...",
        "repo": "TensorPlatform"
      }
    },
    "pr": {
      "present": false,
      "data": {
        "url": null,
        "number": null,
        "checks": {}
      }
    },
    "reports": {
      "present": true,
      "data": [
        {
          "id": 42,
          "title": "谱系追溯 UI 实现报告",
          "scenario_key": "platform-dev",
          "status": "published"
        }
      ]
    },
    "runs": {
      "present": false,
      "data": []
    },
    "approval": {
      "present": true,
      "data": {
        "id": 100,
        "kind": "dev_acceptance",
        "status": "approved",
        "decided_by": "admin"
      }
    },
    "children": {
      "present": false,
      "data": []
    }
  }
}
```

## 前端路由

- `/provenance` — 谱系追溯入口页（需输入 mission_id）
- `/provenance/:id` — 直接追溯指定 mission

## 导航入口

在 Console 顶部导航栏新增「谱系追溯」链接，位于「开发任务」之后。

## 判据

机器判据：对 ≥3 个真实 mission 渲染链路无断点（API 200 且字段齐全）。

验证脚本：`scripts/validate_provenance.py`

```bash
python scripts/validate_provenance.py \
  --api-base http://localhost:8000 \
  --token $TOKEN \
  --mission-ids "641,640,639"
```

## 实现文件

- **后端**: `backend/api/tensor_missions.py` — 新增 `get_provenance` 端点
- **前端视图**: `frontend-console/src/views/ProvenanceView.vue`
- **前端 API**: `frontend-console/src/api/index.ts` — 新增 `missionApi.provenance`
- **路由**: `frontend-console/src/router/index.ts` — 新增 `/provenance` 和 `/provenance/:id`
- **导航**: `frontend-console/src/layouts/ConsoleLayout.vue` — 新增导航链接

## 设计决策

1. **节点可选性** — 并非所有任务都有 PR 或复现 Run，因此节点设计为 `present: bool`，UI 根据此字段渲染断点
2. **分支推导** — 即使 `result.evidence.branch` 为空，分支名仍可推导为 `agent/m{mission_id}`，因此 branch 节点始终 `present: true`
3. **报告关联** — 通过 `tensor_reports.mission_id` 外键关联，支持一对多
4. **Run 关联** — 优先从 `result.registered_run_ids` 获取，回退到 executions 表的 `registered_run_ids`

## 局限与未来改进

- PR 数据依赖 agent 在 complete 时上报 `pr_url`，目前部分任务未上报
- Run 关联逻辑较简单，未来可支持更复杂的溯源（如通过 scenario_key 关联）
- 可增加时间线视图，展示各节点的时间顺序
