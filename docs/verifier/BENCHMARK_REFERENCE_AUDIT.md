# 基准参考数据集审计（验证欠账清偿）— tp-ver-8

**结论先说**：`backend/data/benchmark_reference.json` v1.0 里的 `experimental_rt_n`
（总阻力）与 `experimental_ct`（总阻力系数）**12/12 个算例互相矛盾**：由 (Rt, Ct) 反推的湿表面积
只有几何反推值的 0.10–0.30 倍，违反恒等式 `Rt = ½ρV²SCt` 达 86.8–89.8%（SUBOFF 可直接验算的 4 例）。
该列被 `GET /calculations/benchmark_cases` 原样对外发布，而 15% 精度判决只看 Ct，所以这个错误
从未在判决里暴露。另发现 DTMB-5415 四例的 `froude_number` 列与自身几何不一致（隐含 L≈181 m，
而几何写的是 L=5.72 m），以及 `SHIP_GEOMETRY["SUBOFF"]` 把直径 0.508 复制进了
`displacement` 与 `cb`（0.5 kg 的 4.356 m 艇体）。v1.1 修复后：审计严格通过，8 例 PASS、4 例声明性
WITHHELD（不再产出无意义的 15% 判决）。

## ① 案例介绍（对象与几何）

审计对象是平台阻力精度门禁的唯一参考数据源，不是某一个船型：

| 船型 | 算例数 | 几何来源 | 尺度 | 参考量 | 声明 ρ / ν（v1.1 新增） |
|---|---|---|---|---|---|
| DARPA SUBOFF（回转体潜艇，含指挥台围壳） | 4 | 数据集 `geometry`（L=4.356 m，D=0.508 m，S=5.42 m²） | 模型（淡水） | Ct = 0.0038–0.0044 | 1000 kg/m³, 1.00e-6 m²/s |
| DTMB-5415（细长水面舰） | 4 | L=5.72 m, B=0.759 m, T=0.246 m, Δ=466.3 kg, Cb=0.508 | 模型（淡水） | Ct = 0.0035–0.0045 | 1000 kg/m³, 1.00e-6 m²/s |
| KVLCC2（VLCC 油船） | 4 | L=320 m, B=58 m, T=20.8 m, Δ=307 000 t, Cb=0.8098 | 实船（海水） | Ct = 0.0028–0.0032 | 1025 kg/m³, 1.19e-6 m²/s |

被审计的几何量本身（L/B/T/Δ/Cb）逐项过了 `displacement ≈ ρ·Cb·L·B·T` 一致性检查：
KVLCC2 偏差 1.6–1.8%（通过），DTMB-5415 偏差 14.1%（WARN，Lpp/Lwl 与附体定义差异可能解释）。

## ② 怎么做的（方法 / 流程 / 工具链）

判据先于动手：15% 精度判决的可信度不可能高于其参考数据的一致性，因此先造**解析判据**
（全是恒等式与解析下界，不含任何外部抄录数字）：

| 检查 | 判据 | 容差 |
|---|---|---|
| C1 `speed_unit` | `speed_ms = speed_kn × 0.514444` | 0.2% |
| C2 `froude_consistency` | `Fn = V/√(gL)` | 0.5% |
| C3 `froude_physical` | `Fn ≤ 1.0`（排水型/模型试验物理界） | — |
| C4 `displacement_cb` | `∇ = Cb·L·B·T` | 10%（超出记 WARN） |
| C5 `ct_rt_consistency` | `Rt = ½ρV²SCt` | 5% |
| C6 `implied_wetted_area` | `S_implied/S_yardstick ∈ [0.5, 2.0]`，yardstick 优先取数据集自带 S，否则 5.5·∇^(2/3) | 带内 |
| C7 `preset_sanity` | 几何预置不得把长度类量复制进 `displacement`/`cb`；`displacement` 单位必须声明 | — |
| C8 `friction_floor` | `Ct ≥ Cf_ITTC57(Re)`，且 `Ct/Cf ∈ [1.02, 2.5]`（解析下界） | 带内 |

工具链（全部只读提交在 worktree）：

- `tools/verifier/audit_benchmark_reference.py` — 审计器，逐算例输出 PASS/WARN/FAIL/WITHHELD，
  `--strict` 在有**未声明** FAIL 时退出码 1；`--figures` 出图。
- `tools/verifier/migrate_benchmark_reference_v11.py` — v1.0→v1.1 迁移：只从 v1.0 已有字段**推导**
  新数字，推导式写进算例本身，v1.0 原值保留在 `legacy_v1_0_*` 供追溯。
- `tools/verifier/make_audit_report_assets.py` — 从审计工件生成本文的图与表（零手抄）。
- `backend/api/domain/ship_benchmark.py` — 门禁改造：`data_status != "ok"` 的算例给
  `status="withheld"`，不出 15% 判决；`pass_rate` 只在 scored 算例上定义；SUBOFF 预置修死。
