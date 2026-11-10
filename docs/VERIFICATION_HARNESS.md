# 域无关验证脚手架（verification harness）— `verification-harness/1.0`

> 节点：`P269W2-N01`（战役 DAG，TensorPlatform）。落库：`tools/verification_harness/`。
> 纪律：本仓零领域语义——脚手架代码不含任何真实船名/真实航道/真实工况；物理内容全部经
> `tools/verification_harness/cases/*.json` 配置注入。

## 1. 案例介绍（物理背景 / 几何 / 参数）

验证（verification）与确认（validation）是求解器可信度的两条腿：验证问"方程解对了吗"
（解析解/网格收敛/阶数），确认问"方程建对了吗"（实验）。本节点只做**验证脚手架**：
一个与物理域**完全解耦**的机制，使任意求解器/物理域把"算例 + 权威值 + 容差"以配置形式
注册进来，就能自动执行、判据比对、证据归档。

脚手架的**锚点算例**（application 层）刻意选纯数学问题，因为它们能给出闭合形式权威值：

| 算例 | 被测实现 | 独立权威 | 合成域标签 |
|---|---|---|---|
| `synth-parabolic-form-hydrostatics` | 复合中点求积 | 闭合积分 | `synthetic-quadrature` |
| `synth-damped-oscillator` | 经典 RK4 | 欠阻尼闭合解 | `synthetic-ode` |
| `synth-slab-conduction` | 三对角有限差分（Thomas） | 二次闭合剖面 | `synthetic-pde-1d` |
| `synth-algebraic-law` | Newton 迭代 | 同一残差的二分法 | `synthetic-rootfinding` |
| `synth-tridiagonal-spectrum` | 幂迭代 + Rayleigh 商 | 特征值闭式 `2-2cos(kπ/(n+1))` | `synthetic-linear-algebra` |
| `synth-convergence-order` | 两网格实测误差比 | 理论阶 `p=4` | `synthetic-convergence` |

抛物型船型（Wigley 型形状函数，**数学测试形体**，非真实船）
`y(x,z) = (B/2)(1-(2x/L)²)(1-(z/T)²)`，`L=60 m, B=9 m, T=3.2 m`，闭合形式：

```
V   = (4/9) L B T = 768 m³      Cb = 4/9
Awp = (2/3) L B   = 360 m²      Cw = 2/3
```

几何自校验（先几何后物理）：求积给出的 `Cb = 0.444451` vs 闭合 `0.444444`，
相对偏差 `1.56e-05`，远优于"几何必须先验证 Cb/L/B"的 1e-3 门槛。

## 2. 怎么做的（方法 / 流程 / 工具链）

```
tools/verification_harness/
  model.py      声明式案例模型：CaseSpec / Tolerance / Metric / Verdict + 误差约定
  registry.py   CaseRegistry：JSON 配置注入、`module:func` 点路径惰性解析、fail-closed 校验
  runner.py     run_case / run_registry：执行、比对、误差表（字节确定性）
  evidence.py   sha256 + 环境指纹 + 依赖集 + 链式哈希 + verify_bundle 取证
  cli.py        `python3 -m tools.verification_harness run|verify`
  study.py      锚点研究（跑算例 / 收敛扫描 / 篡改实验）
  anchors/      应用层：合成数学锚点（机制层不 import，见 AST 依赖方向测试）
  cases/        6 个合成算例声明（配置，非代码）
config/verification_harness/neutrality_vocab.json   A4 词表（注入，机制代码不含域词）
```

误差约定（`model.py`）：
`abs_error = |a-e|`，`rel_error = |a-e|/|e|`（`e=0` 时为 `None`，只能用 `atol` 判），
通过判据 `|a-e| <= atol + rtol*|e|`。所有工件都落 `actual/expected/abs/rel/rtol/atol/verdict`
七列，审计者可**手算复现**整张误差表。

执行链：

1. `CaseRegistry.discover(cases/)` → 6 个 `CaseSpec`（域标签、输入、量名全部来自 JSON）；
2. `run_registry` → 逐例解析两个点路径可调用对象、同参调用、逐量比对；
   被测代码抛异常**不中断整轮**，降级为 `ERROR` 并记录异常类型（fail-closed 但不 fail-fast）；
3. `write_bundle` → `registry.json / report.json / error_table.md / cases/<id>.json`
   + `manifest.json`（每条工件 path/role/size/sha256/chain_hash，`root_digest`、`chain_head`、
   `environment_digest`）；
4. `verify_bundle` → 重算全部摘要，分类 `CONTENT`（字节变）/`STRUCTURAL`（缺失或新增）/
   `CHAIN`（链断/根摘要不符）。

