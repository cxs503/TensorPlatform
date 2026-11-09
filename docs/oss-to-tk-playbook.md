# OSS → 知识 → 开发改进：闭环套路正典 v1

**锚点**：FEAT-OSS-PLAYBOOK ｜ **场景键**：`oss-playbook` ｜ **首版日期**：2026-11-09
**适用域**：任一数值方法域（LBM 已跑通全程；SPH/DG/FVM/BEM/DEM/CAD 推广中）
**状态**：v1 正典。套路本体冻结；每步「已知坑」为 append-only 追加区（新坑只增不改）。

> 一句话：**开源仓是矿产，不是产品。** 本套路的产出物不是「读过多少仓」，而是「知识条目 id → 被哪个开发/工装单按锚点消费 → 改进了哪行代码/哪个测试」这条可机检的链。
> 链断了三步等于零：仓库盘点表漂亮、知识条数漂亮、但没有任何下游单引用条目 id，仍然是没产出。

本文件的所有数字都能用文末「复现」一节的只读命令重算，**零手抄**。

---

## 0. 为什么需要正典（LBM 战役的实测教训）

LBM 战役跑完了完整的五步，也因此把五步各自的失败模式都暴露了一遍。当前真实态（`2026-11-09` 只读 SQL，见 `artifacts/oss_playbook/metrics_2026-11-09.json`）：

| 指标 | 实测值 | 判据门限 | 结论 |
|---|---|---|---|
| 注册仓数 / 已深盘仓数 | 32 / 24（75%） | ≥70% | PASS |
| 零条目的已注册仓 | **8**（tier1 3 + tier2 2 + tier3 3） | — | 待补派 |
| 知识条目数 | 196 条（6.1 条/仓，122 个不同 slug） | — | — |
| 条目带 `file:line` 指针比例 | 97%（190/196） | ≥80% | PASS |
| 条目 `evidence` 直指 `file:line` 比例 | **3%**（5/196，其余 191 条只写 `report <id>`） | — | 见 §3 坑 K3-2 |
| 条目按 `GAP-*` 锚点组织比例 | **11%**（22/196；仅 3 个锚点） | ≥50% | **FAIL** |
| 自由标签 slug | 96 条（其中 42 条是含中文/空格的复合标签） | — | 见 §3 坑 K3-1 |
| transfer 词表 | adapt 77 / reference 63 / fits 46 / none 8 + **reject 1 / reject-as-is 1** | 四值封闭 | 词表漂移 |
| 锚点被下游消费比例 | 1/3（`GAP-LBM-MCMP-POISEUILLE` 被 40 个单位引用） | ≥30% | 勉强 PASS |
| 「引用 `oss_solutions` 条目 id」的改进单数 | **5**（#8623 #8634 #8635 #8636 #8637） | — | 改进实证的分子 |

结论很直白：**普查和指针这两步 LBM 做得很扎实（75% / 97%），锚点组织（11%）与证据直引（3%）是短板。** 推广到新域时，前者是模板红利，后者是必须提前堵的两个漏。

---

## 1. 五步套路总览

```
①候选普查+注册+tier      → oss_inventory 行 + 注册 JSON        [OSS-INV] 半成品
②单仓深盘（代码级定位）   → 八节报告 + 文件:行证据表
③[OSS-SOL] 落库+锚点映射  → oss_solutions 行（slug=GAP 锚点）
④四契约纪律落 TK          → TensorKnowledge Knowledge Object（claim/evidence/provenance/review）
⑤GAP 消费：开发/工装/返修 → 下游单引用条目 id + QR 独立复核 + 测试固化
```

四道门的顺序不可倒：**没有 ③ 的形状校验就不许落库；没有 ④ 的实读复认就不许写进知识库；没有 ⑤ 的条目 id 引用就不许宣称「改进了」。**

---

## 2. 分步正典

### 步① 候选普查 + 注册 + tier 分级

