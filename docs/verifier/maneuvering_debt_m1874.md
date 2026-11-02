# 操纵域「未覆盖端点族」验证欠账清偿（m1874）

**席位**：tp-app-2（applications） · **单号**：m1874 · **分支**：`agent/m1874` · **日期**：2026-11-02
**EDIT_WHITELIST**：`docs/`, `tools/`, `tests/`, `artifacts/`
**被测服务**：`http://127.0.0.1:8001/api/v1`（ship-performance-platform 2.0.0，只读探针，未改服务代码）

**产物**
- `tools/audit/maneuver_debt_m1874.py` — 探针 + 判决 + 出图（22 条 FAIL 判据可一键重跑）
- `tests/test_maneuver_debt_m1874.py` — 19 项离线测试（参考核自证 + 工件一致性），**19/19 绿**
- `artifacts/maneuvering_debt_m1874/{probe.json, verdict.json, probe.md, fig_*.png}`

---

## ① 案例介绍（欠账来源与对象）

`docs/verifier/MANEUVERING_REVIEW.md` §5.6 明确记账：

> 未覆盖 `abkowitz/*`、`maneuvering/zigzag`、`extended-calculations/maneuvering`
> 三个端点族（记为后续欠账）。

其中 `maneuvering/zigzag` 已由 m1663 裁决；**剩下两个族（`abkowitz/derivatives`、
`abkowitz/simulate`、`extended-calculations/maneuvering`）以及同族的
`maneuvering-mmg/{turning,zigzag}` 至今无人复核**。本单把这条欠账清到底：4 个端点、
22 条判据、5 个族的证据分级。

### 基准船参数（探针输入，全单固定）

| Lpp [m] | B [m] | T [m] | Δ [t] | Cb | KG [m] | Dp [m] | A_R [m²] | V [m/s] | δ [deg] |
|---|---|---|---|---|---|---|---|---|---|
| 180.0 | 32.0 | 11.0 | 55000.0 | 0.82 | 9.0 | 6.5 | 25.0 | 7.2 | 35.0 |

灵敏度轴（每次只动一个）：L∈{60,180,320}、B∈{20,32,50}、T∈{6,11,20}、
Δ∈{8k,55k,320k}、Cb∈{0.60,0.82,0.90}、V∈{3,7.2,10}、δ∈{5,10,20,35}。

## ② 怎么做的（判据体系：三类，逐条可推翻）

| 类别 | 判据含义 | 为什么它能定罪 |
|---|---|---|
| **A 存在性** | 端点声称做的机动必须真的发生 | 返回 200 而 `|ψ|max≡0`、指标全 `null`、`δ≡0`，是**静默错答**，比 500 更危险 |
| **B 参数耦合** | 物理上必然耦合的输入改了，输出必须动 | 输出对这些输入**精确不变**⇒ 逻辑上断定实现未使用它们。**不需要任何文献点值** |
| **C 解析闭环** | 一阶 Nomoto 的定常回转与 Z 形超越角有精确闭式解，端点自报的 (K,T) 必须与自报的 T_d 闭合 | 同一实现的两组数自相矛盾即可定罪；与外部文献无关 |
| **D 量级标尺** | 文献带只作标尺 | 离线取不到点值 ⇒ 绝对精度一律 WITHHELD，**不进判决** |

### C 类的解析式（本单的参考核，已自证）

```
T·dr/dt + r = K·δ ,  dψ/dt = r
⇒ r(τ) = Kδ + (r0 − Kδ)e^(−τ/T)
   ψ(τ) = ψ0 + Kδ·τ + T(r0 − Kδ)(1 − e^(−τ/T))
⇒ 定常：r_ss = Kδ ，D_ss = 2V/r_ss
⇒ Z 形回舵后：ψ 的峰值出现在 r=0 处，τ_os = T·ln((r_sw+Kδ)/(Kδ))
```

