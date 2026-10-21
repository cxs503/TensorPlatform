# benchmark_closure 派发线（owner 指令：所有 benchmark 做闭环）

owner 2026-10-19 指令：平台 agent 尚不能自主选题 → 先把**全部 benchmark** 做闭环。
本目录是该常设线的全部机制；被派发的 mission 以本手册为工作规范。

- 派发器：`benchmark_dispatch.py`（cron */10）——串行派发（同线仅 1 个 mission 在飞）、
  中继健康门（relay 不通不派）、gateway_unavailable 自动重派（≤3 次）、
  终态 succeeded 自动 push+开 PR+代发平台报告。
- 账本：`backlog.json`（条目=闭环单元）、`state.json`（派发状态，机器维护）、
  `goals/<key>.txt`（每条目 goal 全文，由 `build_backlog.py` 生成）。
- 停机开关：`touch OFF`（本目录内）即暂停派发。
- 范围：TensorLBM `benchmarks/pending/` 全部案例 + Wave-12 WB2 晋级。
  **不在范围**：marine sweep 五案（dtmb5415/kcs/propeller_openwater/series60/
  suboff_highre）与 aircraft_icing_rg15_ipw2——属各自战役线，勿动。

## 你（被派发的 mission）的工作流

1. goal 指明案例与指定动作；先读本手册，再读你 worktree 内
   `benchmarks/pending/<case>/README.md`（卡点、晋级路径、PROVENANCE 机器溯源）。
2. 按该案「晋级路径」推进到闭环终态（见下）。
3. 每案新账本：`/srv/tensor/shared/runs/bm_close/<case>/`——prereg append-only
   （先冻结参考与判据、后跑；跑后只允许追加，不改写既有条目）。

## 闭环标准（严格四门，与 verified/ 一致）

1. 共性模块入口：`src/tensorlbm` 库路径驱动，非独立脚本；
2. 直接观测量对预注册锁定参考 ≤3%；
3. ≥2 档网格严格单调收敛；
4. 参考先锁定后跑（防参考选购）；修正/还原变换不算直接模拟。

## 闭环的两种合法终态（都算完成；禁止无证据改判）

- **A 晋级**：补齐四门 → 归档 PR（`pending/<case>/` → `verified/<case>/`，
  同步 `pending/README.md` 该行状态与统计、`verified/` 索引）；
- **B 终裁**：维持 FAIL/判死/口径裁定 → 更新该案 README（根因证据链 + 修订后的
  晋级路径），或完成 README 标注的 owner 待决策项并给出裁定文档。
  数字零手抄：一切判决值走 PROVENANCE 值-文件-键三元组直读工件。

## 资源纪律

- Python：`/srv/tensor/shared/venvs/tensorlbm/bin/python`（torch cu128）。
- GPU：**只准 GPU5**（`CUDA_VISIBLE_DEVICES=5`）；GPU0-4 他人服务，禁碰。
- 计算超出 mission 墙钟时：`nohup` 分离运行（只占 GPU5）→
  占用标记 `/srv/tensor/shared/runs/bm_close/GPU5_BUSY`（内容：案例、PID、预计时长、
  完成清单路径）→ 运行结束由包装脚本写 `<case>/DONE.json` 完成清单 →
  派发线的收割 mission（`*_harvest` 条目）验收判据、出 PR。
  收割 mission 验收完毕必须删除 GPU5_BUSY。
- kill 仅精确 PID；不碰跳板机 80/8088/8000；yueyue 的 /tmp 不动。

## 交付（全部满足才算完成）

1. 指定动作的闭环产物（PR 内容或终裁文档）；
2. 机器门禁 `gates.json`（见附录）：把你报上来的每个判决数字（晋级与终裁都要，
   失败的数字也要）声明为 值-文件-键 三元组；派发器 push 前用
   `gates_check.py` 逐一重取工件复算，**未过不推**——三元组对不上=手抄=退回；
3. commit 到本 mission 分支（中文提交信息，聚焦该案，不重构无关代码；
   不改 `verified/` 既有案例与判定）；`run_tests` 全绿后 `request_acceptance`
   （branch=`agent/m<id>`，summary 给证据摘要，evidence_files 填判据工件路径）；
