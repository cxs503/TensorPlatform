<!-- 本文件由 tools/tenancy_gate/maturation_acceptance.py 自动生成，数字零手抄 -->

### 表 1 路径预留与交集检测（判据 A1/A2/A4/A5）

| 判据 | 场景数 | 达标数 | 比率 | 阈值 | 判定 |
|---|---|---|---|---|---|
| A1 同路径并发冲突检出 | 33 | 33 | 100.0% | 100% | PASS |
| A2 changed_files 重叠命中 | 20 | 20 | 100.0% | 100% | PASS |
| A4 无重叠路径无冲突 | 47 | 47 | 100.0% | 100% | PASS |
| A4b 无重叠文件无冲突 | 20 | 20 | 100.0% | 100% | PASS |
| A5 单任务独占路径无冲突 | 1 | 1 | 100.0% | 100% | PASS |

独立参考实现（本文件自带 globstar 递归匹配器）与门判定的一致率：80/80 = 100.0%。

### 表 2 三方合并 dry-run（判据 A3）

| 场景族 | 期望状态 | 实测状态 | 退出码 | 裁决 | 命中 |
|---|---|---|---|---|---|
| disjoint_files | clean | clean | 0 | auto_merge | YES |
| disjoint_hunks | clean | clean | 0 | auto_merge | YES |
| both_edit_file_distinct_regions | clean | clean | 0 | auto_merge | YES |
| disjoint_files | clean | clean | 0 | auto_merge | YES |
| same_hunk | conflict | conflict | 1 | manual_arbitration | YES |
| same_hunk | conflict | conflict | 1 | manual_arbitration | YES |
| unknown | unknown | unknown | 2 | rollback | YES |

可自动合并场景 `exit_code==0` 覆盖：4/4；内容冲突 → 人工仲裁：2/2；无法判定 → 回滚：1/1。

### 表 3 调度器接线与回归（判据 A6）

| 检查项 | 结果 |
|---|---|
| off_skips | PASS |
| report_records | PASS |
| report_never_blocks | PASS |
| enforce_admits_first | PASS |
| enforce_blocks_conflict | PASS |
| 台账链校验（4 事件） | PASS |
| 回归测试（tests/test_tenancy_gate.py, tests/test_tenancy_gate_scheduler.py） | 125 passed, 1 warning in 2.86s |

### 表 4 排队动力学

| 阶段 | 任务 | 裁决 | 队列位置 |
|---|---|---|---|
| 入队 | q0 | queued | 1 |
| 入队 | q1 | queued | 2 |
| 入队 | q2 | queued | 3 |
| 入队 | q3 | queued | 4 |
| 入队 | q4 | queued | 5 |
| 入队 | q5 | queued | 6 |
| 释放 owner1 后 drain | q0 | admitted | 剩余 5 |
| 释放 owner2 后 drain | q1 | admitted | 剩余 4 |

两轮释放共放行 2 个；剩余队列长度 4（同文件写面仍互斥，FIFO 不插队）；幂等复跑：空转无新增（重复 drain 判定稳定）。

### 表 5 零领域语义扫描（判据 A7）

扫描文件 16 个，预注册词条 35 条，命中 0 条 → PASS。
