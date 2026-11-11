# 能力证据等级回归门（evidence regression gate）

节点：`p285w2b-n02-evidence-regression-gate`（战役 DAG，平台成熟化单）。
组件：`tools/evidence_regression/`（机制，零领域语义）+ `config/evidence_regression/probes.json`（数据）。

一句话：**把「能力证据等级从 REAL 静默降级成 MOCK」从台账里的一行字，变成一条会红的门。**

---

## 1. 为什么需要它

台账（`artifacts/capability_ledger/capability_ledger_resolved.json`）把每个能力的证据等级记成
`REAL / WITHHELD / MOCK / STUB / NOT_SUPPORTED`。等级本身是**声明**，不是**观测**：

* 实现被换成常量桩 / 随机数 / 写死到期望值的桩，台账仍写着 `REAL` —— 静默降级，无人报警；
* 台账被手工改成 `MOCK`（或从 `MOCK` 改成 `REAL`），没有任何可复算证据 —— 无据改级；
* 已有的静态审计（`tools/capability_audit_gate`）看的是**代码形态**（AST 信号、基准工件），
  它天然看不见「代码形态没变、但可观测行为退化了」这一类。

本模块补的正是**行为面**：为每个能力登记一组**可执行契约探针**，等级变化时必须现场跑一遍。

---

## 2. 结构

```
config/evidence_regression/probes.json         # 注册表（数据：能力→调用链→探针→期望值/容差）
        │
        ├── registry.py    装载/校验（反退化最低强度 Inv1..Inv4）+ 执行（含逐探针 field 覆盖）
        │      ├── targets.py    import 路径解析（拒绝 os/sys/subprocess…）、调用链、字段选择、MOCK 包装器
        │      └── probes.py     6 族通用契约：anchor / scale_law / monotonic / nontriviality / determinism / bounded
        │
        ├── gate.py        audit_claims（声明 vs 观测）/ evaluate_changes（改级守卫）/ 覆盖率
        ├── ledger.py      唯一合规写入路径：门不过就不写，审计 JSONL 留痕（ack 绑定探针摘要）
        ├── mutation.py    退化演练：REAL→MOCK 注入（5 个 MOCK 家族）× 探针，测检出率
        └── report.py      验收编排：C1..C6 + 负控 + 图 + acceptance.json（报告零手抄）
```

## 3. 数据契约（注册表条目）

```json
{
  "capability_id": "cap.alpha",
  "sys_path": ["backend"],
  "target": {
    "steps": [
      {"ref": "module:function", "kwargs": {"x": 2.0}},
      {"ref": "module:second_step", "kwargs": {"offset": 1.0}}
    ],
    "field": "derived.volume_m3"
  },
  "probes": [
    {"kind": "anchor", "field": "reynolds", "cases": [{"kwargs": {"speed": 5.0}, "expect": 4.98e8, "tol_rel": 1e-9}]},
    {"kind": "scale_law", "axis": "length", "factor": 2.0, "exponent": 1.0, "tol_rel": 1e-6},
    {"kind": "nontriviality", "cases": [{"kwargs": {"length": 100.0}}, {"kwargs": {"length": 50.0}}], "min_rel_spread": 0.5},
    {"kind": "determinism", "cases": [{"kwargs": {"length": 100.0}}]}
  ]
}
```

* `steps` 是调用链：第 0 步吃 `kwargs`，之后每步把上一步结果作为唯一位置参数；`field` 用点号路径选择
  标量（数字段表示序列下标，如 `"0"`）。
* **每个探针可以自带 `field`**，一条调用链可同时被「无量纲数」和「有量纲力」两个探针观测，不必重复声明目标。
* 输入、期望值、容差、方向、指数全部是数据 —— 机制层不出现任何领域词条（见 §8 自检）。

## 4. 反退化最低强度（Inv1–Inv4，注册表校验强制）

| 不变式 | 要求 | 被它挡住的退化 | 演练证据 |
|---|---|---|---|
| Inv1 | ≥1 探针 | 空壳条目 | — |
| Inv2 | ≥1 **变化敏感**探针（`nontriviality`/`scale_law`） | 常量桩、写死期望值的桩 | `constant_freeze` / `expected_echo` 家族全检出 |
| Inv3 | ≥1 `determinism` 探针 | 未播种随机桩 | `unseeded_random` 全检出 |
| Inv4 | ≥1 **带期望**探针（`anchor`/`bounded`） | **整体漂移**（×1.7 之类，比值/展布都被保持） | `drift_scale` 全检出 |

