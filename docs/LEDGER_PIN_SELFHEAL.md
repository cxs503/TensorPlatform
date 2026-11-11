# 能力台账 PIN 自愈闭环（`tools/audit/ledger_selfheal.py`）

> 里程碑：m15074（W9 机制自愈）。锚点=`NEW:能力台账 pin 自愈闭环`——
> `GET /health` 长期 `capability_ledger.status="DEGRADED", reasons=["PIN_MISSING"]`。

## 1. 为什么要有它（红为什么是永久的）

能力台账的**真值身份**由唯一解析器 `tools/audit/ledger_paths.py` 定义：
台账内容 sha256 必须与图章文件 `artifacts/capability_ledger/ledger_contract.json`
记录的 sha256 一致（契约 `capability-ledger-consumer-contract/1.0`，判据 C1–C4）。

问题不在判据，而在**闭环缺一半**：`ledger_paths.py` 有 `--write-pin`（人工动作），
`ledger_contract_gate.py --check` 会判红，但**没有任何路径把「红」和「修」连起来**。
于是部署树只要丢过一次 pin（换树、清仓、只拷了台账三件套
`capability_ledger.json` / `_resolved.json` / `_table.md`），`/health` 就永久停在：

```json
{"capability_ledger": {"status": "DEGRADED", "reasons": ["PIN_MISSING"], ...}}
```

没有人被叫醒，没有机制重试。按 owner 的自主续航原则（「一切依赖写成机制」），
这种「红是永久的、修是手动的」形态必须消灭：本模块把
**检测 → 判定可自愈性 → 自愈 → 复检 → 留痕** 做成一条可机器调用的命令。

## 2. 机制（判据分层：只修能修的那一种）

`ledger_selfheal.py` 复用 `ledger_paths.contract_report()` / `resolve_ledger()`
（**不新增判据、不改阈值、不动白名单**），只做「可修性判定 + 合成动作」：

| 失败原因（`reasons`） | 语义 | 自愈策略 |
|---|---|---|
| `PIN_MISSING` | 台账本体合法，只差图章 | **可自动修**：`write_pin()` 补签 |
| `LEDGER_DRIFT` | 台账内容被改但 pin 还是旧值 | **只报不修**（写 pin = 给篡改洗白） |
| `SCHEMA_VERSION_MISMATCH` | schema 越界 | 只报不修（须显式登记新版本） |
| `NO_LEDGER` / `NO_PARSEABLE_LEDGER` | 台账不存在/坏文件 | 只报不修 |
| `DERIVED_MISSING` / `DERIVED_UNREADABLE` | 派生物缺失/不可读 | 只报不修 |
| `FALLBACK_NON_CANONICAL` | 退到等价副本 | 只报不修 |

两条**安全闸**（比「可修清单」更重要的设计）：

1. **叠加否决**：只要 `reasons` 里存在任何一条不可自动修的原因，
   `PIN_MISSING` **也不再修**（`fixable=[]`）。写 pin 只允许发生在
   「台账本体已合法、只差图章」的世界里。杜绝用写章掩盖别的红灯。
2. **重定位只报**：`TP_CAP_LEDGER` 把台账指到仓外时（`relocated=True`），
   仓内 pin 位置已不代表真值来源 → `LEDGER_RELOCATED` 只报不修。

输出字段（`--check` / `--fix` 同构，契约字段固定在顶层）：

```json
{"status": "...", "reasons": [...], "fixable": [...], "action_taken": "none|wrote_pin|refused_needs_human|fix_failed"}
```

`--fix` 额外给 `before` / `after` 两栏对照（`status` / `reasons` / `pin_sha256` /
`sha256` / `pin_matches_ledger` / `gate`）、`fixed: bool`、`gate{verdict,failed,checks}`、
`contract{violations,consumers}`。**退出码：`0`=已健康或已修复；`1`=需人工介入。**

### 2.1 一处重要的作用域约定（`scope`）

四个消费者访问器（`audit_capabilities` / `cli` / `health` / `report_gate`）签名
**没有 `root` 参数**（契约冻结，不允许为本模块改签名），因此它们永远解析真仓：

* `--root == repo_root`（默认，即部署世界）→ `scope="repo"`，
  消费者层（指纹分歧/导入错误）**参与**可修性裁决；
* `--root != repo_root`（演练世界）→ `scope="ledger-only"`，
  消费者层只登记不上账（否则演练结论会被真仓状态污染），gate 用台账层判据。

## 3. Runbook（什么时候跑什么）

| 场景 | 命令 | 期望 |
|---|---|---|
| **部署/换树后自检**（读，无副作用） | `python3 tools/audit/ledger_selfheal.py --check` | 绿→0；红→1 并给 `reasons/fixable` |
| **红且 `fixable=["PIN_MISSING"]`** | `python3 tools/audit/ledger_selfheal.py --fix` | `action_taken="wrote_pin"`，`after.reasons=[]`，0 |
| **红但 `fixable=[]`** | 不要跑 `--fix`（跑了也会 `refused_needs_human`），按 `reasons` 人工处置 | 1 |
| 台账**内容合法变更**后（新增/订正能力条目） | `python3 tools/audit/ledger_paths.py --write-pin`（人工确认变更合法） | 再跑 `--check` 应绿 |
| 篡改演练（阴性对照） | 改台账 → `--fix` | 必须仍 `LEDGER_DRIFT`、退出码 1、pin 字节不变 |
| CI | `python tools/audit/ledger_contract_gate.py --check`（`.github/workflows/ci.yml`） | pin 已入库 → PASS |
| 审计留痕 | `--fix --json --out artifacts/capability_ledger/selfheal_run.json` | 结果落盘可溯源 |

