# evidence_chain — 证据链批量入账 CLI

> 战役节点 **IN17-B3**（判例 #610 清洗 · 证据链 demo 合成算例化）。
> 只增不改：本目录是独立新工具，**不 import/不修改 `backend/`**、不动表结构、不新增端点。

## 它解决什么问题

平台已有运行登记契约 `POST /api/v1/tensorphysics/runs`
（`contracts/tensorphysics-run-manifest-v1.schema.json`，实现在 `backend/api/tensorphysics_runs.py`），
但它**只存元数据、不碰文件**——端点文档明确：*"does not itself ingest files, verify remote
object existence, enforce the complete nested JSON Schema"*。于是每次战役入账都靠人工：
逐个算 sha256、抄进 manifest、粘贴 POST、遇到坏文件整批回滚、中断后不知道登到哪了。

本工具把这段手工活变成流水线：

```
工件目录 ──build──> RunManifest（逐文件 sha256/size）
RunManifest ──ingest──> 逐文件复算 ──对账──> 批量登记（sink）
                                   ├── 一致 → registered / already_registered
                                   └── 不一致 → quarantined（隔离，不阻断同批其它 run）
```

## 用法

```bash
# 1) 扫工件目录 → 生成 RunManifest（必须显式声明 status/validation_status，工具不替你声明"已验证"）
python3 tools/evidence_chain/evidence_chain.py build \
    --run-dir artifacts/m4960/evidence/batch_clean/r2_design_point \
    --meta meta.json --dry-run

# 2) 批量登记：离线演练（本地 JSONL，等价实现端点的幂等 + 409 语义）
python3 tools/evidence_chain/evidence_chain.py ingest \
    --source-dir artifacts/m4960/evidence/batch_clean \
    --out-dir artifacts/m4960/chain --sink local \
    --registry artifacts/m4960/chain/registry.local.jsonl

# 3) 真正写进平台登记表（冻结端点，只读使用）
python3 tools/evidence_chain/evidence_chain.py ingest \
    --source-dir artifacts/m4960/evidence \
    --out-dir artifacts/m4960/chain --sink http \
    --base-url http://127.0.0.1:8000/api/v1 --token-env TENSOR_API_TOKEN --organization-id 1

# 4) 只校验不登记（预演/CI）
python3 tools/evidence_chain/evidence_chain.py ingest ... --sink none --dry-run
```

`--source-dir` 可重复；工具在根目录向下 ≤2 层查找
`run_manifest.json` / `manifest.json` / `RunManifest.json`（每目录取第一个命中的）。

产出（`--out-dir`）：

| 文件 | 内容 | 幂等性 |
|---|---|---|
| `registry.local.jsonl` | 登记表（`--sink local`）；`(run_id, manifest_sha256)` 幂等、异摘要 409 | 重跑逐字节不变 |
| `state.json` | 状态台账：run → 摘要 / 结算状态 / 工件摘要清单（断点续传依据） | 重跑逐字节不变 |
| `quarantine.json` | 被隔离行 + 逐工件的 declared/actual sha256 与 size | 重跑逐字节不变 |
| `ledger.jsonl` | 追加式审计流水（只记**状态跃迁**，resumed 跳过不写） | 重跑逐字节不变 |
| `ingest_report.json` | 本次调用报告；`evidence_state` 段是幂等视图，`rows/counts` 是执行视图 | `evidence_state` 段不变 |

退出码：`0` 全部通过 · `2` 存在隔离行（其它行照常处理）· `3` 致命（无 manifest / 参数错）。

## 判据（回归见 `tests/test_evidence_chain_m4960.py`，21 例）

| 判据 | 断言 |
|---|---|
| I1 幂等重入 | 二轮 `registry/state/quarantine/ledger` 逐字节 `diff=0`，`evidence_state` 段不变，`registered==0`；已结算 run **不再触发 sink 调用** |
| I1b dry-run 可信 | dry-run 预测的各 outcome 计数 == 随后 apply 的实际计数 |
| I2 坏件隔离 | 哈希不符 → `quarantined`（含 declared/actual/size 三元组），同批好件仍 `registered` |
| I3 结构拒收 | 非法 JSON → `manifest_unparsable`；缺键/词表外/非 64hex/负 size → `manifest_invalid`；空目录 → 退出码 3 且不落任何账本 |
| I4 冲突不吞 | 同 `run_id` 异摘要 → `conflict`，登记表零改动 |
| I5 零副作用 | `--dry-run` 不写任何文件、不发任何 HTTP |
| I6 不假装已验证 | `s3://` 等远端 URI → `artifact_uri_unsupported` 隔离；`../` 越界 → `artifact_uri_escapes_run_dir` |

## 诚实边界

* **远端 URI 无法本地复算哈希**：一律隔离，宁可 WITHHELD 不可假 REAL；要入账远端工件必须先
  落地成文件（或另配 object-store 拉取器，本工具不做）。
* **不判数值正确性**：工具只证明「工件与 manifest 声明一致」+「登记幂等」。`validation_status`
  是生产者的声明，工具照抄不背书；数值验收仍归各域验证器。
* **`--sink local` 是端点语义的本地等价实现**（幂等 + 409），不是端点本体；端到端真登记需要可达
  后端与数据库。m4960 演示环境里没有可用的 TensorPlatform 后端进程（`:8000` 未挂载该路由），
  故 `--sink http` 只在本地 stub 服务上验证了 200/409/5xx 三条路径，真后端联调列为本节点遗留项。
* **并发**：同一 `--out-dir` 不支持多进程并发写（无文件锁）；串行重入安全。多实例请各自
  `--out-dir` 或外层加锁。

## 演示（m4960 / m5013 域无关版）

`tools/evidence_chain/demo_m4960.py` 使用**合成解析函数数据**（y = α·x^β + γ·sin(δ·x)）
走完四相入账，产出 `artifacts/m4960/`（工件、账本、判据数字、3 张图）。

**域无关性**：演示数据完全由数学函数生成，不依赖任何物理域 API 或参数。
这证明证据链机制（哈希锚/防篡改/复算对拍）是通用的，可以服务于任何学科域。
域特定数据应通过 API/配置/工件注入，不应硬编码进 backend/tools/tests。