确定性：`report.json` 不带墙钟（除非 `--stamp`），同环境两次运行**逐字节相同**（已由
`test_verification_harness_runner.py::test_run_report_is_byte_identical_across_runs` 与
`test_verification_harness_evidence.py::test_bundle_is_reproducible_and_deterministic` 钉住），
这是证据链可比对的前提。

工具链：CPython 3.11 + 标准库（`hashlib/json/importlib.metadata/platform`）+ matplotlib（仅出图）。
无第三方运行时依赖。

## 3. 计算结果（数字表，程序化注入）

来源：`artifacts/verification_harness/study.json`（唯一数字源）、`bundle/report.json`。

### 3.1 误差表（A1/A2）

| 算例 | 量 | actual | expected | rel_error | rtol | 判定 |
|---|---|---|---|---|---|---|
| synth-parabolic-form-hydrostatics | volume | 768.01200003 | 768.0 | 1.563e-05 | 1e-04 | PASS |
| synth-parabolic-form-hydrostatics | waterplane_area | 360.0045 | 360.0 | 1.250e-05 | 1e-04 | PASS |
| synth-parabolic-form-hydrostatics | block_coefficient | 0.444451388906 | 0.444444444444 | 1.563e-05 | 1e-04 | PASS |
| synth-damped-oscillator | x_end | -0.336851680591 | -0.33685168059 | 2.853e-12 | 1e-08 | PASS |
| synth-damped-oscillator | v_end | 0.370691413968 | 0.370691413969 | 3.375e-12 | 1e-08 | PASS |
| synth-slab-conduction | t_mid | 313.255208333 | 313.255208333 | 5.807e-15 | 1e-09 | PASS |
| synth-slab-conduction | t_quarter | 307.44140625 | 307.44140625 | 3.143e-15 | 1e-09 | PASS |
| synth-algebraic-law | friction_factor | 0.0166101104059 | 0.0166101104059 | 2.089e-16 | 1e-09 | PASS |
| synth-tridiagonal-spectrum | spectral_radius | 3.84775906502 | 3.84775906502 | 0.0 | 1e-10 | PASS |
| synth-convergence-order | observed_order | 4.04514459895 | 4.0 | 1.129e-02 | 2e-02 | PASS |

合计：6 例 / 10 量，PASS 6 / FAIL 0 / ERROR 0。

### 3.2 收敛扫描（合成抛物型求积，B 面板）

| nx | h [m] | V 数值 [m³] | rel_error(V) | rel_error(Awp) |
|---|---|---|---|---|
| 25 | 2.4000 | 768.76812288 | 1.000e-03 | 8.000e-04 |
| 50 | 1.2000 | 768.19200768 | 2.500e-04 | 2.000e-04 |
| 100 | 0.6000 | 768.04800048 | 6.250e-05 | 5.000e-05 |
| 200 | 0.3000 | 768.01200003 | 1.563e-05 | 1.250e-05 |
| 400 | 0.1500 | 768.00300000 | 3.906e-06 | 3.125e-06 |

最小二乘 log-log 斜率：`p_V = 2.0001`、`p_Awp = 2.0000` vs 理论 `p = 2`（复合中点）。
误差每加密一倍降 4 倍，与抛物型形函数上中点法则的二阶精度一致——这说明脚手架的
"权威值—实现"两条路线确实是**独立**的，不是同一条公式自证。

### 3.3 证据与篡改实验（A3）

- bundle 条目 9 条（registry / report / error_table / 6 条 case 记录），`root_digest = 6bf1fc42…56ce`，`chain_head = 05ff5caa…f417`，`environment_digest = eb8b44dd…56fb`；`verify_bundle` 判定 **OK**（9/9 条目重算一致）。
- 随机单字节翻转：40/40 检出（1.0000）。
- 结构性/链式篡改 4 类：`missing_artifact`、`injected_artifact`、`chain_tamper`、
  `forged_recorded_hash`（把篡改文件的 sha256 回写进 manifest）→ 4/4 检出。
- 合计检出率 **44/44 = 100%**。

## 4. 结果图

- `artifacts/verification_harness/fig_vh_error_vs_tolerance.png`（A 面板）：10 个量的相对误差
  （对数轴）与各自 `rtol` 判定带并列；误差跨度 16 个数量级，全部落在带内侧。
  零残差量（谱半径）在对数轴上按 1e-16 地板钳位，属绘图约定。
- `artifacts/verification_harness/fig_vh_convergence.png`（B 面板）：体积/水线面误差 vs h
  的双对数收敛曲线 + `p=2` 参考斜率，实测斜率 2.0001。
- `artifacts/verification_harness/fig_vh_tamper.png`（C 面板）：四类结构性篡改 + 随机字节翻转
  的检出率柱图，全部 100%。