4. 论文级平台报告：写 `<worktree>/report_<case>.md` + 同目录 `fig_*.png`
   （物理问题表述+示意、结果云图/曲线、判据表、审计溯源、局限与缓解；
   纯文档裁定类可豁免云图，但判据表+审计必须齐全；数字全部来自工件）。
   派发线代你发布到平台（tensor_reports）并自动 push+开 PR；**merge 留 owner**。

## 红线

不 git push（派发线统一推）；不动 verified/ 历史；不动范围外五+一案；
kill 仅精确 PID；GPU0-4 与跳板机端口不碰；预算纪律：交付优先，
预算过半尚未开写交付物即停新诊断、先落交付。

## 记忆

memory_key=`benchmark-closure`：班次结束前更新
`/srv/tensor/shared/tensor_eco/agent_memory/benchmark-closure/context.md`（交接笔记：
本案例卡在哪、下一班从哪接手）；事实入 memory.jsonl（终态自动入账）。

## 附录：机器门禁 gates.json（A 改进，2026-10-19 起）

位置：worktree 内 `benchmarks/verified/<case>/gates.json`（晋级后）或
`benchmarks/pending/<case>/gates.json`（终裁/未晋级）。schema：

```json
{
  "case": "sphere_re100_mem",
  "verdict": "promote",            // promote | terminal
  "reason": "",                    // terminal 必填：终裁根因一句话
  "gates": [
    {"gate": "library_entry", "pass": true,
     "evidence": [
       {"v": "from tensorlbm", "f": "benchmarks/verified/sphere_re100_mem/run.py",
        "k": "grep:from tensorlbm"}]},
    {"gate": "direct_observable", "pass": true,
     "evidence": [
       {"v": 0.0027, "f": "results.json", "k": "grid[0].rel_err_pct"},
       {"v": 0.0031, "f": "results.json", "k": "grid[1].rel_err_pct"}]},
    {"gate": "monotone_convergence", "pass": true, "mode": "abs_decrease",
     "evidence": [
       {"v": 0.0031, "f": "results.json", "k": "grid[0].rel_err_pct"},
       {"v": 0.0027, "f": "results.json", "k": "grid[1].rel_err_pct"}]},
    {"gate": "reference_locked_first", "pass": true,
     "evidence": [
       {"v": "2026-10-19", "f": "prereg.md", "k": "grep:锁定参考日期"},
       {"v": "2026-10-19", "f": "results.json", "k": "run_date"}]}
  ]
}
```

三元组 `{"v", "f", "k"}` 语义：`v`=你报的值，`f`=工件文件（相对 worktree，
绝对路径也可），`k`=取值键。`k` 两种形式：

- **JSON 键路径** `a.b[2].c`：`f` 必须是 JSON，机器打开文件走路径重取数值，
  数值相对容差 1e-9、字符串严格相等；
- **grep 模式** `k` 以 `grep:` 开头：子串必须存在于 `f` 文本中，且 `v` 的字符串
  形式也须在文中（用于源码入口、prereg 冻结句等非 JSON 证据）。

机器复算规则（`gates_check.py`，派发器 push 前强制执行）：

- `verdict=promote`：四门齐全且全过；`direct_observable` ≥2 个数值三元组且
  全部 |v|≤3.0（相对误差百分比口径）；`monotone_convergence` 按档位顺序
  严格单调（`mode: abs_decrease` 对绝对值、`decrease` 对原值，eps 1e-12）；
  `reference_locked_first` 若锁定/运行日期都在，锁定不得晚于运行。
- `verdict=terminal`：必须给 `reason`，且至少一门声明未过（`pass:false`）——
  **失败的数字同样要三元组对上工件**（防"编个失败理由"）。
- case 字段与请求案例不符、任何三元组取不到/对不上 → 复算不过 → 不推（终态）。
- 复算通过会写 `gates_verified.json` 在 gates.json 旁（审计痕迹，随 PR 入库）。

