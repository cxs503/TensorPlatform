> **m1614 收编说明（正典 v3.0 优先）**
> 本页来自被收编的分支 `agent/m1383`。其**数据判定面**（哪条参考行可作判据）已被正典 v3.0
> （`backend/data/benchmark_reference.json` + `services/benchmark_canon.py`，#272）取代：
> SUBOFF 4 行在淡水 998.2 kg/m³ 下 Rt=92.52 N 定谳为**可复核**，DTMB-5415 / KVLCC2 8 行因
> 缺实测湿面积而 **WITHHELD**。文中若出现「12/12 WITHHELD」「SUBOFF Ct-Rt 差 7.4x」
> 之类的结论，属收编前记录，**以正典为准**；机制部分（工具/端点/门禁）已保留并适配正典 API。

# 基准真值完整性门禁（Benchmark Reference Integrity Gate）

> 单号：m1383（platform 席自找活） · 分支：agent/m1383
> 产物：`tools/audit/benchmark_reference_integrity.py`、`backend/data/benchmark_reference.json`（integrity 块）、
> `backend/api/domain/ship_benchmark.py`（消费侧门禁）、`backend/tests/test_benchmark_reference_integrity.py`

## ① 案例介绍（物理背景 / 几何 / 参数）

平台「对标算例」的唯一真值表 `backend/data/benchmark_reference.json` 供给
`POST /calculations/benchmark_validate`，用来给阻力计算结果打 pass/fail，并喂给指南 KPI
「对标算例 ≥10（SUBOFF/DTMB/KVLCC2）」。数据集声称引自 Roddy 1990（SUBOFF）、
ITTC V&V Workshop（DTMB-5415）、SIMMAN 2008（KVLCC2），共 12 条记录、3 个船型：

| 船型 | 几何（声明） | 速度范围 | 介质 |
|---|---|---|---|
| SUBOFF（DARPA AFF-1 裸艇） | L=4.356 m, D=0.508 m, S=5.42 m² | 3.0–6.0 m/s | 淡水（模型） |
| DTMB-5415（Arleigh Burke 无桨模型） | L=5.72 m, B=0.759 m, T=0.246 m | 2.57–15.43 m/s（报表） | 淡水（模型） |
| KVLCC2（KRISO 油轮） | L=320 m, B=58 m, T=20.8 m, Cb=0.8098 | 7.20–8.23 m/s | 海水（实船） |

问题：真值本身是否满足阻力基本恒等式？若不满足，任何「误差 ≤15%」的判决都无意义。

## ② 怎么做的（方法 / 判据 / 工具链）

新增 `tools/audit/benchmark_reference_integrity.py`，对每条算例做**独立物理恒等复核**，
不调用求解器、不产生算力负载（load15≈118 的容量纪律下仍可跑）：

- **C1 Froude 恒等**：Fr == V/√(gL)，相对偏差 ≤2%
- **C2 阻力恒等**：Rt == Ct·½ρV²S。由报表 Rt/Ct 反推湿表面积 S_impl，与引用 S 比较（±25%）。
  引用 S 优先取算例自带的 `wetted_surface_m2`；缺失时用 Holtrop & Mennen (1982) 湿表面积公式
  估计，假设（CM / CWP / 介质）逐条写入产物，不隐藏自由度。
- **C3 摩擦下限**：Cf 用 ITTC-1957 `0.075/(log10 Re − 2)²`；Ct/Cf 必须落在 [0.9, 3.5]
- **C4 趋势**：同船型内 Ct 不得随 Fr 显著下降（容差 5%）
- 介质按 L>50 m 判实船（海水 ρ=1025, ν=1.19e-6），否则模型（淡水 ρ=998.2, ν=1.139e-6）

判定：`REAL`（全过且 S 为引用值）/ `DERIVED`（全过但 S 为估计）/ `WITHHELD`（C1–C3 任一失败）。
`--apply` 把判定回填数据集，原始值降级为 `legacy_rt_n` / `legacy_froude_number`（**不改写实验值**）。
消费侧同步加门禁：`pass = (error ≤ 15%) AND truth != WITHHELD`，且 `scored=False` 的条目不进 `pass_rate`。

## ③ 计算结果

判定分布：`{'REAL': 0, 'DERIVED': 0, 'WITHHELD': 12}`（可作真值：0/12）