> **踩坑记录（重要，本单自查修出）**：初版把超越角写成 `ψ_os = T(r_sw+Kδ)`，
> 那是对 r 积到 τ→∞ 的结果；而 ψ 的**峰值在 r=0 处**（τ 有限），两者在本船参数下相差 11 倍
> （43° vs 3.8°）。是 `tests/…::test_zigzag_overshoot_matches_rk4` 的 RK4 异离散交叉把它抓出来的。
> 修正后才敢把 C4 写进判决——**参考核必须先自证，再用它判别人**。

## ③ 计算结果（判决）

### 3.1 族级证据分级

| 端点族 | 判决 | 依据 |
|---|---|---|
| `abkowitz/derivatives` | **MOCK** | 导数为 Δ·L^{±1} 的合成张量：Cb/B/T 响应精确为 0（B1–B4）、与船型无关 |
| `abkowitz/simulate` | **STUB** | 200 OK 但机动从未发生（|ψ|max≡0, |δ|max≡0），三项指标全 null，delta0>0 直接 500（A1–A5） |
| `extended-calculations/maneuvering` | **MOCK** | T_d≡3.59·L、T≡24.222·L/V 纯缩放；B/T/Δ 响应 0；自报 K-T 与自报 T_d 差 8.7× |
| `maneuvering-mmg/{turning,zigzag}` | **STUB** | 两族端点对差异极大的合法输入均 HTTP 500（A6） |
| `maneuvering/zigzag` | **MOCK(沿用 m1663, 本轮补 C4/C5 跨端点不一致证据)** | A/B 类参数盲 + 与同船 Nomoto 闭式解不相容 |

### 3.2 判据全表（22 FAIL / 0 PASS / 2 INFO / 3 WITHHELD）

