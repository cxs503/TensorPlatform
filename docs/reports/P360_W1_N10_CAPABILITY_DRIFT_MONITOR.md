# P360-W1-N10 能力回归检测（Capability Drift Monitor）成熟化报告

> 仓 TensorPlatform · 分支 `agent/m11318` · 组件 `tools/capability_drift` · 生成时间 2026-11-10T22:28:00+00:00
> 判据来源：战役节点 `p360w1-n10-capability-drift`（机器可算判据 A1–A8）
> 验收结论：**✅ PASS**（逐条判据见 §3）

## 1 案例介绍

**问题**：平台/领域仓的能力（求解器内核 + 验证案例 + 基线数字）会随代码变更悄悄退化——测试仍绿、PR 仍合，
但关键数字（柔度、应力强度因子、能量…）漂了。人工比对不可持续，需要机制化的一条链：
「改完自动重跑 → 与基线比 → 超阈告警 → 定位到 commit」。

**本轮的受检对象**是合成数值核（零领域语义铁律：TensorPlatform 不放船舶域词条/算例）：
一维阻尼 Jacobi 迭代求解 `-u''=f`，固定 60 次扫掠后取离散 Dirichlet 能量与峰值两个指标。

$$u_i^{(k+1)}=(1-\omega)u_i^{(k)}+\omega\cdot\tfrac12\left(u_{i-1}^{(k)}+u_{i+1}^{(k)}+h^2 f\right),\qquad
E=\tfrac12 h\sum_i\left(\frac{u_{i+1}-u_i}{h}\right)^2$$

- 参数表：10 个能力各有一条参数行 `RELAX_C00..RELAX_C09 = 0.60`（退化注入口），网格 n=24..40、60 次扫掠；
- 每个能力 1 个 pytest 验证案例（首个能力带 2 个指标 energy/peak）；
- 判据：每能力每指标相对误差阈值 `rel_tol = 0.05`（gates.json，可调）；
- "物理"意义仅在于：结果对参数敏感（0.60→0.65 使能量漂 ~12%），因此注入退化可被真实检出。

能力注册表（由 `artifacts/capability_drift/registry_snapshot/registry.json` 注入）：

| 能力 | 求解器 | 案例（pytest node） | 指标 |
|---|---|---|---|
| `synth.solver.energy_00` | synth | `verif/test_cap_00.py::test_cap_00` | `energy_00`, `peak_00` |
| `synth.solver.energy_01` | synth | `verif/test_cap_01.py::test_cap_01` | `energy_01` |
| `synth.solver.energy_02` | synth | `verif/test_cap_02.py::test_cap_02` | `energy_02` |
| `synth.solver.energy_03` | synth | `verif/test_cap_03.py::test_cap_03` | `energy_03` |
| `synth.solver.energy_04` | synth | `verif/test_cap_04.py::test_cap_04` | `energy_04` |
| `synth.solver.energy_05` | synth | `verif/test_cap_05.py::test_cap_05` | `energy_05` |
| `synth.solver.energy_06` | synth | `verif/test_cap_06.py::test_cap_06` | `energy_06` |
| `synth.solver.energy_07` | synth | `verif/test_cap_07.py::test_cap_07` | `energy_07` |
| `synth.solver.energy_08` | synth | `verif/test_cap_08.py::test_cap_08` | `energy_08` |
| `synth.solver.energy_09` | synth | `verif/test_cap_09.py::test_cap_09` | `energy_09` |

基线数字（标定轮实测，`baseline_calibration.json`）：

| 能力 | 基线数字 |
|---|---|
| `synth.solver.energy_00` | `energy_00`=0.003032258828, `peak_00`=0.02828689198 |
| `synth.solver.energy_01` | `energy_01`=0.002353706412 |
| `synth.solver.energy_02` | `energy_02`=0.001901356335 |
| `synth.solver.energy_03` | `energy_03`=0.0009368005661 |
| `synth.solver.energy_04` | `energy_04`=0.0008330805555 |
| `synth.solver.energy_05` | `energy_05`=0.004366452712 |
| `synth.solver.energy_06` | `energy_06`=0.001945211911 |
| `synth.solver.energy_07` | `energy_07`=0.001597667476 |
| `synth.solver.energy_08` | `energy_08`=0.001348992815 |
| `synth.solver.energy_09` | `energy_09`=0.0006884963269 |

