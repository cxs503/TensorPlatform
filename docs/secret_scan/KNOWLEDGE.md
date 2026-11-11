# 可复用知识：写一个「有牙」的秘密泄露门（P9263-N09 蒸馏）

> **技能**：knowledge-distillation　|　**来源任务**：P9263-N09 / mission 12471 / 仓 `TensorPlatform`
> **源改动**：`tools/secret_scan/**`、`data/secret_scan/policy.json`、`tests/secret_scan/**`、
> `.github/workflows/secret-scan.yml`、`tools/secret_scan/hooks/pre-commit`、`Makefile:secret-scan*`
> **证据工件**：`artifacts/secret_scan/acceptance.json`（C1–C9）、`artifacts/secret_scan/history_exposure.json`（暴露清单）、
> `artifacts/secret_scan/exposure_adjudication.json`（暴露清单结构归类）、
> `artifacts/secret_scan/verdict_samples.json`（三出口判决样本）、`artifacts/secret_scan/route_matrix.json`（路线归因）
> **复算**：`python -m tools.secret_scan.acceptance` / `gen_doc`
> **最后验证**：2026-11-11（C1–C9 全 PASS，见 acceptance.json；pytest 75 条；平台报告 id 见任务报告）
> **复核状态**：自检 + 机器判据通过，未经独立席位复判

---

## K1 · 门禁正则先做复杂度体检，再做检出率调优

**规则**：任何要进 CI 的正则，**先证其时间复杂度**，再谈召回。禁止「无界重复 + 后续必需字面量」的组合
（`[A-Za-z0-9_]*keyword` 是典型回溯炸弹）；所有可能在前缀位置试探的重复必须加界（`{0,N}`）或在其前加
`\b` 收紧起点。

**证据**：本节点 `password_kv` 规则原形在 0.9 MB 文本上耗 **4.7 s**，其余 16 条规则合计 0.3 s；
加 `\b` + 有界前缀后降到毫秒级（同一批文本，同一机器）。

**why**：慢门 = 被关的门。人不会为了一个每文件几秒的钩子保持耐心，绕过（`--no-verify`）会变成常态。

**反例/边界**：`\b` 收紧起点会把「关键字嵌在下划线中间」的形态（`db_password = …`)推到别的规则身上，
必须另配一条上下文锚定规则兜住；不能只加 `\b` 就把召回当没事。

**回归护栏**：`tests/secret_scan/test_secret_scan_performance.py::test_no_rule_backtracks_catastrophically`
对每条规则在 4 类对抗输入（长小写串 / 长字母串 / 长数字字母串 / 重复字面量）上设定 0.75 s/规则预算；
`test_full_scan_throughput` 锁 1 MB ≤ 5 s。

---

## K2 · 解码层必须用 strict 解码，「替换字符」是最隐蔽的误判源

**规则**：payload 解码（base64/hex）用 **strict UTF-8**；`errors="replace"` 产生的 U+FFFD 是
`isprintable()==True`，会让垃圾解码结果通过「可打印率」过滤，并**抢占同一 span** 上真正能解出文本的
codec（base64 ⊃ hex 字母表）。策略：一个 span 只接受**一次**成功解码，且 decoded 中出现 U+FFFD 即弃。

**证据**：修复前 `hex_wrapped_bearer` 传输例 0 命中；修复后 `decoded:bearer_header` 命中（`route_matrix.json`）。

**反例/边界**：严格解码会放弃非 UTF-8 的合法载荷（如 latin-1 文本）——这类内容由模式路线兜底，
是可接受的取舍；若业务需要，应显式增加 codec 而不是放宽成 `replace`。

**回归护栏**：`tests/secret_scan/test_secret_scan_calibration.py::test_transport_cases_are_seen_through_encoding`；
`test_decoder_depth_is_injected`。

---

## K3 · 纯字符统计无法区分「裸随机 key」与「长路径/驼峰标识符」——要分层，不要硬调阈值

**规则**：熵路线的命中**不要**与形状路线（模式/已知值/解码后）混算成一类"泄露"。用**置信度分层**记账：
形状/已知值/解码命中 = high，纯熵 = low candidate；然后把阈值留在「能抓住裸 key」的位置。
把阈值抬到路径不进来的高度，等于用漏检换安静。

**证据**：150 个真实 mission / 1139 文件 / 36.5 MB 语料上，熵命中 164 条，逐行重取 token 比对 sha256 后
结构归类 = 路径/文件名 129、带分隔符标识符 20、未复核 2、**凭据样 0**（`artifacts/secret_scan/
exposure_adjudication.json`；token 本身不落盘）。同期合成语料给出 20/20 检出 + 0 误报（双 seed）。

**反例/边界**："low confidence" 不等于假阳性——真实裸 key 恰恰长得像随机串；因此 low 档**必须仍然拦截**
（本例三出口对 low 档同样 BLOCK），只是报告里单列，避免把噪声计成功绩。

**回归护栏**：`history.py` 输出 `by_confidence`；`tools/secret_scan/adjudicate.py` 产出结构归类工件；`test_secret_scan_calibration.py` 双 seed（标定 + 留出）
各自要求 ≥19/20 检出、≤1/20 误报。

---

## K4 · 「只读扫描」要结构化证明，不能靠约定

**规则**：声称只读的扫描器，必须 (a) 静态证明源码里没有写模式 `open`，(b) 运行时对每个文件做 sha256
前后比对，(c) 把「期间被外部进程改动/删除」单独计数——历史回扫跑在其它 agent 正在写的 worktree 上，
并发写是常态，不能算成扫描器写入，也不能因此让判据随机红。

**证据**：开发期回扫因另一个 mission 删文件触发 `FileNotFoundError`；改为 `""` 兜底 + 单列
`externally_removed_count` 后，判据稳定（`history_exposure.json.readonly_static_ok=true`）。

**回归护栏**：`tests/secret_scan/test_secret_scan_history.py::test_history_module_is_structurally_read_only`
（正则扫描源码禁止 `open(..., "w"/"a"/"x")`）+ `test_history_is_read_only`（sha256 前后一致）。

---

## K5 · 标定语料要在运行时生成，不能让测试夹具自己变成泄漏源

**规则**：注入标定用的"合成秘密"由 `hashlib(seed|label|counter)` 确定性生成，**仓库里不存字面量**；
同一套生成器换 seed = 留出验证集，避免阈值被单份语料拟合。

**证据**：`test_corpus_values_are_not_stored_in_the_repository` 逐个语料值扫描 `tools/secret_scan/**` 与
`data/secret_scan/**`，0 命中；`test_corpus_is_reproducible_from_seed` 锁可复算；双 seed 各自达标。

**反例/边界**：确定性生成只能覆盖"你想到的形态"。真实世界的形态漂移要靠已知值源（内存比对）与
历史回扫补充，不能拿合成语料 100% 当"覆盖完备"。

---

## 不适用范围（scope boundary）

- 本知识适用于**文本/JSON payload 级**的凭据泄露检测；二进制容器内部（如压缩包、SQLite 页）未覆盖。
- 熵路线不覆盖 >256 字符的不透明长块（内嵌图片 base64 等），由模式与解码路线覆盖。
- pre-commit 钩子天然可被 `--no-verify` 绕过；不可绕的形态是 CI required check（本节点已交付 workflow，
  启用属治理动作）。
- 阈值/模式是**数据**：换域、换厂商、换风险偏好只改 `data/secret_scan/policy.json`，不改代码。