# 基准库完整性审计（验证欠账清偿）—— m1407

> 类型：验证欠账（对已合并功能的参考基准复核）｜只读扫描 + 最小修复｜2026-11-01
> 工具：`tools/audit/baseline_integrity.py`（可当 CI 门禁）、`tools/audit/normalize_benchmark_reference.py`
> 测试：`backend/tests/test_baseline_integrity.py`（13 passed）

## 1. 为什么审「基准」而不是审「算法」

平台上所有精度结论都是「计算值 X 对参考值 Y 误差 < tol」。这条链的强度不超过参考集本身。
本轮先审计参考集，用四条对任何物理自洽基准都必须成立的恒等式，无需任何求解器：

- **C1** `Ct = Rt / (0.5·ρ·S·V²)`
- **C2** `Fr = V/√(gL)`
- **C3** `V[m/s] = V[kn]·0.514444`
- **C4** 物理性：`Ct ≥ (1+k_min)·Cf_ITTC57(Re)`（(1+k_min)=1.03）
- **C5** 跨库一致：`services/ship_proxy.BENCHMARK_REFERENCE` vs `backend/data/benchmark_reference.json`
- **C6** 门禁策略：`tolerance_pct ≤ 15%`（平台精度标准）；超限即「恒过门禁」，不算门禁（PB-005）

## 2. 审计结果（修复前 → 修复后）

修复前：**verdict=FAIL，13 项失败**（`artifacts/baseline_integrity/audit_before.json`）。
修复后：**verdict=PASS，0 失败**（`artifacts/baseline_integrity/audit_after.json`）。

### 2.1 C1 恒等式：12/12 条基线的 Rt 列不可用（偏差 3.4×–9.9×）

| case | Ct-implied Rt (N) | 旧 ratio | 新 ratio | 旧 Fr 反解船长 (m) | 修正后 Fr |
|---|---|---|---|---|---|
| SUBOFF-01 | 92.68 | 0.1349 | 1.0 | 4.35 | 0.4589 |
| SUBOFF-02 | 173.44 | 0.1211 | 1.0 | 4.35 | 0.6119 |
| SUBOFF-03 | 284.55 | 0.1107 | 1.0 | 4.35 | 0.7649 |
| SUBOFF-04 | 429.26 | 0.1048 | 1.0 | 4.35 | 0.9179 |
| DTMB5415-01 | 65.66 | 0.1249 | 1.0 | 181.22 | 0.3434 |
| DTMB5415-02 | 221.78 | 0.1285 | 1.0 | 181.22 | 0.6867 |
| DTMB5415-03 | 817.24 | 0.1162 | 1.0 | 181.26 | 1.3735 |
| DTMB5415-04 | 1996.28 | 0.1052 | 1.0 | 181.25 | 2.0602 |
| KVLCC2-01 | 1911294.23 | 0.2878 | 1.0 | 317.73 | 0.1285 |
| KVLCC2-02 | 2351156.5 | 0.2765 | 1.0 | 318.76 | 0.1377 |
| KVLCC2-03 | 2594044.6 | 0.2698 | 1.0 | 316.97 | 0.1423 |
| KVLCC2-04 | 2853109.66 | 0.2664 | 1.0 | 319.6 | 0.1469 |

根因：`experimental_rt_n` 列与 `experimental_ct` 不同源（比例随航速漂移而非常数，说明不是单一密度/面积口径错），
旧值比摩擦下界对应的阻力还小一个量级——任何 Re>1e6 的水中物体都不可能，属**物理不可能值**。
而 Ct 列通过 C4 物理性检查（Ct/Cf = 1.33–2.30），故以 Ct 为可信列、Rt 由 Ct 恒等式反算。

### 2.2 C2 Froude：DTMB5415 四条全部错，反解船长恒为 181.2 m

