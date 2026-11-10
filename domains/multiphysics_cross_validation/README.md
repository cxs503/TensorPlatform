# domains/multiphysics_cross_validation —— 十物理面集成验证与交叉互裁（领域包）

节点：`p135w2-n11-maturity-cross-validation`（P135-W2 成熟化·集成验证）。

本目录是**领域侧**：10 个物理面的闭式/半解析锚模型、代表性工况、耦合端口定义、
输入不确定度来源、以及**先验预声明**的灵敏度期望。域无关的机械判定全部在
`ops/taskplane/physics_integration_verify.py`（平台侧，零领域语义）。

## 目录

| 文件 | 作用 |
|---|---|
| `anchors.py` | 10 个面的闭式锚模型（UNDEX/碰撞/疲劳/MHD/空化/LNG/电力推进/电力系统/ISO15016/操纵性）+ `INPUTS` 不确定度来源表 + 中心差分求偏导 |
| `manifest.py` | 装配引擎输入：单位表、26 条锚（含量级带/文献参考）、3 条守恒式、5 条序关系、12 条耦合边（含 2 条声明式桥接）、10 个传播目标、9 条交叉检查、**预声明期望排序** |
| `run_validation.py` | 驱动器：两遍构建 → 引擎裁决 → 落 `artifacts/p135w2/`（manifest / validation.json / benchmark_suite.json / acceptance.json / md） |
| `make_report.py` | 出 4 张图 + 论文级报告 markdown + 报告载荷；`--publish` 发布到平台报告面 |
| `tests/test_multiphysics_cross_validation.py` | 判据 (a)–(e) 验收 + 零手抄守卫 + 契约非空转守卫 |

## 裁决判据（机算，落 `artifacts/p135w2/acceptance.json`）

| 判据 | 含义 | 本节点结果 |
|---|---|---|
| `a_energy_self_consistent` | 26 条锚量级带命中 + 3 条守恒式残差 ≤ 容差 + 5 条序关系成立 | PASS，最大残差 **4.24%**（UNDEX 化学能分配，容差 10%） |
| `b_interface_contracts` | 12 条耦合边量纲/帧/形状/dtype/符号约定逐项通过 | PASS，通过率 **100%** |
| `c_benchmark_db` | ≥10 条解析锚入库，每条 sha256 可重算且唯一 | PASS，**26 条**（唯一 26），suite_digest 链式派生 |
| `d_error_propagation` | 域内排序 + 全局双口径 Top-5 命中**预声明**；弹性符号/量级符合物理直觉；交叉检查通过 | PASS，10/10 target 与 2/2 全局口径全中 |
| `e_report` | 六要素段齐全 | PASS（report.md） |

全部通过 ⇒ `verdict = "pass"`；任一不过 ⇒ `not_ready`（CLI 退出码 2）。

## 复现

```bash
python3 domains/multiphysics_cross_validation/run_validation.py     # ~1 s，落 artifacts/p135w2/
python3 domains/multiphysics_cross_validation/make_report.py        # 出图 + report.md + payload
python3 domains/multiphysics_cross_validation/make_report.py --publish
pytest tests/test_physics_integration_verify.py domains/multiphysics_cross_validation/tests -q
```

## 三条工程纪律（本节点踩坑所得）

1. **排序键必须去抖**：中心差分噪声 ~1e-9 会让理论上相等的弹性（ΔW 对 Δσ/Δε_p/V 均为 1.0）
   分出假高低，排序口径直接失效。引擎排序键取 6 位小数（`SORT_DECIMALS`），原值保留高精度。
2. **量纲≠标度**：BOR 误标 `1/day`（scale=1/86400）会让 `y` 缩 86400 倍、弹性同倍放大，
   单一 target 霸榜全局排序。无量纲比值一律标 `1`。
3. **预声明优先**：期望排序由弹性结构**先验推导**（含固定平局规则）写进 manifest 后才跑，
   引擎只比对不调参；否则"物理直觉一致"无法证伪。

## 已知局限

* 气泡脉动/热能份额（42%/35%）、疲劳耗能分配（93%/5%）为文献经验值 —— 对应守恒式的意义是
  "残差在容差内"，不构成对份额的独立验证；
* 量级带是量级判据（±半个数量级），非精度判据；3% 精度只对 4 条有参考值的锚逐项回答；
* 热应力为全约束上界（未计柔性/滑动支座释放），不可直接判失效；
* 方差口径 Top-5 随 σ 注入变化，弹性口径（纯物理结构）为稳健主口径；
* Nomoto 一阶模型不含回转速降/横倾耦合，战术直径偏乐观；
* 桥接边 e06（热漏→壁面热流，section 降维）与 e08（轴功率→电功率，效率反算）的增益是
  声明式约定，需下游求解器确认。