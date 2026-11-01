# 验证器题库真值独立复核（verifying the verifier）

**任务**：tp-doc-4 自主工时（data 席） · mission m1409 · 分支 `agent/m1409`
**对象**：`tools/verifier/question_bank/questions.db` —— 全平台候选实现的裁判真值
**日期**：2026-11-01 · **算力**：纯 Python/闭式公式，load15=117/128 下仅做轻量核算（无重算力进程）

---

## ① 案例介绍：为什么复核「裁判」而不是「选手」

平台的每个候选实现都要以题库里的 `expected_result` 为基准判 pass/fail。若真值本身错，
整条验证链就退化为橡皮章——而 owner 的 KPI 明确「自洽测试不计入」。因此本单的物理背景
不是船，而是**验证证据链的有效性**：题库的参考值必须能被研究者用**独立于候选实现**的
一次计算复现。

题库现状（只读扫描，`mode=ro`）：

| 项 | 值 |
|---|---|
| 题目总数 | 15 |
| 覆盖学科域 | 1（`resistance`） |
| 公式族 | ITTC-1957 ×5、Blasius 层流平板 ×3、Prandtl–Schlichting 湍流平板 ×4、Holtrop–Mennen 形状因子 ×3 |
| 难度 | basic / intermediate |
| 已提交（`committed=1`） | 15/15 |

复核实现在 `tools/verifier/reference_impl.py`（纯 stdlib，逐字照抄原始文献），
审计器在 `tools/verifier/audit_question_bank.py`，两者都不 import 生成器
`generate_questions.py`，也不 import 候选实现，避免自证。

## ② 怎么做的：方法与判据

1. **独立重算**：按题目 `specification.formula/method` 分派到 `reference_impl` 的文献实现：
   - Blasius (1908) `Cf = 1.328/√Re_L`（Schlichting, *Boundary-Layer Theory*）
   - Prandtl–Schlichting `Cf = 0.455/(log10 Re_L)^2.58`（Schlichting 8th ed., Eq. 20.18）
   - ITTC-1957 `Cf = 0.075/(log10 Re − 2)²`（Proc. 7th ITTC）
   - **Holtrop & Mennen (1982) Eq.(12)**：
     `1+k = 0.93 + 0.487118·c14·(B/L)^1.06806·(T/L)^0.46106·(L/LR)^0.121563·(L³/∇)^0.36486·(1−Cp)^(−0.604247)`，
     `c14=1+0.011·Cstern`，`LR=L(1−Cp+0.06·Cp·LCB/(4Cp−1))`，`Cp=Cb/Cm`，`∇=Cb·L·B·T`
     （Holtrop & Mennen, ISP 29 (1982) 166–170；Molland et al., *Ship Resistance and Propulsion*, CUP 2011, Eq. 5.24）
2. **环形证据检测**：题库生成器 `_holtrop_form_factor()` 与候选 `pilots/resistance_calculator.py`
   用的是同一个「简化公式」；若存储真值与该式**逐位相等**（<1e-9），而文献实现与之偏离，
   则判定 `CIRCULAR_REFERENCE`——记录下来的 pass 是自洽，不是验证。
3. **稳健性（防「换个假设就翻案」）**：4 参数题目（Cb,L,B,T）欠定 Cp/LCB/Cstern，
   故对 `Cm∈[0.95,0.99]`、`LCB∈±2%L`、`Cstern∈±10` 做 9³ 网格扫描给出**可接受带**；
   存储值若落在带外，则任何合理假设都无法复现它，结论与假设无关。
4. **题库级体检**：`commit_hash` 重算（与 `QuestionBank.commit` 同法）、
   容差语义（相对/绝对）、权限隔离（模块承诺 0600）、物理趋势（形状因子随丰满度单调增）、
   学科覆盖度。
5. **判决实验**：用两个候选跑同一题库（题库副本 `/tmp/qb_copy.db`，不动交付库）：
   现行「简化公式」候选 vs 文献忠实候选 `pilots/literature_calculator.py`。

判据阈值：闭式公式之间的一致性取 `1e-9`（纯舍入量级）；平台验收线 `3%`。

## ③ 计算结果

### 逐题复核（程序化注入，零手抄：`artifacts/question_bank_audit.json`）

