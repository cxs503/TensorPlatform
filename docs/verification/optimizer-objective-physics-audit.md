# 优化器目标函数物理内核复核（m1610：委托正典 Holtrop-1984）

> 本文件取代 m1601 的 `docs/verification/optimizer-objective-mock-audit.md`：
> 该文判 MOCK，其结论自 m1610 起作废，仅作历史记录。

## ① 案例介绍

KCS（KRISO Container Ship）模型尺度艏部型线优化：CMA-ES 在 8 维艏部变形参数空间内
最小化设计 Froude 数下的总阻力系数 Ct。m1601 复核发现目标函数实际是裸 ITTC-57 摩擦线
（Ct ≡ Cf，Cr/Cf ~ 3e-5 vs 正典 0.335；Ca 以 [m²] 量加到 [N]），证据等级 MOCK。
m1610 把物理内核改为**委托**正典 Holtrop-Mennen (1984)（`backend/api/holtrop1984.py`，provenance `worktree`，sha256=7449b33090984ef9），旧手拟链条只作 fallback 并继续标注 MOCK。

| 量 | 值 | 来源 |
|---|---|---|
| L / B / T | 7.278 / 1.019 / 0.342 m | `kcs_bow_param.KCS_BASELINE` |
| Cb | 0.651 | 同上（p4 修正后） |
| Cm / Cwp / Cp | 0.9843 / 0.7673 / 0.6614 | 正典 `estimate_cm` / `estimate_cwp` |
| LCB | -0.7 %L（中后为负=前） | LCB_BASELINE_PCT + p6/p7 通路 |
| ∇ | 1.6512 m³（@1025 → 1692.46 kg） | ∇=Cb·L·B·T |
| 球鼻艏 Abt / hb | 0.030872 m² / 0.1539 m | Abt/Am=0.09、hb/T=0.45 基准 + p4/p5 |
| 设计 Fn / V | 0.26 / 2.1969 m/s | V=Fn·√(gL) |
| 流体 | ρ=1025 kg/m³，ν=1.190e-06 m²/s | 两轨同一流体，只比方法 |

## ② 怎么做的（方法/流程/关键设置/工具链）

1. **单实现原则**：`optimizer/interfaces/canonical_bridge.py` 按 `worktree → git:<ref>` 顺序装载正典内核，
   `resistance_eval.holtrop_resistance()` 直接调用 `holtrop1984_resistance()`；
   报告 1144 已裁决多分支漂移的代价，故优化器侧不再出现第二份 Holtrop 系数链。
2. **几何先验证**：`kcs_bow_param` 输出 cm/cwp/LCB/∇/Abt/hb，8 个艏部参数只通过方法中
   真实存在的通路生效（Cwp 满度、Abt、hb、LCB）；deck camber 在 Holtrop-1984 里没有
   静水阻力通路，显式置为惰性，而不是像 m1601 那样用手工乘子伪造灵敏度。
3. **双轨判据**：live 轨 = 实际目标函数（正典委托）；legacy 轨 = 强制旧手拟 fallback。
   同一套 C1~C7 分别判级，legacy 轨固定失败项 {C3,C4,C5,C6}，作为“退回旧链条”的回归钉子。
4. **七条独立判据**：几何恒等式、湿面积夹逼+异实现、量纲一致性、残余阻力量级、总阻力误差、
   物理下界、速度极限/单调性；每条都可被变异体单独判负（见 `tests/test_optimizer_objective_audit.py`）。
5. **复算**：`python3 tools/audit/optimizer_objective_audit.py --write`；漂移闸 `--check`。
   文中数字全部由本工具注入，零手抄。

## ③ 计算结果（程序化注入）

**live 目标函数证据等级：REAL**（7/7 条可判，FAIL=无，WITHHELD=无）
**legacy fallback 证据等级：MOCK**（FAIL=['C3', 'C4', 'C5', 'C6']）