（图由 `generate_verification_harness_figures.py` 直接读 `study.json` 生成，无手抄数字。）

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

**实现期真实踩坑（都由锚点当场抓出，正是脚手架的价值证明）**

1. 隐式代数律的 Newton 导数项写错（把 `2.51/(Re·√f)` 当作 `2.51/Re` 的因子），
   迭代收敛到 `f = 0.020904`，与二分法差 25.9% → 被 `synth-algebraic-law` 判 FAIL。
2. 三对角算子按 `tridiag(-1,2,-1)` 组装后右端符号未反号、且已知端点项移位方向写反，
   解出 `t_mid = -313.26`（正确 `+313.26`）→ 被判 FAIL，相对误差 2.0。
3. 幂迭代的闭式最大特征值写成 `2-2cos(π/(n+1))`（那是 `k=1`），实际 `λmax = 2-2cos(nπ/(n+1))`
   → 相对误差 24.3，被判 FAIL。

三次都是"脚手架 PASS 才是可信的"：错误实现没有一次蒙混过关。

**局限（诚实申报）**

- 证据链是**无密钥**哈希链：同时改写载荷与整本 manifest 的攻击者无法被纯哈希发现
  （`forged_recorded_hash` 之所以能被抓，是因为他只改了 sha 未同步链哈希）。要防这种攻击
  需外部签名/HMAC，本节点不做。
- 环境指纹是**信息项**：`verify_bundle` 的 `ok` 只判完整性，跨机器搬运后 `environment_match`
  为 False 但完整性仍可为 OK（避免把可移植性误判为篡改）。
- 锚点是**自洽性**证据，不构成物理可信度证据：真正的物理验证要由各领域把"被测求解器 +
  实验/文献值"以配置注入（例如 `reference_kind: external_reference|baseline`）。
- `synth-convergence-order` 只用两个网格量阶，容差放到 2%；要做得更硬需三点网格 + Richardson。
- 过渡期偏差：`models` 复用的容差必须由注入方声明，脚手架不提供"默认容差"，以免把
  "没有判据"包装成"通过"。

## 6. 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 关键量 | 基准（权威） | 实测相对误差 | 是否 < 3% |
|---|---|---|---|
| 合成抛物型体积 V | 闭合积分 `(4/9)LBT` | 1.563e-05 | 是 |
| 合成抛物型水线面积 Awp | 闭合积分 `(2/3)LB` | 1.250e-05 | 是 |
| 方形系数 Cb | 4/9 | 1.563e-05 | 是 |
| 振荡器末端位移/速度 | 闭合解 | 2.85e-12 / 3.38e-12 | 是 |
| 一维稳态温度剖面（中点、四分点） | 二次闭合剖面 | 5.81e-15 / 3.14e-15 | 是 |
| 隐式代数律解 | 二分法（独立算法） | 2.09e-16 | 是 |
| 三对角谱半径 | 特征值闭式 | 0.0（逐位相同） | 是 |
| 实测收敛阶 | 理论 `p=4` | 1.13e-02 | 是 |
| 求积收敛斜率 | 理论 `p=2` | 5.0e-05（2.0001 vs 2.0） | 是 |

全部关键量误差 < 3%（最差 1.13e-02）。**注意**：这些数字度量的是"脚手架比对能力"本身，
不是任何船舶性能量的精度；平台船舶能力的对标精度仍由各领域的实验/文献基准单独立项判定。

## 7. 机器验收判据对照

| 判据 | 要求 | 证据 | 结论 |
|---|---|---|---|
| A1 注册表 | ≥5 个合成案例可注册并执行 | `cases/` 6 文件，`study.json: cases.count=6`，`run.counts.PASS=6` | 满足 |
| A2 判据比对 | 误差计算 vs 手算相对偏差 ≤0.1% | `test_..._runner.py::test_metric_error_fields_agree_with_hand_calculation_within_0p1pct`（实测偏差 0，双精度同一表达式） | 满足 |
| A3 证据归档 | 每案例 sha256+环境指纹，篡改检出 100% | `cases/*.json` 每条 `environment_digest` + manifest sha256；40 随机字节翻转 + 4 类结构性篡改 = 44/44 | 满足 |
| A4 零领域语义 | 脚手架无真实船名/航道/工况 | `test_..._domain_neutral.py`（注入词表扫描 + 扫描器咬合自检 + AST 依赖方向 + 包结构 + 算例标签 `synth-*`） | 满足 |
| A5 pytest | ≥15 例，全合成夹具 | 4 个测试文件 60 例（registry 22 / runner 19 / evidence 13 / neutral 6，含参数化展开） | 满足 |