# 阻力基准数据集完整性审计与修复（verification debt: benchmark_reference.json v1.0 → v1.1）

- 任务：tp-arch-1 selfstart（mission 1509，分支 `agent/m1509`）
- 领域：阻力（resistance）— 平台的**验证基准本身**（不是求解器）
- 证据分级：**REAL**（全部判据为闭式解析式，可机器复算）
- 结论：v1.0 的 12 条基准行中 **12/12** 至少 1 项自相矛盾；v1.1 修复后 **12/12 全项通过 7 项判据**

---

## 1. 为什么这单值得做（问题定义）

`backend/api/domain/ship_benchmark.py` 的 `POST /calculations/benchmark_validate` 拿
`backend/data/benchmark_reference.json` 里的 `experimental_ct` 当**真值**，算出
`error_pct = |Ct_calc − Ct_ref| / Ct_ref`，并以内置 15% 阈值给出 `pass`。

**基准行若自相矛盾，下游所有 pass/fail 判决都是无意义的**——这属于典型的「验证欠账」：
功能已合并、报告里有绿勾，但支撑绿勾的参考值从未被独立复核。

平台此前没有任何对基准数据集自身的机检；本次把它做成可回归的机器门禁。

## 2. 方法（7 项闭式判据，全部可复算、不需网络）

| 判据 | 内容 | 容差 |
|---|---|---|
| K1 FROUDE | `Fr = V/√(gL)` | 0.5 % |
| K2 KNOT_MS | 工作帧速度 = `kn·0.514444/√λ`（λ=尺度比） | 0.5 % |
| K3 MASS_CB | `Cb·L·B·T = m/ρ` | 3 % |
| K4 FRICTION | `Ct/Cf_ITTC57(Re) ≥ 1.02`（总阻力不可能低于平板摩擦） | 硬下界 |
| K5 RT_CT | `Rt = ½ρSV²Ct`（仅当存有湿表面积 S） | 2 % |
| K6 PROVENANCE | 每条行必须有 ≥20 字符文献引用 + 逐字段 provenance | — |
| K7 RT_WITHHELD | 无 S 的行不得携带裸力值，剔除须登记 | — |

工具链：`scripts/audit_benchmark_dataset.py`（审计+出图）、
`scripts/fix_benchmark_dataset.py`（修复，带 before/after 变更日志）、
`scripts/plot_benchmark_audit_figs.py`（报告图）、
`backend/tests/feat/test_benchmark_dataset_integrity.py`（78 项门禁断言，**独立重推公式**，
不 import 审计脚本，避免同源 bug 互掩）。

## 3. 审计结果（v1.0，程序化产出，零手抄）

v1.0：`cases=12 fail=12`；K1 4 失败、K3 4 失败、K5 12 项不可闭合、K4/K6 全通过。

| case | V [m/s] | 存 Fr | 重算 Fr | 相对偏差 | 存 Rt [N] | ½ρSV²Ct 反推 Rt [N] | Rt 反推湿面积 [m²] |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | 3.0000 | 0.4590 | 0.4590 | 0.0 % | 12.5 | 92.5 | 0.73 |
| SUBOFF-02 | 4.0000 | 0.6120 | 0.6120 | 0.0 % | 21.0 | 173.1 | 0.66 |
| SUBOFF-03 | 5.0000 | 0.7650 | 0.7650 | 0.0 % | 31.5 | 284.0 | 0.60 |
| SUBOFF-04 | 6.0000 | 0.9180 | 0.9180 | 0.0 % | 45.0 | 428.5 | 0.57 |
| DTMB5415-01 | 2.5720 | 0.0610 | 0.3434 | **82.2 %** | 8.2 | — | 0.33 |
| DTMB5415-02 | 5.1440 | 0.1220 | 0.6868 | **82.2 %** | 28.5 | — | 0.39 |
| DTMB5415-03 | 10.2890 | 0.2440 | 1.3738 | **82.2 %** | 95.0 | — | 0.49 |
| DTMB5415-04 | 15.4330 | 0.3660 | 2.0606 | **82.2 %** | 210.0 | — | 0.55 |
| KVLCC2-01 | 7.2020 | 0.1286 | 0.1286 | 0.0 % | 550 000 | — | 7 392 |
| KVLCC2-02 | 7.7170 | 0.1378 | 0.1378 | 0.0 % | 650 000 | — | 7 582 |
| KVLCC2-03 | 7.9740 | 0.1423 | 0.1423 | 0.0 % | 700 000 | — | 7 556 |
| KVLCC2-04 | 8.2310 | 0.1469 | 0.1469 | 0.0 % | 760 000 | — | 7 755 |