0.061/0.122/0.244/0.366 反解出的隐含船长均为 **181.2 m**，而记录几何 L=5.72 m。
即该列是用另一条船（或错误长度）算出来的常量。其中 Fr=2.060 在拖曳水池里属滑行/超空泡区，
物理上不可能出现在 DTMB5415 的基准工况里。修正后用记录几何重算：0.343/0.687/1.374/2.060。
副作用：`api/domain/ship_benchmark.py:196` 用 `froude_number > 0.4` 判定 SUBOFF honest-FAIL 区，
错误的 Fr 列会让该判据失真。

### 2.3 C5 跨库一致：门禁库继承了坏值

`ship_proxy.BENCHMARK_REFERENCE` 原先硬编码 `suboff_model.reference_rt = 15 N`（继承坏 Rt 列，
Ct 恒等式应为 **92.68 N**，差 6.2×）；`kvlcc2_full.reference_rt = 2500 kN` 与 Ct 恒等式
（2351 kN）差 6%，方向正确但同样是手抄值。现两库均改为**派生值**，C5 偏差 0.0%。

### 2.4 C6 门禁策略：300% 容差 = 假门禁，已拆除

`suboff_model` 原 `tolerance_pct = 300`，任何实现都能过——等于没有门禁，且旧测试
`test_suboff_honest_fail` 还把 300% 断言为「正确行为」，把缺陷写成了契约。
现改为 `gate_enabled = False` + `status = honest_fail_not_a_gate`，`benchmark_validate` 对它输出
**WITHHELD / pass=None**，不再打印假绿灯。同一策略也拦住「上游缺键默认取 0」这类伪结果
（0 会被算成 100% 误差，看似失败实为静默错）。

### 2.5 稳性基准：手抄 3.5 m vs 解析 6.804 m

`stability.generic_barge`（L=80, B=20, T=4, Cb=0.85, KG=5.0）墙侧公式解析值：
KB=T/2=2.000 m，BM=I_wp/∇=B²/(12·T·Cb)=9.804 m，**GM=6.804 m**。旧参考值 3.5 m 低 94%。
即一个**正确**的稳性实现也会被判 FAIL——基准错比算法错更危险，因为它把正确实现判成错误。

## 3. 修复内容（最小改动）

| 文件 | 改动 |
|---|---|
| `tools/audit/baseline_integrity.py` | 新增：C1–C6 审计器，JSON/Markdown 输出，非零退出即失败 |
| `tools/audit/normalize_benchmark_reference.py` | 新增：派生列重算，旧值保留在 `*_as_listed_20261031` |
| `backend/data/benchmark_reference.json` | Rt 由 Ct 恒等式重算、Fr 用记录船长重算、补 S/ρ/provenance |
| `backend/services/ship_proxy.py` | `derive_rt_from_ct` / `wall_sided_gm` 派生参考值；15% 容差策略；gate_enabled；缺键 WITHHELD |
| `backend/tests/test_baseline_integrity.py` | 新增 13 项回归（含「门禁必须能失败」的反向测试） |
| `backend/tests/test_ship_proxy.py` | 旧 `test_suboff_honest_fail` 改为 `..._not_a_gate`（不再断言 300%） |

## 4. 图

- `figures/fig1_ct_fr_envelope.png`：参考 Ct 点 vs 摩擦下界 1.03·Cf（物理性包络，双对数）。
- `figures/fig2_identity_before_after.png`：C1 恒等式比值修复前后对比（12 条全部回到 1.000）。

## 5. 局限

- DTMB5415 / KVLCC2 无实测湿表面积，S 用 Holtrop(1982) 估算（±2–5%），C1/Rt 派生带该不确定度；SUBOFF 用实测 S=5.42 m²。
- Ct 只做物理性与恒等式检查，未与 Roddy/SIMMAN 原文逐点比对（无离线文献全文），故这一层仍是 **WITHHELD**，不可宣称「基准已对标文献」。
- 稳性用墙侧公式，对 Cb=0.85 驳船近似误差约 1%；非墙侧船型需换静水力积分。
- 未触上游 8002 服务（本轮负载 load15=117，只做轻量）；`call_ship_api` 仍不可达，端到端 `benchmark_validate` 实跑留待服务恢复。
