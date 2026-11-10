# 能力台账三源自动对账引擎（capability_reconcile）

把「能力台账自述等级」与「代码 / 测试 / 文档三源机器证据」做成可复算对账，输出：

1. **三源一致性报告**：每项能力的 code / test / doc 结构信号 + 判定理由码；
2. **过度声明检测**：`rank(derived) < rank(declared)` → `OVERCLAIM`，
   细分 `HARD`（派生为 NOT_SUPPORTED/STUB/MOCK，有正面反证）与 `SOFT`（派生为
   WITHHELD，证据不足）；
3. **等级调整建议**：方向（DOWNGRADE/HOLD/UPGRADE）+ 可执行动作码 + 动作释义；
4. **sha256 审计日志**：逐能力 sha256 链（prev/payload/record）+ 报告锚，锚剔除
   易变字段与自述区块，因此换生成时间重跑锚不变、改任一判定字段锚即变。

## 用法

```bash
# 单次对账（报告打 stdout 或 --out）
python3 -m tools.capability_reconcile.cli \
    --ledger artifacts/capability_ledger/capability_ledger.json \
    --root . \
    --contrast artifacts/capability_ledger/capability_ledger_resolved.json \
    --out artifacts/capability_reconcile/report.json

# 退出码：0=通过；1=schema 校验失败；2=存在过度声明（配合 --fail-on-overclaim）；3=输入缺失

# 显式绑定（实现挂在树外或按别的命名分发时必须用）
#   bindings.json: {"cap.<id>": {"code": ["path/a.py"], "test": ["tests/test_a.py"], "doc": ["docs/a.md"]}}
python3 -m tools.capability_reconcile.cli --ledger L --root . --bindings bindings.json --out r.json

# 包内自检（合成语料，12 项金标准）
python3 -m tools.capability_reconcile.selfcheck

# 正式跑批：真实台账 → report.json / audit_log.json / acceptance.json / 3 张图 / Markdown 报告
python3 -m tools.capability_reconcile.run_report
```

## 判定级联（默认 `policy=evidence`）

| 序 | 条件 | 派生等级 |
|---|---|---|
| 1 | 无实现文件被解析/绑定 | `NOT_SUPPORTED`（理由 `NO_SOURCE_BINDING` / `CODE_MISSING_IN_TREE`） |
| 2 | 空壳函数占比 ≥ 0.5 或有效代码 < 6 行 | `STUB` |
| 3 | 未播种随机 / 代码行硬编码标记 / 常量直返占优 | `MOCK` |
| 4 | 验证工件判失败（误差 > 15% 且有失败语义） | `WITHHELD`（只否决 REAL，不直接判 MOCK） |
| 5 | 无测试 / 占位断言 / 无数值断言 / junit 失败 | `WITHHELD` |
| 6 | 无文档 / 文档反述弱等级 / 无数值验证 | `WITHHELD` |
| 7 | 全部通过且数值误差 ≤ 3% | `REAL` |

`policy=presence` 退回「三源齐全即 REAL」的存在性口径，仅用于口径对照——存在不等于
被验证。阈值全部是 `scanners.THRESHOLDS` 常量并随报告落盘。

## 与仓内既有工具的关系（不重复造）

| 已有 | 关注点 | 与本引擎的边界 |
|---|---|---|
| `tools/audit/capability_ledger.py --check` | 台账**证据行**的对账（quote 匹配、时效） | 本引擎不用证据行，只看树内三源 |
| `tools/evidence_reconcile.py` | 自述 vs 证据合同的 `CONSISTENT/OVERCLAIM/UNVERIFIED` | 本引擎多出代码/测试/文档三源扫描、动作建议、sha256 链 |
| `tools/capability_auto_audit/` | 分类器 + provenance DB | 本引擎无 DB 依赖，产物是单文件 JSON + 审计锚 |
| `tools/audit/capability_audit_scan.py` | 量化评分 + 已验证工件数值判据 | 本引擎复用其「数值判据优先于关键词」的思路，但判定级联与建议面独立 |

## 纪律

- **零领域语义**：能力 id / 域标签 / 源路径全部由输入注入；`tests/test_capability_reconcile.py`
  对引擎源码做「真实台账 id 与域标签字面量零命中」的机器校验。
- **判定范围 = 被扫描工作树**：树外实现不可见，只能记 `NO_SOURCE_BINDING`。
- **honest FAIL 不覆盖**：真实台账上的准确率/一致率未达标时如实写进报告 `§6`。

## 单测

```bash
python3 -m pytest tests/test_capability_reconcile.py -q   # 18 passed
```