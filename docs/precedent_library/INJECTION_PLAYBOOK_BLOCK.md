# E3 注入块（PLAYBOOK append-only 通道）

> 机器生成：`python3 -m tools.precedent_library.make_artifacts`
> 目标文件：`/root/tp-agent/AGENT_BOOTSTRAP.md`；落位锚点：`## 逃生规则` 之前；单条上限 120 字符。

## 1. 渲染块（13 条）

```text
- E3-GATE 开工自检 8 项：数字判据/工件路径/重跑命令/基线与参考/honest FAIL/worktree 分支/报告/不重复；缺一项即返工。
- E3-S1-01 对标单先过几何门再报误差；单项 FAIL 就写 FAIL+xfail，不许用总分掩盖（m5118：G2 失败、−0.24% 同报）。
- E3-S1-02 判据表逐条裁决：机制跑通≠指标达标；C2 实测 30.5%<90% 就写 FAIL，不换算口径充数。
- E3-S1-03 聚合端点判据写成等式（返回值 vs DB 直查误差=0），别验收'形状对'；m5121 12 项 KPI 逐项 0 误差。
- E3-S1-04 预算类单先量再判：80 s/工况→批量可行性裁决写进交付，数字与工件路径同报（m5123）。
- E3-S2-01 改进单必须同判据 A/B：atomic 92.1ms vs 降级 400.9ms、残留 0 vs 1 行，才算 S2（m5115）。
- E3-S2-02 域词硬编码是债：15 条字面量→domain_display.json+缺文件恒等降级，前后对照才算 S2。
- E3-S3-01 防造假靠结构不靠自觉：图只从 SQL 取数+每图 sha256↔snapshot_hash（demo PASS 且两次重跑同哈希）。
- E3-S3-02 评价要能拒：无理由/无证据拒登记，auto_scan 只能 S1-S3、S4/S5 落 candidate，禁加权排名。
- E3-S4-01 空缺级如实标注：无独立评审+外部基准的'突破'不算 S4，别把自证 demo 抬成突破。
- E3-A-01 反例：58 次调用不改判据、不 commit、不 finish=m5047 式裸死；每步都要留下可复算证据。
- E3-A-02 反例：PG1-PG8 全绿仍被否（W2）——检查通过≠结论成立；数字必须绑到哈希锚定的不冻工件。
- E3-A-03 反例：auto_scan 回填是草案；S4/S5/S-N 落 candidate——机器扫描冒充同行评审=等级通胀。
```

## 2. 注入语义（测试可失败）

| 不变式 | 行为 | 验收测试 |
|---|---|---|
| append-only | 既有字节与顺序原样保留，新行插在锚点前 | `test_injection_is_append_only_and_idempotent` |
| 幂等 | 同一 case token 已存在即跳过，重跑插入 0 条 | 同上 |
| 锚点缺失即报错 | 绝不静默写到错误小节 | `test_injection_refuses_missing_anchor` |
| 字符预算 | 单条 ≤120 字符（CJK 计 1） | `test_injection_lines_within_budget_and_unique` |
| 回执 | `apply` 后把新行逐条读回，缺失即抛错 | 注入函数内置 |

## 3. 注入回执（对目标文件的现场记录）

```json
{
  "apply": {
    "at": "2026-11-07T06:34:16+00:00",
    "target": "/root/tp-agent/AGENT_BOOTSTRAP.md",
    "anchor": "## 逃生规则",
    "inserted_count": 13,
    "skipped_count": 0
  },
  "rerun": {
    "target": "/root/tp-agent/AGENT_BOOTSTRAP.md",
    "anchor": "## 逃生规则",
    "inserted_count": 0,
    "skipped_count": 13,
    "verified_in_target": 12,
    "lines_total": 13
  }
}
```

读法：`apply` = 首次落地写入的行数；`rerun` = 紧接着再执行一次的干跑结果（`inserted_count=0`
即幂等）；`verified_in_target` = 目标文件中逐条读回命中的行数。

## 4. 判例 → 注入行对应

| case | 级别 | 标题 | 行字符数 |
|---|---|---|---|
| `E3-S1-01` | S1 | 基准对标按判据完成，几何门失败如实降级（honest FAIL 不可覆盖） | 77 |
| `E3-S1-02` | S1 | 外部真值面任务：判据逐条裁决，未达标项如实 FAIL | 61 |
| `E3-S1-03` | S1 | 只读 API 的数值一致性验收（与 DB 直查误差=0） | 72 |
| `E3-S1-04` | S1 | 计时基线单：结论（是否可行）先于数字好看 | 57 |
| `E3-S2-01` | S2 | 原子批入库 vs 降级循环：同一判据下的前后 A/B | 77 |
| `E3-S2-02` | S2 | 词表外置：硬编码 15 条→数据文件 + 缺文件恒等降级 | 65 |
| `E3-S3-01` | S3 | 报告图管线重写为 SQL-only + 每图 sha256 入 provenance（结构性防造假） | 77 |
| `E3-S3-02` | S3 | S×E 成果评价机：无理由必拒 / auto_scan 封顶 / 禁加权排名 | 71 |
| `E3-S4-01` | S4 | （空缺）突破级判例：本轮无满足者 | 55 |

## 5. 用法

```bash
# 预演（不写盘）
python3 -m tools.precedent_library.library inject --target <BOOTSTRAP.md>
# 落地（append-only + 幂等，可反复执行）
python3 -m tools.precedent_library.library inject --target <BOOTSTRAP.md> --apply
```

## 6. 局限

- 注入只保证"开工前看见判据与反例"；它不能替代独立评审（S4 仍空缺即为此故）。
- 目标文件在仓库之外（agent 运行时文件），本仓只提交渲染块与回执；跨环境部署需再执行一次
  `--apply`（幂等，重复执行安全）。