> 纪律：`--fix` **只写 pin 文件**，绝不改写台账/派生物，也绝不改 gate 判据。
> 台账内容变更后写章仍需人工确认（否则等于取消 C3 的篡改检测）。

## 4. 一页真实对照（部署世界形态 → 自愈）

部署世界形态 = `artifacts/capability_ledger/` 下只有三个文件、没有 pin
（= 计划师 17:02 实测 `DEGRADED reasons=["PIN_MISSING"]` 的现场）。
复现命令（把三件套拷到临时世界，避免在真仓里造红）：

```bash
mkdir -p /tmp/w/artifacts/capability_ledger
cp artifacts/capability_ledger/capability_ledger.json \
   artifacts/capability_ledger/capability_ledger_resolved.json \
   artifacts/capability_ledger/capability_ledger_table.md /tmp/w/artifacts/capability_ledger/
python3 tools/audit/ledger_selfheal.py --check --json --root /tmp/w   # 红：PIN_MISSING（退出码 1）
python3 tools/audit/ledger_selfheal.py --fix   --json --root /tmp/w   # 修：写章并复检（退出码 0）
python3 tools/audit/ledger_selfheal.py --check        --root /tmp/w   # 绿
```

演练工件（本仓，程序化生成，非手抄）：
`artifacts/ledger_selfheal_drill/{check_before.json, fix.json, check_after.json}`。

**修复前**（`check_before.json`，退出码 1）：

```json
{"status": "DEGRADED", "reasons": ["PIN_MISSING"], "fixable": ["PIN_MISSING"],
 "unfixable": [], "action_taken": "none",
 "sha256": "618d37ff38a162da385c05e84e23c04c9f761fc3f8355f4e37d5b4316b58ac89", "pin_sha256": null,
 "gate": {"verdict": "FAIL", "failed": ["LEDGER:PIN_MISSING"]}}
```

**修复动作 + 复检**（`fix.json`，退出码 0）：

```json
{"action_taken": "wrote_pin", "status": "OK", "reasons": [], "fixed": true,
 "before": {"status": "DEGRADED", "reasons": ["PIN_MISSING"], "pin_sha256": null, "gate": "FAIL"},
 "after":  {"status": "OK",       "reasons": [],               "pin_sha256": "618d37ff…ac89", "gate": "PASS"},
 "pin_matches_ledger": true}
```

**修复后**（`check_after.json`，退出码 0）：

```json
{"status": "OK", "reasons": [], "fixable": [], "action_taken": "none"}
```

真仓同形态自愈（本里程碑实跑，未落临时世界）：`/health` 的
`reasons=["PIN_MISSING"] → []`、gate `FAIL → PASS`（C1–C4 全 PASS），
pin sha256 与台账 sha256 均为
`618d37ff38a162da385c05e84e23c04c9f761fc3f8355f4e37d5b4316b58ac89`。

## 5. 测试（机检判据）

```
python -m pytest -q tests/test_ledger_selfheal.py tests/test_ledger_pin_contract.py
# 12 passed
```

`tests/test_ledger_selfheal.py`（7 例，均在 `tmp_path` 自造世界）：

| # | 用例 | 断言 |
|---|---|---|
| a | pin 缺失 → `--fix` | 退出码 0，`wrote_pin`，`before.reasons=["PIN_MISSING"]`，`after.reasons=[]`，pin sha256 == 台账 sha256 |
| b | 篡改台账 → `--fix` | 退出码 1，`refused_needs_human`，`LEDGER_DRIFT` 仍在，**pin 字节不变** |
| c | 幂等 | 连续两次 `--fix`，pin 文件字节完全一致，第二次 `action_taken="none"` |
| d | 已健康 → `--fix` | no-op：pin 字节与 `st_mtime_ns` 均不变 |
| e | 无台账 → `--fix` | 退出码 1，`NO_LEDGER`，`fixable=[]`，**pin 未创建** |
| f | pin 缺失 + 派生物缺失 | `fixable=[]`（叠加否决），`DERIVED_MISSING` 进 `unfixable`，不写 pin |
| g | 真仓 `--check` | 只读：不落盘、pin 字节不变、`scope="repo"`、gate PASS |

`tests/test_ledger_pin_contract.py`（m14437 既有 5 例）**全部保持绿、判据未改**：
其中 4 例在本里程碑之前是红的（pin 从未入库），由本机制补章后转绿——不是放宽判据换来的绿。

## 6. 局限与边界（honest）

* **不会自动"变健康"**：本模块只在被调用时动作。部署树（如 `/root/tp-agent`）
  在 `--fix` 跑过一次、pin 入库并同步后才会绿；`/health` 每次请求实时
  `resolve_ledger()`，所以 pin 一落盘即翻绿，**无需重启**。目前尚无定时器/启动钩子
  调用它（定时任务/启动钩子不在本单白名单内），机制以 CI + 部署 runbook 两条路径落地。
* **写章仍是"合成动作"**：`PIN_MISSING` 自愈等价于"补签"，它**不能**证明台账内容
  当时合法——内容合法性靠 C3/C4（篡改仍红）与人工 `--write-pin` 纪律。
* **作用域**：`--root` 演练世界里消费者层不参与裁决（见 §2.1），
  演练的绿只代表台账层绿。
* 一个共享 `write_pin()` 的已知脆弱点：pin 的 `pinned_at` 是时间戳，
  若台账内容不变而重复 `--fix`，因已健康路径为 no-op，pin 字节稳定（用例 c/d 钉住）。