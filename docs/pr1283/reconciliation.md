# PR#1283 交付路径对账（main 实况 vs 原 PR 清单）

- 生成时间 `2026-11-13T01:56:11+00:00` · 对账基准 rev `f5bbcdb2a5a7`
- 原 PR 清单路径 12 条，main 中存在 12 条；状态分布 `{"differs": 1, "identical_to_repair": 11}`
- 首版合并提交 `6d6bec219d96`，返修轮1 `6858301ea2b1`

| # | path | in_main | main sha256 | repair sha256 | 对账 |
|---|---|---|---|---|---|
| 1 | `backend/api/external_tools.py` | True | `a70f2a251590` | `a70f2a251590` | identical_to_repair |
| 2 | `backend/main.py` | True | `fd588fd3af96` | `1b23b3e03933` | differs |
| 3 | `backend/services/external_tools_service.py` | True | `f2602739bda8` | `f2602739bda8` | identical_to_repair |
| 4 | `data/external_tools/classes.json` | True | `3eebbef6c723` | `3eebbef6c723` | identical_to_repair |
| 5 | `docs/KPI_TOOLS_5_external_tools_onboarding.md` | True | `f9e93c979614` | `f9e93c979614` | identical_to_repair |
| 6 | `tests/test_external_tools.py` | True | `1955ea4bea81` | `1955ea4bea81` | identical_to_repair |
| 7 | `tools/external_tools/__init__.py` | True | `f8ca60310b2d` | `f8ca60310b2d` | identical_to_repair |
| 8 | `tools/external_tools/adapters.py` | True | `39e3a2f27572` | `39e3a2f27572` | identical_to_repair |
| 9 | `tools/external_tools/cli.py` | True | `8de1d27bbece` | `8de1d27bbece` | identical_to_repair |
| 10 | `tools/external_tools/harness.py` | True | `9418a42fb062` | `9418a42fb062` | identical_to_repair |
| 11 | `tools/external_tools/probe.py` | True | `4b302f506426` | `4b302f506426` | identical_to_repair |
| 12 | `tools/external_tools/report.py` | True | `97a004a633b1` | `97a004a633b1` | identical_to_repair |

判读：`identical_to_repair` = main 内容与返修轮一字不差（重开单无需重写）；`differs` = 合并后又被后续提交演进（需按新状态复核，不得回退）；`missing` = 真缺口。
