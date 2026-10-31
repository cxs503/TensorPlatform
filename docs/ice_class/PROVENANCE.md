# Ice-Class 优化工件溯源（返工单 mission 774）

本目录承载 **报告 700 的返工重发**（工作单 mission 774，原工作单 mission 767 / 战役节点 W2-N08）。

## 工件来源

`runs/ice_class/` 与 `artifacts/ice_class/*.png` 是从原工作单工作树
(`work/m767`, run 时间 2026-10-31T08:29) **逐字节拷贝**而来的原始 run 产出，
未做任何改写。MD5 对拍一致：

| 文件 | MD5 |
|---|---|
| runs/ice_class/pareto_front.csv | 17cdca10f98973381d4df1fc83a06a48 |
| runs/ice_class/optimization_result.json | 45ce291dfe584552ba5e83c21d2b090d |
| runs/ice_class/convergence_history.json | f2f8f9c9deadc89a8bcf8330d9ec3f0a |

拷贝目的：让报告在本工作树内可被独立复算（报告的全部数字都从这三个文件读出）。

## 本轮改动了什么

只改报告层，**未改任何业务代码**（`optimizer/`, `backend/` 无改动）：

| 文件 | 性质 |
|---|---|
| `docs/ice_class/build_report_m774.py` | 报告生成器（读工件 → 算统计量 → 渲染 md + metrics json） |
| `docs/ice_class/report_m774.md` | 由生成器产出的报告正文（返工重发版，取代 report id=700） |
| `docs/ice_class/metrics_m774.json` | 生成器算出的全部数值中转产物（机器可 diff） |
| `docs/ice_class/PROVENANCE.md` | 本文件 |
| `artifacts/ice_class/objective_ranges_check.png` | 本版新增图：CSV 排序散点 + min/max + 原报告错误区间 |
| `tests/test_ice_class_report_consistency.py` | 护栏：从 CSV 重算并断言报告数字与工件一致 |

## 重新生成

```bash
# 冻结目标模块位置（只读导入，用于 §8 自洽性校核）
export ICECLASS_CODE_DIR=/srv/tensor/agent/work/m767/optimizer/ice_class
python3 docs/ice_class/build_report_m774.py            # 写 md + metrics json + 图
python3 -m pytest tests/test_ice_class_report_consistency.py -q
```

生成器对「工件缺失 / CSV 为空 / 判据不满足 / 前沿行数与 n_solutions 不符」**fail-closed**：
任一不满足即退出码 2，不写报告。

## 已知未闭合项（如实记录）

1. **单种子**：只有 seed=42 的 run 工件，未做多随机种子研究 → 报告不得出现「多种子」声明。
2. **前沿含 1 个退化解**（CSV L41 被 L33 支配，两解仅 L 不同、GM 解析等价）：属前沿后处理去重缺陷，
   本轮返工仅授权改报告，故只披露不修改。
3. **无实验基准**：R_ice / R_ow 的模型误差无法判定 <3%（详见报告 §8）。