> Inv4 是演练逼出来的：第一版 `calc.stability` 只把锚点放在 GZ(0°)=0，`drift_scale` 家族整体 ×1.7 后
> 0×1.7=0，锚点、单调、变化敏感、确定性**全部通过** —— 全矩阵演练 49/50 逮到这个洞。
> 修法是补一个**非零**锚点（0.2 rad 的复原臂），并把它写成注册表不变式 Inv4。
> 这条经验就是本节点最重要的可复用结论：**只有"零值锚点"的注册表对乘性漂移是瞎的。**

## 5. 门语义（`gate.py`）

| 场景 | 判定码 | 严重度 | 说明 |
|---|---|---|---|
| 台账声明 REAL，探针通过 | `CLAIM_SUPPORTED` | OK | 声明被复现 |
| 台账声明 REAL，探针失败 | **`REFUTED_CLAIM`** | **RED** | 静默降级：实现坏了台账没动 |
| 降级（REAL→MOCK…），探针失败 | **`DEGRADATION_DETECTED`** | **RED** | 真退化被探针确认，改级需 ack |
| 降级，探针通过 | **`UNSUBSTANTIATED_DOWNGRADE`** | **RED** | 行为仍满足契约 → 降级无据 |
| 降级 + 新摘要 ack | `ACKED_DEGRADATION` / `ACKED_DOWNGRADE` | ACK | 允许写，留痕 |
| 升级（MOCK→REAL…），探针失败 | **`UNSUPPORTED_UPGRADE`** | **RED** | 升级必须可复现（fail-closed） |
| 升级，探针通过 | `UPGRADE_VERIFIED` | OK | 允许写 |
| 能力未注册 | `UNCOVERED_CAPABILITY` | ACK | 覆盖缺口，记录不阻断（见 §7） |

严重度契约：`RED` 阻断（CLI 退出码 3），`ACK` 放行但入库，`OK` 干净通过。只有 RED 计检出/假阳性。

**ack 绑定探针摘要**：ack 必须携带 `probe_digest`（本次探针运行的证据摘要，**不含耗时**，见
`ProbeReport.digest()`），digest 不匹配即视为过期 ack → 仍 RED。这样「先跑一次绿、再改级」无法复用旧证据。

## 6. 退化演练（`mutation.py`）

不改文件、不脏工作树：把条目声明的调用链**在运行时**包成 5 个 MOCK 家族之一，再跑条目自己的探针：

| 家族 | 语义（真实故障模式） | 主要杀手 |
|---|---|---|
| `constant_freeze` | 首次结果冻结成常量（硬编码桩） | nontriviality / anchor |
| `unseeded_random` | 数值叶子换成未播种随机 | determinism |
| `drift_scale` | 所有数值一致 ×1.7（整体偏置，形状不变） | anchor / bounded（Inv4） |
| `zero_fill` | 全部归零 | anchor / scale_law |
| `expected_echo` | 按顺序返回**探针期望值**（为过测试而写死的桩） | determinism / nontriviality |

抽样演练 = 20 次注入（验收判据 C2）；全矩阵 = 条目×家族（补充证据，更严）。

## 7. 台账集成（`ledger.py`）

```bash
# 门判定（只读，不写台账）
python3 -m tools.evidence_regression.cli audit
python3 -m tools.evidence_regression.cli --ledger <ledger.json> gate --changes changes.json

# 唯一合规写入路径：门不过（RED）→ 不写文件 + 审计 JSONL 记一次拒绝
python3 -m tools.evidence_regression.cli --ledger <ledger.json> apply \
  --changes changes.json --out ledger_new.json --audit artifacts/evidence_regression/ledger_audit.jsonl --actor <who>
# 退出码 0 写成功 / 3 被门阻断（台账字节不变）/ 1 用法或注册表错误
```

写入时会保留未知字段、追加 `level_change_log`（from/to/at），并在台账顶层写 `last_gated_edit`
（verdict + gate_digest），使每次改级都能追到那次探针运行。

覆盖缺口（诚实披露）：仓内台账 21 个能力里本注册表覆盖 **10** 个；18 条 REAL 声明中覆盖 9 条，
`dev.*`/`knowledge.docs`/`geometry.list_models` 等 9 条 REAL 声明**尚无探针**（多数是平台运维端点，
没有可离线复现的数值契约）。这些能力改级记为 `UNCOVERED_CAPABILITY`（ACK 不阻断），缺口写进
`acceptance.json → ledger.coverage.uncovered_real_claims`。