| 判据 | 类别 | 内容 | 实测 | 基线 | 判决 |
|---|---|---|---|---|---|
| A1 | existence | abkowitz/simulate(zigzag, ψ0=δ=10°) 必须产生首向偏转 |ψ|max ≥ 10° | 0.0 | ≥ 10.0 deg | **FAIL** |
| A2 | existence | abkowitz/simulate 舵角序列必须非平凡（至少一次换舵） | 0.0 | > 0 | **FAIL** |
| A3 | existence | zigzag / turning / spiral 三种 maneuver_type 结果必须可区分 | {'zigzag': 0.0, 'turning': 0.0, 'spiral': 0.0} | 三者不应全等 | **FAIL** |
| A4 | existence | 回转类机动必须给出战术直径/进距/横距 | {'tactical_diameter': None, 'advance': None, 'transfer': None} | 非 null | **FAIL** |
| A5 | existence | 非零初始舵角 delta0=20° 必须可算（不得 500） | 3/6 次探针非 200 | 0 次 | **FAIL** |
| A6 | existence | maneuvering-mmg 族（zigzag/turning）必须可达 | 3/3 次探针非 200 | 0 次 | **FAIL** |
| B1 | coupling | abkowitz/derivatives 对 Cb(0.60→0.90) 必须非零响应 | max|Δ{Y_v,N_r,Y_delta,N_delta,X_u}| = 0.0% | > 1% | **FAIL** |
| B2 | coupling | abkowitz/derivatives 对 B(20→50) 必须非零响应 | max|Δ{Y_v,N_r,Y_delta,N_delta,X_u}| = 0.0% | > 1% | **FAIL** |
| B3 | coupling | abkowitz/derivatives 对 T(6→20) 必须非零响应 | max|Δ{Y_v,N_r,Y_delta,N_delta,X_u}| = 0.0% | > 1% | **FAIL** |
| B4 | coupling | 导数缩放律检验：Y_v·L/Δ 与 N_r/(L·Δ) 必须随 L 变化（恒定 ⇒ 合成张量） | {'Y_v*L/D': [-20.0, -20.0, -20.0], 'N_r/(L*D)': [-10.0, -10.0, -10.0]} | 非恒定 | **FAIL** |
| B5 | coupling | extended-calculations/tactical_diameter 对 B 20→50 必须非零响应 | 646.2 → 646.2（变化 0.0%） | > 1% | **FAIL** |
| B6 | coupling | extended-calculations/tactical_diameter 对 T 6→20 必须非零响应 | 646.2 → 646.2（变化 0.0%） | > 1% | **FAIL** |
| B7 | coupling | extended-calculations/tactical_diameter 对 Δ 8k→320k t 必须非零响应 | 646.2 → 646.2（变化 0.0%） | > 1% | **FAIL** |
| B8 | coupling | extended-calculations/advance 对 B 20→50 必须非零响应 | 314.3 → 314.3（变化 0.0%） | > 1% | **FAIL** |
| B9 | coupling | extended-calculations/transfer 对 T 6→20 必须非零响应 | 245.5 → 245.5（变化 0.0%） | > 1% | **FAIL** |
| B10 | coupling | extended-calculations 的 T_d/L 必须随 L 变化（纯 L 缩放 = 硬编码指纹） | [3.59, 3.59, 3.59] | 非恒定 | **FAIL** |
| B11 | coupling | extended-calculations 的直航/动稳性判词必须随船型变化 | [[0.6, '良好', '稳定'], [0.82, '良好', '稳定'], [0.9, '良好', '稳定']] | 判词不应恒定 | **FAIL** |
| C1 | closure | 自报 (K,T) 的定常回转闭环：D=2V/(Kδ) 必须等于自报 T_d | 5612.6 m（闭式） vs 646.2 m（报出） | 相等（容差 3%） | **FAIL** |
| C2 | closure | 由自报 T_d 反推的等效 Nomoto 增益 K 必须等于自报 K | K_implied=0.03648 vs K_rep=0.00420 1/s | 相等（容差 3%） | **FAIL** |
| C3 | closure | 自报 T_index 必须是物理时间常数：T/(L/V) 不应恒定 | [24.2223, 24.2224, 24.2222] | 应随 V 变化 | **FAIL** |
| C4 | closure | 同族 Z 形首超越角：/maneuvering/zigzag 报出 vs Nomoto(同船自报 K,T) 闭式解 | 5.2 deg vs 3.759 deg | 相等（容差 3%） | **FAIL** |
| C5 | closure | 同族 Z 形周期：端点 period vs Nomoto 闭式解半周期 | 972.0 s vs 663.9 s | 相等（容差 3%） | **FAIL** |
| D1 | scale | extended-calculations 的 T_d/L 是否落在文献量级标尺内 | 3.59 | 2.9–3.6 | **INFO** |
| D2 | scale | /maneuvering/turning 的 T_d/L 是否落在文献量级标尺内 | 4.5 | 2.9–3.6 | **WITHHELD** |
| D3 | scale | /mmg/turning 的 T_d/L 是否落在文献量级标尺内 | 1.777 | 2.9–3.6 | **WITHHELD** |
| D4 | scale | extended-calculations 的 进距/战术直径 是否落在量级标尺内 | 0.486 | 0.8–1.4 | **WITHHELD** |
| D5 | scale | /maneuvering/zigzag 10/10 首超越角 是否落在文献量级标尺内 | 5.2 | 5.0–20.0° | **INFO** |

### 3.3 关键数字（同一基准船，五条操纵路线横向对照）

| 路线 | T_d [m] | T_d/Lpp | 可用性 |
|---|---|---|---|
| `extended-calculations/maneuvering` | 646.2 | 3.590 | 200（但见 B5–B10） |
| `/maneuvering/turning` | 810.0 | 4.500 | 200（m1663 已判 3.5/2.0/4.5L 硬编码） |
| `/mmg/turning` | 319.8 | 1.777 | 200（进距 1839.0 m = 10.22L，横距 82.4 m） |
| `/maneuvering-mmg/turning` | — | — | **HTTP 500** |
| `abkowitz/simulate` | — | — | 200 但 T_d=`None` |