## 2 怎么做的

**流水线**（`tools/capability_drift`，7 个模块）：

```
变更文件 ──trigger──▶ 受影响能力 ──runner──▶ 重跑案例（pytest 子进程 + JSON 指标插件）
   │                     │                        │
   │                     └── 无命中 → 不重跑        ▼
   │                                        detector：相对误差 vs 判据(5%)，白名单豁免须签名
   └── git blame 定位点 ──attribution──▶ 嫌疑 commit ──alerts──▶ 平台事件(jsonl + POST)
                                                          └──▶ dashboard：绿/黄/红 + 历史趋势 + 图
```

关键设计（都为了"可验证、不可自欺"）：

1. **指标由被测案例自己上报**（`plugin.record`），不解析 print 文本；NaN/inf 直接拒绝。
2. **单案例一个 pytest 子进程**：案例崩溃/超时只让该能力变黄（不可验证），不会污染整轮。
3. **不可验证 ≠ 绿**：案例失败、指标缺失、无基线 → 黄/标定态，绝不静默放行。
4. **豁免与基线更新都要 owner 签名**（HMAC-SHA256，key 只在环境变量 `CAPDRIFT_APPROVAL_KEY`，
   仓库零密钥；缺 key 一律拒绝 = fail-closed）；审批针对**具体数字**（`values_sha256`），不是空白授权。
5. **定位靠声明式 blame 点**：注册表为每个能力声明"哪一行的哪个模式决定这个数字"，
   `git blame` 该行 → 最后修改它的提交即嫌疑 commit（不是把整条历史都甩给用户）。
6. **验收在临时合成 git 仓里真实注入退化**（`sandbox.py`），沙箱不落工作树，工件只留 JSON/PNG 证据。

**验收设计（预声明，见 `artifacts/capability_drift/`）**：判据阈值 5%（A1/A6）、假阳性率上限 1%（A2）、
重跑预算 600 s（A3）、定位准确率下限 80%（A5）；注入幅度由灵敏度扫描按**先声明规则**选取
（小注入 = 使相对误差 <3% 的最大参数值；大注入 = 使相对误差 >8% 的最小参数值），规则写在
`sensitivity_scan.json::selection_rule`，不看结果再改阈值。

灵敏度扫描（能力 `synth.solver.energy_01`，指标 `energy_01`，基线 0.002353706412）：

| 参数值 | 观测指标 | 相对误差 | 案例状态 |
|---|---|---|---|
| 0.601 | 0.0023596159 | 0.25 % | passed |
| 0.605 | 0.0023833022 | 1.26 % | passed |
| 0.61 | 0.0024130191 | 2.52 % | passed |
| 0.62 | 0.0024728132 | 5.06 % | passed |
| 0.65 | 0.0026550365 | 12.80 % | passed |
| 0.68 | 0.0028414212 | 20.72 % | passed |
| 0.7 | 0.0029679275 | 26.10 % | passed |

选定：小注入 ω=0.61（相对误差 2.52 %），
大注入 ω=0.65（相对误差 12.80 %）。

**复现命令**：

```bash
python3 -m tools.capability_drift.run_acceptance        # A1–A8 全量验收（约 3–5 分钟）
python3 -m pytest tools/capability_drift/tests -q      # 单元测试
python3 -m tools.capability_drift.ci --registry-dir config/capability_drift/demo --repo . \
        --changed-files-file /tmp/changed.txt          # CI 触发（退化则退出码 1）
```

## 3 计算结果（逐条判据）

| 判据 | 内容 | 结论 | 关键数字 |
|---|---|---|---|
| A1 | 注入退化被检测并告警 | ✅ PASS | energy_00: 12.72 % (阈值 5.00 %); peak_00: 7.80 % (阈值 5.00 %); blame 命中=是 |
| A2 | 无害变更零告警，假阳性率 <1% | ✅ PASS | 变体 100 个，误报 0，FPR=0.000 % |
| A3 | 10 案例重跑 <10 min | ✅ PASS | 10 案例，整轮 5.8947 s (预算 600 s) |
| A4 | 基线更新须审批且 sha256 变化 | ✅ PASS | 拒绝 4/4 非法尝试；sha 4b89d9f5e57a → 179b30bd3ef2 → 7337317efc6e |
| A5 | git blame 定位退化 commit，准确率 >80% | ✅ PASS | 10/10 命中，准确率 100.0 % |
| A6 | >5% 告警、<5% 静默、阈值可调 | ✅ PASS | +3% → ok；+6% → red；阈值 10% 时 6% → green；注入小/大 = 2.52 %/12.80 % |
| A7 | pytest ≥15 全绿 | ✅ PASS | passed=55, failed=0, errors=0 |
| A8 | 成熟化节点/分支/报告/honest FAIL | ✅ PASS | 分支 agent/m11318，报告 docs/reports/P360_W1_N10_CAPABILITY_DRIFT_MONITOR.md |

