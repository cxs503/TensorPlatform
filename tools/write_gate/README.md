# 写入边界门（Write Boundary Gate）

## 概述

四合一写入边界门，用于在提交前拦截不合规的写入操作：

1. **R1 白名单越界**：路径不在任务声明的允许写入范围内
2. **R2 跨仓污染**：路径越出工作树根、进入 .git 内部、或属于其他租户仓
3. **R3 凭证泄漏**：提交内容包含密钥、口令、私钥等敏感信息
4. **R4 大文件/二进制**：文件超过大小阈值或为二进制内容（不在豁免列表内）

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI / pre-commit                          │
│  python3 -m tools.write_gate.cli staged --root . --active-repo X │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         gate.py (编排层)                         │
│  evaluate_changes() → 四道判据 → Verdict                         │
└─────────────────────────────┬───────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  whitelist.py │   │  crossrepo.py │   │  secrets.py   │
│  R1: 白名单   │   │  R2: 跨仓     │   │  R3: 凭证     │
└───────────────┘   └───────────────┘   └───────────────┘
                              │
                              ▼
                    ┌───────────────┐
                    │ binary_gate.py│
                    │ R4: 大文件    │
                    └───────────────┘
                              │
                              ▼
                    ┌───────────────┐
                    │   ledger.py   │
                    │ 事件账本      │
                    └───────────────┘
```

## 使用

### pre-commit 钩子

```bash
# 安装
cp tools/write_gate/pre-commit-hook .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# 或符号链接
ln -sf ../../tools/write_gate/pre-commit-hook .git/hooks/pre-commit
```

### CLI

```bash
# 预提交门（读暂存区）
python3 -m tools.write_gate.cli staged --root . --active-repo TensorPlatform

# 按路径判
python3 -m tools.write_gate.cli paths src/main.py docs/README.md

# 由 JSON 变更清单判（CI 复用）
python3 -m tools.write_gate.cli json changes.json

# 只做凭证扫描
python3 -m tools.write_gate.cli content some_file.txt
```

### 环境变量

- `EDIT_WHITELIST`：逗号分隔的白名单 glob（覆盖策略默认值）
- `WRITE_GATE_POLICY`：策略文件路径（默认 `data/write_gate/policy.json`）
- `WRITE_GATE_ACTIVE_REPO`：本工作树所属仓名
- `WRITE_GATE_QUIET`：非空时只输出错误

### CLI 选项

- `--no-ledger`：不写事件账本
- `--ledger-path PATH`：账本路径（覆盖策略配置）
- `--context JSON`：附加上下文（如 mission_id, branch）
- `--format {text,json}`：输出格式
- `--json PATH`：判决 JSON 输出路径

## 策略配置

策略文件 `data/write_gate/policy.json` 控制所有判据行为：

```json
{
  "schema": "write_gate/policy/v1",
  "whitelist": {
    "enabled": true,
    "allow_globs": ["tools/**", "docs/**"],
    "deny_globs": [".git", ".git/**"],
    "read_only_globs": []
  },
  "cross_repo": {
    "enabled": true,
    "forbid_absolute": true,
    "forbid_escaped_root": true,
    "forbid_git_internals": true,
    "forbid_nested_repo_markers": true,
    "check_symlinks": true
  },
  "secrets": {
    "enabled": true,
    "rules": [
      {"id": "private_key_block", "pattern": "-----BEGIN ... PRIVATE KEY-----"},
      {"id": "credential_assignment", "pattern": "(?i)api_key\\s*=\\s*..."}
    ]
  },
  "binary": {
    "enabled": true,
    "max_bytes": 5242880,
    "allow_globs": ["artifacts/**", "**/*.png"]
  },
  "ledger": {
    "enabled": true,
    "path": "runs/write_gate/events.jsonl"
  }
}
```

## 判决输出

```json
{
  "schema": "tp.write_gate.verdict/v2",
  "verdict": "FAIL",
  "decision": "allow",
  "violations": [
    {
      "code": "git_internals",
      "category": "cross_repo",
      "path": ".git/config",
      "rule_id": "cross_repo.forbid_git_internals",
      "detail": "写入仓元数据目录（.git 内部只读）",
      "evidence_sha256": "bc9e1d49"
    }
  ],
  "totals": {
    "changes": 1,
    "blocked": 1,
    "by_category": {"cross_repo": 1, "whitelist": 0, "secret": 0, "binary": 0}
  },
  "policy": {"sha256": "..."},
  "digest": "..."
}
```

## 事件账本

每次 BLOCK 判决自动写入 `runs/write_gate/events.jsonl`：

```json
{
  "timestamp": "2026-11-09T08:27:09Z",
  "verdict": "FAIL",
  "digest": "...",
  "blocked": 1,
  "by_category": {"cross_repo": 1},
  "policy_sha256": "...",
  "violations_summary": [...],
  "context": {"mission_id": "m8497", "branch": "agent/m8497"}
}
```

## 退出码

- `0`：PASS（放行）
- `2`：FAIL（拦截）
- `3`：内部错误（策略/参数错）

## 设计原则

1. **零领域语义**：代码不含任何学科/业务词条，规则全部外置到策略文件
2. **可复算**：判决是纯函数，同一输入必得逐字节相同 JSON
3. **fail-closed**：判不了的情形一律拦截，绝不默认放行
4. **证据链**：每条违规含 `evidence_sha256`（patch 片段摘要），整份判决有稳定 `digest`
5. **脱敏**：凭证命中永远只回显前缀 + 掩码，绝不回显原文

## 测试

```bash
# 单元测试
python3 -m pytest tests/test_write_gate.py tests/test_write_gate_maturity.py -v

# 验收跑批
python3 tools/write_gate/acceptance.py --out artifacts/write_gate --work /tmp/write_gate_accept
```

## CI 集成

`.github/workflows/write-gate.yml` 定义了 CI 作业，在 PR 和 push 时自动运行。
