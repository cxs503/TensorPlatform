# 未落地分支台账（branch ledger）— TensorPlatform agent 分支全量分类与成果入库风险量化

**任务**：mission 1875（tp-infra-1 infrastructure 席，自找活）　**工具**：`tools/branch_ledger.py`（本单新增）
**产物**：`artifacts/branch_ledger/ledger.json`（逐分支台账）、`ledger.md`、三张图、`tests/test_branch_ledger.py`（23 用例）

---

## ① 案例介绍（对象与几何式的「参数表」）

本单的对象不是船体，而是**平台的成果入库链路**：仓库 TensorPlatform 的 `agent/m*` 分支堆。
它决定「某席做过的工作是否真的进了主干」，跟船型参数一样需要先度量再决策。

| 参数 | 取值 |
|---|---|
| 仓库 / 基准分支 | `/root/tp-agent/work/m1875` / `main` |
| ref 匹配模式 | `refs/heads/agent/*`（本次不扫 origin，`--include-remote` 可开） |
| 扫描分支数 | 621 |
| 报告快照 | `artifacts/branch_ledger/reports_snapshot.json`：97 篇（API 上限，见 ⑤-1） |
| 按 mission 补探 | 558 条（`GET /tensor/reports?mission_id=X`） |
| 诊断用时 | ≈2 min（纯 git 读 + HTTP 查询，无重算；load15≈122 下按轻量活执行） |

## ② 怎么做的（方法与判据）

分类判据（三重闸，逐条可被推翻）：

1. **ANCHOR**：`git rev-list --count main..branch == 0` ⇒ 分支已在主干历史，内容必然已入库；
2. **BANKED**：`git cherry main branch` 全为 `-`（patch-id 全部命中主干）⇒ rebase / cherry-pick 残壳；
3. **CONTENT_SAME**：patch-id 不等价，但 `git diff --quiet main branch -- <该分支触及路径>` 为空
   ⇒ 内容等价（**专治 squash 合并改写 patch-id 的假阳性**）；
4. **UNIQUE**：以上都不成立 ⇒ 主干既无等价补丁、触及路径上又有净差异。

交叉核验：分支名解析 `m<id>` → 与已发布报告 `mission_id` 关联 ⇒
`REPORTED_UNMERGED`（结论已公布、代码未在主干预期）/ `UNREPORTED`（无痕）/ `ALREADY_IN_BASE`。

工具纪律：零第三方依赖（matplotlib 仅 `--figures` 时导入）；只跑只读 git 查询，不建/删/改任何 ref
（`test_tool_is_read_only` 用 for-each-ref 前后快照钉死）。

## ③ 计算结果（数字全部由程序从 ledger.json 注入）

| 指标 | 数值 |
|---|---|
| 分支总数 | **621** |
| ANCHOR（已在主干） | **43** |
| BANKED（补丁等价） | **26** |
| CONTENT_SAME（内容等价 / squash 残壳） | **2** |
| UNIQUE（未落地） | **550** |
| 未落地分支占比 | **88.6%** |
| 未落地提交合计 | **25256** 条 |
| UNIQUE 分支的未落地提交数（中位/均值/最大） | 9 / 45.9 / 218 |
| REPORTED_UNMERGED（有报告未落地） | **421** |
| UNREPORTED（无痕未落地） | **129** |
| 重复劳动预警簇 | **1** 簇 |

### 按月分布（分支尖端提交月）

| 月 | ANCHOR | BANKED | CONTENT_SAME | UNIQUE |
|---|---|---|---|---|
| 2026-10 | 43 | 26 | 2 | 154 |
| 2026-11 | 0 | 0 | 0 | 396 |

## ⑦ 版本说明

- 本文件与平台报告为**修正版**：目录维度原先按文件计数却标注为「分支数」（实测出现 42876 这类越界值），
  已改为按分支去重计数，并把文件计数单列为 `files_touching`；新增测试 `test_top_dirs_counts_branches_not_files` 钉死口径。