- **输入**：域关键词清单（方法名/近亲方法/教学正统/GPU 实现路线）；`api.github.com` 检索（clone 不通时走 `trees`/`contents` API）。
- **产出**：`artifacts/oss_<域>_registry.json`（`[{repo,tier,focus,lang,license,stars,last_commit}]`，机器可解析）+ `oss_inventory` 行（`repo/tier/focus/status/task_key`）+ 每仓一句话。
- **tier 分级**（沿用 LBM，可跨域复用）：**T1 = 方法专家仓**（该方法的参考实现/作者维护，物理正确性可信）；**T2 = 可微同族与框架**（JAX/Taichi/自动微分、教学正统，价值在 API 与验证面）；**T3 = 专项能力仓**（HPC/GPU 融合/多相/自由面等单项深度）。分级的作用是决定深盘预算：T1 全盘，T2/T3 按 GAP 需要盘。
- **机器可校验判据**：
  1. `≥20` 仓且 JSON 可解析、字段齐全（缺字段 = 注册失败）；
  2. 每仓 `tier ∈ {1,2,3}`、`license` 非空（GPL 家族必须写明——**决定能不能搬码，只能净室重写**）；
  3. GAP 锚点提议 `≥4` 个，命名匹配 `^GAP-[A-Z0-9-]+$`；
  4. 注册单**只注册不深盘**（深盘是波 2 的事，见坑 K1-2）。
- **已知坑**（LBM 实例）：
  - **K1-1 综合单钉模型 → 零席饥饿。** 派发层一旦把单钉在某个具体模型（如 `glm-5.2`）上，只有同名活体 worker 能领；模型没活体实例时单子永远没人领，积压成「静默饿死」。LBM 战役的综合盘点单就踩过这个坑（0 席认领）。根因与修复记录在 `docs/IN20_R4_propose_channel_rootcause.md`（E3：钉模型 = 必须有一个同名活体 daemon；修复 `pick_proposer_models()` 在名册全灭时退化为 `[None]`=不钉模型，任何 worker 可领），以及 `docs/IN27_adoption_stall_a151356d.md`（孤儿单 `unpin_orphan_model` 自动解钉）。
    **纪律：普查/深盘/综合类单一律不钉模型**，模型异构由角色解析决定，不由单子钉死。
  - **K1-2 普查单塞深盘要求 → 批量半成品。** 把「≥20 仓 + 每仓代码级定位」写进同一单，必然导致部分仓只有摘要没有指针。LBM 的处置是分波：wave-0 普查注册、wave-2 深盘补派（`oss_inventory.task_key = campaign-osslbm-w2-*`），并用**补派帽**限制波次规模（`FEAT-OSS-INVENTORY-LBM` 验收条款：T1 多相 6/T2 可微 GPU 8/T3 HPC 10 + 发现波，**总帽 30**）；平台侧并发上限是 `DISPATCH_MAX_CHILDREN = 8`/派发者、`DISPATCH_DAILY_CAP = 24`/24h（`backend/api/tensor_missions.py`）——**排队上限不等于预算上限，波次帽要写在单子里**。
  - **K1-3 用 star 数当质量代理。** 星多的仓常常是教学 demo；T1 应由「是否该方法作者/参考实现」判定，不由 star 判。

### 步② 单仓深盘（代码级定位）

- **输入**：注册行（`repo` + `cache_commit` 固定版本，如 `oss_inventory.cache_commit`/`cache_nodes`）；`/root/oss-cache/<owner>/<repo>` 检出。
- **产出**：八节 markdown 报告（`docs/oss_inventory/OSS-INV-<repo>.md` 或 `docs/oss_inventory/<date>_<repo>_deep_dive.md`）+ **证据指针表**（`tools/oss_inventory/<repo>_evidence.py` 之类的机器可读证据表：`claim → file:line`）+ 可选的独立复算脚本（`*_physics.py`，把报告里的物理关系独立重算一遍）。
- **机器可校验判据**：
  1. 每条能力声明都带 `path:line` 或 `path:start-end`（不是「在 xxx 文件里有」这种无指针描述）；
  2. 版本固定到 commit（浮动 `main` 的指针三个月后就会失效）；
  3. 报告里出现的每个"没有 X"的否定声明，必须给检索证据（`grep -rn "X" | wc -l == 0` 或等价检索命令），**否定声明和肯定声明同等举证责任**；
  4. 独立的第二个脚本能复算出报告里至少一个数字（防「读完即抄」）。