| # | 题目 | 公式族 | 存储值 | 独立重算 | 相对偏差 | 判决 |
|---|---|---|---|---|---|---|
| 1 | ITTC-1957 Cf @ Re=1e6 | ittc_1957 | 0.0046875 | 0.0046875 | 0% | CONFIRMED |
| 2 | ITTC-1957 Cf @ Re=1e7 | ittc_1957 | 0.003 | 0.003 | 0% | CONFIRMED |
| 3 | ITTC-1957 Cf @ Re=1e8 | ittc_1957 | 0.00208333 | 0.00208333 | 0% | CONFIRMED |
| 4 | ITTC-1957 Cf @ Re=1e9 | ittc_1957 | 0.00153061 | 0.00153061 | 0% | CONFIRMED |
| 5 | ITTC-1957 Cf @ Re=5e6 | ittc_1957 | 0.00339669 | 0.00339669 | 0% | CONFIRMED |
| 6 | 层流平板 @ Re=1e5 | blasius | 0.00419950 | 0.00419950 | 0% | CONFIRMED |
| 7 | 层流平板 @ Re=1e6 | blasius | 0.001328 | 0.001328 | 0% | CONFIRMED |
| 8 | 层流平板 @ Re=5e5 | blasius | 0.00187808 | 0.00187808 | 0% | CONFIRMED |
| 9 | 形状因子 散货船 Cb=0.7 | holtrop | 1.03928 | **1.20094** | **15.55%** | CIRCULAR_REFERENCE |
| 10 | 形状因子 Series60 Cb=0.6 | holtrop | 1.05474 | **1.16458** | **10.41%** | CIRCULAR_REFERENCE |
| 11 | 形状因子 油船 Cb=0.8 | holtrop | 1.02759 | **1.30698** | **27.19%** | CIRCULAR_REFERENCE |
| 12–15 | 湍流平板 @ Re=1e6…1e9 | prandtl_schlichting | 0.00447076 / 0.00300371 / 0.00212833 / 0.00157060 | 同左 | 0% | CONFIRMED ×4 |

### 形状因子：存储值不仅错，且落在可接受带之外

| 船型 | 存储 1+k | 文献 1+k（标称） | 可接受带 [min,max] | 带外？ | 偏差 |
|---|---|---|---|---|---|
| Series 60 Cb=0.6 (L100·B15·T6) | 1.05474 | 1.16458 | [1.1364, 1.2003] | 是（低于下限 7.2%） | 10.41% |
| 散货船 Cb=0.7 (L150·B22·T8.5) | 1.03928 | 1.20094 | [1.1668, 1.2493] | 是（低于下限 10.9%） | 15.55% |
| 油船 Cb=0.8 (L200·B32·T11) | 1.02759 | 1.30698 | [1.2549, 1.3971] | 是（低于下限 18.1%） | 27.19% |

三条都落在**整个可接受带之外**，因此结论不依赖 Cm/LCB/Cstern 的具体取值。

### 缺陷根因（代码定位）

`tools/verifier/generate_questions.py:141` `_holtrop_form_factor()`：

```python
term2 = 0.487118 * (Cb ** (-0.9249))
term3 = (L / B) ** 0.15
term4 = (B / T) ** 0.2
return 0.93 + term2 * term3 * term4 * 0.1     # "* 0.1" —— 注释自述 "Scaled for pilot"
```

对照文献：`(B/L)`、`(T/L)` 比值被**倒置**成 `(L/B)`、`(B/T)`；`Cb^(-0.9249)` 与 `×0.1`
缩放因子均非 1982 式所有；缺 `c14`、`LR`、`(1−Cp)^(-0.604247)`、`(L³/∇)^0.36486` 四项。
后果是**趋势反向**：存储值 `1.0547→1.0393→1.0276` 随 Cb 递减，而任何船型族都是丰满度越大
形状因子越大（文献 `1.1646→1.2009→1.3070` 递增）。

而该函数 docstring 自述「using the same formula as the candidate」——真值与候选同源，
故 `verification_runs` 表中 3 条形状因子 pass 记录偏差全为 `0.0`：**自洽，非验证**。

### 判决实验（`artifacts/question_bank_crosscheck.json`）

| 候选实现 | pass | fail | 结论 |
|---|---|---|---|
| `pilots/resistance_calculator.py`（现行简化式） | 15/15 | 0 | 全绿，但形状因子 3 题为同义反复 |
| `pilots/literature_calculator.py`（Holtrop 1982 原式） | 12/15 | **3** | **正确的实现被题库判死** |

这是最强证据：题库不是"漏检"，而是**主动拒绝正确实现**，方向性与误差量级一致
（15.55% / 27.19% / 10.41% 对应上表三船）。

## ④ 结果图

`artifacts/question_bank_audit.png`（脚本 `tools/verifier/audit_figure.py`，可重跑）：

- **(a)** 1+k–Cb：文献 Eq.(12) 曲线 + 可接受带（阴影）+ 题库存储点（红色虚线，下降）
  + 独立重算点（绿色）→ 一眼看出趋势反向且全部出带。
- **(b)** Cf–Re 双对数：12 题存储值与独立重算完全重合（偏差 ≤1e-9）。
- **(c)** 逐题相对偏差条形图（横轴对数），平台 3% 线；红=拒收（3 题），绿=确认（12 题）。

## ⑤ 注意点 / 踩过的坑 / 局限