## 8. 验收判据（机器可算，全在 `artifacts/evidence_regression/acceptance.json`）

| 判据 | 目标 | 落盘字段 |
|---|---|---|
| C1 | ≥10 个能力条目注册探针 | `criteria.C1_registered_entries` |
| C2 | 20 次 REAL→MOCK 注入检出率 100% | `criteria.C2_degradation_detection`（+ `supplementary_matrix`） |
| C3 | 100 次无退化运行，假阳性 0 | `criteria.C3_zero_false_positive` |
| C4 | 等级变化→门判定 ≤2 s | `criteria.C4_gate_latency` |
| C5 | pytest ≥12 全绿（含负控） | `criteria.C5_pytest` |
| C6 | 反退化不变式 Inv1–Inv4 | `criteria.C6_registry_invariants` |

负控（必须为真，否则机制不成立）：`negative_controls.*` —— 静默降级被拒、无据降级被拒、新鲜 ack 放行、
过期 ack 被拒。

```bash
python3 -m pytest -q tests/evidence_regression                  # 50 用例
python3 -m tools.evidence_regression.cli acceptance \
  --out-dir artifacts/evidence_regression                       # 判据 + 图 + drill/FP/latency JSON
python3 -m tools.audit.domain_leak_scan \
  --paths tools/evidence_regression tests/evidence_regression    # 零领域词自检（0 命中）
```

## 9. 坑与局限（适用范围）

1. **anchor 的成色不均**：注册表 `label` 标注来源（`analytic identity` 独立闭式推导 / `formula pin`
   公开公式口径 / `published pin` 公开尺寸数据 / `golden pin` 冻结基线观测量）。`golden pin` 只能证明
   「没变」，不能证明「对」；所有 anchor 都只能检出**形状类**退化（常量/随机/漂移/写死），
   **检不出同量级（<30%）的物理漂移** —— 那要靠 `tools/capability_audit_gate` 的基准数值面与领域验证面。
2. **探针覆盖 ≠ 能力覆盖**：10/21 条目，9 条 REAL 声明无探针（§7）。覆盖率是公开数字，不做加权粉饰。
3. **UNCOVERED 选择 ACK 而非 RED**：若把未覆盖改级一律判红，平台每次运维端点改动都会误报；
   代价是缺口处的静默降级**仍然无人报警**。这是显式取舍，缺口列表逐条落在产物里。
4. **冷启动**：门判定本身（进程内、含模块导入）最坏约 1.5 s；但**解释器启动 + 平台后端导入**的 wall
   时间在多核共享机上波动很大（同一棵树多次实测 0.12–2.33 s），所以 CI 钩子应常驻进程或预热，
   否则「等级变化→门判定 ≤2 s」会被进程启动时间吃掉。该风险与实测区间逐次写入
   `criteria.C4_gate_latency.cold_wall_ms` / `cold_wall_over_2s`。
5. **注入层级**：本模块在**运行时**注入退化（改可观测行为，不动文件），因此不覆盖「实现文件被改写」
   这一类；那是 `tools/capability_audit_gate.cli drill` 的领域（文件级注入 + 静态/基准面重判）。
   两者互补，不互相替代。
6. **注册表即信任边界**：`ref` 只允许公开属性、拒绝 `os/sys/subprocess/importlib/...`，但仍应把注册表
   当代码审（谁能改注册表谁就能改期望值）。因此注册表进 git、条目带 `label` 溯源、acceptance 记录
   `registry.digest`（sha256），且 `tests/evidence_regression/test_shipped_registry.py` 断言产物摘要与
   当前注册表一致 —— 改了注册表没重跑验收，测试会红。

## 10. 回归守卫（CI 接入）

```yaml
- name: evidence-regression-gate
  run: |
    python3 -m pytest -q tests/evidence_regression
    python3 -m tools.evidence_regression.cli audit          # 声明 vs 观测，RED 即失败
    # 改级 PR 额外跑：cli gate --changes pr_level_changes.json（RED → 退出码 3）
```

新增能力的正确姿势：在 `probes.json` 里加条目（**必须**满足 Inv1–Inv4，否则装载即报错），
跑 `pytest tests/evidence_regression/test_shipped_registry.py` 与 `cli drill --matrix`，
再跑 `cli acceptance` 刷新产物。**不要**为了让验收变绿而放宽 `tol_rel` 或删除变化敏感探针 ——
那等价于把门拆了。