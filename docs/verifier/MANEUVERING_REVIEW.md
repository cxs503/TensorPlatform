# 操纵域既有能力复核 v1：D5 判决（maneuvering 六端点证据分级）

**案例**：KVLCC2 油轮，L_pp=320 m / B=58 m / d=20.8 m / Cb=0.8098 / ∇=312622 m³ /
D_p=9.86 m / A_R=112.5 m² / U=7.97 m/s（Fn=0.142）/ δ=35°。
**缘起**：进展报告 1032 缺陷登记 D5 —— "`maneuvering-mmg/turning`、`/zigzag`
未知异常，稳定 500（未判决）"。本单把 D5 判到底，并把整个操纵域（6 个端点族）
一次性定级。

## ① 结论速览

| 端点族 | 证据等级 | 依据 |
|---|---|---|
| `/maneuvering-mmg/turning`,`/zigzag` | **STUB** | HTTP 500；根因=构造 `MMGInput` 漏传必填 `Hr` → pydantic `ValidationError`，MMG 内核从未被调用 |
| `/maneuvering/turning` | **MOCK** | δ=0° 与 δ=35° 响应**逐位相同**；数值恰为 3.5L/2.0L/4.5L 的硬编码比例表 |
| `/mmg/turning` | **MOCK** | δ=0° 仍报"回转圆"（T_d=313.5 m，角速度 0.9 rad/s）；δ=35° T_d=1.59L（文献带下界外 −45%） |
| `/advanced/mmg-full` | **STUB** | HTTP 500（1032 未覆盖，本单新增发现） |
| 参考 MMG 核（本仓 `tools/verifier/mmg_reference.py`） | 部分 REAL | 进距/横距入带、定常平衡残差 1.3e-14；**T_d 偏高 +64%、Z 形超调未标定 → 记 WITHHELD** |

**操纵域现状：无任何一个可在工程上使用的 REAL 能力。**

## ② 怎么做的

1. **全端点系统探测**（`runs/m1460_maneuver_probe.py`）：同一 KVLCC2 输入打 6 个端点族，
   记录 status + 轨迹/指标，δ∈{0°,35°} 双点对照 —— 用来判"输入是否真的进模型"。
2. **根因定位**（`runs/m1460_rootcause.py`）：把平台模块
   `backend/api/maneuvering_mmg.py` 以只读方式 import（不修改该服务），直接复现
   traceback：`simulate_turning_test`(L270) / `simulate_zigzag_test`(L324) 构造
   `MMGInput` 时未传 `Hr`，而 `MMGInput` 已把 `Hr: float` 升为必填 → 500。
3. **"修好 Hr 就好吗"预后**（`runs/m1460_hr_prognosis.py`）：注入 Hr 后跑平台自身
   `MMGModel`：ψ 从未到 90°（自终止于 t≈15.6 s），advance/transfer/T_d **全为 0**，
   δ=35° 定常角速度 0.269 °/s。→ 即便补字段，端点仍出全零指标，**不是一行修复即可**。
4. **独立参考核**（`tools/verifier/mmg_reference.py`）：按 Yasukawa & Yoshimura (2015)
   MMG 标准方程 + KVLCC2 系数表（含螺旋桨尾流加速舵来流、RK4、自航点二分求解
   n=105 rpm, J=0.277）从一阶原理实现，作为"对理论而非对自身"的标尺。
5. **判据工程**（`tools/verifier/maneuvering_audit.py`）：每条判据配**正向对照**
   （物理自洽的假想响应必须判 PASS），防止判据退化为恒真否决器。
6. **回归测试**：`tests/verifier/test_maneuvering_reference.py` 13 例全绿。

## ③ 计算结果

### 参考核（KVLCC2，δ=35°，U=7.97 m/s）

| 量 | 参考核 | 文献带/L |
|---|---|---|
| 进距 advance | 926 m (2.89L) | 2.70–3.40 |
| 横距 transfer | 400 m (1.25L) | 1.20–1.90 |
| 战术直径 T_d | 1895 m (5.92L) | 2.90–3.60 |
| 定常回转直径 | 1600 m (5.00L) | — |
| Z 形 20/10 超调角 | 0.08° / 0.01° | 4–18 / 2–16 |
| 定常回转平衡残差 | 1.30e-14 | <1e-6 |


### 平台端点响应（同一输入）

| 端点 | HTTP | 关键响应 | 判决 |
|---|---|---|---|
| `/maneuvering-mmg/turning` | 422 | ValidationError: MMGInput.Hr missing | **STUB** |
| `/maneuvering/turning` δ=0° | 200 | adv 1120 m, T_d 1440 m | **MOCK** |
| `/maneuvering/turning` δ=35° | 200 | adv 1120 m, T_d 1440 m | **MOCK** |
| `/mmg/turning` δ=0° | 200 | T_d 313.5 m, rate 0.90 | **MOCK** |
| `/mmg/turning` δ=35° | 200 | T_d 507.9 m (1.59L) | **MOCK** |


### 判决明细（程序化生成）

- `maneuvering-mmg/turning_http` → **STUB**
- `maneuvering/turning_delta_invariance` → **MOCK**
- `maneuvering/turning_scale` → **MOCK**
- `mmg/turning_zero_rudder` → **MOCK**
- `mmg/turning_visibility` → **RESPONSIVE**
- `control_positive_invariance` → **RESPONSIVE**
- `control_positive_zero_rudder` → **RESPONSIVE**

