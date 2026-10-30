# m371 值班长班次工件（分析/调度，不改业务代码）

| 文件 | 作用 |
|---|---|
| `analyze.py` → `ledger.json` | 从 `/tmp/missions.json`（GET /tensor/missions?limit=50）程序化生成 mission 级/案例级判定台账 |
| `figs.py` → `fig/*.png` | 图1 判定构成 / 图2 逐案例尝试与最新判定 / 图3 API 引用的误差阶梯 vs 3% 门限 |
| `dispatch.py` → `dispatch_result.json` | POST /tensor/missions 派发 m372/m373/m374 的 goal 与响应 id |
| `docgen.py` → `../../docs/operator_shift_m371.md` + `/tmp/report.json` | 交付文档与论文级报告 payload（数字全脚本注入） |
| `sweep_detail.txt` | 40 单扫荡 mission 的原始 result 摘录（复核用） |

分类规则与解释层（含 `suboff_re1000` OVERRIDES、历史句剔除）见 `analyze.py` 注释与文档 §3/§4.1。
