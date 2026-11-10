# Sealed Evidence & Recovery Gate

## 概述

`tools/sealed_evidence` 模块实现证据封存与回收闸机制，确保任务产出的可复现性与完整性。

## 核心组件

### 1. 契约 Schema (`schema.py`)

定义 `sealed_evidence.json` 结构：

```json
{
  "schema_version": "sealed-evidence/1.0",
  "mission_id": "m10144",
  "criteria": [
    {
      "id": "c1",
      "text_hash": "<sha256>",
      "artifacts": [
        {
          "path": "artifacts/result.json",
          "sha256": "<hex>",
          "bytes": 1234,
          "produced_by": "pytest tests/test_x.py",
          "params_digest": "<sha256>"
        }
      ]
    }
  ],
  "replay": {
    "cmd": "pytest tests/test_x.py",
    "cwd": ".",
    "env_digest": "<sha256>",
    "timeout_s": 300,
    "last_result": {"exit_code": 0, "timed_out": false}
  },
  "sealed_at": "2026-11-10T16:00:00Z",
  "provenance_digest": "<sha256>"
}
```

### 2. 封存器 (`sealer.py`)

从任务 result/报告抽取制品引用 → 路径归一化 → sha256 → 写契约（幂等）。

```python
from tools.sealed_evidence import seal_evidence

contract = seal_evidence(
    mission_id="m10144",
    workdir="/path/to/work",
    criteria=[{"id": "c1", "text": "...", "artifacts": [...]}],
    replay_cmd="pytest tests/",
    output_path="artifacts/sealed_evidence.json",
)
```

### 3. 回收闸 (`recovery_gate.py`)

worktree_recyclable 候选四关拦截：

| 关 | 检查项 | 失败 verdict |
|---|---|---|
| 1 | 契约存在性 | `blocked_no_contract` |
| 2 | sha256 匹配 | `blocked_sha256_mismatch` |
| 3 | 重放可行 | `blocked_replay_failed` |
| 4 | 缺件显式登记 | `blocked_missing_artifacts` |

```python
from tools.sealed_evidence import run_recovery_gate, GateVerdict

result = run_recovery_gate(
    workdir="/path/to/work",
    mission_id="m10144",
    replay_timeout_s=300,
)

if result.verdict == GateVerdict.PASS:
    print("Gate passed")
else:
    print(f"Gate blocked: {result.reasons}")
```

### 4. 重放沙箱 (`replay_sandbox.py`)

最小依赖环境执行 replay.cmd：
- 禁网：设置空代理
- 禁写库：PYTHONDONTWRITEBYTECODE=1
- 超时：硬超时 300s

```python
from tools.sealed_evidence import run_replay_sandbox

result = run_replay_sandbox(
    cmd="pytest tests/",
    cwd="/path/to/work",
    timeout_s=300,
)
```

### 5. 缺件归因器 (`drift_attributor.py`)

把 repro_audit 的 drift 逐条映射到四类：

| 类别 | 条件 |
|---|---|
| `missing` | 文件完全不存在 |
| `truncated` | 文件存在但大小/内容不完整 |
| `sha256_tampered` | 文件存在且大小匹配但 sha256 不同 |
| `command_not_found` | 重放命令不存在 |

```python
from tools.sealed_evidence import attribute_drift_causes, DRIFT_CATEGORIES

causes = attribute_drift_causes(drift_entries, workdir=".")
# 所有 drift 必须落入四类，unknown=0
```

## 验收判据

| 判据 | 要求 | 状态 |
|---|---|---|
| D1 | 8 类缺件注入 → 拦截 8/8=100% | ✅ |
| D2 | 合规样本 N=200 → 放行 200/200 | ✅ |
| D3 | 幂等：同任务连跑两次封存，契约 sha256 相同 | ✅ |
| D4 | 回收保护：N≥20，至少 1 条 drift 由具体缺件解释 | ✅ |
| D5 | drift 归因完备：drift=9 全部落入四类，unknown=0 | ✅ |
| D6 | 零领域语义门：全仓 grep 域词表命中数=0 | ✅ |
| D7 | 回归：既有平台测试通过数≥基线通过数 | ✅ |

## 设计约束

- **零领域语义**：本包不认识任何学科词/船型/仓名；所有域数据经配置/工件注入
- **幂等**：同输入 → 同输出
- **只读**：封存器不修改原始工件；回收闸只写事件与债务账本