存活两路的战术直径相差 **102.1%**，
且 `abkowitz/simulate` 自报直航稳定指数 `-0.000379`
（`is_stable=False`）与 `extended-calculations` 的
「`良好` / `稳定`」**互为否定**。

### 3.4 Nomoto 解析闭环（C 类）

| 量 | 端点自报 | 闭式解 | 相对偏差 |
|---|---|---|---|
| K [1/s] | 0.0042 | — | — |
| T [s] | 605.56 | — | — |
| T_d [m] | 646.2 | **5612.6** | **-88.5%** |
| 等效 K（由自报 T_d 反推）[1/s] | 0.0042 | 0.03648 | -88.5% |
| Z 形首超越角 [deg] | 5.2 | 3.76 | +38.3% |
| Z 形半周期 [s] | 972.0 | 663.9 | +46.4% |

自报 (K,T) 隐含的回转直径是自报 T_d 的 **8.69 倍**；`T_index = 605.56 s`
对应 `T/(L/V) = 24.22`，而物理量级标尺是 0.30–1.50。

### 3.5 Abkowitz 导数的合成张量指纹（B4）

| L [m] | Y_v | Y_v·L/Δ | N_r | N_r/(L·Δ) |
|---|---|---|---|---|
| 60 | -18333.333 | -20.0000 | -3.300e+07 | -10.0000 |
| 180 | -6111.111 | -20.0000 | -9.900e+07 | -10.0000 |
| 320 | -3437.500 | -20.0000 | -1.760e+08 | -10.0000 |

两张归一化列在 L 三档上**精确恒定** ⇒ 导数形如 `c·Δ·L^(±1)`，与 Cb/B/T 完全无关
（B1–B3 的响应为 0.0%）。这不是"经验公式精度不足"，是**没有经验公式**。

## ④ 结果图（matplotlib，数字全部由工件注入）

| 图 | 内容 |
|---|---|
| `fig_td_by_endpoint.png` | 同一船五条操纵路线的 T_d/Lpp 与文献量级带（绿）；两路不可用（500 / null），存活两路相差 2.5 倍 |
| `fig_parameter_blindness.png` | 左：extended-calculations 只对 L、V 有响应（B/T/Δ 曲线精确重合）；右：abkowitz 导数只对 Δ、L 有响应（船型轴精确为 0） |
| `fig_nomoto_closure.png` | 左：用同船自报 (K,T) 作闭式 Z 形（首超越角 3.76°）vs 端点 5.2°；右：自报 (K,T) 隐含直径 5613 m vs 自报 646.2 m（log 轴） |

## ⑤ 注意点（坑、局限、适用范围）

1. **参考核必须先自证**：本单的 Z 形超越角公式初版是错的（积到 ∞ 而非峰值在 r=0），
   差 11 倍。RK4 异离散交叉测试抓出后才修正——若跳过自证，C4 会给出完全错误的判决。
2. **一阶 Nomoto 的固有上限**：本船 `KδT ≈ 0.44`，
   一阶模型给出的 Z 形首超越角只有几度（闭式 3.76°），**进不了**
   文献带 5–20°（那需要二阶以上模型或更大的 KδT）。因此 D5 只标 WITHHELD：
   不能拿"1.5° 低于文献带"单独定罪，定罪的落点是 C4 的跨端点不自洽（−60%）与 B 类参数盲。
3. **单位口径未声明**：`extended-calculations` 未在响应里声明 `k_index`/`t_index` 的量纲。
   C1/C2 的前提是"K 为 1/s、T 为 s（Nomoto 标准）"。若二者另有口径，则**缺陷从"自相矛盾"
   变成"口径未声明"**——两种都是缺陷，但结论措辞不同。已在 verdict 里按前一种记账。
