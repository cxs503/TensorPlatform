# m9356 · p216w3-n09 跨架构基准复现门成熟化 —— 蒸馏

## 交付
- 模块：`tools/cross_arch_gate/`（record / classify / gate / selftest / audit / criteria / pipeline /
  numerics / fixtures / figures / report_md / cli + `default_policy.json` + `README.md`）
- 用例：`tests/test_cross_arch_gate.py`（19 passed；逐条钉住六项判据）
- 产物：`artifacts/cross_arch_gate/`（`arms/` 逐臂记录、`pairs/` 逐字段判决、`mutation_matrix.json`、
  `domain_audit.json`、`criteria.json`、`figures/`、`external/` 外部架构臂）
- 论文级报告：`docs/reports/p216w3_n09_cross_arch_gate.md`（程序化注入，零手抄）；
  平台报告 id **11033**（权威版；11029 为同内容早期版，②节变异族计数未动态化，已被 11033 替代）
- 发布脚本：`scripts/publish_p216w3_n09_report.py`；CI 臂：`.github/workflows/cross-arch-gate-x86-arm.yml`
- 复现：`python3 -m tools.cross_arch_gate.cli pipeline --outdir artifacts/cross_arch_gate --report-md docs/reports/p216w3_n09_cross_arch_gate.md`

## 判据实测（criteria.json）
整数逐位一致率 1.0 · 双精度容差带内占比 1.0 · 超容差检出率 1.0（156/156 注入字段）·
误报率 0.0（0/2364 良性字段）· NaN 检出率 1.0（36/36）· 端到端时延 ≈0.36 s（门限 300 s）·
零领域语义命中 0。`all_pass = all_pairs_pass = True`。

## TRAPS（可复用，按发现顺序）
1. **对精确 `0.0` 做相对扰动等于没做**：`0.0*(1+1e-9) == 0.0`。变异自检会显示「漏检」，
   实际是注入无效。零值字段必须用**绝对**注入（本模块 `mutate_shift` / `zero_abs_shift` 族）。
   同一坑在本单咬过两次（自检 + pipeline 负控用例），写判据自检时先查 `value != 0`。
2. **相对度量在抵消型字段上是假红机器**：O(1e6) 项抵到 1e-17，相对差是 O(1)。这类字段必须
   声明成「诊断量 + 绝对容差」，并在报告里公开字段名，不接受混进观测量总数。
3. **`nan != nan` 会把「两边都是 NaN」判成不一致**：按 bit pattern 比，并区分
   `both_nan_same_bits` / `both_nan_diff_payload` / `single_sided_nan`。
4. **`+0.0` 与 `-0.0` 的 ULP 距离是 0 但 bit 不同**：判决必须「先比 bit 再比 ULP」，否则符号零漂移被容差吞掉。
5. **统计判据 ≠ 逐对裁决**：`c2 > 95%` 是统计地板，`verdict` 是严格门（任何超容差即 FAIL）。
   两者都必须进 `all_pass`——只统计时，一个 34 字段里坏 1 个的外来臂会被放行（本单实测踩到）。
6. **`fma` 不是 `mul`+`add`**：把 FMA 收缩当「违反 IEEE」会造假红；它是另一种运算（一次舍入），只能进容差带。
7. **整数逐位一致的边界是位宽/溢出语义**：换语言或换位宽即击穿，判红不放宽。
8. **`run_cluster` 排队与判据时延口径**：300 s 判据只覆盖门自身计算路径；集群排队（本单 cj271/cj272
   递交后 6 min 仍 queued，前面 97 个作业）必须单独披露，不能算进门时延，也不能没有就假装有实测。

## 未完成（honest）
- **x86 实测臂缺席**：执行环境全为 loongarch64（控制面 + n315/n330），`192.168.215.56` 连接超时，
  且无 x86 交叉编译器 / qemu / 容器运行时。x86 轴当前由「可移植 IEEE oracle 臂」（+ − × ÷ √ fma 逐 op
  精确有理数 + 一次 half-to-even 舍入，语义等价于任意合规 binary64 实现）+ CI 工作流承担，等级=待实测。
- **集群两臂**：`cj271`（n315）、`cj272`（n330）已递交，报告落盘时仍在排队；
  取回后 `python3 -m tools.cross_arch_gate.cli pipeline --external-arm loong64-n315=<record.json>` 即可并入同一门。