### 3.1 A1 注入退化 → 检测/告警/定位

漂移指标（相对误差 vs 基线，阈值 5.00 %）：

| 能力 | 指标 | 基线 | 实测 | 相对误差 | 阈值 | 判定 |
|---|---|---|---|---|---|---|
| `synth.solver.energy_00` | `energy_00` | 0.0030322588 | 0.0034180666 | 12.72 % | 5.00 % | 超阈（红） |
| `synth.solver.energy_00` | `peak_00` | 0.028286892 | 0.030492754 | 7.80 % | 5.00 % | 超阈（红） |

告警事件（`artifacts/capability_drift/events.jsonl`）：severity=`red`，
drifted 条目 2 个，含
`baseline/observed/rel_err/threshold` 四元组与嫌疑 commit；
平台投递结果：`{"delivered": false, "error": "HTTP Error 401: Unauthorized", "http_status": null}`
（无平台 endpoint 时事件仍完整落 jsonl——传输层故障不丢告警）。

### 3.2 A2 无害变更（假阳性）

| 无害变更类型 | 变体数 | 触发重跑案例数 | 误报数 | 最大相对误差 |
|---|---|---|---|---|
| case_comment | 16 | 16 | 0 | 0.000 % |
| dead_code | 12 | 120 | 0 | 0.000 % |
| docs | 30 | 0 | 0 | 0.000 % |
| readme | 30 | 0 | 0 | 0.000 % |
| solver_comment | 12 | 120 | 0 | 0.000 % |
| **合计** | **100** | - | **0** | FPR=0.000 % |

纯文档变更（`docs/NOTES.md`）触发判定为空集 → `skipped=True`、零重跑零告警；
注释/空白/死代码变更会触发重跑（因为它改了求解器目录），但数字不变 → 全绿。

### 3.3 A3 重跑效率

| 案例 | 墙钟 [s] | 状态 |
|---|---|---|
| `verif/test_cap_00.py::test_cap_00` | 1.895 | passed |
| `verif/test_cap_01.py::test_cap_01` | 1.970 | passed |
| `verif/test_cap_02.py::test_cap_02` | 2.086 | passed |
| `verif/test_cap_03.py::test_cap_03` | 1.903 | passed |
| `verif/test_cap_04.py::test_cap_04` | 2.232 | passed |
| `verif/test_cap_05.py::test_cap_05` | 1.699 | passed |
| `verif/test_cap_06.py::test_cap_06` | 2.068 | passed |
| `verif/test_cap_07.py::test_cap_07` | 2.103 | passed |
| `verif/test_cap_08.py::test_cap_08` | 2.245 | passed |
| `verif/test_cap_09.py::test_cap_09` | 1.849 | passed |
| **整轮（10 案例，并发 4）** | **5.8947** | 预算 600 s |

### 3.4 A4 基线审批

| 审批尝试 | 是否接受 | 说明 |
|---|---|---|
| unsigned | 拒绝 | approval without signature |
| bad_signature | 拒绝 | approval signature mismatch |
| wrong_capability | 拒绝 | approval field 'capability' mismatch: expected 'synth.solver.energy_03', got 'synth.solver.energy_04' |
| values_tampered_after_signing | 拒绝 | approval field 'values_sha256' mismatch: expected '4d8aaddf85d12d44ae0a0c5c8fb7facae01c660228da882f4963d061c9a23f6b', got 'da36d1e2b31e48ca8e07aff08a3b40b9e619cb7cb3a2a48c68f2f0a8fae7b65f' |
| signed_approved | 接受 | 签名与内容匹配 |

`baseline.json` sha256：4b89d9f5e57ada70 →（审批后）
179b30bd3ef260b2 →（回滚后）7337317efc6ee330；
审批审计链 `artifacts/capability_drift/baseline_audit.jsonl`。