- **已知坑**：
  - **K2-1 读 README 当读代码。** 多数仓 README 的功能列表比代码宽（TCLB 的 MCMP 文档写着接入 EOS，代码里 `psi = rho` 线性且 EOS 是 `TO-DO`）。LBM 的处置是在 `[OSS-SOL].conventions` 里显式写 **"EOS 接入为 TO-DO，勿当已验证"**，把文档-代码落差记进条目本身。
  - **K2-2 综合报告替代单仓报告。** 一单盘 5 仓 → 每仓 2 段概览，无指针。深盘必须一仓一单（一仓一报告），综合报告是波末汇总，不是深盘产物。
  - **K2-3 许可证失忆。** GPL-3.0（TCLB）/ 部分 GPL 家族的仓只能**净室重写**，条目 `transfer` 必须落到 `adapt`（照思路重写）/`reference`（只当参照）/`fits`（可直接移植）/`none`（不采用），不能默默照搬代码片段。

### 步③ [OSS-SOL] 机器块落库 + GAP 锚点映射

- **输入**：步②的证据表与报告。
- **产出**：报告内嵌的 `[OSS-INV]` / `[OSS-SOL]` 机器块（形如 LBM 的 `docs/oss_inventory/2026-11-09_TCLB_machine_block.txt`）→ 落 `oss_inventory` / `oss_solutions` 行。

  块契约（字段名冻结；改字段名 = 改契约，须升版本）：

  ```
  [OSS-SOL]
  repo: owner/name            # 必须 owner/name
  slug: GAP-<域>-<主题>-<子题> # 锚点是「缺口」，不是「功能点」
  tag: short-method-tag       # 方法标签，可多条目共用
  method: 一到两句：做了什么、怎么做的、绕开了什么瓶颈
  files: path.c:580, path.c:586-599   # 至少一个带行号的指针
  conventions: 符号 → 含义；含「TO-DO/未验证」显式标注
  transfer: adapt | reference | fits | none
  evidence: 更细的证据（推荐 path:line；只有 report id 时在 §3 记为核心债）
  [/OSS-SOL]
  ```

- **机器可校验判据**（`tools/oss_inventory/machine_blocks.py`，本单新增）：
  - 必备字段齐全（`repo/slug/tag/method/files/conventions/transfer`），未知字段=违约（键同义漂移会被 loader 静默丢弃）；
  - `transfer ∈ {adapt, reference, fits, none}`（**四值封闭**）；
  - `files` 至少一个 `path:line` / `path:start-end`；
  - `slug` 锚点化（非 ASCII 自由标签判违约）；
  - 重复键判违约。
  CI 用法：`python -m tools.oss_inventory.machine_blocks docs/oss_inventory --strict`（有违约即 exit 1）。
- **已知坑**（均由本单的校验器在真实文档上复现，不是推测）：
  - **K3-1 slug 命名漂移 → 锚点失效。** 196 条里只有 22 条（11%）是 `GAP-*` 锚点；`NEW:` 前缀 115 条，其中 **42 条是含中文/空格的复合标签**（如 `NEW:GPU 融合 kernel 与压缩布局`、`分布式多卡`、`案例组织`）。这些标签当"功能点"读没问题，但当锚点读就查不出来——`feature_slug` 里既存缺口锚点又存功能分区，锚点空间被污染。
    **纪律（v1 起）**：`GAP-<域>-<主题>-<子题>` 只描述**缺口**；功能分区信息放 `tag`；`NEW:/CAP-` 前缀留给"新增能力"，不得与 `GAP-` 混用。
  - **K3-2 证据降级成 report id。** 196 条里 191 条的 `evidence` 只写 `report 9545`（指向平台报告 id），只有 5 条直指 `file:line`；真正可复现的指针躺在 `files` 列（97% 有行号）。后果：知识库的"证据"字段需要二次跳转才能定位，自动核验不能做。
    **纪律**：`evidence` 必须是 `path:line`；report id 放 `[OSS-INV].report_id`（表列），不占证据位。
  - **K3-3 词表漂移。** `transfer` 出现 `reject`（1 条）与 `reject-as-is`（1 条）两个契约外取值——语义上它们都等于 `none`，但下游按枚举过滤时会漏。
  - **K3-4 上帝脚本。** 每个仓的机器块曾被手敲进 psql，没有摄入器 → 词表与字段漂移就是这么进来的。**v1 起：块由校验器过闸后再入库**；手敲 psql 写入等于无审计。

### 步④ 四契约纪律落 TensorKnowledge（实读复认，非搬运）

