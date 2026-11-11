# 秘密泄露三出口 fail-closed 扫描器（P9263-N09）

> 本文件由 `python -m tools.secret_scan.gen_doc` 从 `artifacts/secret_scan/acceptance.json`
> 与 `artifacts/secret_scan/history_exposure.json` **程序化注入**生成，正文数字零手抄。
> 策略 `data/secret_scan/policy.json`，sha256 `0f043ed216ae6d18a87d25d3d39570e98e99339432e2b372409d185f9a21449c`。

## 1. 案例介绍：为什么这是一个第一性的门

owner 纪律明文要求「commit 不得包含秘密」，同一条纪律也适用于报告 POST 与 artifact 落盘。
在此之前，这条纪律**没有任何机制能证明它成立**：秘密一旦进 commit 就进了历史（不可逆），
一进已发布的报告就是公开，一进 artifact 就是随证据出厂。三者是同一失效在三个出口上的投影，
所以本节点是**一个引擎 + 三个 fail-closed 出口**，不是一个检查脚本。

对象：一次「内容离开工作树」的事件。三个出口 = `pre_commit`（diff 级）、`report_gate`
（payload 级，含 base64/hex 解码后重扫）、`artifact_writer`（写盘前）。三条检测路线并联：
模式库（PEM/JWT/DB URL/云 key/Bearer/kv）、Shannon 熵（无前缀裸 key）、已知值精确匹配
（内存比对，不落盘）。命中即可自动脱敏改写为 `<REDACTED:type:len>`。

参数表（全部外置 `data/secret_scan/policy.json`，代码零硬编码）：

| 组 | 键 | 含义 | 现值 |
|---|---|---|---|
| entropy | `min_length` / `max_length` | 裸 key 长度窗 | 20 / 256 |
| entropy | `min_bits_per_char` | 香农熵门 | 4.0 bit/char |
| entropy | `min_digit_ratio` / `max_digit_ratio` | 数字占比窗 | 0.05 / 0.6 |
| entropy | `max_letter_run_ratio` | 最长连续字母/长度（挡长标识符） | 0.5 |
| decoders | `max_depth` / `min_blob_length` | 解码层深/入参 | 2 / 24 |
| whitelist | 四类白名单 | sha256 / git commit / UUID / 测试夹具 | 仅接受这四类 |

## 2. 怎么做的

**工具链**：`tools/secret_scan/`（`detectors` 三路线 → `decoders` 解码层 →
`whitelist` 误报控制 → `engine` 编排 → `redact` 脱敏 → `gates` 三出口 → `history` 回扫 →
`acceptance` C1–C9），`data/secret_scan/policy.json`（策略），`tests/secret_scan/`（`75` 条用例），
`tools/secret_scan/hooks/pre-commit` + `.github/workflows/secret-scan.yml` + `make secret-scan`（成熟化）。

**三个决定成败的选择**

1. **顺序即安全属性**：先遮罩既有 `<REDACTED:…>` 占位符 → 再跑已知值 → 再跑模式 → 再跑熵 →
   最后才解码重扫。遮罩在最前，是「改写幂等」的充要条件（否则扫描器会把自己的占位符当命中）。
2. **fail-closed 贯穿到底**：策略缺失/损坏、git 不可用、脱敏后复扫不干净，一律 `decision=unknown`
   = 拦截；`allow` 只可能来自「扫过且干净」。策略里 `required=true` 的已知值源为空会直接抛错，
   不允许「已知值路线静默变成空转」。
3. **命中必须脱敏，且脱敏要可验**：判据输出只有「2 字符前缀 + 掩码 + 长度 + sha256」，
   因此判决 JSON / 账本可以归档、可以 POST、可以进 commit，而不会变成新的泄漏渠道；
   自动改写后再扫必须 0 命中，否则升级为 `unknown` 拦截。

**判决 schema**：`tp.secret_scan.verdict/v1` —— `exit` ∈ {pre_commit, report_gate, artifact_writer}、
`decision` ∈ {allow, block, unknown}、`scan.by_type` / `scan.by_confidence` / `hits[]`（脱敏）、
`policy_sha256`、`digest`（内容寻址，无墙钟 → 可复算）。