## paper 档两门（owner 2026-10-20 质量门升级：案例要有细节+云图+曲线）

`--profile paper`（backlog 条目 `paper:true`，2026-10-20 起的新派发条目与全部
图档补强条目）在原四门之上**追加两门**；这两门**不信 gates.json 声明**，
`gates_check.py` 直接读文件本体机械检查：

### case_readme（案例细节介绍）

`<case_dir>/README.md`（case_dir = gates.json 所在目录）机械五查：

1. 存在且 ≥1200 字节（独立成篇的深度下限）；
2. ≥2 个 `##` 小节（物理问题 / 运行 / 判据参考 等分节）；
3. 含 `PROVENANCE` 字样（溯源块）；
4. 含 `python`（可复现的运行命令）；
5. 含 `参考`/`Reference`/`文献`/`anchor`/`锚` 之一（判据锚交代）。

### figures（云图+曲线）

`<case_dir>/figures.json` manifest + `<case_dir>/figures/*.png`：

```json
{"figures": [
   {"file": "figures/field_midplane.png", "kind": "field",
    "caption": "中剖面速度幅值云图", "source": "runs/.../dump.npz + plot_figs.py"},
   {"file": "figures/curve_conv.png", "kind": "curve",
    "caption": "误差-网格收敛曲线", "source": "results.json + plot_figs.py"}],
 "field_waiver": ""}
```

机械规则：`kind ∈ {field, curve, diagram}`；**≥1 曲线（curve）**；**≥1 云图
（field）或 `field_waiver` 非空豁免理由**（无空间场量的案，如碰撞核弛豫谱）；
有云图时合计 ≥2 图；每图真 PNG 魔数且 ≥6KB；caption ≥8 字；source 非空
（图从哪个数据工件+哪个脚本生成——图件面的零手抄）。plot 脚本入库。

### 裁决规则

- `promote`（paper）：六门俱全且全过（原四门 + 两新门）。
- `terminal`（paper）：两新门**同样必须过**（曲线必须；云图可走豁免），
  外加原有 terminal 语义（reason + 至少一门声明未过）。
- 派发器 push 前 `--profile paper` 复算，未过不推；失败细节（缺什么检查项）
  上浮到 problems 顶部，直接看派发日志即可诊断。
- classic 档（2026-10-20 前派发的在飞/已终态条目）不受追溯；其案例的图档
  补强走 `figdoc-*` 补强条目，补齐后同样过 paper 六门。
- 平台报告代发时自动携带 worktree 根 `fig_*.png` 与案例目录 `figures/*.png`。


## 终态路径教育（2026-10-20 增补）

两条合法完成路径，都算闭环、都 push 归档：
1. **promote**：判据全过（单调收敛+误差达标+参考锁定）→ verified/<case>/；
2. **terminal**：判据不达标 → pending/<case>/ + 物理诊断 reason + 证据三元组
   照齐。负结果同样是研究产出，不扣分、不重派。

报假 promote 的下场：六门机械复算拦下 → 条目进人工位 → 白跑一轮预算。
门禁不查"你是否努力"，只查"数字是否真的从工件里读出来"。

## 派发器新字段（2026-10-20 P2a/P2b/P3）

- `isolated:true` —— 条目只碰自己案目录+自己的 report 文件（figdoc 类）。
  cpu 道此类条目可并行至 3；非隔离条目（可能碰共享索引/README）须空道串行。
- `wait_for_pr:<num>` —— 等 GitHub PR #num 并入 main 后才派（防同案双 PR）。
- push 前新增**本地套件门** suite_check.py：全库 pytest 收集 + GPU5 空闲时
  四 Triton 模块聚焦测试；红 → SUITE_FAILED 终态（CI runner 失联期间的
  本地兜底，防 #371 型合并伤损再进库）。
- gates_check 新增：figures.source 提到的文件必须存在（防 manifest 造空引用）；
  PNG 字节多样性 <8 判空白/单色占位图拒收；--auto 模式扫全库 gates.json。
- line_status.py —— 一条命令看全线（条目状态表+一次通过率+open PR）。