- **输入**：已过闸的 `[OSS-SOL]` 条目 + 上游仓检出（`/root/oss-cache/...`）。
- **产出**：TensorKnowledge `Knowledge Object` + **四份契约记录**（schema 见 `TensorKnowledge/schemas/`）：
  1. **`claim-record`**：一条可证伪的断言（`statement / claim_type / epistemic_status / evidence_refs`）；
  2. **`evidence-record`**：`source + relation + locator + captured_at + assessment_status`（`locator` 必须能直接定位，即 `file:line`）；
  3. **`provenance-record`**：`generated_entity / activity / agents / derived_from / recorded_at`（谁、用什么流程、从哪个上游产物派生的）；
  4. **`review-event`**：`object_ref / decision / reviewer / reviewed_at / policy_version`，**reviewer ≠ 生产席**（无自批）。
- **机器可校验判据**：
  1. 四个 schema 全过（`TensorKnowledge/schemas/*.json`）；
  2. 每条 claim 的 `evidence_refs` 非空，且 evidence 的 `locator` 能在实读检出里命中（实读复认的机器形式：**指针可解析**）；
  3. `epistemic_status` 只允许诚实取值——代码确认的证据不能写 `verified` 除非有独立复算；文档推断一律降级；
  4. `review-event.decision` 的 `reviewer` 与 `provenance.agents` 不相交（防自批）；
  5. 每条知识对象映射回至少 1 个 `GAP-*` 锚点（否则它在 ⑤ 里找不到家）。
- **已知坑**：
  - **K4-1 搬运替代复认。** 把 LBM 报告里的段落复制进知识对象，`locator` 填报告行号——这不是实读，是二手转录。LBM 的知识落地单验收条款明确要求"**全部 oss-cache 实读定位逐字引文**"，即 locator 指向上游源码而不是我们自己的报告。
  - **K4-2 自批。** 生产席给自己签 `review-event` → 知识库退化成草稿箱。四契约里的 review 必须异席（LBM 的 A/B 试点就是这么做的：开发臂与 QR 复核臂物理隔离分支，`docs/IN20_*`/试点 prereg 冻结判据）。
  - **K4-3 知识对象与锚点脱钩。** 知识条数涨了但没人按锚点检索得到 → ⑤ 空转。落地单验收里"每条映射回 GAP 锚点"就是防这个。

### 步⑤ GAP 消费：开发/工装/返修单引用条目 id + QR 验证

- **输入**：`GAP-*` 锚点 + 其下条目（`oss_solutions` 行）+ `feature_ledger` 里该锚点的 `acceptance`（机器可判的验收条款）+ prereg 哈希。
- **产出**：下游改进单（开发/工装/返修三类）+ 报告里的**消费链映射表**（「用了哪几条 `oss_solutions` 条目 → 落在哪个文件哪一段」）+ pytest 固化的基准/护栏 + 独立 QR 复核结论。
- **机器可校验判据**：
  1. 单子里必须写「消费链显式」为验收硬项：报告含 ≥N 条 `条目 id → 代码位置` 映射（LBM 的工装单取 N=5）；
  2. 有量化判据（例：Young-Laplace Δp vs 2σ/R 误差 <5%，spurious current <0.005·U_max），**判据先于动手**并存哈希；
  3. 每项固定进 `tests/`（验收= `pytest` 全绿 + 数字表程序化注入，零手抄）；
  4. 独立复核席按冻结判据盲裁（只读本臂与 main，禁读对臂分支）。
