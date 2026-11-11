# 判据度量学（criteria metrology）

平台共性机制，**零领域语义**：本目录不认识任何物理量名，所有领域数值经工件
（报告表格 / 语料快照 / 案例 JSON）注入。

## 治什么病

平台的每次「验收通过」都挂在一条判据上，而判据历史上被当成「相对误差 < τ」的同义词。
三类情形让这条尺子失效，且失效是**静默**的（门照旧变绿）：

| 类型 | 结构特征 | 后果 |
|---|---|---|
| T1 基准量级趋零 | `\|y_ref\| → 0`（含恰为 0 的「残差 vs 0」） | 分母趋零，除零 → inf/NaN，判决不可复算 |
| T2 跨零/变号 | `y·y_ref < 0`、`\|y\| ≪ scale`、或相对误差 >100% | 数值随符号漂移不连续，>100% 时量的不是误差 |
| T3 多口径并列 | 基准是区间，或并列 relative/absolute 两套口径而无优先级 | 没有单值基准 ⇒ 没有单值相对误差 |

另外两个病：**新颖性主张无凭证**（空检索/过期检索/走样检索无法区分）、
**能力台账一维化**（把「有代码」当「有证据」，或把「不支持」藏成「未评估」）。

## 五件套

| 模块 | 做什么 |
|---|---|
| `model.py` | 四元组 `(y, y_ref, τ, 口径+单位+工况)`；**永不返回 inf/NaN** 的安全标量函数；`None` 一律传播成 UNDECIDABLE |
| `parse.py` / `harvest.py` | 从 markdown 误差表抽四元组：数值 token 化（`1e-10` 不是区间）、日期屏蔽、散文单元格不取数、τ 从列头读；逐行溯源 + 指纹 + 忠实度分层 + 自洽门 |
| `degeneracy.py` | T3 → T1 → T2 分类，带触发数值证据；策略参数外置 |
| `adaptive.py` | 自适应判据族 A1…A5 + 严格优先合并；零基准必须声明 scale，否则 UNDECIDABLE（不放行也不误判） |
| `rejudge.py` | 回放重判：退化类 + 朴素判决 + 替代判据数值 + 翻转/冲突标记 + `recompute` 段（算式与代入值） |
| `retrieval.py` / `build_receipts.py` | 检索凭证重放门（MATCH/DRIFT/UNREPLAYABLE；新颖性/覆盖主张判决）+ 负空间图谱 |
| `triad.py` | capability/delivery/evidence 三元台账 + 三条硬约束（REAL 需强证据、NOT_SUPPORTED 必须显式且裸、digest 校验） |
| `pipeline.py` / `__main__.py` | 一条命令跑全流水线，落 `artifacts/criteria_metrology/*` |
| `figures.py` / `report_doc.py` | 出图 + 从工件生成论文级 markdown（数字零手抄） |

## 用法

```bash
python3 -m tools.criteria_metrology corpus      # 语料快照（离线、确定性）
python3 -m tools.criteria_metrology all         # 全流水线 + 出图
python3 -m tools.criteria_metrology check       # 只读校验，不过则非零退出（可进 CI）
python3 -m tools.criteria_metrology cases       # 四个历史案例的再判定
python3 -m tools.criteria_metrology invariants  # 不变量属性测试（≥1000 样本）
```

## 数据与配置

* `data/criteria_metrology/corpus.json` —— 语料快照（逐行 `row_sha256`、`faithfulness` 分级）
* `data/criteria_metrology/controls.json` —— 12 退化 + 12 良性负控
* `data/criteria_metrology/cases_m7241_m7248.json` —— 四个历史案例（数值逐字来自报告）
* `data/criteria_metrology/labels_40.json` —— 40 条人工标注（rubric + 局限声明）
* `data/criteria_metrology/capability_triad.json` —— 能力三元台账（含 NOT_SUPPORTED）
* `data/criteria_metrology/retrieval_{receipts,claims,regions}.json` + `*_pool_snapshot.json`
* `config/criteria_metrology/policy.json` —— 退化判定策略

## 纪律

* **不返回无界值**：任何路径下 `inf/nan` 都会被拦下并转成 UNDECIDABLE。
* **不放行**：合并规则任一 FAIL 即 FAIL、任一 UNDECIDABLE 即 UNDECIDABLE；不允许「取最好的一条」。
* **不静默**：抽取拒收、重放不可用、证据不足都进报告，不降级成「通过」。
* **不改被审计对象**：流水线只读仓内文件，只写 `--out` 与 `--data-out`。

详见 `docs/P266W3-N10_criteria_metrology.md`。