### 3.5 A5 退化定位（git blame）

| 能力 | 注入 commit | blame 命中 commit | 是否一致 | 相对误差 |
|---|---|---|---|---|
| `synth.solver.energy_00` | 0dfd4ab550 | 0dfd4ab550 | ✅ | 12.72 % |
| `synth.solver.energy_01` | 491b25c79c | 491b25c79c | ✅ | 12.80 % |
| `synth.solver.energy_02` | 75333c1cfd | 75333c1cfd | ✅ | 12.82 % |
| `synth.solver.energy_03` | 0feff7e671 | 0feff7e671 | ✅ | 12.82 % |
| `synth.solver.energy_04` | 97ba623fc9 | 97ba623fc9 | ✅ | 12.82 % |
| `synth.solver.energy_05` | 7050d17d9e | 7050d17d9e | ✅ | 12.72 % |
| `synth.solver.energy_06` | be5755557a | be5755557a | ✅ | 12.80 % |
| `synth.solver.energy_07` | 1be4ba42df | 1be4ba42df | ✅ | 12.82 % |
| `synth.solver.energy_08` | b0aca9becd | b0aca9becd | ✅ | 12.82 % |
| `synth.solver.energy_09` | 53cc00f458 | 53cc00f458 | ✅ | 12.82 % |

### 3.6 白名单豁免（已知退化，须 owner 签名）

| 场景 | 能力状态 |
|---|---|
| 无豁免（注入退化） | ['red'] |
| 签名有效、未过期、误差在豁免上限内 | ['green']（waived 指标 ['energy_02']） |
| 签名被篡改 | ['red'] |
| 豁免记录被改（过期字段篡改） | ['red'] |

### 3.7 A6 阈值行为

+3% 观测 → `ok`；+6% 观测 → `red`；
把 `rel_tol` 调到 10% 后同一 6% 观测 → `green`
（阈值可调、调节行为可验证）；端到端注入：小注入 2.52 % → 红
0 个，大注入 12.80 % → 红
1 个。

### 3.8 A7 测试套件

`/usr/bin/python3 -m pytest tools/capability_drift/tests -q --no-header -p no:cacheprovider` → passed=55, failed=0,
errors=0（rc=0）。

## 4 结果图

| 图 | 内容 |
|---|---|
| `docs/reports/capability_drift/a1_drift_bars.png` | A1 注入退化后各能力指标的相对误差 vs 5% 判据阈值（红=超阈） |
| `docs/reports/capability_drift/health_history.png` | 历史健康度：每轮检测的绿/黄/红能力数（含 A2 无害变体与 A5 注入） |
| `docs/reports/capability_drift/a3_case_timings.png` | 10 个验证案例重跑墙钟 vs 10 分钟预算 |

（纯后端机制的可视化即上述三张：漂移阈值对照、健康度历史、重跑耗时；无场量云图——本任务不涉及物理场求解。）

## 5 注意点（踩过的坑）

1. **pytest 插件要靠 PYTHONPATH 注入**：案例在沙箱仓里，`-p tools.capability_drift.plugin` 需要把平台根
   放进 `PYTHONPATH`，否则插件加载失败 → 案例"无指标"变黄（不是绿）。
2. **沙箱必须是独立临时 git 仓**：嵌套 git 仓不能提交进工作树；验收在 `tempfile.mkdtemp()` 里造仓，
   工件只留 JSON/PNG 与注册表快照。
3. **审批密钥不进仓**：验收用一次性随机 key（只记指纹 sha256 前 16 位），否则白名单/基线审批形同虚设。
4. **blame 定位需要声明式定位点**：路径级 `git log` 无法把"数字变了"归因到 commit；必须声明
   "哪一行决定指标"。定位点由领域仓在注册表里注入，本仓不含域知识。
5. **基线 sha256 会因元数据变化**：即使数字不变，`updated_at/approval` 也不同 → A4 用 sha 变化作为
   "审批生效"的证据是成立的，但不能把它当成"数字必然变了"。
6. **黄 ≠ 绿**：案例失败、指标缺失、无基线都判黄（不可验证），避免"没跑=没退化"的自欺。
7. **触发规则宁可多跑不可漏跑**：目录前缀 `synth/` 命中所有依赖该目录的能力；漏跑等于静默退化。