1. **权限位不是 git 属性**：`question_bank.py` 承诺题库 `chmod 0600`，但 git 不跟踪权限位，
   任何新 checkout/拷贝都得到 `0644`；原实现在此**直接抛 `PermissionError`**，
   实测 `QuestionBank()` 与 `tp_ver_resistance.py` 在干净 worktree 上**完全跑不起来**
   （见下节修复）。这属于「验证被拒绝服务」，比权限泄露更隐蔽。
2. **环形证据要专门检测**：真值由候选同源公式算出的情形，偏差恒为 0，常规门禁看不出来；
   必须显式比对生成器公式。
3. **欠定题目必须给可接受带**：4 参数生成形状因子题目，若只给单点真值，复核者换个 Cm 就能翻案；
   本单用 9³ 网格带把结论钉死。
4. **局限**：题库只有 `resistance` 单域，稳性/推进/耐波**没有**真值题，
   因此本审计无法为这些能力背书（覆盖度缺口已由审计器输出）。
5. **本单未做的事**：未改动已提交题目的真值（题库设计为 append-only + `commit_hash` 防篡改，
   就地改数会摧毁证据链）；修正值以 version-2 提案形式落盘，待题库 owner 重新播种。
6. 复核用的三个文献公式里，Prandtl–Schlichting 只含湍流项（不含层流混合区修正），
   与题库原引文一致，故按原引文比对，未额外加转捩修正。

## ⑥ 误差判定（逐项，显式回答「是否 <3%」）

| 关键量 | 基准 | 相对偏差 | <3%？ |
|---|---|---|---|
| 12 题平板/ITTC 摩擦系数（3 公式族 × 5/3/4 题） | Blasius / Prandtl–Schlichting / ITTC-1957 原式 | ≤1e-9（舍入量级） | **是** |
| 形状因子 Series 60 Cb=0.6 | Holtrop & Mennen (1982) Eq.(12) | 10.41% | **否** |
| 形状因子 散货船 Cb=0.7 | 同上 | 15.55% | **否** |
| 形状因子 油船 Cb=0.8 | 同上 | 27.19% | **否** |
| 形状因子趋势 | d(1+k)/dCb > 0（文献族） | 符号相反 | **否**（结构性错误） |
| `commit_hash` 防篡改 | 同法重算 | 15/15 逐位一致 | **是** |

关键结论 **3/15 条真值不满足 3% 标准**，且均超平台 15% 不可接受线中的两条、
一条接近（10.4%），属必须修正项；12/15 条为可靠真值。**本项判定为 FAIL（题库部分真值不可信）**，
不粉饰。

## 交付物与复现

| 产物 | 路径 |
|---|---|
| 文献独立参考实现 | `tools/verifier/reference_impl.py` |
| 题库审计器（只读） | `tools/verifier/audit_question_bank.py` |
| 文献忠实对照候选 | `tools/verifier/pilots/literature_calculator.py` |
| 出图脚本 | `tools/verifier/audit_figure.py` |
| 审计结果 JSON | `artifacts/question_bank_audit.json` |
| 判决实验 JSON | `artifacts/question_bank_crosscheck.json` |
| version-2 修正提案 | `artifacts/question_bank_corrections_v2.json` |
| 回归测试（14 项） | `tools/verifier/tests/test_question_bank_audit.py` |
| 图 | `artifacts/question_bank_audit.png` |

```bash
python -m tools.verifier.audit_question_bank --json artifacts/question_bank_audit.json \
        --emit-corrections artifacts/question_bank_corrections_v2.json
python -m tools.verifier.audit_figure
python -m pytest tools/verifier/tests/test_question_bank_audit.py -q   # 14 passed
```

## 修复与后续（本单已做 / 待办）

**已做**
- `question_bank.py::_ensure_permissions`：文件含 group/other 权限位时**自动收紧为 0600**
  （仅当可写），无法收紧才抛错。修复前，干净 checkout 上验证器完全不可用（已被实测证明：
  `PermissionError: ... 0o644; expected 0o600`）。安全不变量保留，可用性恢复。
- 建立**真值回归门**：14 项测试锁死 12 条好真值（偏差不得漂移）、锁死 3 条坏真值的环形特征、
  锁死「审计器只读」「权限泄露可检出」「文献实现趋势正确」。

**待办（超出本单范围，建议派单）**
1. 以 `artifacts/question_bank_corrections_v2.json` 重新播种形状因子 3 题（version=2，
   `supersedes` 指向旧 id），旧题标 `deprecated` 而非删除——保留证据链。
2. 题库扩域：稳性（箱型驳解析 GM/BM/KB）、推进（B 系列 KT/KQ 公开表）、耐波（Lewis 形式附加质量）
   目前**零真值**，这些能力的「复核通过」目前无处可依，是已合并功能复核 KPI 的真实瓶颈。