| case | 船型 | 判定 | 反推S/引用S | Rt 报表 (N) | Rt 由 Ct 重算 (N) |
|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | WITHHELD | 0.1351 | 12.5 | 92.52 |
| SUBOFF-02 | SUBOFF | WITHHELD | 0.1213 | 21.0 | 173.13 |
| SUBOFF-03 | SUBOFF | WITHHELD | 0.1109 | 31.5 | 284.04 |
| SUBOFF-04 | SUBOFF | WITHHELD | 0.105 | 45.0 | 428.49 |
| DTMB5415-01 | DTMB-5415 | WITHHELD | 0.1245 | 8.2 | 65.89 |
| DTMB5415-02 | DTMB-5415 | WITHHELD | 0.1281 | 28.5 | 222.55 |
| DTMB5415-03 | DTMB-5415 | WITHHELD | 0.1158 | 95.0 | 820.09 |
| DTMB5415-04 | DTMB-5415 | WITHHELD | 0.1048 | 210.0 | 2003.22 |
| KVLCC2-01 | KVLCC2 | WITHHELD | 0.2787 | 550000.0 | 1973298.15 |
| KVLCC2-02 | KVLCC2 | WITHHELD | 0.2678 | 650000.0 | 2427429.9 |
| KVLCC2-03 | KVLCC2 | WITHHELD | 0.2614 | 700000.0 | 2678197.49 |
| KVLCC2-04 | KVLCC2 | WITHHELD | 0.258 | 760000.0 | 2945666.83 |

关键比值：最接近恒等的是 KVLCC2-01（反推/引用 = 0.2787），
最差的是 DTMB5415-04（0.1048）。
C4 告警：2 处 DTMB-5415 内部 Ct 随 Fr 下降（真实无桨驱逐舰模型不会出现）。

## ④ 结果图

见 `artifacts/benchmark_integrity/benchmark_integrity.png`：
左图 = 12 条算例的反推/引用湿表面积比（对数轴，绿带为 ±25% 容差），全部落在 0.10–0.28；
右图 = 报表 Fr vs 几何复算 Fr 的 parity 图，DTMB-5415 组偏离 y=x 达 5.6 倍（报表 Fr 对应 L≈181 m，
而几何声明 L=5.72 m —— 量纲/尺度混用），KVLCC2 组落在线上（说明问题只出在 Rt/S 列）。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **不能靠「Froude 对得上」放行**：KVLCC2 组 C1 全过、C2 全败，说明 C1 只是必要条件。
2. **不改写实验值**：Ct 是唯一无量纲可引用的量，S 已知时可反推 Rt；但 Ct 本身溯源未能核验，
   因此只给 `rt_derived_from_ct_n` 供修复参考，不把数据集自行「修好」冒充 REAL —— honest 优先。
3. **湿表面积估计的容差**：Holtrop 公式默认 CM/CWP 由 Schneekluth 近似给出，误差约 ±10–20%，
   故 C2 用 ±25% 容差带；实测失配 3.6–9.5 倍，远超该不确定性，结论稳健。
4. **DTMB-5415 的 CM=0.85 是假设值**，未从型线核验；该假设只影响 S 估计 ±10% 量级，不影响判定方向。
5. **环境限制**：本机缺 `psycopg2` / `cryptography` 且无外网，`backend/tests/conftest.py`（导入 `main.app`）
   在该容器内无法加载，故新测试以 `--noconftest` + 文件路径加载模块运行；这是既有环境缺口，非本单引入。
6. **C4 只作告警**，不参与 `WITHHELD` 判定（避免单点误杀）。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 实测偏差 | 是否 <3% |
|---|---|---|---|
| ITTC-57 Cf(Re=1e7) | 0.002934（文献平板线） | 0.002934 | ✅ <3%（引擎自检，测试锁定） |
| Holtrop S（KVLCC2 主尺度） | 27,194 m²（公开值） | 偏差见测试 `test_holtrop_wetted_surface_kvlcc2_band` 断言 <10% | ✅ <10%（估计量，非判定量） |
| Fr 恒等（SUBOFF/KVLCC2 10 条） | V/√(gL) | ≤0.4% | ✅ <3% |
| Fr 恒等（DTMB-5415 4 条） | V/√(gL) | 462%（0.061 vs 0.343） | ❌ 远超 3% → WITHHELD |
| Rt 恒等（全 12 条） | Ct·½ρV²S | 反推/引用 S = 0.105–0.279（即 Rt 偏小 3.6–9.5 倍） | ❌ 远超 3% → WITHHELD |

结论：**数据集 12/12 条不得作为验收真值**；已建机器门禁，使「伪真值授予 PASS」在机制上不可能
（回归测试 `test_perfect_hit_on_withheld_reference_never_passes` 证明 error=0% 也不给 PASS）。

## 后续（给下一单的线索）

1. 按 citation 逐条回填真值：SUBOFF 用 Roddy 1990 报告表格的 Rt（模型 4.356 m，淡水）；
   KVLCC2 用 SIMMAN 2008 / KRISO 的 R_T（实船 + 模型双档）；DTMB-5415 修复 L（模型 5.72 m 或实船 142 m）与速度口径。
2. 回填后重跑 `--apply`，判定自动升级 REAL/DERIVED，KPI「对标算例 ≥10」才有真实含义。
3. 同样的物理恒等门禁可复用到冰区（SIR）与推进（KT/KQ）真值表。