## 6 误差判定

参考量 = 标定轮实测基线数字（合成核无实验基准，故不做物理精度对标；本任务判的是"退化检测灵敏度"
而非物理精度）。逐项列出（判据阈值 5%，另列 3% 门线）：

| 量 | 参考/基线 | 实测 | 相对误差 | 是否 <3% | 是否 <5%（判据阈值） |
|---|---|---|---|---|---|
| `energy_00`（A1 注入后） | 0.0030322588 | 0.0034180666 | 12.72 % | 否（退化被检出） | 否（触发告警） |
| `peak_00`（A1 注入后） | 0.028286892 | 0.030492754 | 7.80 % | 否（退化被检出） | 否（触发告警） |
| A6 小注入 | 基线 | 0.0024130191 | 2.52 % | 是 | 是（不告警） |
| A6 大注入 | 基线 | 0.0026550365 | 12.80 % | 否 | 否（触发告警） |
| A2 无害变更最大漂移 | 基线 | 与基线相同 | 0.000 % | 是 | 是（不告警） |

**是否 <3%？** —— 无害变更（A2）漂移 0.000 %，A6 小注入 2.52 %
（**是**，因此必须不告警）；A1/A5 的注入退化 12.72 %
（**否**，这正是要被检出的目标）。判据是"超 5% 才告警"，A1 的 12%+ 与 A6 小注入的 2.5% 分别落在阈值两侧，
说明判据在阈值附近分辨力正常。

## 7 审计链

| 工件 | sha256 |
|---|---|
| `baseline.json` | `7337317efc6ee330ec9ecd220c6521d94ac46518131e3329408240a9203a2c43` |
| `gates.json` | `b658d7587cb9810fba57c4e44b8b525f83d142eb0ce4899e27987cf51068c36d` |
| `params.py` | `bca7df4c85f95432b97248ddb63f682b0266f282b6aed220b307bf350bae9ed6` |
| `registry.json` | `c65ef72e4cbb8466de20e9392f3a51257cfeddf2e6707042c067506d8a2dce74` |
| `whitelist.json` | `1a53dacac2848b1e0f8989f876d8f7b27c1afc641c6fb7fc2348f15d24551c6a` |

- 审批 key 指纹（sha256 前 16 位）：`91d6a1d84b84328c`（密钥本体不落盘）；
- 验收报告：`artifacts/capability_drift/acceptance_report.json`；
- 告警事件：`artifacts/capability_drift/events.jsonl`；健康历史：`history.jsonl`（114 轮）；
- 基线审批审计：`baseline_audit.jsonl`；
- 环境：Python 3.11.6，沙箱 `/dev/shm/capdrift_accept_jrh7ggog/repo`（验收后删除），
  并发 4 进程/案例子进程隔离。

## 8 局限与 honest FAIL 声明

1. 受检对象是**合成数值核**：本仓零领域语义铁律下不引入船舶/领域算例；真实能力需领域仓注入注册表
   （`registry.json` 的 cases/blame/paths + `gates.json` 阈值 + 首次标定基线），本机制本身与域无关。
2. 触发判定是**路径级**启发式（精确/目录前缀/glob）：改名/移动文件时需同步注册表路径，否则会漏跑。
3. `git blame` 定位依赖声明式定位点；一次 commit 若同时改多个定位点，只能给出候选集（按提交时间排序）。
4. 告警投递到平台事件的 endpoint 若不存在/不可达，只在工件内留 `delivered=false`，不重试（无队列）。
5. 白名单豁免是"已知退化"的显式豁免，不是修复；豁免记录必须带到期时间与理由，过期即失效（fail-closed）。
6. 本轮所有判据均 ✅ PASS；**任何一条未过即如实 FAIL**，未做任何覆盖或粉饰。

## 9 能力登记

- 新增能力：`platform.capability_drift.monitor` —— 变更触发的验证重跑 + 基线对比 + 退化告警 + blame 定位
  （证据等级：REAL，判据 A1–A8 机器可复核）。
- 复用面：`trigger/runner/detector/attribution/alerts/dashboard` 六个模块可被任意领域仓的 CI 直接调用，
  域内容全部来自 `capdrift/` 四份 JSON；`ci.py` 提供 PR 门（退化 → 退出码 1）。