## ④ 结果图

- `docs/verifier/figures/maneuvering_trajectories.png`：参考核 35° 回转轨迹 vs 平台
  `/mmg/turning` 轨迹（平台轨迹 y 轴取反以对齐转向手性）——平台轨迹在 t 早期即
  大半径漂移，与参考核的定常圆完全不同。
- `docs/verifier/figures/maneuvering_td_comparison.png`：四个候选的 T_d/L 与
  SIMMAN 2008 文献带（绿带）对照柱图，`/maneuvering-mmg/turning` 标为 HTTP 500。

## ⑤ 注意点（坑与局限）

1. **"补一个字段"不等于修好**：Hr 是 500 的直接原因，但注入 Hr 后指标全零
   （ψ 未过 90°），说明积分/收敛判据（`abs(r[-1]-r[-50])<1e-4` 早停）也坏了。
   派单时不能只写"补 Hr"。
2. **判据必须有正向对照**：只写"能识别硬编码"的检查器，遇到合法响应也会否决；
   本轮 `control_positive_*` 两条即为此设。
3. **单位口径坑**：`/mmg/turning` 的 `turning_rate` 是 rad/s，`/maneuvering/turning`
   是 deg/s，同一字段名两种口径 —— 跨端点比较前必须先归一（与 1032 的 D4 同型）。
4. **手性/符号**：不同端点 y 轴正方向与舵角符号约定不一致，画图时必须显式对齐，
   否则会把"镜像"误读成"轨迹不符"。
5. **本参考核的局限（诚实标注）**：进距 2.89L 与横距 1.25L 入带，但 T_d 5.92L
   **偏高 +64%**、Z 形超调角 0.08°/0.01° 明显低于文献量级（数度）—— 说明本核的
   阻尼/惯性配比与舵效仍未标定。故**参考核自身记 WITHHELD**，本轮平台判决
   **不依赖**参考核：STUB/MOCK 三条判决全部来自"证伪"（500 traceback、δ 不变性、
   零舵角出回转圆、3.5/2.0/4.5L 硬编码），无需任何外部参考值即可成立。
   参考核在本单里的角色只是**量级标尺**，不是判决依据。
6. **未做**：未修改 `/root/ship-performance-platform-incoming`（不在本工作树、禁碰
   master）；未做浅水/低速/Z 形实船标定；未覆盖 `abkowitz/*`、`maneuvering/zigzag`、
   `extended-calculations/maneuvering` 三个端点族（记为后续欠账）。

## ⑥ 误差判定（逐项显式回答"是否 <3%"）

| 端点 | T_d/L | 文献带/L | 相对带边偏差 | <15%? |
|---|---|---|---|---|
| reference(this repo) | 5.92 | 2.90–3.60 | +64.5% | **否** |
| maneuvering/turning | 4.50 | 2.90–3.60 | +25.0% | **否** |
| mmg/turning | 1.59 | 2.90–3.60 | -45.3% | **否** |
| maneuvering-mmg/turning | nan | 2.90–3.60 | — | — |


- 对齐文献带的量：**参考核 T_d +64.5% → 否**；`/maneuvering/turning` +25.0% → **否**；
  `/mmg/turning` −45.3% → **否**；`/maneuvering-mmg/turning` 500，无值可比 → **WITHHELD**。
- **A 类（解析/自洽）判据全部 <3%**：δ=0 直航（|y'|、|r'| 恒为 0，误差 = 0）、
  左右 35° 镜像（最大偏差 0.0）、自航点净推力残差 <1e-6、定常回转平衡残差 1.3e-14、
  RK4 步长减半进距变化 <0.5%。
- **结论**：本题**没有任何一项**操纵指标达到 <3%。可 <3% 的只有自洽恒等式，
  而自洽恒等式（1013 的教训）不能当作平台正确性的证据。

---

## 更正（m1867，2026-11-02）：`+64.5%` 一行是口径伪像，不是物理偏差

`T_d/L = 5.92`（以及本节表格里 reference 行的 `+64.5%`）由
`tools/verifier/mmg_reference.py::turning_indices` 的**旧口径** `2|y|@180°` 产生。
解析判据（稳态回转的 180° 横向偏移**就是**回转圆直径）证明该口径多乘了 2：

- 修复后同一核同一舵角：`T_d/L = 2.961`（文献带 2.90–3.60，带内）；
  旧口径 `2 × 2.961 = 5.922` ← 正好复现历史 `5.92`。
- 判据证据：`tests/test_turning_caliber.py`（13 passed）在**精确合成圆**上要求
  `advance = transfer = R`、`tactical = steady = 2R`（阈值 1e-9），并把旧口径判为
  +100 % 拒绝（负控制）。
- 因此 m1816 观察到的「口径敏感 1.68×」在本单被定性为**代码缺陷**（factor 2），
  而非文献对标歧义。

**但 WITHHELD 判定维持**，理由改写为：瞬态未收敛（δ=35° 时
`D_ss/T_d = 1.697`，稳态圆直径 5.025 L 仍在带外）+ KVLCC2 导数组未用 SIMMAN/EFD 标定。
详见 `docs/verification_debt/turning_caliber/REPORT.md`（工件
`artifacts/turning_caliber/numbers.json`、两张图）。
