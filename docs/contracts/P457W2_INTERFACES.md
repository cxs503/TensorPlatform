# p457w2 跨节点数据交换接口契约

本文件是战役 `p457w2`（plan 9388）集成门 `p457w2-n09` 的**接口契约登记簿**。
机器可读版本：`data/campaigns/p457w2/interfaces.json`（schema `tp.integration.interfaces/v1`）。
闸按「文档存在 → 节点存在 → 单位一致 → 区间覆盖（或声明降级护栏） → 两侧有可核证据」
五步逐条核验，任何一步不满足都不会判 `VALIDATED`（见 `tools/integration_gate/coupling.py`）。

## 契约语义

| 字段 | 含义 |
|---|---|
| `payload.id` | 生产方导出的量（含自变量） |
| `payload.unit` | 该量的单位（`-` 为无量纲） |
| `payload.fields` | 导出记录必须携带的字段名 |
| `payload.coverage` | 生产方**认证**的适用区间（不是它能算出的区间） |
| `requirement` | 消费方真实需要的区间 |
| `requirement.fallback` | 区间不覆盖时的处置：`clamp_with_status`（域外返回必须带 `domain_status` 标记且不外推）/ `reject` |

区间短缺口只有在 `fallback=clamp_with_status` **且** `payload.fields` 含 `domain_status`
时才算「有护栏的通过」（`guarded`），否则记 `CONTRACT_MISMATCH`（blocking）。
这条规则的用意：**允许生产方在认证域外被调用，但禁止沉默外推**。

## 五条接口

### IF-1 曝气减载律 → 砰击/疲劳载荷链（n04 → n01）

- 载荷：`g_load(alpha_patch, u_impact)`，无量纲，字段 `g_peak` / `g_load` / `domain_status`
- 生产方认证域：`alpha ∈ [1e-8, 0.05]`（`ALPHA_MIN_RESOLVABLE` / `ALPHA_VW_MAX`，由轨迹 meta 机器读出）
- 消费方需求：`alpha ∈ [0, 0.5]`
- 处置：`clamp_with_status`。生产方自身算例集里 `alpha_patch` 最大 0.4349，
  已有 3/6 算例落在声学域外（`criteria.C5.design_envelope.cases_out_of_domain`），
  因此**域外调用是常态**，必须由 `domain_status` 标记，禁止把域外值当实数用。

### IF-2 疲劳寿命曲线（n05 → n01）

- 载荷：`log10_N(D_curve, delta_sigma)`，单位 decade，字段 `log_a1` / `log_a2` / `knee` / `fatigue_life`
- 两侧覆盖：15–150 MPa（log10 N ≈ 6.85–9.47，由轨迹实测）
- 单位/区间一致，但**参数同一性不在本接口判**：见 `assumptions.json` 键
  `fatigue.sn_curve.D`（本门实测两侧尾段参数不一致，见集成报告 §8）。

### IF-3 冰脊载荷（n03 → n01）

- 载荷：`ice_load_amplitude(ridge, speed)`，MPa，字段 `load_amplitude` / `contact_area` / `domain_status`
- 适用域按战役任务书登记（生产方本门运行时无验收数值工件）⇒ 状态 `UNATTESTED`。

### IF-4 进水状态交换（n02 → n08）

- 载荷：`flooding_state(t)`，m，字段 `level_m` / `dt_s` / `mass_balance_kg`
- 区间 `[0, 8] m` 与生产方自证舱高一致；交换必须带 `dt` 与质量账本，
  因为 n08 的契约是「时间步协调 + 守恒 <1%」。
- 消费方无验收工件 ⇒ `UNATTESTED`。

### IF-5 破舱后装载状态（n02 → n06）

- 载荷：`GM_eff_m, KG_m`，m，字段 `gm_eff` / `kg` / `fsc`
- 生产方实测 `GM_eff ∈ [-0.618, 1.718] m`；消费端操纵性模型须拒绝负 `GM_eff` 工况。
- 消费方无验收工件 ⇒ `UNATTESTED`。

## 局限

- 本登记簿只核**契约形状**（单位/区间/字段/文档/证据可得性），不核物理正确性；
  物理正确性由互裁对（`pairs.json`）与假设一致性（`assumptions.json`）分别承担。
- `coverage` 区间来自生产方实现中的域常量或实测极值，来源写在各自 `note` 与
  `data/campaigns/p457w2/assumptions.json` 的 `source.sha256` 中；接口本身不做区间发现。