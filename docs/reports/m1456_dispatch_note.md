# m1456 派单记录与交接（诚实记录）

## 已派单

| 项 | 值 |
|---|---|
| mission id | **1488** |
| runner | `native`（body 显式含 `"runner":"native"`） |
| repo | `ship-performance-platform` |
| 状态 | `failed`（被 `tp-ver-2` 领取后 <1 min 崩） |
| 失败原因（原文） | `gate=worker-crash`：`RuntimeError: 仓库不存在或不是 git: /srv/tensor/workspace/ship-performance-platform` |

## 为什么失败 / 复查结论

- 平台仓**不在生态仓库根目录**：实际源码在
  `/srv/tensor/ship-workspace/ship-performance-platform/`
  （:8001 服务的 cwd 即 `…/ship-performance-platform/backend`），
  而 dispatcher 只在 `/srv/tensor/workspace/<repo>` 下解析 repo 名 ⇒ 必然建 worktree 失败。
- 且该仓不在本席仓白名单（TensorLBM/TensorPlatform/TensorPhysics/TensorDEM/TensorBEM/
  TensorSPH/TensorDG），**本席按纪律不修改它**，只做只读诊断。

## 交接给 owner / 下一单的两条可行路径

1. 把平台仓建为生态 git 仓（或加软链 `/srv/tensor/workspace/ship-performance-platform`
   → 实际目录）并把它加入仓白名单，然后用 **mission 1488 的 goal 原文**重派
   （goal 内的 EDIT_WHITELIST 为 `api/, tests/, docs/`）。修复项共 4 条 + 6 条验收判据，
   判据工具已就绪：`python3 tools/maneuver_judge/run_audit.py --out artifacts/m1456_maneuver`。
2. 人工修 4 处（行号见 `manoeuvring_evidence_audit.md` 末节）：补 `Hr`、删 6 处 `results.get(...,L*倍数)`
   兜底、删 Z 形 `|δ|*0.15/0.25`、改写 `calculate_kt` 并统一节/米每秒口径。

修复后本地即可复核：`pytest tests/test_maneuver_judge.py` 的 `TestForensicReplay`
会因基线变化变红——这是预期的“判决已改变”提示，届时按新快照更新断言。
