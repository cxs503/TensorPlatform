# P143W2C-N03 补测：平台真实库只读扩样（判据 (a) 的规模外推）

> 主报告：`docs/backfill_wiring_P143W2C_N03.md`（合成语料 240 条，判据 a~f 全过）。
> 本篇补的是**真实数据**：直接在平台 PostgreSQL 的 `outcome_evaluations` 上做一次**只读**回填体检
> （`PGOPTIONS='-c default_transaction_read_only=on'`，连接后任何写语句都会被库拒绝），
> 回答「历史评价能不能被内容追溯」，而不是只看合成语料跑得漂亮。

## ① 案例介绍

平台真实库里 `outcome_evaluations` 共 **4632** 条历史评价，其中 `confirmed` **4370** 条。
它们来自历次任务的自动登记，`evidence_refs` 是平台内部引用形态（`{kind, ref}` 列表），
本次体检把它们逐条喂给回填器的内容可追溯体检（`verify_run_content` + 五元形态复核）。

## ② 怎么做的

1. 只读连接真实库，取全量 `outcome_evaluations`（4632 条）；
2. 对每条：解析 `evidence_refs` → 五元形态复核 → 尝试解析 run 目录（本次**未注入目录映射**，
   因为历史 run 的目录布局属平台外知识；解析不到即 `BACKFILL_NO_RUN_DIR`）→ fail-closed 定级；
3. 统计三分类、`evidence_refs` 形态分布、结算面现状（`outcome_eval_chain_audit.chain_blocked`）；
4. 产出 `real_db_probe.json` 与本篇。

## ③ 计算结果

| 项 | 值 |
|---|---|
| `outcome_evaluations` | 4632 |
| `confirmed` | 4370 |
| `confirmed` 且 S3+/S4/S5/S-N | **0** |
| 抽样体检条数 | 4632 |

三分类（真实库）：

| 分类 | 条数 |
|---|---|
| `full_chain_granted` | 0 |
| `capped_missing` | 4632 |
| `verify_failed` | 0 |

`evidence_refs` 形态分布（前 6）：

| 形态（kind 集合） | 条数 |
|---|---|
| `api,report` | 1787 |
| `artifact,report,run` | 1189 |
| `artifact,run` | 755 |
| `api,artifact,report` | 517 |
| `artifact,commit,run` | 199 |
| `<空>` | 87 |

样例（真实行，原样摘录 3 条）：

```json
[
  {
    "evaluation_id": "0a92f52e-b4f1-59ac-ae7a-c593c93adb5c",
    "evidence_refs": [
      {
        "kind": "api",
        "ref": "/api/v1/tensor/missions/6198/provenance"
      },
      {
        "kind": "report",
        "ref": "7365"
      },
      {
        "kind": "artifact",
        "ref": "tensor_capability_deltas:mission=6198"
      }
    ]
  },
  {
    "evaluation_id": "0ce0ccac-ca7f-5fd8-8199-393dd74426b3",
    "evidence_refs": [
      {
        "kind": "api",
        "ref": "/api/v1/tensor/missions/5712/provenance"
      },
      {
        "kind": "artifact",
        "ref": "tensor_capability_deltas:mission=5712"
      }
    ]
  },
  {
    "evaluation_id": "84ae5c39-91fb-5e77-b425-9ac7cf2a0a12",
    "evidence_refs": [
      {
        "kind": "api",
        "ref": "/api/v1/tensor/missions/6202/provenance"
      },
      {
        "kind": "report",
        "ref": "7367"
      },
      {
        "kind": "artifact",
        "ref": "tensor_capability_deltas:mission=6202"
      }
    ]
  }
]
```

结算面现状：`outcome_evidence_chains` 存在且 **0** 行；`outcome_eval_chain_audit` 存在，
`chain_blocked = TRUE` 的行数 **0**；`confirmed` 高等级行数为 **0** ——
即真实库当前**没有** S3+ confirmed 结论需要链检拦付，E1 封顶的欠账是「潜在」而非「已发生拦付」。

## ④ 结果图

`artifacts/backfill_wiring/p143w2c_n03/figures/fig4_real_db_probe.png`：
左=真实库 `evidence_refs` 形态分布；右=真实库三分类（全部 fail-closed 封顶）。

## ⑤ 注意点（诚实结论）

1. **真实历史评价无法内容追溯**：`evidence_refs` 里的 `run` 引用是 `mission:N#result.field` 的
   **数据库指针**（JSON 指针），不是工件目录；`artifact` 引用是 `tensor_capability_deltas:mission=N`
   之类的表标识，不是文件路径。没有任何一条能落到「含 manifest 的 run 目录」→
   `run_dir_resolvable = 0`，因此 0 条授权是**正确行为**，不是回填器失灵。
2. 这正是主报告注意点 6 说的「真实通过率须在真实库复跑」的结果：**通过率 0%**。
   原因不是判据太严，而是历史 run 从未 manifest 化（平台此前只登记元数据、不登记工件摘要）。
3. 清偿路径（机制而非人工）：新 run 走 `tools/evidence_chain/evidence_chain.py ingest` 登记
   （产出 `registry.local.jsonl` 独立锚）后，回填器即可授完整链；历史行若要清偿，
   必须先补登记工件摘要——**不能**由回填器自己生成 manifest 来自证（那就是自签）。
4. 本次体检**只读**，未在真实库写任何一行（`default_transaction_read_only=on`）。

## ⑥ 误差判定

| 关键量 | 基准 | 实测 | 误差 |
|---|---|---|---|
| `outcome_evaluations` 行数 | PostgreSQL `count(*)`（同库同事务） | 4632 | 0（同一来源，无估计） |
| 抽样条数 | 全量（limit=None） | 4632 | 0 |
| `capped_missing` 条数 | 抽样条数（每条均无 run 目录） | 4632 | 0 |
| `full_chain_granted` | 0（无 manifest 可验） | 0 | 0 |

**是否 <3%**：是——上表为**精确计数**（非抽样估计），与库内 `count(*)` 逐条相等；无统计误差。
唯一不可裁项是「若补登 manifest 后能清偿多少条」，本次不声明（需真实补登实验，不做猜测）。
