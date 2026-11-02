# 验证欠账借偿（m1848）：被「推送黑名单」结构性吞掉的验证资产

**仓**：TensorPlatform　**分支**：agent/m1848　**EDIT_WHITELIST**：docs/, tests/, artifacts/

## 1. 现象：包里 5 个模块「有测试、零执行」

本单起点是最朴素的健康检查（本分支基点 adb853c7）：

```
python3 -m pytest tests -q --co
→ 2598 tests collected, 5 errors      # 收集期 ERROR ⇒ 这棵树上没有一条被判据过
```

其中两个 ERROR 的成因完全相同，且**不是代码错**：

```
ERROR tests/test_vibration_analytic_bench.py             ModuleNotFoundError: ref_vibration
ERROR tests/verif/test_seakeeping_section_analytic.py    ModuleNotFoundError: lewis_reference
```

两个模块要求 `artifacts/verif_vibration/ref_vibration.py` 与
`artifacts/verif/seakeeping_section/lewis_reference.py`（m1797 / m1798 的参考核与证据工件），
而这些文件在其「成熟提交」里被静默滤除：

```
36c300ff  m1797: matured mission deliverables（9 个文件被门禁滤除）
7334145a  m1798: matured mission deliverables（8 个文件被门禁滤除）
```

后果：**参考核、证据工件与消费它们的判据同源不同命** —— 判据进了 main，证据没进。
122 条解析级判据（切片水动力 45 条 + 振动噪声 77 条）在任何新分支上都只 ERROR，不判定。

## 2. 根因（读 `/root/tp-agent/tp_pusher.py` v5.4，只读定位）

推送线在把分支交付物上 GitHub 前做路径过滤：

```python
_EXCLUDE  = re.compile(r'^(artifacts?/|runs/|results[-/]|REPORT_GATE)')
_EXCLUDE2 = re.compile(r'\.(db|sqlite3?|png|log)$')
```

即 **`artifacts/**` 与任何 `*.png` 在推送时结构性地被 strip**。这与「记录区不上仓」的
策略一致，但它同时把**验证证据**（参考核、证据 json、判决图）一并吞掉——于是：

* m1797/m1798 把参考核放在 `artifacts/` → 滤除；
* m1802 已把资产迁到版本化路径 `tests/data/verifier_assets/`（正确方向），但
  `damage_stability/fig_*.png` 仍因 `.png` 后缀被滤除 → 本分支上守护
  `test_every_asset_present_with_matching_checksum` 一直是红的（3 文件 missing）。

**这是一个可复发的失效模式**：任何把验证资产放进 `artifacts/` 或存成 `.png` 的写法，
下次推送都必然消失，且消失是静默的（门禁看似还在，实际不判定）。

## 3. 修法（最小、可验证）

1. **资产版本化**：把 `lewis_reference.py` + `evidence.json`（m1797 产物）与
   `ref_vibration.py` + `results.json`（m1798 产物，109 项判据账本）原样迁入
   `tests/data/verifier_assets/{seakeeping_section,verif_vibration}/`，并在 `manifest.json`
   登记 sha256 / bytes / origin_commit / 描述（清单 11 → 15 项）。迁移文件均为 `.py` / `.json`，
   落在推送白区。
2. **消费方路径解析**：两个测试模块改为「版本化目录优先、`artifacts/` 兜底、全缺失则显式
   raise」（与 m1802 的 `SNAP_CANDIDATES` 范式一致），不再硬编码 `artifacts/`。
3. **清单分层（本次新增的关键设计）**：`manifest.files` = 硬门（参考核/证据，必须存在且校验和
   一致）；新增 `manifest.derived` = 派生图（`.png`，在场则校验、缺席合法，且每条必须声明
   `regenerator` 出处）。并在 `transport_note` 里写明 fork 事实与推送黑名单依据。
