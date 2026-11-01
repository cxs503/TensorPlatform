# m1396 — refs corpus / verifier 体系（归档）

**分支**：`agent/m1396`（提交 fad48e7）
**交付**：`backend/services/benchmark_integrity.py`、`tools/refs/{corpus.json,schema.py,build.py,audit.py}`、
`tests/test_benchmark_integrity.py`、`tests/test_refs_corpus.py`、`docs/verifier/REFS_CORPUS_V1.md`、
`docs/verifier/refs_debt_ledger.md`。

**数值主张**：未改数值（沿用 12.5 / 8.2 / 550000 等记录值），主打"把验证欠账变成可判"的
语料库 + 债务台账方法论。

**裁决**：**方法论采纳，数值与实现不采纳。**
- 采纳：`docs/verifier/refs_debt_ledger.md` 的"欠账台账"思想 → 正典用
  `rt_status=WITHHELD` + `audit_flags` + `adjudication_reason` 落成机器可判字段。
- 不采纳：语料库不解决"记录值本身与恒等式冲突"的问题；把 12.5 N 之类的错值登记为"语料"
  反而给了它出处感。数值必须由 `Rt = Ct·½ρV²S` 复算，不能只做引用管理。
- 不采纳：`benchmark_integrity` 在 m1396 是新增文件，但检查族少于基线版（后者已有 C1–C5 与
  严重度梯），合并会造成第二套闸门。

**现状**：语料库与台账留在分支；台账字段思路进正典。
