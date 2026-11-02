# m1802 · 验证资产被合并门禁滤除导致 12 项验证测试静默失效：复位 + 守卫

日期：2026-11-01/02 · 席位：verifier（tp-ver-4）· 仓库：TensorPlatform · 分支：agent/m1802

## 1 问题（怎么发现的）

按 verifier 席常规做验证欠账扫描：把 tests/ 下引用 `artifacts/` 的验证测试成套跑一遍，
发现**已合并的验证测试在 HEAD 上是红的**：

| 测试文件 | 症状 | 数量 |
|---|---|---|
| `tests/test_hydrostatics_reference.py` | `FileNotFoundError: artifacts/verifier/hydro_curves_kcs_snapshot.json` | 3 FAIL |
| `tests/test_propulsion_openwater_gate.py` | `FileNotFoundError: artifacts/m1584/prop_openwater_fixture.json`（module fixture） | 9 ERROR |
| `tests/test_maneuvering_reference.py` | `ModuleNotFoundError: mmg_ref`（收集期即失败） | 整模块 ERROR |
| `tests/verif/test_damage_stability_verdict.py` | `ModuleNotFoundError: solas_reference`（收集期即失败） | 整模块 ERROR |

根因不是算法，也不是测试写错，而是**证据文件与消费方走了两条不同的门禁通道**：

* 各 mission 分支的合并提交普遍带注记「N 个文件被门禁滤除」，被滤掉的正是 `artifacts/**`；
* 验证测试本身在 `tests/**`，不受滤除，于是留下**指向已消失证据的断言**。

后果比"文件缺失"严重：门禁还在 CI 列表里、名字还叫 gate，但**不执行任何物理判定**——
零成本假绿（或假红被当环境问题忽略）。这正是「证据分级」体系最怕的一类退化。

## 2 做了什么（修复）

1. **资产回捞**：从 git 历史 blob 精确还原 11 个文件（`git rev-list --all --objects` 定位，
   按首次新增提交取版本），落到**不被门禁滤除**的版本化目录
   `tests/data/verifier_assets/{hydrostatics,propulsion,maneuvering,damage_stability}/`。
2. **消费方路径改为"版本化优先 + artifacts 回退"**（4 个测试模块），
   保证已手工恢复过 artifacts 的环境不受影响：
   - `test_hydrostatics_reference.py`：新增 `_load_snapshot()`，缺资产时 `pytest.fail`（**不 skip**，避免"环境问题"式假绿）。
   - `test_propulsion_openwater_gate.py`：`FIXTURE_CANDIDATES` 候选链。
   - `test_maneuvering_reference.py` / `tests/verif/test_damage_stability_verdict.py`：`VDIR` 候选链。
3. **采集端改为写版本化目录**：`tools/verifier/geometry/audit_hydro_curves.py`
   的 `--live` 快照现在落到 `tests/data/verifier_assets/hydrostatics/`（`SNAP_VER`），
   读回时优先版本化路径；下次重采集不会再生成"会被滤掉"的证据。
4. **防复发守卫** `tests/test_verifier_assets_integrity.py`：
   - 清单 `manifest.json` 逐个文件校验 sha256 + 字节数（资产被替换/截断即失败）；
   - 断言资产路径**不得**位于 `artifacts/` 下（堵住同形问题）；
   - 断言 4 个消费方测试含版本化路径（防止有人改回去）；
   - **负对照**：把候选路径指到空目录，断言 `_load_snapshot()` 抛 `pytest.fail` 而不是 skip。
5. 文档纪律写进 `tests/data/verifier_assets/README.md`（只增不删、必须登记校验和、重采集命令）。

## 3 结果（可复现）

```
python3 -m pytest tests/test_verifier_assets_integrity.py tests/test_hydrostatics_reference.py \
    tests/test_propulsion_openwater_gate.py tests/test_maneuvering_reference.py \
    tests/verif/test_damage_stability_verdict.py -q
53 passed in 3.27s          # 修复前：3 failed + 11 error（2 个模块收集期即死）
```

全量收集副作用（此前 `tests/` 收集被 4 个 ERROR 中断，只能证明 1327 项里的前一部分）：

