# 参考值语料库（reference-value corpus）

文献/规范中的参考值 → 机器可读、sha256 锚定、带容差与有效性范围、可被 pytest 直接消费的资产。
本目录**随分支入库**，服务运行时不联网抓取。

## 目录布局

```
refs.schema.json       参考值条目 schema（Draft 2020-12，加载即强校验）
capability.json        平台能力位声明（证据等级 REAL / 端点 / 限制）
pending.json           已请求但按纪律未数字化的族（WITHHELD，写明理由，禁止编造）
entries/<id>.json      条目元数据（source / sha256 / tolerance+k / validity_range / provenance / dual_pass / verification）
sources/<id>.csv       数字化 pass A（闭式解机器精度）
sources/<id>.passB.csv 数字化 pass B（按印刷精度独立重打）
wave8_nodes/           Wave-8 八节点验收证据归档 + manifest.json（来源 worktree/分支/commit/sha256）
```

## 纪律（写进加载路径，不靠自觉）

1. **schema 不通过 == 不服务**：`ReferenceCorpus` 加载即用 jsonschema 校验，失败抛 `SchemaViolation`。
2. **锚点不符 == 不服务**：每次访问都重算 `sha256( artefacts )`，任一 bit 翻转即 `IntegrityError`（API 409）。
3. **范围外 == 不外插**：请求落在 `validity_range` 之外抛 `OutOfRangeError`（API 416，`code=OUT_OF_RANGE`）。
4. **双人独立数字化是硬门**：两通道逐点相对差 > 1e-3 抛 `DualPassViolation`。
5. **没有可锚定来源就不建条目**：写进 `pending.json` 记 WITHHELD，不许用记忆里的数字充数。

## 重建

```bash
python3 tools/archive_wave8_nodes.py          # 归档 Wave-8 节点证据（只读源 worktree）
python3 tools/build_reference_corpus.py       # 重建 CSV 双通道 + entries + pending + 独立数值复核
python3 tools/render_reference_corpus_report.py   # 审计 / 台账 / 重判 / 图
cd backend && python -m pytest tests/test_reference_corpus.py tests/test_reference_api.py -q
```

新增一族参考值的流程：先在 `pending.json` 里登记 → 拿到可转写的原始工件（扫描件/PDF 表格）→
写 pass A / pass B 两条独立转写 → 填 `entries/<id>.json`（含 `verification` 独立复核）→ 跑双人复核与插值一致性 →
在 `capability.json` 里更新证据等级与限制。