4. **守护硬化**（`tests/test_verifier_assets_integrity.py`，5 → 9 条）：
   | 新判据 | 作用 |
   |---|---|
   | `test_required_assets_are_pushable` | 硬门资产不得是 `*.png/*.db/*.log` 或 `artifacts/**`——从源头堵死「下次推送又消失」 |
   | `test_derived_entries_declare_regenerator` | 派生图必须写明重建来源，否则缺失即不可复现 |
   | `test_derived_figures_verified_when_present_but_absence_is_legal` | 派生图在场则校验 sha256，缺席不算失败 |
   | `test_consumers_resolve_versioned_path_first` | 消费方代码里版本化路径必须先于 `artifacts/`（剥离 docstring/注释后判定，防文案误报） |
5. **受损图归位**：从 m1802 工作树按 manifest 记录的 sha256 逐字节校验后恢复
   `damage_stability/fig_*.png`（3 张，sha256 全部一致），本分支守护由红转绿。

## 4. 结果（程序化产出，见 `docs/m1848/asset_numbers.json`）

| 指标 | 基点 | 本单 |
|---|---|---|
| pytest 收集 ERROR | 5 | **3**（余下均为预存、非本单范围） |
| 收集用例数 | 2598 | **2725**（+127，复活模块重新入册） |
| 本单守护 + 两个复活模块 | 0 executed | **131 passed, 1 xfailed** |
| `verifier_assets` 清单 | 11 项 / 3 图 missing | **12 硬门 + 3 派生**，冲突 0 |

复活的两组判据实数（取自 `evidence.json` / `results.json`，非手抄）：

| 剖面/船 | 平台切片内核 A33 [kg] | 解析参考 A33 [kg] | 相对误差 |
|---|---|---|---|
| KVLCC2 | 3.2044e8 | 3.6013e8 | −11.02% |
| KCS | 5.3331e7 | 7.1162e7 | −25.06% |
| BARGE60 | 2.3524e6 | 4.5164e6 | −47.91% |

色散关系误差 0.0（深水 k=ω²/g 精确），剖面族 9 组。振动噪声判据账本 109 项检查、0 FAIL，
最差相对误差 9.24e-4（SSSS 板 Rayleigh 商 vs Gauss 积分，门槛 3%）。

**误差判定（逐项，显式回答）**：解析闭式项（半圆极限 A33/(ρπa²/2)=1、色散、等价椭圆面积
恒等）实测 1e-12 量级；独立数值算路（振动 109 项）最差 9.24e-4，**< 3%** ✅。
切片内核 vs 解析参考 −11%…−48%，**>15%，不可接受** —— 但其成因已在 m1797 锁定并作为
**缺陷锁**入账：线上内核 `f_a(ν)=1−0.25ν²/(1+ν²)`、`f_b(ν)=1.6ν/(1+ν²)` 与剖面几何无关
（形状盲），误差随 Cb/剖面丰满度单调放大正是形状盲指纹。本单的贡献是**把这条已知缺陷
从「无人判定」变成「每次 CI 都判定并按带宽锁死」**：一旦有人换成 Lewis/Ursel 形状相关真解，
`test_kernel_section_law_is_shape_blind` 与带宽断言会立刻失败，强制重跑探针并复核
`evidence.json`，而非让旧结论悄悄失效。修复该内核本身不属本单范围（需改线上物理内核，
另立单）。

## 5. 局限与未完成（honest）

* 余下 3 个收集 ERROR 未处理，均为预存且与本单同源不同因：
  `tests/factory`（环境缺 `pgserver` 包）、`tests/test_public_wall.py`（跨模块共享
  `:memory:` engine 的裸 DDL 冲突）、`tests/verifier/test_maneuvering_reference.py`
  （与 `tests/test_maneuvering_reference.py` 同名，`import file mismatch`）。
* 推送黑名单 `tp_pusher.py` 位于工作树外（agent 工具链），**未改动**；本单是在仓内
  用「分层清单 + 可推送性判据」把它事实化为可执行约束，属绕过而非修复推送线。
* 派生图不在仓内长期存活（结构性），需运行时按 `regenerator` 重建；重建脚本的自动化
  接线未做（当前以 `regenerator` 文字出处登记）。
