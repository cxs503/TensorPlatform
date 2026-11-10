# claim_watch — 声明时效契约（零领域语义）

让「某条数字声明从什么时候起不再可信」成为机器可判：给一条声明记录和当前文件系统状态，
判定 `OK / ENV_DRIFT / SUSPECT / STALE / BROKEN / INVALID`，并把证据等级按判定结果**自动降级**，
全程不留人工判断。

## 契约（`schemas/claim.json`，`claim/1.0`）

| 字段 | 含义 |
|---|---|
| `claim_id` / `value` / `unit` | 声明标识 / 被断言的数 / 不透明单位 token |
| `artifact_uri` / `artifact_sha256` | 承载数字的工件 + 字节指纹 |
| `depends_on[]` (`uri`,`sha256`,`kind`) | 依赖集（`artifact`=算据，`declaration`=清单/锁） |
| `env_fingerprint` | 产出时的环境指纹（arch 族/字节序/指针宽/python/packages/digest） |
| `verified_at` / `evidence_grade` | 绑定时刻 / 绑定时声明的等级（检查器只能降不能升） |

## 判据

| 状态 | 判据 | 等级动作 |
|---|---|---|
| `BROKEN` | 工件缺失 / 哈希不符 / 无哈希 | → WITHHELD |
| `STALE` | 依赖被改 / 依赖消失 / 依赖未钉住 | → WITHHELD |
| `SUSPECT` | 环境指纹跨架构不一致 | → WITHHELD |
| `ENV_DRIFT` | 同架构、解释器/包版本变化 | 降一档 REAL→MOCK→WITHHELD |
| `INVALID` | 不符合 schema | → WITHHELD（不臆测） |
| `OK` | 三项全等 | 保持声明等级 |

优先级：`INVALID > BROKEN > STALE > SUSPECT > ENV_DRIFT > OK`；一条声明可同时列出多个原因。
未记录环境指纹报 `assurance=unasserted`（不判故障）；`STUB`（仅接口）不承载数字，不受时效影响。

## 用法

```bash
python3 -m tools.claim_watch.audit \
  --root TensorPlatform=$PWD --root TensorFEM=/opt/mm-verify/TensorFEM \
  --root TensorLBM=/opt/mm-verify/TensorLBM --root TensorDEM=/opt/mm-verify/TensorDEM \
  --root TensorCAD=/opt/mm-verify/TensorCAD \
  --out artifacts/claim_watch --ledger data/claim_watch/drift_ledger.json
python3 -m tools.claim_watch.figures --artifacts artifacts/claim_watch
python3 -m tools.claim_watch.report  --artifacts artifacts/claim_watch \
  --out docs/reports/claim_watch_m9378.md
```

单条声明也可直接用 `checker.check_claim(claim, resolver, current_env)`，它是纯函数：
只比较字节哈希与环境字段，不读领域语义、不联网、不写盘。

## 只读保证

批量只做 `open(..., 'rb')` 与 `git status/log/show`；注入变异在 `/tmp` 副本上执行；
A4 在**任何产物写出之前**取「跑批后」快照，让快照对恰好夹住审计动作本身。

## 边界（如实声明）

- 只能判定**已声明**依赖的时效；未声明的隐藏依赖漂移时声明仍判 `OK`。
- 历史文档多数没有环境指纹，`SUSPECT` 只对新声明与注入变异生效。
- 依赖指向未入库生成物时判 `STALE`（`dep_missing`）——看不见的依赖不等于成立。