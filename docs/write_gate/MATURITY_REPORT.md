# 写入边界门成熟化报告

## 任务目标

实现工作树写入边界门的成熟化：白名单越界/跨仓污染/凭证泄漏/大文件拦截四合一预提交拦截，零领域语义平台机制。

## 实现内容

### 1. R4 大文件/二进制拦截（新增）

**文件**: `tools/write_gate/binary_gate.py`

- 文件大小超过 `binary.max_bytes`（默认 5MB）且不在豁免列表 → 拦截
- 二进制内容检测（NUL 字节）且不在豁免列表 → 拦截
- 豁免列表支持 glob 模式（`artifacts/**`, `**/*.png` 等）
- 配置全外置到 `policy.binary`

### 2. 事件账本（新增）

**文件**: `tools/write_gate/ledger.py`

- 每次 BLOCK 判决自动写一条 JSONL 记录到 `runs/write_gate/events.jsonl`
- 记录含：时间戳、verdict digest、违规摘要、策略引用、上下文（mission_id 等）
- 写入失败不阻塞门判决（辅助审计）
- CLI 支持 `--no-ledger`、`--ledger-path`、`--context` 选项

### 3. 证据哈希（增强）

**文件**: `tools/write_gate/gate.py`, `tools/write_gate/model.py`

- 每条违规含 `evidence_sha256` 字段（patch 片段 sha256，16 字符截断）
- 对内容违规：取命中行前后上下文的 sha256
- 对路径违规：取路径 + 违规类型的 sha256
- 整份判决有稳定 `digest`（逐字节可复算）

### 4. pre-commit 钩子（新增）

**文件**: `tools/write_gate/pre-commit-hook`

- Bash 脚本，安装到 `.git/hooks/pre-commit`
- 读暂存区变更，四合一判决
- BLOCK 时退出非 0，阻止提交
- 支持环境变量配置

### 5. CI 工作流（新增）

**文件**: `.github/workflows/write-gate.yml`

- 在 PR 和 push 时自动运行
- 跑单元测试 + CLI 自检 + 验收跑批
- 上传验收产物到 GitHub Artifacts

### 6. 策略配置更新

**文件**: `data/write_gate/policy.json`

- 新增 `binary` 组（大文件/二进制判据配置）
- 新增 `ledger` 组（事件账本配置）
- 豁免列表使用 `**/*.ext` 模式匹配任意深度的文件

## 测试覆盖

### 新增测试

**文件**: `tests/test_write_gate_maturity.py`（23 个测试）

- `TestBinaryGate`（9 个）：R4 大文件/二进制判据
- `TestLedger`（5 个）：事件账本
- `TestEvidenceHash`（4 个）：证据哈希
- `TestIntegration`（3 个）：四道判据协同
- `TestCLIIntegration`（2 个）：CLI 集成

### 全部测试

```
132 passed, 1 warning in 7.12s
```

包含：
- `tests/test_write_gate.py`（50 个）
- `tests/test_boundary_gates.py`（36 个）
- `tests/test_boundary_gate_recall.py`（23 个）
- `tests/test_write_gate_maturity.py`（23 个）

## 验收结果

```json
{
  "schema": "tp.write_gate.acceptance/v1",
  "criteria": {
    "whitelist": {"pass": true, "rate": 1.0},
    "cross_repo": {"pass": true, "rate": 1.0},
    "secrets": {"pass": true, "rate": 1.0, "redaction_ok": true},
    "false_positive": {"pass": true, "rate": 0.0},
    "determinism": {"pass": true},
    "fail_closed": {"pass": true},
    "semantics": {"pass": true, "hits": []},
    "pytest": {"pass": true, "passed": 50},
    "performance": {"pass": true, "seconds": 0.30},
    "allow_samples": {"pass": true}
  }
}
```

## 产物清单

| 路径 | 说明 |
|------|------|
| `tools/write_gate/binary_gate.py` | R4 大文件/二进制判据模块 |
| `tools/write_gate/ledger.py` | 事件账本模块 |
| `tools/write_gate/pre-commit-hook` | pre-commit 钩子脚本 |
| `tools/write_gate/gate.py` | 编排层（集成 R4 + 证据哈希） |
| `tools/write_gate/model.py` | 数据模型（v2 schema + evidence_sha256） |
| `tools/write_gate/policy.py` | 策略装载（binary + ledger 组） |
| `tools/write_gate/cli.py` | CLI（ledger 集成 + 新选项） |
| `tools/write_gate/__init__.py` | 包导出（新增模块） |
| `tools/write_gate/README.md` | 使用文档 |
| `.github/workflows/write-gate.yml` | CI 工作流 |
| `data/write_gate/policy.json` | 策略配置（binary + ledger） |
| `tests/test_write_gate_maturity.py` | 成熟化测试（23 个） |
| `artifacts/write_gate/acceptance.json` | 验收报告 |

## 纪律遵守

1. **零领域语义**：代码不含任何学科/业务词条，规则全部外置到策略文件
2. **可复算**：判决是纯函数，同一输入必得逐字节相同 JSON
3. **fail-closed**：判不了的情形一律拦截，绝不默认放行
4. **证据链**：每条违规含 `evidence_sha256`，整份判决有稳定 `digest`
5. **脱敏**：凭证命中永远只回显前缀 + 掩码，绝不回显原文

## 局限与未来工作

1. **二进制检测**：当前使用简单的 NUL 字节检测，可考虑更精确的 magic number 检测
2. **账本轮转**：当前账本为单文件追加，大量 BLOCK 时可能需要轮转机制
3. **性能**：1000 路径判决约 0.3 秒，对于大型提交可能需要优化
4. **策略热加载**：当前策略在启动时加载，修改后需重启