| 判据 | 名称 | live | legacy |
|---|---|---|---|
| C1 | 几何恒等式 Cb·L·B·T / 标定域 | PASS | PASS |
| C2 | 湿表面积（夹逼 + 异实现） | PASS | PASS |
| C3 | 相关补贴 Ca 量纲一致性 | PASS | FAIL |
| C4 | 残余阻力显著性 Cr/Cf（量级仲裁） | PASS | FAIL |
| C5 | 总阻力 Ct / Rt vs 正典 Holtrop-1984 | PASS | FAIL |
| C6 | 物理下界 Ct ≥ Cf 且 Ct ≥ (1+k)Cf | PASS | FAIL |
| C7 | 速度极限 Rt(V)↑ / Ct(V)↓（摩擦主导域） | PASS | PASS |

关键量对照（KCS 模型，设计点 Fn=0.26）：

| 量 | live 目标函数 | legacy fallback | 正典参考 |
|---|---|---|---|
| Ct [-] | 0.00485084 | 0.00285303 | 0.00485084 |
| Rt [N] | 109.45 | 70.661 | 109.45 |
| S_wet [m²] | 9.1219 | 10.013 | 9.1219 |
| Cr/Cf [-] | 0.229188 | 3.285363e-05 | 0.229188 |
| Ca（无量纲） | 7.896809e-04 | 1.148016e-06 | 7.896809e-04 |
| (1+k) | 1.18563 | 1 | 1.18563 |

目标函数输出 JSON 携带 `objective_evidence_level=REAL`、`objective_physics_source=backend/api/holtrop1984.py::holtrop1984_resistance (Holtrop-Mennen 1984, worktree, sha256=7449b33090984ef9)`
（见 `optimizer/runs/opt_run_holtrop1984/optimization_result.json`）。

## ④ 图

`docs/verification/figures/optobj_fig1_ct_vs_v.png`（Ct(V)：live / legacy / 正典 / 摩擦下界）
与 `optobj_fig2_components.png`（分量分解，symlog）。

## ⑤ 注意点（坑/局限/适用范围）

- **委托而非重写**：优化器侧不允许出现第二份 Holtrop 系数链；任何“为了让优化跑通”而就地
  改系数的改动都会重新打开报告 1144 的漂移风险，必须走正典 + 审计。
- **fallback 仍是 MOCK**：正典内核不可用时（未合入该文件的分支）会退回旧链条，并在
  `objective_evidence_level` 标注 MOCK、`fallback_reason` 记录原因；下游不得当 REAL 引用。
- **参数灵敏度变小是预期的**：m1601 的“改善 %”来自人工乘子；改为物理通路后，静水阻力对
  deck camber / shoulder 曲率的响应本就接近零，优化收益必然下降——这是保真度的代价。
- **位移约束仍是 mock**：`generate_geometry` 的 ±5% 排水量与水密性判据仍是 m546 的 mock
  逻辑（不在本单范围），故 p4（球鼻艏体积）在大幅度时会被判无效。
- **与 m1601 的 44.3%% 对齐**：m1601 的正典参考解不含球鼻艏（Abt=0），本单的目标函数
  携带 Abt/hb（KCS 有球鼻艏），故 legacy 的 Ct 误差为 41.18%；两者都远超 15% 门。
- **正典自身局限**：Ca 的 L<50 m 修正未实现（模型尺度 L=7.278 m 属外插），标定域为
  Cb 0.55~0.85、Fn≤0.4；本复核只覆盖 KCS 位移船工况。

## ⑥ 误差判定

- live Ct 相对误差 **0%** vs 正典 Holtrop-1984 → **是否 <3%：是**（委托同一内核，误差为数值同源）
- live Rt 相对误差 **0%**（同一判据的力形式）。
- legacy Ct 相对误差 **41.18%**、Rt **35.44%** → 维持 MOCK。
- legacy 相关补贴系数误差 **99.85%**（量纲错误，远超 15% 门）。
- legacy Cr/Cf 量级比 **1.433482e-04**（要求 0.5~2）。
- **结论**：live 目标函数七条判据全过 → 证据等级 **REAL**；legacy fallback 维持 **MOCK**，
  优化器结果 JSON 必须按 `objective_evidence_level` 字段引用。

## ⑦ 复现

```
python3 tools/audit/optimizer_objective_audit.py --write   # 生成 JSON+MD+图
python3 tools/audit/optimizer_objective_audit.py --check   # 漂移闸（复算==入库）
python3 -m pytest tests/test_optimizer_objective_audit.py -q
```