- `tools/verifier/tests/test_benchmark_reference_audit.py` — 14 项回归测试（含"审计要有牙"的反向用例：
  回放 v1.0 数字必须被判 FAIL）。

修复策略（**不造数**）：

1. 密度/运动粘度按算例声明（模型淡水 / 实船海水），因为 `Ct↔Rt` 换算此前无 ρ 可依；
2. `froude_number` 一律按 `V/√(gL)` 重算（恒等式，无争议）；
3. `experimental_rt_n`：只有数据集自带湿表面积（SUBOFF）时按恒等式重算，并标
   `rt_status="derived_from_ct"`；DTMB-5415 / KVLCC2 缺湿表面积，**置 null** 并标
   `rt_status="unavailable_no_wetted_surface"` —— 从文献补 S 属于新数据的引入，超出本单授权，
   故按 honest-WITHHELD 处理；
4. 速度与几何自相矛盾的算例（DTMB-5415 四例）标 `data_status="withheld"` + 理由，门禁不再给它判决。

## ③ 计算结果（数字表，程序化注入）

### T1 v1.0 残差诊断（`artifacts/verifier/benchmark_audit_prefix.json`）

| case | Rt v1.0 [N] | 恒等式给出的 Rt [N] | 相对误差 | S 反推 [m²] | S 参照 [m²] | 比值 |
|---|---|---|---|---|---|---|
| SUBOFF-01 | 12.5 | 92.682 | 86.8% | 0.7132 | 5.42 | 0.132 |
| SUBOFF-02 | 21.0 | 173.44 | 88.2% | 0.6402 | 5.42 | 0.118 |
| SUBOFF-03 | 31.5 | 284.55 | 89.2% | 0.5854 | 5.42 | 0.108 |
| SUBOFF-04 | 45.0 | 429.264 | 89.8% | 0.5543 | 5.42 | 0.102 |
| DTMB5415-01 | 8.2 | 恒等式不可算（无 S） | — | 0.5375 | 3.253 | 0.165 |
| DTMB5415-02 | 28.5 | 恒等式不可算（无 S） | — | 0.5531 | 3.253 | 0.170 |
| DTMB5415-03 | 95.0 | 恒等式不可算（无 S） | — | 0.5003 | 3.253 | 0.154 |
| DTMB5415-04 | 210.0 | 恒等式不可算（无 S） | — | 0.4527 | 3.253 | 0.139 |
| KVLCC2-01 | 550000 | 恒等式不可算（无 S） | — | 7389 | 2.462e+04 | 0.300 |
| KVLCC2-02 | 650000 | 恒等式不可算（无 S） | — | 7099 | 2.462e+04 | 0.288 |
| KVLCC2-03 | 700000 | 恒等式不可算（无 S） | — | 6929 | 2.462e+04 | 0.281 |
| KVLCC2-04 | 760000 | 恒等式不可算（无 S） | — | 6840 | 2.462e+04 | 0.278 |

### T2 修复后审计（`artifacts/verifier/benchmark_audit_postfix.json`）

| case | 判决 | Rt [N] | rt_status | Fn（重算） | Ct/Cf | 关键检查 |
|---|---|---|---|---|---|---|
| SUBOFF-01..04 | PASS | 92.682 / 173.44 / 284.55 / 429.264 | derived_from_ct | 0.4589 / 0.6119 / 0.7649 / 0.9179 | 1.326 / 1.465 / 1.596 / 1.722 | ct_rt, implied_S, friction_floor 全 PASS |
| DTMB5415-01..04 | WITHHELD（声明） | null | unavailable_no_wetted_surface | 0.3434 / 0.6867 / 1.3735 / 2.0602 | 1.602 / 1.515 / 1.554 / 1.791 | froude_physical FAIL（-03/-04）→ 整族声明 withheld |
| KVLCC2-01..04 | PASS | null | unavailable_no_wetted_surface | 0.1285 / 0.1377 / 0.1423 / 0.1469 | 1.982 / 2.142 / 2.222 / 2.302 | friction_floor, displacement_cb 全 PASS |

汇总：修复前 `{FAIL: 12}` / strict=False / 3 个预置 FAIL；修复后 `{PASS: 8, WITHHELD: 4}` /
strict=True / 预置 0 FAIL（2 WARN 已声明）。

## ④ 结果图

- `artifacts/verifier/audit_report_diagnosis.png` — 左：v1.0 由 (Rt, Ct) 反推的湿表面积 / 几何反推值
  逐算例条形图（全部落在接受带 [0.5, 2.0] 之外，0.10–0.30）；右：v1.0 `froude_number` 列与
  `V/√(gL)` 重算值的比较曲线（DTMB-5415 四例系统性偏离，隐含 L≈181 m）。
