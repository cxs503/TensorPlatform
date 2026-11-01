# mission 1547 — 耐波性三端点 D5 判决修复（runbook）

| 文件 | 作用 |
| --- | --- |
| `backend/api/seakeeping_physics.py` | 物理内核：20 站 STF 切片理论、遭遇频率、sinβ 横摇激励、自洽自然周期 |
| `backend/api/seakeeping_physical_api.py` | 三端点路由（`/seakeeping/rao`、`/seakeeping/motion`、`/seakeeping-advanced/strip-theory`），schema 与历史端点一致 |
| `backend/api/seakeeping_physics_app.py` | 隔离 FastAPI 应用（`/api/v1` 前缀），供机器裁决与 TestClient |
| `tests/test_seakeeping_d5_fix.py` | 验收单测 A1~A6 + 物理不变量（11 用例） |
| `tests/seakeeping_audit/audit_seakeeping.py` | 11 条解析判据的机器裁决脚本（来源 mission 1515，含 3 处口径修正，见脚本头部 P1~P3） |
| `tests/seakeeping_audit/gen_fix_figures.py` | 图表 + `artifacts/m1547/results_table.json`（数字全程序化注入） |
| `tests/seakeeping_audit/gen_fix_report.py` | 生成本目录报告 Markdown 与 `/tmp/report.json` 提交载荷 |
| `artifacts/m1547/seakeeping_audit/` | probes.json / verdict.json / 判据图（在线探针原始响应与裁决） |
| `artifacts/m1547/figures/` | 报告图 1~4 |

复跑（≈10 s）：

```bash
# 1) 起隔离服务（与线上 :8001 同构路由，端口 8021）
mkdir -p /tmp/seakeeping_surface && cd /tmp/seakeeping_surface
for f in seakeeping_physics seakeeping_physical_api seakeeping_physics_app; do
  ln -sf $OLDPWD/backend/api/$f.py $f.py; done
python3 -m uvicorn seakeeping_physics_app:app --host 127.0.0.1 --port 8021 &

# 2) 机器裁决（须 exit 0 / 11 PASS）
python3 tests/seakeeping_audit/audit_seakeeping.py \
  --base http://127.0.0.1:8021 --out artifacts/m1547/seakeeping_audit

# 3) 单测与图表
python3 -m pytest tests/test_seakeeping_d5_fix.py -q
python3 tests/seakeeping_audit/gen_fix_figures.py
```

**落地到线上**：线上 :8001 由 `ship-performance-platform` 仓库的 master 检出提供服务，
不在本工作树范围内。把 `seakeeping_physics.py` 拷入该仓库 `backend/api/`，并让
`api/seakeeping.py` 的 `/rao`、`/motion` 与 `api/seakeeping_advanced.py` 的
`/strip-theory` 改为调用 `seakeeping_physical_api` 的同名处理函数即可（请求/响应 schema 未变）。