```
python3 -m pytest --collect-only -q tests/     # 修复前 4 errors
  ERROR tests/factory                -> ModuleNotFoundError: pgserver     （环境缺依赖，非本单范围）
  ERROR tests/test_maneuvering_reference.py   -> 本单已修
  ERROR tests/test_public_wall.py    -> sqlite "table organizations already exists"（环境/遗留，非本单范围）
  ERROR tests/verif/test_damage_stability_verdict.py -> 本单已修
```

即：**验证通道自身导致的收集中断已全部消除**，剩下 2 项属依赖/环境问题，已如实记录未修。

## 4 顺带复核出的两个平台物理缺陷（存档证据重算，非新结论）

复位后的证据重算仍在（见 `artifacts/m1802/metrics.json`、图 1/2）：

| 缺陷 | 证据 | 判定 |
|---|---|---|
| KCS 水曲线 `Cb` 恒为 **1.1949**（5 个吃水完全不随吃水变），且 **Cb>1 物理不可能**，声明的 Cb=0.651 | 判据 I1 相对误差 19.5% (>15% 不可接受门) | FAIL |
| `d∇/dT` 与 `Awp` 不自洽（浮体几何恒等式） | 相对误差 **46.0%**（门 1%） | FAIL |
| 螺旋桨 open-water：`eta0` 在 J≥0.6 后被**硬钳制在 0.85**，与动量恒等式 `η=J·KT/(2πKQ)` 最大偏离 **40.7%** | m1584 fixture 78 点回放，gate 判决 **MOCK** | MOCK 维持 |

这三条是**回归判据**，现在由 53 项测试常驻把守，任何人改动水曲线/推进链都会立刻见红。

## 5 注意点与局限

* 本单是**验证基础设施**修复，不是算法修复：平台缺陷（Cb>1、eta0 钳制）仍是**平台侧**欠账，
  修它属 `ship-performance-platform` 仓（纪律：船舶缺口修复落该仓），本单只保证"欠账不会被静默吞掉"。
* `mmg_ref.py` / `solas_reference.py` 等参考实现是从历史 blob 回捞的**原样副本**，
  未做算法审计；其正确性由各自测试的解析断言（SOLAS Reg.6/Reg.7 闭式、MMG 量纲自洽）覆盖。
* 资产清单只登记 11 个**当前被测试直接消费**的文件；`artifacts/**` 下其余图片属可再生，不登记。
* sha256 锁定意味着**有意更新证据必须同步改 manifest**——这是设计意图（防无声替换），不是障碍。
* 全量 `tests/` 仍不能一次跑完（pgserver / sqlite 遗留），本单未触碰该环境问题。

## 6 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 基准 | 相对误差 | <3% ? |
|---|---|---|---|
| 平台水曲线 Cb（KCS，5 吃水） | 声明 Cb=0.651；另 Cb≤1 物理上限 | 19.5% | **否** |
| d∇/dT vs Awp（浮体恒等式） | 严格恒等式 | 46.0% | **否** |
| VCB/T（水曲线各吃水） | 该船型合理带 KB/T≈0.52–0.56；此处仅以 VCB 与 T 的比例偏离 1 计 | 最大 8.9% | **否** |
| 螺旋桨 η0 vs 动量恒等式 | η=J·KT/(2πKQ) | 40.7%（最大） | **否** |
| 验证资产完整性（11 文件） | 自身 sha256 | 0% | **是** |
| 修复后测试通过率 | 目标 100% | 53/53 = 100% | **是** |

> 结论：**测试基础设施**达标（0 误差、53/53 绿）；**平台物理量**仍不达标（19.5%/46.0%/40.7%
> 全部 >3%，也 >15% 不可接受门）。本单不粉饰：修好的是"看得见"，不是"算得对"。

## 7 产物指针

* 资产：`tests/data/verifier_assets/`（含 `manifest.json`、`README.md`）
* 守卫：`tests/test_verifier_assets_integrity.py`
* 复算脚本：`docs/verification/gen_m1802_assets.py`
* 数据/图：`artifacts/m1802/metrics.json`、`fig1_hydro_curves_identities.png`、`fig2_propulsion_openwater_gate.png`