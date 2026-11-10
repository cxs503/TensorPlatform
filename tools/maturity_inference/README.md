# tools/maturity_inference —— 五级成熟度推断引擎（零领域语义）

把「能力有多成熟」从**自述**改成**从证据图推出来的结论**。

```
python3 -m tools.maturity_inference.cli run                # 全流程验收（含批量重跑仓库测试）
python3 -m tools.maturity_inference.cli run --no-execute   # 不跑测试：tested 环一律不可支撑
python3 -m tools.maturity_inference.cli run --reuse-links  # 复用上一轮实测产物，只重算推断/报告
python3 -m tools.maturity_inference.cli golden             # 只跑黄金集（CI 回归门，退出码即判定）
python3 -m tools.maturity_inference.cli infer evidence.json  # 对给定证据节点跑推断
python3 -m tools.maturity_inference.cli gate synth.a.b     # 只跑命名空间门
```

产物：`artifacts/maturity_inference/`（JSON 工件 + `level_table.md` + `acceptance.json`）与
`docs/reports/p315w1_n11_maturity_inference/`（报告 + 4 张 png，均带 sha256）。

## 1. 五级阶梯（`ladder.py`）

| rank | 等级 | 该环必须绑定（缺一项即 fail-closed） |
|---|---|---|
| 0 | `not_supported` | — |
| 1 | `implemented` | 源码/端点定位 + 工件 sha256 + `provenance{producer,recorded_at,sha256}` |
| 2 | `tested` | + 重跑命令 + 执行状态 `passed` + 日志 sha256 + 执行时刻 |
| 3 | `benchmark_verified` | + 判据 `{name,value,tolerance,baseline}` 且 \|value−baseline_ref\| ≤ tolerance |
| 4 | `coupled_prototype` | + 耦合组件 ≥2 + 接口契约摘要 sha256 + 端到端重跑命令 |
| 5 | `engineering_qualified` | + 独立复核席位 + 复跑 ≥2 次且 sha256 互异且全 pass + 包线 + 签署工件 sha256 |

历史四档（`NOT_SUPPORTED/STUB/WITHHELD/MOCK/REAL`）只用于**封顶**（矛盾证据）与**判高报**，
不用于抬级；`REAL` 的入口门槛是 rank ≥ 3（`benchmark_verified`）。

## 2. 推断规则（`engine.py`）

1. `level = 支撑得起的最高环`，**逐环累积**，跳级无效；
2. **fail-closed**：某环证据缺件即在该环截断（等级停上一环，原因入 `reasons`）；
   `source.scope=fixture`（测试夹具/合成）一律封顶 `tested`；
3. **矛盾封顶**：`stance=contradicts` 且断言档位 ∈ {STUB, MOCK, WITHHELD, NOT_SUPPORTED}
   → 按 `LEGACY_CAP` 封顶（MOCK/WITHHELD → 1，STUB/NOT_SUPPORTED → 0）。

判定顺序（不可交换）：逐环支撑 → 矛盾封顶 → 自述比对（`unbacked_overclaim` / `consistent` / `understated`）。

## 3. 命名空间门（`namespace.py`）

能力键必须匹配 `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`，且不得以
`node.` / `campaign.` / `delivery.` / `tmp.` / `test.` 开头——那些是**记账行**不是能力。
拒收行不丢弃：`migrate.plan_migration` 给去向（`campaign_node_registry` 等），
`apply_migration` 幂等执行，并断言迁移后 `node.*` 残留 = 0。

## 4. 适配层（上游契约边界）

- `ledger_adapter.py`：台账 / 能力定义 / 活体快照 → 推断输入行（含矛盾档位、叙述支持证据）；
  上游字段漂移**只改这里**，引擎与黄金集不动。契约见 `INTERFACE_CONTRACT`。
- `linker.py`：仓内可执行事实装配（源码 sha → `implemented`；批量 pytest junitxml → `tested`；
  `**/bench_evidence/*.json` 自洽判据 → `benchmark_verified`）。`coupled`/`engineering` 两环
  **刻意不自动装配**——自动装配无法证明耦合与独立复核。

## 5. 踩过的坑（回归保护）

- **`/tmp` 不可靠**：共享主机上会被别的作业挤满（实测 ENOSPC 直接把跑批打死）。junit 临时目录
  一律落 `artifacts/maturity_inference/_tmp/`，解析完即删（`linker.run_pytest_batch`）。
- **pytest 混跑会塌**：仓内 `backend/pyproject.toml` 声明 `[tool.pytest.ini_options]`，若把
  `backend/tests/*` 与仓根 `tests/*` 塞进同一次调用，rootdir 会切到 `backend/`，其
  `pythonpath=[".",".."]` 让 `backend/tools`（无 `__init__`）抢在仓根 `tools/` 前 → 仓根 conftest
  插件导入失败。解法：`_config_root` 按配置根**分组**分别调用。
- **临时目录先删再解析 = 全是 `not_run`**：曾因 `rmtree` 在 `_parse_junit` 之前，19 个执行成功的
  测试全被记成未跑（假阴性）。现在顺序是「解析完再清」。
- **叙述证据不能抬级**：报告/文档里的"我们是 REAL"只能作为矛盾通道与线索；不可执行就没有
  sha/命令，按定义只能算 rank 0/1。

## 6. 回归门（CI 可挂）

```
python3 -m tools.maturity_inference.cli golden   # 黄金集宏 F1 < 0.9 → 退出码 1
python3 -m pytest -q tests/test_maturity_inference_core.py tests/test_maturity_inference_pipeline.py
```

黄金集文件内容与冻结清单 `fixtures/golden_set.sha256` 绑定：改了黄金集必须显式重跑
`python3 -m tools.maturity_inference.fixtures.build_golden` 并提交新 sha（防"改题不改答案"）。