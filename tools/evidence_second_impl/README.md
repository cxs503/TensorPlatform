# evidence_second_impl — 第二实现成熟度层（证据独立性 + 变形关系门 + 注入标定）

平台证据等级引擎（`tools/evidence_grade.py`）判 REAL 只看**契约结构**；本包补上"**证据必须独立于被测代码**"
与"**判据必须可判别**"两根轴，使 REAL 具备抗伪造含义。

| 模块 | 职责 |
|---|---|
| `model.py` | 冻结数据模型：参考来源类型、独立性判定、注入类、关系类、句柄/关系规格 |
| `independence.py` | 声明重读校验（path+line+行哈希）→ 重判（自指/定位器失效 → WITHHELD；自指但有独立佐证 → 保留并标记） |
| `metamorphic.py` | 6 类变形关系注册表（equivalence / scaling / zero_point / additivity / mirror_parity / conservation）+ 每类否定控制变异体（防"空转关系"） |
| `injection.py` | 注入测试台：×1.05 / 常数偏移 / 符号翻转，检出率与误报率双向标定 |
| `handles.py` | 合成句柄群体（3 target + 20 control），每句柄两份独立实现（闭式 vs 独立路径，9 位有效数字舍入） |
| `ledger.py` | SQLite 写回（备份 + 回滚演练 + 计时）与全部 SQL 查询（报告数字只从这里来） |
| `build_declarations.py` | 把声明 needle 解析成行号并钉扎哈希（不命中/多命中即失败） |
| `runner.py` | 端到端流水线 + 6 条机器验收判据 → `acceptance.json` |
| `figures.py` | 三张图（检出矩阵 / 门限分离 / 重判分布） |

**领域中立**：本目录不出现任何领域词条（`tools/evidence_domain_scan.py` 常设扫描，0 命中）；
能力 id、探针、容差、证据行全部经 `artifacts/evidence_second_impl/*.json` 注入。

## 用法

```bash
python -m tools.evidence_second_impl.runner        # 端到端（写 artifacts/evidence_second_impl/）
python -m tools.evidence_second_impl.figures       # 图
python -m pytest tests/test_evidence_second_impl.py -q
```

产物：`run_report.json`（全量）、`acceptance.json`（6 条判据）、`bench_manifest.json`（句柄群体）、
`capability_declarations.json`（钉扎后的声明）、`evidence_second_impl.sqlite`（台账，含备份 `.bak`）。

## 核心结论（见 `docs/m9793/REPORT.md`）

* 注入检出 9/9 = 1.00（每类 3/3）；未改动 20 句柄误报 0；否定控制 87/87 可判别；台账写回 0.008 s、回滚演练 verified。
* 重判：REAL 5 → 4，唯一降级为同代码自指的能量账本（证据行 `docs/m7924/INTEGRATION_REPORT.md:60`）。
* **结构性发现**：齐次/可加/对称/零输入等**自指相对关系对整体倍乘与符号翻转在数学上失明**（实测 0 检出），
  因此 REAL 证据至少要含一条**绝对锚**（第二实现一致性或守恒总量）。