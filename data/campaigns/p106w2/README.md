# p106w2 数据包（战役 plan 499 · 跨求解器互裁集成门 n11）

域内容全部在本目录，机制代码（`backend/services/xsolver/interop`、`tools/xsolver_interop`）零域语义。

| 文件 | 作用 |
|---|---|
| `nodes.json` | 战役 11 节点表（平台 `/tensor/campaigns/plans/499/nodes` 机器抽取） |
| `sources.json` | 兄弟节点工件来源声明（worktree / 仓 / 期望快照路径 / 缺席原因） |
| `raw/` | 兄弟节点工件快照（sha256 钉在 `interop_manifest.json` 里） |
| `extract_spec.json` | 从 markdown 结论/表格机器抽取数字的模式（匹配失败=摄取失败，fail-closed） |
| `provenance.json` | 摄取台账：来源 worktree、git commit、源/快照 sha256（由 `ingest` 生成） |
| `interop_manifest.json` | **门清单**：四个验收项、阈值、共享参数、接口契约、缺件说明（sha256 钉住工件） |
| `domain_vocab.json` | 域词表（pytest 反查机制代码，出现即红） |
| `doc_prose.json` | 报告散文（不含数值；数值由 docgen 从 verdict.json 注入） |
| `runners/figures.py` | 战役图（域叙事；由 `run_gate --figures <runner>` 挂载） |

复现：

```bash
python3 -m tools.xsolver_interop.ingest --campaign p106w2 --data data/campaigns/p106w2
python3 -m tools.xsolver_interop.run_gate --manifest data/campaigns/p106w2/interop_manifest.json \
  --data-dir data/campaigns/p106w2 --out artifacts/p106w2_interop \
  --figures data/campaigns/p106w2/runners/figures.py
python3 -m tools.xsolver_interop.docgen --verdict artifacts/p106w2_interop/verdict.json \
  --data data/campaigns/p106w2 --prose data/campaigns/p106w2/doc_prose.json \
  --figures artifacts/p106w2_interop/figures --figures-rel ../artifacts/p106w2_interop/figures \
  --pytest-summary artifacts/p106w2_interop/pytest_summary.txt \
  --out docs/P106W2-N11_INTEGRATION_GATE.md
```

`run_gate` 退出码：门 PASS=0 / FAIL 或 WITHHELD=1 / 清单违约=2（fail-closed，绝不把失败跑成 0）。