- **LBM 的实证样本**：锚点 `GAP-LBM-MCMP-POISEUILLE`（`feature_ledger` 里 `acceptance` 五条判据，prereg sha256 冻结）→ 引用它的单位 40 个（含 A/B 双臂开发 #8280 与 QR 复核 #8540/#8542、技术审查 #8541）→ 首个"知识→工装"改进单 #8636（MCMP 校准与证伪基准工装，`tests/benchmarks/` 固化，要求 ≥5 条条目 id 映射、禁凭盘点报告直接抄实现、源码读 `/root/oss-cache/` 核实）。
- **已知坑**：
  - **K5-1 消费链造假。** 报告列了条目 id，但代码与条目毫无关系（贴 id 凑数）。机检方式：条目 `files` 指针指向的上游语义必须能在下游代码里找到对应物（同行数级别的功能对应），QR 席逐条对照。LBM 的处置是**同步要求"禁凭盘点报告直接抄实现，源码读 oss-cache 核实"**——条目只是索引，实现必须回源。
  - **K5-2 判据事后编。** 先跑出数字再写验收线，永远绿。纪律：prereg 哈希先冻结（`GAP-LBM-MCMP-POISEUILLE.evidence` 里存 sha256）。
  - **K5-3 只做"改进了"的叙事，不做回归。** 没有 pytest 固化的工装，下一波会被静默改坏。改进单的验收必须包含测试文件路径。
  - **K5-4 honest FAIL 被覆盖。** 校准没达到 <5% 就写"接近"。诚实 FAIL 入 S-N 否定性成果轨，不进失败率——**写 FAIL 比写假绿便宜**。

---

## 3. 度量指标（三个头条 + 门限）

由 `tools/oss_inventory/playbook_metrics.py` 只读计算（`--json-out` 存工件；`--strict` 让门限失败 exit 1，可直接当波次收口门）：

| # | 指标 | 定义（SQL 口径） | 门限 | LBM 基线 2026-11-09 |
|---|---|---|---|---|
| M1 | **知识条数** | `oss_inventory` 仓数；至少有 1 条 `oss_solutions` 的仓数（深盘覆盖）；`oss_solutions` 行数与条/仓 | 深盘覆盖 ≥70% | 32 仓 / 24 深盘 = **75%**；196 行；6.12 行/仓 |
| M2 | **锚点组织度** | slug 分类 gap / namespaced / free-label 比例；带 `file:line` 指针比例 | 锚点化 ≥50%，指针 ≥80% | 11% / 97% → **锚点 FAIL** |
| M3 | **按锚点消费次数** | 每个 `GAP-*` 锚点被多少下游单（`tensor_dev_missions.goal`）逐字引用 | 被消费锚点比例 ≥30% | 3 锚点，1 被消费（40 次引用）= **33%** |
| M4 | **改进实证数** | goal 里显式要求引用 `oss_solutions` 条目 id 的单位数（消费链硬项） | 单调增 | **5**（#8623 #8634 #8635 #8636 #8637） |

门限理由：
- **锚点化 ≥50%**：v1 之后新条目一律锚点化，存量 22 条是历史债；50% 是"存量不算、增量补齐"下一年内可达的水平，而不是给现状放水。
- **消费锚点 ≥30%**：不是所有锚点都值得开发（有些是 `none`/观察项）。30% 防止"提议一堆锚点刷指标"。
- **深盘覆盖 ≥70%**：剩下 30% 留给"注册后判定不值得深盘"的仓，但必须显式标 `tier`+理由。

---

## 4. 六域推广 checklist（各域差异项）

通用部分照抄 §2 五步；下表只列**必须替换**的差异项。`[]` 为开工前必须填的空。

