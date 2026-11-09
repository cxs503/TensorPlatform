# IN143-W1-N01: Run-Manifest 回填器接线与五元证据链成熟化

## 物理背景

历史 run 目录（`runs/`、`artifacts/`）大多只有工件散落、没有 manifest，
或 manifest 结构不完整。平台需要一种**机器可复算**的方式把历史 run 的
五元证据链补齐，而非依赖人工填写。

## 五元证据链定义

| 元素 | 含义 | 验证方式 |
|------|------|----------|
| 1. manifest | RunManifest v1 契约结构 | `validate_manifest()` 结构校验 |
| 2. digest | sha256(canonical_json(manifest)) | `manifest_digest()` |
| 3. artifact_verify | 逐工件 sha256 + size 复算 | `verify_artifacts()` |
| 4. chain_level | E1 / E2 / E3 等级 | 基于 1-3 的通过情况 |
| 5. reason | 结构化失败原因 | fail-closed 词表 |

## 等级定义

- **E1**：manifest 缺失或结构校验不通过。reason 给出机器可读失败原因。
- **E2**：manifest 合法 + digest 已算，但工件验证未全部通过。
- **E3**：manifest 合法 + digest 已算 + 全部工件验证通过（完整五元链）。

## 实现

- `tools/evidence_chain/backfill_wiring.py`：核心模块
- `tests/evidence_chain/test_backfill_wiring.py`：16 项 pytest 用例
- `artifacts/backfill_wiring/backfill_report.json`：回填报告

## 验证结果

对 3 个历史 run 目录执行回填：

| Run | 工件数 | 验证通过 | 等级 | Manifest Digest |
|-----|--------|----------|------|-----------------|
| m1386 | 7 | 7 | E3 | c1714132... |
| m1614 | 1 | 1 | E3 | 2dee01ca... |
| m591 | 7 | 7 | E3 | e3982d84... |

**Full chain rate: 100%**（3/3 达到 E3）

## 判据满足

| 判据 | 描述 | 结果 |
|------|------|------|
| B1 | manifest 缺失 → E1 | ✅ (auto-build 路径) |
| B2 | manifest JSON 非法 → E1 | ✅ |
| B3 | manifest 结构不合法 → E1 | ✅ |
| B4 | manifest 合法 + 工件缺失 → E2 | ✅ |
| B5 | 全部通过 → E3 | ✅ |
| B6 | 哈希不符 → 不授予 E3 | ✅ |
| B7 | 幂等性 | ✅ |
| B8 | digest 正确性 | ✅ |
| B9 | 空目录 → E1 | ✅ |
| B10 | 不存在目录 → E1 | ✅ |

## 局限

1. 自动构建的 manifest 使用保守默认值（status=succeeded, validation_status=not_run），
   不代表生产者声明。
2. 远端 URI（s3:// 等）无法本地复算哈希，标 artifact_uri_unsupported 隔离。
3. 本工具证明「工件与声明一致」，不证明数值正确性。
