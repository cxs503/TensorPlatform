# CMA-ES 优化内核参考复核（m1866）

**结论：`optimizer/core/cmaes.py` 判 **REAL**，8 个解析基准 7 项合格、Rastrigin(多模态) 80% 成功率（如实声明局限）；不存在"自洽测试冒充验证"的问题，此前是**零测试、零参考对标**。**

| 项 | 值 |
|---|---|
| 被复核对象 | `optimizer/core/cmaes.py`（267 行，Hansen 2016 教程式 CMA-ES） |
| 复核工具 | `optimizer/verify/cmaes_reference.py`（判据 C1–C7）、`optimizer/verify/cmaes_study.py`（BBOB/COCO 式多种子统计） |
| 参考基准 | 闭式最优（球/椭球/雪茄/旋转椭球/Rosenbrock/Rastrigin/平移球），Hansen 2016 arXiv:1604.00772；统计口径 Hansen et al. 2021 COCO |
| 证据工件 | `outputs/cmaes_verify_m1866/cmaes_reference.json`、`cmaes_study.json`、4 张 png |
| 测试 | `tests/test_cmaes_reference_verification.py`（11 passed，约 5 s 单核） |
| 复现命令 | `python3 -m optimizer.verify.cmaes_reference --out outputs/cmaes_verify_m1866 --figures`；`python3 -m optimizer.verify.cmaes_study --out ... --figures` |

## 1. 为什么这是"验证欠账"

`grep -rn "CMAES\|cmaes" --include=*.py` 在本仓只命中两处：实现本体与唯一调用方
`optimizer/core/optimization_loop.py`。也就是说：作为看板 KPI"工具接入 ≥5 类"的第 5 类
（优化库）与 KCS 艏部型线闭环的求解引擎，**从未有任何测试或与文献基准的比较**。m1601/m1610
复核的是**目标函数物理**（Holtrop-1984），不是**搜索算法**本身；本单补这一半。

## 2. 判据与结果（单种子参考精度 C1）

归一化误差 `err_norm = (f_best - f_opt)/(f_start - f_opt)`（尺度无关，0 最优）。

| 基准 | 维数 | 预算 | f_opt | err_norm | 判据 | 判定 |
|---|---|---|---|---|---|---|
| sphere | 4 | 600 | 0 | 2.567e-18 | ≤1e-6 | PASS |
| sphere | 8 | 1200 | 0 | 4.487e-16 | ≤1e-6 | PASS |
| ellipsoid cond 1e6 | 8 | 20000 | 0 | 2.722e-37 | ≤1e-6 | PASS |
| cigar cond 1e6 | 8 | 20000 | 0 | 9.336e-36 | ≤1e-6 | PASS |
| rotated ellipsoid cond 1e4 | 8 | 20000 | 0 | 4.422e-39 | ≤1e-6 | PASS |
| Rosenbrock | 4 | 20000 | 0 | 1.660e-30 | ≤1e-3 | PASS |
| Rastrigin（多模态） | 10 | 6000 | 0 | 0.0 | ≤1e-2 | PASS |
| shifted sphere（箱 [0,2]^8，最优点 1） | 8 | 1200 | 0 | 6.905e-15 | ≤1e-6 | PASS |

其余判据：C2 协方差自适应（病态二次型退化 ≤100×球面）PASS；C3 搜索箱平移等价性 PASS；
C4 同种子逐位可复现 PASS；C5 评估点从不越界 PASS；C6 预算越界 ≤ 一代 λ PASS；
C7 步长有限且单峰收缩 PASS。**总判 PASS。**

## 3. 多种子统计（BBOB 口径，5 种子，预算 6000）

| 基准 | 成功率@目标精度 | ERT（评估次数） | 中位 err_norm | IQR |
|---|---|---|---|---|
| sphere n8（eps 1e-8） | 100% | 682 | 9.52e-27 | — |
| ellipsoid n8 cond1e6（1e-8） | 100% | 4052 | 1.98e-21 | — |
| cigar n8 cond1e6（1e-8） | 100% | 5200 | 2.34e-18 | — |
| rot. ellipsoid n8 cond1e4（1e-8） | 100% | 2544 | 2.06e-23 | — |
| Rosenbrock n4（1e-6） | 100% | 1333 | 4.68e-31 | — |
| Rastrigin n10（1e-4） | **80%** | 2202 | 0.0 | — |

ERT 随条件数单调增（sphere 682 → cigar 5200），与理论一致；球面收敛是对数线性的：
线性回归 `log10 f ~ evals` 斜率 −0.0114 dex/eval，**R² = 0.992**，590 次评估内下降 6.56 个数量级。

## 4. 工程量级结论（本单最有用的一条）

部署配置（`optimization_loop.py`：dim=8，`max_evals=300`）下的**优化器自身数值噪声地板**：

| 预算 | 100 | 200 | **300（部署）** | 600 | 1200 | 2400 | 4800 |
|---|---|---|---|---|---|---|---|
| 中位 err_norm | 3.09e-2 | 4.00e-3 | **3.63e-4** | 7.88e-8 | 6.19e-16 | 2.57e-22 | 2.94e-25 |

即 300 次评估下优化器分辨力约 **3.6e-4**（占初始误差尺度的 0.036%）。`optimizer/REPORT.md`
报告的 m1610 重跑阻力改善 **1.377%** 比该噪声地板高约 **38 倍**，因此该改善不是优化器噪声；
反之，若将来报告 <0.04% 量级的改善，需要先加预算再下结论。

## 5. 注意点 / 局限（如实）

1. **预算而非算法是限制因素**：300 evals 只能到 3.6e-4；本仓 KCS 闭环若要 1e-6 级收敛须 ≥600 evals。
   这是配置结论，不改默认值（避免动已归档的 m1610 结果）。
2. **Rastrigin 多模态 80%**：单次 6000 evals 会偶发落入局部极小；对多模态船型优化问题（如
   带局部极小的总阻力-稳性权衡）不能假设全局最优，需多种子重启（建议 ≥5 种子）。
3. **越界处理是裁剪（clip）而非常用的反射/罚函数**：C5 证明评估点从不越界，但裁剪会扭曲
   协方差估计；C3 显示把搜索箱平移到 [0,2]^n（最优在箱心）时误差仍到 6.9e-15，说明该实现
   的均值初始化若被硬写成 0 会退化 —— 本单以 C3 作为**回归钉子**防止未来改成 `m0=0`。
   实测当前实现对偏心箱（最优点在箱心但箱非对称于原点）无显著退化，故**不做代码改动**。
4. **sigma0=0.3 是绝对步长**：若把同一内核用于量纲远大于 ±1 的设计空间（如主尺度 [0,300] m），
   步长需要按箱宽重标定，否则首代几乎原地；当前 KCS 用 ±1 归一化参数空间，不触发。
5. 本复核不覆盖目标函数物理（见 `docs/verification/optimizer-objective-physics-audit.md`），
   也不覆盖几何约束罚函数（`optimizer/interfaces/kcs_bow_param.py`）的正确性。

## 6. 误差判定（显式回答"是否 <3%"）

* 关键量 = 归一化参考误差 `err_norm` 对闭式最优：**是**，全部 ≤1e-6（球 2.6e-18 … 平移球 6.9e-15），
  远优于 3%。
* 多种子中位误差：**是**，5/6 基准 ≤1e-20；Rastrigin 中位 0.0 但成功率 80%（非误差问题而是
  多模态全局性，**不能**宣称 100% 全局最优）。
* 收敛率线性度：R²=0.992（判据 >0.99）：**是**。