## 3. 计算结果（机器判据 C1–C9）

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| C1 | 20 个合成秘密检出率 ≥95%，含 ≥4 个高熵无前缀裸 key | 20/20 = 100.0%；裸 key 4/4 | PASS |
| C2 | 20 个良性样本误报率 ≤5% | 0/20 = 0.0% | PASS |
| C3 | 三出口各注入 5 例，均须在写盘/提交/POST 前拦截 | pre-commit block=block / commits=0；report transport 调用=0；artifact 严格模式写入=0 | PASS |
| C4 | 改写→重扫 0 命中；二次改写不变 | 覆盖 22 例（20 秘密 + 2 传输例），失败 0 | PASS |
| C5 | 最近 ≥150 mission 的 report/artifacts 只读回扫 + 暴露清单 + 修前/后对比 | missions=150 / files=1139 / 36.5 MB；full=164 vs baseline=0（Δ=+164）；残余=0 | PASS |
| C6 | 扫描器自身日志不得出现被扫内容 | 检查 26 份输出，回声 0 处 | PASS |
| C7 | 零领域语义：模式/阈值/白名单经配置注入 | 领域词泄漏 0 处；注入实验 custom_pattern_injection_detected=True/threshold_injection_suppresses_bare_keys=True/whitelist_toggle_changes_digest_verdict=True | PASS |
| C8 | 标准 CI/CD 集成 | workflow=True / hook=True / make=True / `cli ci` 退出码=0 | PASS |
| C9 | pytest ≥18 全绿 | 75 passed / 0 failed | PASS |

**路线归因**（同一批 22 个注入用例＝20 个合成秘密 + 2 个编码传输例，单路线隔离扫描）：pattern 命中 16、entropy 命中 17、decoded 命中 2；并集 22 / 22。裸 key 只有熵路线能看见 —— 这就是「三路并联」而非「一条正则」的量化理由。

### 历史回扫（C5）暴露清单摘要

| mission | 文件 | 行 | 类型 | 置信度 | 长度 | 值指纹(sha256 前 16) |
|---|---|---|---|---|---|---|
| m12292 | `report_p162w1_n09.json` | 7 | high_entropy_string | low | 41 | `1f2164061ae80add` |
| m12526 | `runs/m669_acceptance.json` | 3 | high_entropy_string | low | 32 | `09c845802505ba56` |
| m12283 | `report_p162w1_n09.json` | 7 | high_entropy_string | low | 41 | `1f2164061ae80add` |
| m12296 | `report_p162w1_n09.json` | 7 | high_entropy_string | low | 41 | `1f2164061ae80add` |
| m12293 | `report_p162w1_n09.json` | 7 | high_entropy_string | low | 41 | `1f2164061ae80add` |
| m12479 | `docs/DELIVERY_HYGIENE_MECHANISM.md` | 131 | high_entropy_string | low | 42 | `1f606cfee407fc8d` |
| m12525 | `artifacts/capability_ledger/capability_ledger.json` | 57 | high_entropy_string | low | 44 | `8c25ad694d9f7451` |
| m12514 | `runs/openwater_uq_smoke/results.json` | 49 | high_entropy_string | low | 37 | `3f732495187956e9` |
| m12514 | `runs/openwater_uq_smoke/results.json` | 3187 | high_entropy_string | low | 42 | `04fc1aa9eb5d4ad5` |
| m12391 | `report_p162w1_n09.json` | 7 | high_entropy_string | low | 41 | `1f2164061ae80add` |
| m12387 | `report_p162w1_n09.json` | 7 | high_entropy_string | low | 41 | `1f2164061ae80add` |
| m12524 | `report_p162w1_n09.json` | 7 | high_entropy_string | low | 41 | `1f2164061ae80add` |

**暴露清单结构归类**（逐行重取 token 并比对 sha256 复核，token 本身不落盘）：

| 结构类 | 行数 |
|---|---|
| identifier_with_separators | 20 |
| not_found_in_line | 2 |
| path_or_filename | 129 |

合计 151 行，其中「凭据样」= 0 行（占比 0.0%）：即这 150 个 mission 的**积压语料里没有形状可疑的凭据**，低置信度候选全部是路径/标识符/文件名类结构串。

按置信度：高 0 / 低 164。高置信度 = 形状/已知值/解码后命中；低置信度 = 纯熵候选（字符统计无法把裸随机 key 与长路径/标识符完全分开，故分开列账，不做混算）。

## 4. 结果图

![fig1_route_matrix.png](figures/fig1_route_matrix.png)