| 域 | 候选源（搜索面） | 分级标准差异 | GAP 锚点命名 | 域特有坑 |
|---|---|---|---|---|
| **SPH** | WCSPH/ISPH/ΔSPH 实现、多相/自由面、FEM-SPH 耦合、GPU（CUDA/JAX/Taichi）、HPC、教学正统 | T1 加"是否含核函数/时间积分器可选实现"（影响可复用性判定）；T2 重"可微/可并行" | `GAP-SPH-<现象>-<机制>`，如 `GAP-SPH-FREESURFACE-INTEGRITY` | 粒子法**无格子解析基准**，验证面必须绑解析/实验锚（dam break、Poiseuille、Taylor-Green）；"参数敏感"条目必须写稳定域 |
| **DG** | 可压缩 Euler/NS DG、DG-FVM 混合、熵稳定/子单元限界、GPU DG、SD/FR(huxi/CPR) 近亲、教学正统(Hesthaven-Warburton) | T1 加"是否有熵稳定性/限幅器证明或数值验证"；T2 教学正统单列（正典公式来源） | `GAP-DG-<算子>-<障碍>`，如 `GAP-DG-LIMITER-SUBCELL` | 收敛阶必须给**实测收敛表**（h/p 双维），只写"高阶"不算证据；边界条件实现差异极大，指针要指到 face/limiter 具体函数 |
| **FVM** | OpenFOAM 系求解器/库、可压缩/不可压、多相 VOF/Level-Set、GPU FVM、开源 CFD 教学码 | T1="工业级参考实现"；T2="库/工具链"；T3="单项物理模型" | `GAP-FVM-<方程/模型>-<子项>`，如 `GAP-FVM-VOF-SHARPNESS` | 仓大切片深：**先按 solver + 库两层定位**再指指针，否则 `file:line` 第二天漂移；许可证（GPL 家族）多为净室重写 |
| **BEM** | 势流/波浪 BEM（Rankine/Green 函数）、水弹性耦合、快速多极、GPU BEM、开源船舶水动力码 | T1=船舶/海洋工程验证过的实现；T2=通用 BEM 库；T3=特定 Green 函数/奇异积分处理 | `GAP-BEM-<边界条件>-<数值障碍>`，如 `GAP-BEM-FREESURFACE-IRREGULARFREQ` | 奇异积分与不规则频率是核心缺口，条目必须写清**去奇异手法**；验证锚要绑解析（球/圆柱绕射）与实验两口径 |
| **DEM** | Yade/LIGGGHTS/MFiX 系、GPU DEM、接触模型库、颗粒-流体耦合（CFD-DEM） | T1=接触模型完整且被验证；T2=框架/耦合器；T3=特定接触律 | `GAP-DEM-<接触/耦合>-<子项>`，如 `GAP-DEM-CONTACT-ROLLING` | 接触参数（摩擦/恢复系数）**不可跨仓照搬**，条目 `conventions` 必须写单位制与标定来源；时间步由接触刚度决定，必须给稳定判据 |
| **CAD** | OpenCASCADE 系、几何内核/网格生成、参数化与特征树库、船舶型线工具 | T1=几何内核；T2=上层建模/网格；T3=格式与互操作 | `GAP-CAD-<几何操作>-<鲁棒性障碍>`，如 `GAP-CAD-BOOLEAN-ROBUSTNESS` | 几何必须**先验证 Cb/L/B** 再谈性能（与船舶平台纪律一致）；内核许可证差异大（LGPL/OCCT 例外条款），`transfer` 判定要先过法务口径 |

推广时的三条硬纪律：
1. **一域一波**：普查（wave-0）与深盘（wave-2）分开下单；波次帽写进单子（LBM：总帽 30）。
2. **不钉模型**（K1-1）；深盘单**一仓一单**（K2-2）。
3. **入域第一件产物是校验器**：新域的 `slug` 前缀（`GAP-SPH-`…）先配好，再让 agent 写块——否则 42 条自由标签的债会重演（K3-1）。

---

## 5. 复现（全部只读）

```bash
# 1) 机器块体检（真实文档集，违约即非零退出）
python -m tools.oss_inventory.machine_blocks docs/oss_inventory

# 2) 五步度量 + 门限（只读 SQL；--strict 作为波次收口门）
python -m tools.oss_inventory.playbook_metrics \
    --json-out artifacts/oss_playbook/metrics_2026-11-09.json

# 3) 图（数值全部从库里读，零手抄）
python -m tools.oss_inventory.plot_playbook_metrics   # -> docs/oss_playbook_metrics.png

# 4) 工具自测（合成夹具，无域词表）
python -m pytest tests/tools/test_oss_playbook.py -q
```

---

## 6. v1 遗留（诚实登记）

1. **入库无摄入器**：机器块 → `oss_inventory`/`oss_solutions` 仍靠人工 psql；本单只交付**过闸校验器**与**度量器**，摄入器（`--apply`）留待下一单（需要事务/幂等/回滚三件事）。
2. **锚点历史债 174→196 条口径不一**：任务书写的 174 条、本文实测 196 条，说明战役期间的条数是滚动增长的；引用条数时**必须带时间戳**（本节口径 = 2026-11-09 只读快照，见 `artifacts/oss_playbook/metrics_2026-11-09.json`）。
3. **M3/M4 的 goal 文本匹配是弱机检**（逐字包含锚点 id）。强机检需要下游单显式结构化字段（`consumes_anchors: [slug...]`），属平台 schema 变更，未在本单范围。
4. **8 个零深盘仓未补派**（tier1 3 / tier2 2 / tier3 3），已列入 M1 门限监控，未在本单补盘（本单是套路正典化，不是补盘单）。