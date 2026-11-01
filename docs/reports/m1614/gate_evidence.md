# m1614 门禁证据（命令 → 输出，零手抄）

执行环境：`/srv/tensor/agent/work/m1614`（branch `agent/m1614`，基线 `HEAD=6e24298`）。
本机无 `.venv`，用系统 `python3`（pytest 9.2.0 / fastapi 0.136.1 / pydantic 2.13.3）。

## 1. 正典门禁（任务书指定命令，全绿）

```
$ TORCH_DEVICE_BACKEND_AUTOLOAD=0 PYTHONPATH=backend python3 -m pytest \
    tests/test_benchmark_canon.py backend/tests/test_benchmark_integrity.py \
    tests/test_m1564_stability_canon.py --noconftest -q
.........................................................                [100%]
（57 passed）
```

## 2. 收编/新增测试（全绿）

```
$ TORCH_DEVICE_BACKEND_AUTOLOAD=0 PYTHONPATH=backend python3 -m pytest \
    backend/tests/feat/test_benchmark_chain_m1388.py \
    backend/tests/feat/test_ship_benchmark.py \
    backend/tests/test_benchmark_audit_endpoint.py \
    backend/tests/test_baseline_integrity.py \
    backend/tests/test_benchmark_reference_integrity.py -q
........................................................................ [ 91%]
.......                                                                  [100%]
（79 passed：17 + 25 + 9 + 10 + 18）
```

## 3. 双套件签名对照（零新增红）

基线跑法：`git worktree add /tmp/m1614_base HEAD`（HEAD=6e24298，即 main 正典），
两棵树用同一条命令、同一解释器：

```
$ cd <tree> && TORCH_DEVICE_BACKEND_AUTOLOAD=0 PYTHONPATH=backend python3 -m pytest \
    backend/tests tests -q -p no:cacheprovider --continue-on-collection-errors --tb=no -rf -n 4
```

| 指标 | 基线（/tmp/m1614_base） | 本分支（/srv/tensor/agent/work/m1614） | 差值 |
|---|---|---|---|
| FAILED/ERROR 测试 ID 数 | 157 | 157 | **0** |
| 新增红（head ∖ base） | — | — | **0 条** |
| 消失红（base ∖ head） | — | — | 0 条 |

失败集完全相同的 157 条为**环境性**既有红（缺 `psycopg2`/`pgserver`/`pytest-asyncio`
插件、SQLite 夹具冲突、`file::memory:` 数据库竞态），与本次收编无关：基线同样红。

## 4. AST 解析（全部变更 .py 均可解析）

```
$ for f in $(git status --porcelain | awk '{print $2}' | grep '\.py$'); do
    python3 -c "import ast;ast.parse(open('$f').read())"; done
（无输出 = 全部通过）
```

## 5. 工件生成命令（报告数字来源）

| 工件 | 生成命令 |
|---|---|
| `runs/m1614/benchmark_audit.json` | `python3 scripts/benchmark_audit.py --api http://127.0.0.1:8001/api/v1` |
| `artifacts/m1388/*` | `cd backend && ENVIRONMENT=test DATABASE_URL=sqlite:///:memory: python3 ../scripts/audit_benchmark_chain_m1388.py` |
| `artifacts/benchmark_integrity/*` | `python3 tools/audit/benchmark_reference_integrity.py` + `python3 tools/audit/make_benchmark_integrity_figures.py` |
| `artifacts/baseline_integrity/audit_canon_v3.*` | `python3 tools/audit/baseline_integrity.py --dataset backend/data/benchmark_reference.json --json … --markdown …` |
| `docs/reports/m1614/fig*.png` | `python3 scripts/m1614_integration_figures.py` |
| `docs/benchmarks/benchmark_data_integrity.md` | `python3 scripts/gen_benchmark_doc.py` |
