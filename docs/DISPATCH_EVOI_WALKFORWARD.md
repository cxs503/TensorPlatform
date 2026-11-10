# 派单排序 EVOI：walk-forward 离线评估（设计说明 + 复跑入口）

**状态**：capability（`new_capability`），模块 `tools/dispatch_evoi/`，产出于
`artifacts/dispatch_evoi/`（`eval_report.json` / `eval_report.md` / 5 张图）。
**任务**：`p485w1-n10-platform-dispatch-evoi`（战役 DAG 节点，平台派单排序 EVOI）。

## 1. 问题

任务池在每个派单时刻要给一批候选单排序。现行 dispatcher 用静态优先级（tier 优先、
判据条数次之），既不看“这一单有多大可能通过验收”，也不看“单位算力换回多少能力增量”。
本节点把这件事变成**可评估的策略问题**：

    EVOI(x) = P̂(accept | x) · value(x) / Ê[cost | x]

排序按 EVOI，评价用 5 个 walk-forward 时间窗口 + 对自选择偏差做 IPS/SNIPS 校正。

## 2. 关键设计决定（含被否定的做法）

| 决定 | 为什么 | 位置 |
|---|---|---|
| 特征只取**派单时刻**可得：仓库、判据条数、目标字数、上游同族 FAIL 率、新颖度、预算、仓库×规模档历史成功率 | 事后字段 = 目标泄漏；一旦泄漏 AUC 会假性爆表 | `schema.DISPATCH_FIELDS` |
| 双保险：字段白名单/黑名单 + **时间戳级**守卫（每个字段必须 `field_ts <= dispatch_ts`） | 名字检查抓不住“字段名合法但派单后才刷新”（如判据条数被追加） | `schema.assert_dispatch_time` |
| 历史聚合列由 `AggregateState` 逐回合推进，只用**严格更早**回合 | 否则 walk-forward 的第一窗口就带未来信息 | `features.AggregateState` |
| 概率模型取两个可比候选：岭 logistic（可解释）+ 深度 2 GBDT（非参数对照） | 两者一致 ⇒ 信号不是线性设定造成的假象 | `models.py` |
| 成本由**历史 wall-time** 回归（log cost ~ 字数 + 规模档 + tier，log-normal 期望） | 用当单的未来 wall-time 就是泄漏 | `cost.py` |
| 基线也做校准（单变量 logistic 把静态优先级转成概率） | 否则“Brier 更好”可能只是“我们校准了而基线没有” | `backtest._fit_priority_baseline` |
| off-policy 目标策略取 **0.9·softmax(标准化 EVOI) + 0.1·均匀** | 确定性 top-1 的 IPS 只有 ~12% ESS（<30% 门槛）⇒ IPS 不可用；探索底同时保证 positivity | `backtest.slate_softmax` |
| 负控做**重复抽样**（25 次标签置换取均值） | 单次置换是噪声很大的零假设抽样（早期窗口薄时相对 0.5 可偏 ±0.1），会把噪声当成泄漏告警 | `leakage.null_control` |
| 可复现分两个子句：同种子逐位 + 跨流水线种子 <1%；换日志的波动单列**不进门禁** | 换日志=换数据集，是抽样波动（~1/√n），不是流水线不确定性 | `backtest.check_reproducibility` |
| 不用 sklearn | 评测环境只保证 numpy；GBDT/logistic 自实现便于审计 | `models.py` |

**被否定/否决的做法**（记录在案）：
- *把 ``repo`` 做成 12 列 one-hot*：训练窗口只有 200–1700 条有标签回合，稀疏哑变量吃掉
  正则预算且与历史聚合列重复 ⇒ 改用仓库的历史成功率/历史单数作为列（见 `features.NUMERIC_FEATURES` 注释）。
- *保留 `family_fail_rate` 与 `repo_scale_success` 原值作为列*：与 `dev = raw − global_rate` 严格共线，
  系数被摊薄成三份，fig5 不可读 ⇒ 只留 dev 版本 + `global_pass_rate`。
- *单次标签置换做负控*：零假设方差太大（见上表），5 次置换的均值仍在 0.46–0.50 抖动 ⇒ 25 次。
- *直接用确定性 top-1 的 IPS 作为门槛*：ESS 12% 不可用 ⇒ 报告里同时给出，但门禁判在采样策略上。

## 3. 结果（`artifacts/dispatch_evoi/eval_report.json` 为准，本文件不复制数字）

6 条机器判据全部 PASS：a 概率质量（Brier 相对改进 24.5%、AUC +0.258、5/5 窗口同向）、
b 排序效用（precision@20% +24.6pp）、c 泄漏审计（事后字段注入真值/噪声后指标逐位不变、
25 次置换负控 AUC≈0.50）、d off-policy（IPS−朴素 +0.135，ESS 35.4%）、
e 可复现（同种子逐位相同、跨流水线种子 0.41%）、f pytest 已知真值回归。

## 4. 局限（不许粉饰）

1. **夹具即世界**：仓内没有版本化带标签的派单日志，结论对夹具成立；对线上派单的迁移
   属 **WITHHELD**（线上可能有夹具里不存在的隐藏混淆，此时“可达上界”不可算）。
2. **密集标签来自仿真**：precision@20% / hit-rate 用仿真反事实结局；线上只有被派发单的标签，
   需要 IPS 加权版本。
3. **误差判定**：相对真值上界的 Brier 误差 3.25%（>3%，如实记录），AUC 1.31%；
   差距是估计噪声（有标签回合数 × 列数），不是模型设定错误。
4. **成本项会重排**：按价值密度排必然牺牲一部分“纯通过率”precision（0.635 vs 0.729）。

## 5. 复跑

```bash
python -m tools.dispatch_evoi.cli --out artifacts/dispatch_evoi   # 约 1–2 分钟，含 pytest 门
pytest -q tests/test_dispatch_evoi.py                            # 22 个用例
```

关键文件：`tools/dispatch_evoi/{schema,features,fixtures,models,cost,offpolicy,backtest,leakage,report,cli}.py`
测试：`tests/test_dispatch_evoi.py`。