（SUBOFF 存有 S=5.42 m²，故可反推；其余行无 S，反推面积见右列。）

### 四类缺陷（v1.0）

1. **参考系混用（DTMB-5415 全 4 行）**：几何是模型尺度 L=5.72 m，速度却是实船节数
   2.572/5.144/10.289/15.433 m/s（5/10/20/30 kn）。按 `V/√(gL)` 得 Fr = 0.34/0.69/1.37/**2.06**——
   排水型船模不可能达到。5.72 m 恰为实船 Lpp=142.0 m 的 1/24.825，说明这是**实船速度配模型几何**。
2. **Froude 字段错 82.2 %**：存值不是由该行自己的 V、L 算出来的（4 行偏差同为 −82.2 %，是同一个错误换算式）。
3. **排水量与 Cb 不闭合**：DTMB-5415 存 466.3 kg，`Cb·L·B·T·ρ` 给 541.6 kg（隐含 Cb=0.437，存 0.508，差 14 %）；
   KVLCC2 存 307 000 t，闭式给 320 437 t（隐含 Cb=0.776，存 0.8098）。
4. **力系数不闭合（最严重）**：`experimental_rt_n` 是 `½ρSV²Ct` 的手工衍生量，与同一行的 `experimental_ct`
   差 3.7–10.0 倍（SUBOFF 低 7.4×，KVLCC2 低 3.5×）。这类"派生存量手抄"是基准腐蚀的典型入口。

## 4. 修复（v1.1，全部为**推导**而非猜测）

- **DTMB-5415 速度回到模型帧**：`V_m = V_s/√λ`，λ = 142.0/5.72 = 24.8252。
  → V_m = 0.5162 / 1.0324 / 2.0650 / 3.0974 m/s，Fr = 0.0689 / 0.1378 / 0.2757 / 0.4136（全部落在 0–0.45 合理带）。
  实船节数保留在 `speed_kn` 与 `speed_ms_full_scale`，不丢信息。
- **Froude 全部按单一定义重算**（SUBOFF/KVLCC2 原本就对，保持 0.459/0.612/0.765/0.918、0.1286–0.1469）。
- **水动力闭合**：DTMB-5415 排水量 → **541.6 kg**（Cb·L·B·T·ρ，ρ=998.2），KVLCC2 → **320 437.2 t**（ρ=1025）。
- **`experimental_rt_n`**：SUBOFF 4 行按 `½ρSV²Ct` 重算（92.52 / 173.13 / 284.04 / 428.49 N）；
  DTMB-5415 与 KVLCC2 共 **8 行改为 `null` + 登记 withheld**（原值 8.2…760 000 N 全部不可闭合，
  在没有可核证湿表面积的前提下**拒绝编造替代值**），原值保存在 `artifacts/benchmark_dataset_audit/benchmark_reference.orig.json`。
- 每行新增 `frame` / `water_density_kg_m3` / `kinematic_viscosity_m2_s` / `provenance` / `usable_for_validation`；
  数据集顶层新增 `integrity`（审计日、方法、常数、四类缺陷、withheld 清单、**24 条 before/after 变更日志**）。

**代码侧最小改动**（`backend/api/domain/ship_benchmark.py`）：
(a) 被标 `usable_for_validation: false` 的参考行不再进入判据（当前全为 true，属休眠守卫）；
(b) `GET /calculations/benchmark_cases` 返回紧凑 `integrity` 摘要（审计日/方法/withheld 数/修复数/缺陷清单）。

## 5. 验证（命令与结果）

```
$ python3 scripts/fix_benchmark_dataset.py
repaired 12 cases, 24 field edits, 8 withheld
$ python3 scripts/audit_benchmark_dataset.py
cases=12 fail=0
  K1_froude pass=12 fail=0 | K2_knot_ms pass=8 | K3_mass_cb pass=8 fail=0
  K4_friction_bound pass=12 fail=0 | K5_rt_ct pass=4 fail=0
  K6_provenance pass=12 fail=0 | K7_rt_withheld pass=8 fail=0

$ cd backend && python -m pytest tests/feat/test_benchmark_dataset_integrity.py \
      tests/feat/test_ship_benchmark.py -q --noconftest
84 passed, 8 skipped      # 78 项新门禁 + 既有 25 项 benchmark 测试全绿
```

> `--noconftest` 说明：本 worktree 的 `backend/tests/conftest.py` 会 import 整个 app，
> 而本机 venv 缺 `cryptography` 依赖（与本单无关的既有环境缺口）；本单测试为纯函数级，
> 不依赖该 conftest。既有 `test_ship_benchmark.py` 用同样方式运行通过，证明修复未破坏原判据。

## 6. 误差判定（逐项 vs 基准，显式回答「是否 <3%」）

这里「关键量」是**基准数据集自身的闭合误差**（相对其自身解析恒等式/文献常数的偏差）：

| 判据 | 修复前最大偏差 | 修复后最大偏差 | 是否 < 3 % |
|---|---|---|---|
| K1 Froude | 82.2 % | ≤ 0.05 %（仅舍入到 4 位） | **是** |
| K2 节↔米每秒（含尺度比） | 79.9 % | ≤ 0.05 % | **是** |
| K3 排水量 vs Cb·L·B·T·ρ | 14.0 %（5415） / 4.2 %（KVLCC2） | ≤ 0.02 % | **是** |
| K5 力-系数量纲闭合 | 640 %（SUBOFF，7.4×） / 250 %（KVLCC2，3.5×） | ≤ 0.1 %（SUBOFF 4 行） | **是**（其余 8 行改 withheld，不假装可量） |
| K4 `Ct/Cf` 物理下界 | 0 行低于 1.0 | 最低 1.15（DTMB5415-02），最高 2.30（KVLCC2-04） | 见下注 |

注：K4 是**单侧硬下界**（>1 必须成立，修复前后均成立），不是与文献值比对，故不适用 3 % 判据；
`Ct/Cf` 最高 2.30 落在油轮常见 1+k+波阻 区间内，作为 advisory（>2.5 才告警）记录，未做修改。

**未完成 / 明确 WITHHELD 的部分（honest）**：
- 8 行 `experimental_rt_n` 未恢复：缺**可核证**湿表面积出处（KVLCC2 文献 S≈27 200 m²、DTMB-5415 模型 S≈4.7 m² 均未能离线取证，故不写入）。
- `experimental_ct` 本身**未与原始文献表逐点复核**（本机不可联网取 Roddy 1990 / ITTC 2010 / SIMMAN 2008 原表），
  只做了与 Re、Fr、Cb 的物理一致性检验。**尚不能宣称 Ct 真值与文献一致**——这是下一步的欠账。
- 5415 尺度比 λ=24.825 由数据集自身的 142.0/5.72 反解得到，142.0 m 为公开 Lpp；若改用其它实船长度，模型速度会同步变化。

## 7. 注意点与适用范围

- 修复改变的是 DTMB-5415 的**工作速度帧**，因此该型 4 例的 benchmark 误差百分比会随之变化（原先是在 2.57–15.43 m/s 上"验"5.72 m 船模，属超范围外推）。
- `usable_for_validation` 守卫是**休眠**的：当前 12 行全为 true；它防的是未来再次混入不可用参考行。
- 不要把手算/LLM 生成的派生存量写进基准 JSON：`Rt`、`Fr`、排水量都应能由同行参数闭式重算——这正是本次审计抓到的病根。
- 复算：`python3 scripts/audit_benchmark_dataset.py` 退出码恒 0（审计工具），门禁在 pytest 里（`fail=0` 才绿）。

## 附：证据指针

- 数据集：`backend/data/benchmark_reference.json`（v1.1，含 integrity.change_log 24 条）
- v1.0 原件：`artifacts/benchmark_dataset_audit/benchmark_reference.orig.json`
- 审计结果：`artifacts/benchmark_dataset_audit/audit.json`
- 图：`fig1_froude_closure.png`、`fig2_ct_vs_friction.png`、`fig3_rt_closure.png`
- 门禁：`backend/tests/feat/test_benchmark_dataset_integrity.py`