# tools/capability_discovery — 求解器能力自动发现与注册

让测试自己声明能力，平台自动扫描 → 定级 → 登记 → 出差异报告 → 灌进
`tools/capability_upgrade` 自动升级管线。零领域语义：能力身份只来自被扫描测试源码的
`@pytest.mark.capability(...)` 元数据。

完整方法、判据对照、局限与误差判定见 **`docs/capability_discovery.md`**。

## 30 秒上手

```python
# 1. 求解器仓的测试里声明能力（零领域语义的写法）
@pytest.mark.capability(
    id="solver.quadrature.trapezoid",
    level="REAL",                            # 声明等级；不声明则最高只能到 WITHHELD
    anchors=["ref:closed_form:quadratic"],   # 验证锚：REAL 的必要条件
    params={"n_samples": {"min": 32, "max": 4096}},
)
def test_trapezoid_matches_closed_form(): ...

# 2. 跑测试并采集结局（passed/failed/skipped/xfailed 会被区分）
pytest -q -p tools.capability_discovery.plugin \
    --capability-outcomes artifacts/capability_discovery/outcomes.json tests/

# 3. 扫描 + 定级 + 登记 + 差异报告
PYTHONPATH=. python -m tools.capability_discovery.cli register \
    --roots tests/ --outcomes artifacts/capability_discovery/outcomes.json \
    --ledger artifacts/capability_discovery/ledger.json --write

# 4. 回滚（必须给原因）/ 校验哈希链 / 导出到升级管线
PYTHONPATH=. python -m tools.capability_discovery.cli retract --ledger ... --capability X --reason "..."
PYTHONPATH=. python -m tools.capability_discovery.cli verify  --ledger ...
PYTHONPATH=. python -m tools.capability_discovery.cli export-upgrade --ledger ... --validate
```

约定与保证：

- **保守定级**：只有 `passed` 能到达测试声明的等级；无锚点、未声明、跳过、预期失败、
  失败、未运行一律 ≤ `WITHHELD`（`failed`/`error` 判 `MOCK`）。策略在
  `config/capability_discovery/policy.json`（数据，不是代码分支）。
- **幂等**：同输入重复运行台账字节不变（不刷时间戳、不追加链记录）。
- **可审计**：每次写操作进哈希链（`verify_chain()` 能抓出手改）；等级历史与撤回原因全部留痕。
- **回滚**：`retract`/`restore` 必须带原因；自动登记不会覆盖人工撤回。