*Route attribution for the 22 injected cases (20 synthetic secrets + 2 encoded-transport cases). Bare high-entropy keys are caught only by the entropy route; shaped secrets by the pattern route; transported (encoded) secrets only by the decode route.*

![fig2_calibration.png](figures/fig2_calibration.png)

*Injection calibration against the acceptance thresholds: C1 detection rate (left, dashed line = 95% floor) and C2 false-positive budget (right, dashed line = 1 of 20 admissible).*

![fig3_history_rescan.png](figures/fig3_history_rescan.png)

*History rescan: findings split by confidence (left) and the naive-baseline vs full-engine delta on the identical corpus (right). Residual hits after in-memory repair: 0.*

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **`password_kv` 正则曾是灾难性回溯**：`[A-Za-z0-9_]*` 无界前缀在 0.9 MB 文本上耗 4.7 s
   （全部其余规则合计 0.3 s）。改为有界 `{0,24}` 后降至毫秒级 —— 扫描器进 CI 后，
   正则的**时间复杂度**与检出率同等重要。
2. **base64 解码层踩了 `errors="replace"` 的坑**：替换字符 U+FFFD 是 `isprintable()`，
   垃圾解码结果因此「像文本」一样被接受，并抢占了同一 span 上真正的 hex 解码机会。
   改为 strict UTF-8 解码 + 一个 span 只接受一次成功解码后，hex 传输例才被看见。
3. **熵路线无法区分「裸 key」与「长路径/驼峰标识符」**（这是信息论层面的天然模糊）：
   实测在 12 个 mission 的真实语料上，熵命中 100% 是路径/标识符类结构串。工程上不硬调
   阈值的做法是**分置信度记账**（高/低），而不是把阈值调高到漏过真 key。
4. **白名单四类在当前阈值下不是承重件**：sha256/commit/UUID 的熵（3.6–3.8）本就低于 4.0 门，
   白名单是「阈值被放松时」的纵深防御（C7 用放松阈值证明其可注入生效）。这一点如实记录，
   不把「白名单挡住了误报」当作虚假功绩。
5. **熵路线不覆盖超长不透明块**（>256 字符，如内嵌图片 base64）：这类块由模式路线与解码路线覆盖，
   熵路线跳过是为了抑制误报——这是显式设计的取舍，不是漏洞修补。
6. **pre-commit 钩子可被 `--no-verify` 绕过**：本节点交付钩子与命令，**启用属治理动作**；
   不可绕的形态是 CI required check（`.github/workflows/secret-scan.yml` 已就位）。
7. **历史回扫的只读性由 sha256 前后比对证明**，但回扫期间其它 agent 可能正在自己的 worktree
   写文件：报告把「外部修改」单独计数（本次 7 个），不算作扫描器写入。

## 6. 误差判定（对基准的逐项回答）

本节点是安全机制而非物理场求解，**没有实验/文献基准可对标**，因此按「判据 vs 实测」逐项给误差：

| 关键量 | 基准（判据） | 实测 | 误差 | 是否达标 |
|---|---|---|---|---|
| 检出率 | ≥0.95 | 1.0000 | +0.0500 | 是 |
| 误报率 | ≤0.05 | 0.0000 | -0.0500 | 是 |
| 三出口拦截率 | 3/3 | 3/3 | +0 | 是 |
| 脱敏幂等残余命中 | 0 | 0 | +0 | 是 |
| 历史回扫残余命中 | 0 | 0 | +0 | 是 |

**是否 <3%？** 本节点无物理基准；对上表所有判据，实测均为**恰好达界或优于界**（检出率 100% vs
95% 门、误报率 0 vs 5% 门、残余命中 0 vs 0 门），即判据意义下误差为 0，满足「<3%」。
唯一未达 100% 的能力边界是「熵路线在真实语料上的精确率」（见注意点 3），已按置信度分账如实披露。

## 7. 复算入口

```bash
python -m pytest tests/secret_scan -q             # 75 条用例
python -m tools.secret_scan.cli ci                # 标定 + 三出口自注入 + 自扫
python -m tools.secret_scan.cli calibrate         # C1/C2
python -m tools.secret_scan.cli history --limit 150   # C5 暴露清单
python -m tools.secret_scan.acceptance            # C1–C9 全量
python -m tools.secret_scan.gen_doc               # 重生成本文档与图
```

