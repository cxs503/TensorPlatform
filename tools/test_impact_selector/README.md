# test_impact_selector —— 变更影响驱动的测试子集选择（平台标准能力）

把"改了哪些文件 → 该跑哪些测试"变成可审计的机器决策：AST 依赖图 + 字面引用边 + 历史共现边
求**反向闭包**，配 12 类 **fail-closed**（不可静态推理即退回全量）与只追加台账。
零领域语义：本包只谈导入图、路径、测试收集与记账。

## 用法

```bash
# 建图（冷 ~20 s；热 ~1.5 s，图缓存 + 单文件扫描缓存）
python -m tools.test_impact_selector.cli build-graph --out artifacts/test_impact_selector/import_graph.json

# 决策（shadow = 仍跑全量、台账记"本会跑什么"；enforcing = 只跑子集）
python -m tools.test_impact_selector.cli select --staged --mode shadow \
    --ledger runs/test_impact_selector/ledger.jsonl

# 证明：fail-closed 矩阵 / 故障注入重放 / 历史重放 / 灰度台账
python -m tools.test_impact_selector.cli matrix
python -m tools.test_impact_selector.cli injections --slice tools/failure_minimizer tools/dispatch_contract \
    tools/test_impact_selector --modules 15 --module-range 0:3
python -m tools.test_impact_selector.cli aggregate-injections --inputs inj_c0.json inj_c1.json --out injection_proof.json
python -m tools.test_impact_selector.cli shadow-rollout --commits 240 --ledger runs/test_impact_selector/ledger.jsonl
python -m tools.test_impact_selector.acceptance --replay-commits 60   # 全部判据数字 → acceptance.json
python -m tools.test_impact_selector.make_figures                     # 4 张 png
```

Python API：

```python
from pathlib import Path
from tools.test_impact_selector import ImportGraph, ImpactSelector, SelectorConfig

graph = ImportGraph.build(Path("."), cache_path=".cache/test_impact_selector/scans.json", jobs=8)
sel = ImpactSelector(graph, SelectorConfig(mode="enforcing"), root=Path("."))
s = sel.select(["tools/failure_minimizer/ddmin.py"])
s.mode, s.trigger, len(s.tests), s.ratio   # 'subset', None, 4, 0.0046
sel.effective_plan(s)                       # 灰度：shadow 模式下 executed == 'full'
```

## 设计要点

| 模块 | 作用 |
|---|---|
| `graph.py` | 文件级导入图：相对导入、`__init__` 再导出、`try/except` 条件导入、子模块导入；**字面引用边**（subprocess/`python -m` 式使用）；**历史共现边**；动态导入分级（常量实参→真边，非常量→`dynamic`）；解析失败/跨仓引用记账 |
| `selector.py` | 反向闭包 → 测试文件 + 机减安全集（依赖闭包最宽的 K 个测试兜底）；12 类 fail-closed；比例上限；`shadow`/`enforcing`/`off` 灰度 |
| `ledger.py` | 只追加 JSONL；每行自带 changed 哈希、图指纹、配置哈希、选择哈希、耗时与证据计数；`reproduce(run_id)` 由台账独立复算 |
| `matrix.py` | 合成树（alpha/beta/gamma）上的 12 类 fail-closed 场景矩阵 + 两个对照 |
| `proof_injection.py` | 真实仓库真实模块注入真实缺陷 → 真跑全量取真值 → 跑子集比对；注入前脏树预检、注后内容 sha256 校验还原、`TMPDIR` 内嵌避免共享 `/tmp` 写满 |
| `replay.py` | 60 个真实 commit 重放；只把**非平凡义务**（改动测试 / 字面引用测试 / 历史共现测试）算召回 |
| `acceptance.py` | 由上述产物计算 C1–C7 判据，输出 `acceptance.json`（零手抄） |
| `make_figures.py` | 直读产物出图 |

## 边界与已知局限

* 冷构建 20 s / 热构建 1.5 s 是**固定开销**：适合"一条流水线建一次图、多次决策"，不适合单次改动场景。
* 非常量实参的动态导入（188 个模块）与新增文件一律 fail-closed；这是**有意保守**，也是全量率的主因。
* 故障注入的真值面限于能绿跑的干净切片（99 测试）；切片外只有静态检查。
* 解析失败的文件只在"被 import"时才致命（仓库内现有 2 个语法错误文件为孤立叶子，已记账）。

详细报告见 `docs/test_subset_selection_maturity.md`。