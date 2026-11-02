# 能力证据台账：仪器说明与「入库防腐朽」闸门（m2031，2026-11-02）

## 1. 这台仪器是什么

`artifacts/capability_ledger/` 是平台能力分级的**单一真值**：

| 文件 | 作用 |
|---|---|
| `capability_ledger.json` | 台账原件：21 项能力（自述等级 + 证据行，每行含 `quote_match` 正则、`stance`、`asserted_level`、`as_of`）+ 5 条未注册域缺口 + curation 段（`max_evidence_age_days` / `must_consume`） |
| `capability_ledger_resolved.json` | 对账结果：`claimed_level` vs `reconciled_level` + `divergence`(OK/OVERCLAIM/REVERIFY/UNVERIFIED) + `summary` 计数 + 每行 `line_sha256` |
| `capability_ledger_table.md` | 上表的人读版本 |
| `fig_capability_ledger.png` | 图（`tools/audit/gen_capability_ledger_figure.py` 生成） |
| `m2031_gate_mutation_probe.json` | 闸门变异探测证据（m2031） |

工具链：

```bash
python3 tools/audit/capability_ledger.py --check            # 离线对账 C1~C10（CI 用，退出码 0/1/2）
python3 tools/audit/capability_ledger.py --check --write    # 同时刷新 resolved 快照与表格
python3 tools/audit/capability_ledger.py --refresh          # 从 :8010 重取证据正文（需 token）
python3 tools/audit/curate_capability_ledger_m1811.py --check   # curation 幂等（SYNCED/STALE）
python3 tools/audit/m2031_gate_mutation_probe.py            # 闸门变异探测（5 变异 + 阴性对照）
python3 tools/audit/m2031_publish_report.py                 # 重算图表并发布报告
```

## 2. 两次真实丢失（为什么要加闸）

台账由 m1567 与 m1811 分别产出，**两次都只活在个人工作树里，HEAD 上从未存在**：

- `tools/audit/capability_ledger.py --check` 在干净工作树退出 2（输入缺失）；
- `tests/test_capability_ledger.py` 全红（m1811 记录 13 error + 2 failed）；
- agent 侧工具 `audit_capabilities`（`ops/tp_worker.py::_tool_audit_caps`）此前只认一条
  硬编码路径（船舶 harness 的 `capabilities.json`，incoming 树里只剩 `.pyc`），
  对**全部席位**恒返回「读取能力清单失败」——证据分级仪静默死亡，且无任何告警。

## 3. m2031 的修复（可回归）

| 项 | 内容 | 判据 |
|---|---|---|
| 工件入库 | 三件套 `git add` 入库；`--write` 重算的 resolved 与 m1811 工作树**逐字节等价** | `test_capability_ledger.py` 21/21 |
| G1 | 三件套必须存在 | `test_capability_ledger_persisted.py` |
| G2 | 三件套必须被 **git 跟踪**（`git ls-files --error-unmatch`）——两次事故的直接形态；配「未跟踪文件必须判 False」阴性对照 | 同上 |
| G3 | C1~C10 自检退出码 0 | 同上 |
| G4 | curation 幂等（SYNCED） | 同上 |
| G5 | 反例：破坏引文正则 → 自检必须翻为 C3 违规 | 同上 |
| 仪器多候选 | `_tool_audit_caps` 顺序：`TP_CAP_LEDGER` > 仓内 resolved > `TP_SHIP_HARNESS_DIR`/船舶 harness > 台账原件；输出「自述 vs 对账」双列；全灭时**明确报失败并列出已试路径** | `test_audit_capabilities_instrument.py` T1~T4 |

变异探测实测：**5/5 检出，阴性对照不报警**。注意其中两条是「闸门覆盖边界」的实测结论，
不是猜想：

- **M4 证据等级被手改** → curation 幂等闸**不报警**（`build()` 是台账的纯函数，任何内容都自洽
  SYNCED）；由 **C7 快照漂移闸**（对账结果含每行 `line_sha256` vs 已入库 resolved）抓到。
- **M5 v1 证据行被删** → 同理，curation 闸看不见；由 **C7** 抓到。

## 4. 当前对账口径（读 resolved 才能引用，勿引台账自述）

21 项能力：`OK 4` / `OVERCLAIM 2` / `UNVERIFIED 15`；证据行 10 条；未注册域缺口 5 个
（耐波、绿色航行、基准参照数据、结构、振动噪声——能力在跑而账上无条目）。
`as_of = 2026-11-02`，`max_evidence_age_days = 30`：C8 会随证据老化要求重新 curation。

关键分歧：

- `propeller.open_water` 自述 REAL → 对账 **MOCK**（证据门 2/8；端点 eta0 越动量理论上限）；
- `calc.resistance` 自述 REAL → 对账 **WITHHELD**（inline 湿表面积 −20.9%、SUBOFF Ct +31.5%）；
- `calc.stability` 自述 REAL → 对账 REAL（m1564 平行实现裁决，箱型驳船基准 1e-6% 量级）。

## 5. 给下一班的动作

1. 修完某能力后，**必须重新 curation**（`curate_capability_ledger_m1811.py --write` 或新增
   curation 脚本）→ `--check --write` 刷新 resolved，否则台账把「已修复」永久钉在旧判决上；
2. 新增证据一律用「正则 + 逐字命中」（C3），禁止手抄数值；
3. 台账/快照/表格**三件套一起入库**，G2 会拦。