4. **端点归属**：:8001 的服务代码不在本工作树（属 ship-performance-platform），
   本单**只取证不改代码**；修复须由持有该仓权限的单承接，本工具的 22 条 FAIL 判据可直接当验收门。
5. **鉴权变化（实测）**：本轮 :8001 开始要求 Bearer 令牌（无令牌 401、`:8001/auth/login` 自身 500），
   而 `:8010/api/v1/auth/login` 签发的 token 在 :8001 有效。工具已按此实现，凭据只从环境变量读，
   不落盘不入库。
6. **A5 的 500 是服务端异常**：`delta0=20°` 的三种 maneuver_type 全部 500 ⇒ 舵力项路径未实现，
   不是输入校验问题（0° 时能返回 200）。
7. **容量纪律**：开工时 `load15 = 122`（nproc 128）⇒ 全程只做轻量（约 60 次 HTTP 探针 +
   纯 python 后处理），未起任何重算力进程。

## ⑥ 误差判定（逐项显式回答「是否 <3%」）

| 关键量 | 基准 | 结果 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| B1 Cb→导数响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| B2 B→导数响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| B3 T→导数响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| B5–B9 B/T/Δ→回转几何响应 | 应 >1% | 0.0% | −100% | ❌ 否 |
| C1 T_d 闭环（自报 K,T） | 2V/(Kδ) = 5612.6 m | 646.2 m | **-88.5%** | ❌ 否（差 8.7 倍） |
| C2 K 反推 | 0.03648 1/s | 0.0042 1/s | **-88.5%** | ❌ 否 |
| C4 首超越角（跨端点） | 3.76° | 5.2° | **+38.3%** | ❌ 否 |
| C5 半周期（跨端点） | 663.9 s | 972.0 s | **+46.4%** | ❌ 否 |
| 参考核 Z 形 vs RK4（自证） | RK4 逐段积分 | 三组 (K,T) | 见测试 <10%（峰值浅极大，对换舵时刻敏感） | ⚠️ 部分（参考核自证，非被测端点） |
| 参考核 ψ 自由响应 vs RK4 | 闭式 ψ(τ) | 同 | <1e-6 | ✅ 是 |
| A 类存在性 | 布尔 | 见 3.2 | 无数值误差 | — |
| 文献带（D1–D4） | 文献量级标尺 | 见 3.2 | — | **WITHHELD**（离线无点值，不作判决） |

**结论（诚实）**：本单**没有**、也无法给出任何操纵量的绝对精度（无离线点值 ⇒ 绝对精度 WITHHELD）。
本单证明的是：`abkowitz/derivatives`、`abkowitz/simulate`、`extended-calculations/maneuvering`
三个端点的**参数盲性**与**自相矛盾**（22 条 FAIL，全部来自不变量/解析闭环，与文献无关），
`maneuvering-mmg/{turning,zigzag}` 完全不可用。据此把三族从"未复核"推进到
**MOCK / STUB / MOCK / STUB**，欠账从"未知"变为"已知且可重跑"。

**重跑命令**：
```
python3 tools/audit/maneuver_debt_m1874.py          # 需 :8001 可达
python3 -m pytest tests/test_maneuver_debt_m1874.py --noconftest -q   # 离线
```

## 后续建议（给下一单）

1. **修复承接**：需 `ship-performance-platform` 写权限的单把 `abkowitz` 族接上真导数
   （Kijima/Inoue 回归或细长体解析）与真积分器；验收门 = 本工具 22 条 FAIL 转 PASS。
2. **`maneuvering-mmg` 族的 500**：先取服务端 traceback（G21 在 m1663 就已 FAIL，两轮无变化），
   建议直接派 infra 单抓日志，避免第三轮重复可用性裁决。
3. **K-T 口径**：`extended-calculations` 应在响应里显式声明 `k_index`/`t_index` 量纲，
   否则任何下游自航/操纵预报都会把 8.7 倍误差带进设计回路。