- `artifacts/verifier/audit_report_friction_floor.png` — 修复后参考 Ct 与解析 ITTC-1957 摩擦下界的
  比较曲线（Ct/Cf vs Re，三个船型分色），全部落在 [1.02, 2.5] 合理带内——说明参考 Ct 本身是物理上
  自洽的，坏的只是 v1.0 的 Rt 列。
- `artifacts/verifier/benchmark_audit_wetted_area.png`、`benchmark_audit_froude.png` — 审计器
  `--figures` 的原始出图。

（说明：本单是"参考数据 + 门禁逻辑"审计，没有场量云图这一物理对象；平台阻力服务的 8001/8002 端口
本单期间不可达（`Connection refused`），因此**未**重跑模型-Ct-vs-参考-Ct 的 15% 判决——那一步
属于下一次复现单，见 ⑥ 与"未完成部分"。）

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **判决健康 ≠ 数据健康**：门禁只比 Ct，所以 Rt 列坏了 12/12 也不影响 pass_rate——这类"没有检查
   覆盖的发布列"正是审计的靶心。
2. **单位混用是静默的**：`SHIP_GEOMETRY` 里 SUBOFF/DTMB 的 `displacement` 是 kg，KVLCC2 是 t，
   v1.0 未声明单位；若下游按 kg 解释，320 m 油船只给 307 t。v1.1 加 `displacement_unit` 显式声明
   （KVLCC2 保持 `t`，不擅自改数值语义，只在审计里记 WARN）。
3. **复制粘贴指纹**：`displacement = cb = 0.508`（= 直径）是可机器检出的坏味道，已固化成 C7；SUBOFF 预置
   现按 `ρ_fw·Cb·L·B·T` 推导 `displacement=571.1 kg` 并标 `displacement_basis`，`cb=0.508` 保留但
   降级为 `cb_status="assumed"`（潜艇包络 Cb 典型 0.50–0.65，与直径数值相同属巧合但无法排除复制，
   故审计记 WARN 而非 PASS）。
4. **不引入新数据**：缺湿表面积的 8 例，Rt 一律置 null。要补齐必须引文献 S 值并写明出处，那是
   另一个（需要来源授权的）任务，不在本单偷偷做。
5. **解析判据 ≠ 精度验证**：C1–C8 证明"参考数据自洽且物理合理"，不证明平台的 Holtrop 实现对实验
   在 15% 内；后者需要 8002 服务可用（本单不可达）。
6. 适用边界：C6 的 5.5·∇^(2/3) 只是量级屏（K_S 对油船 5.5–6.5，细长型更高），因此接受带放得很宽
   （[0.5, 2.0]）；它只能抓 3–10 倍级别的错，抓不住 10% 级别的偏差。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准（解析恒等式/解析下界） | v1.0 误差 | v1.1 误差 | <3%？ |
|---|---|---|---|---|
| SUBOFF-01..04 总阻力 Rt | `½ρV²SCt`（ρ=1000, S=5.42 m²） | 86.8% / 88.2% / 89.2% / 89.8% | 0（<1e-9，由恒等式生成） | v1.0 否；v1.1 是 |
| 湿表面积反推 S_implied | 数据集自带 S / 5.5·∇^(2/3) | 比值 0.102–0.300（即 −70%~−90%） | 4 例恒等式闭环 0%，8 例提交 WITHHELD | v1.0 否；v1.1：可验的 4 例是 |
| `froude_number` 列 | `V/√(gL)` | DTMB-5415 四例 82.2%（隐含 L=181.2 m vs 5.72 m） | 0.00%（重算后 <0.01%） | v1.0 否；v1.1 是 |
| 参考 Ct 的物理性 | ITTC-1957 摩擦下界 Cf(Re) | Ct/Cf = 1.33–2.30（本就合理） | 1.33–2.30（不变） | 是（Ct 是可信的那一列） |
| 几何一致性 ∇ vs Cb·L·B·T | 恒等式 | KVLCC2 1.6–1.8%，DTMB 14.1% | 同左（未改几何） | KVLCC2 是；DTMB 否（WARN 已声明） |
| 平台模型 Ct vs 参考 Ct（15% 门禁本身） | 实验 Ct | **未测** | **未测** | **无基准可达 → 不做结论**（8001/8002 不可达） |

诚实结论：本单清偿的是"参考数据可被信任"这一层欠账（可验项全部 <1e-9 闭合）；"模型精度本身"
这一层仍是 WITHHELD，需要阻力服务恢复后由复现单补测，**不得**用本次结果冒充 15% 精度已验证。

## 复现命令

```bash
python3 tools/verifier/audit_benchmark_reference.py --strict \
    --out artifacts/verifier/benchmark_audit_postfix.json --figures artifacts/verifier
python3 tools/verifier/migrate_benchmark_reference_v11.py   # v1.0 -> v1.1 迁移
python3 tools/verifier/make_audit_report_assets.py          # 本文表格与图
python3 -m pytest tools/verifier/tests/test_benchmark_reference_audit.py -q   # 14 passed
```