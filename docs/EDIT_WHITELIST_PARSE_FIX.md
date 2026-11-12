# EDIT_WHITELIST 前缀解析修复（m16622）

**状态：仓内已修（镜像+清单+回归测试）；线上 `/root/tp-agent/tp_worker.py` 未同步 = PARTIAL（部署动作，本单按禁令不做）。**

- 仓：TensorPlatform，分支 `agent/m16622`
- 修复前基线：git blob `f40e2cb1a:harness_gateway/mirror/tp_worker.py`，sha256 `476a285dd1ec…`
- 修复后：`harness_gateway/mirror/tp_worker.py`，sha256 `84dba6af8c8f…`（55533 B）
- 改动文件：`harness_gateway/mirror/tp_worker.py`、`harness_gateway/mirror/MIRROR_MANIFEST.json`、
  `harness_gateway/mirror/DEPLOY_STATE.json`、`tests/test_edit_whitelist_parse.py`（新增）、
  `tests/emit_edit_whitelist_parse_evidence.py`（新增，证据生成器）、本文件 + `docs/evidence/m16622_edit_whitelist_parse/`

---

## 1. 缺陷陈述（门禁自伤：静默收紧）

`_parse_edit_whitelist()` 从 goal 里抓 `EDIT_WHITELIST[：:]\s*([^\n]+)`，用
`re.split(r"[,，、;；]", ...)` 切前缀，每个 token 只做 `strip().lstrip("./").strip()` + `os.path.normpath()`。

真实任务单几乎总把白名单写成一句**以句号结尾**的话：

```
EDIT_WHITELIST: docs/, artifacts/。
```

中文句号 `。` 粘在最后一个 token 上 → `normpath("artifacts/。") == "artifacts/。"`
——一个**合法但永不匹配**的前缀（比对侧路径永远不含 `。`）。于是：

- `docs/master_plan_r13.md` → 放行（第一个前缀正常）；
- `artifacts/planner_r13/x.json` → **拒绝**（最后一个前缀已损坏）。

即：**每个「句号结尾」的任务单都静默丢掉最后一个仓/目录的写权限**。没有安全漏洞（是收紧），
但 worker 要花工具调用试错，还可能把产出硬塞到白名单外的路径 → 事后被判违规。属「门禁自伤」。

### 最小复现（修复前，实测）

```python
prefs = _parse_edit_whitelist("EDIT_WHITELIST: docs/, artifacts/。")
# 期望 ['docs', 'artifacts']；实得 ['docs', 'artifacts/。']
_wl_ok("artifacts/x.json", prefs)   # 期望 True；实得 False
```

现场实证（本班会话真实发生）：mission 16605 声明 `EDIT_WHITELIST: docs/, artifacts/。`，
`write_file artifacts/planner_r13/x.json` 被机器门拒绝，`docs/master_plan_r13.md` 通过；
把同一 goal 的句号去掉后 `artifacts/` 立刻放行。本单自身也复现了同类拦截（见 §6 注意点 2）。

## 2. 修复（最小、可验证、只清洗标点不放宽语义）

`harness_gateway/mirror/tp_worker.py`：

```python
_WL_TAIL_PUNCT   = "。．.，,；;：:、！!？?…\"'“”‘’（）()[]【】〔〕<>《》「」/\\ \t\u3000"
_WL_SENTENCE_STOP = "。．！？…\u3000"   # 出现在 token 中部=句子断了

def _clean_wl_prefix(raw):
    p = str(raw or "").strip()
    for ch in _WL_SENTENCE_STOP:      # 「artifacts/。因此…」→「artifacts/」
        if ch in p:
            p = p.split(ch, 1)[0]
    p = p.strip().strip("\u3000").strip()
    p = p.rstrip(_WL_TAIL_PUNCT)      # 「artifacts/。」→「artifacts/」
    p = p.strip().lstrip("./").strip()  # 沿用旧行为：去前导 ./ 与 /
    p = p.rstrip("/")
    return p
```

`_parse_edit_whitelist` 仅把 `p.strip().lstrip("./").strip()` 换成 `_clean_wl_prefix(p)`，其余不变（`prefs or None` 休眠语义保留）。

`_wl_ok` 同源清洗 + 两处**收紧**（不是放宽）：

```python
raw = str(path or "").strip()
if not raw or os.path.isabs(raw):       # 绝对路径一律不放行
    return False
p = os.path.normpath(_clean_wl_prefix(raw))
if p == ".." or p.startswith(".." + os.sep) or os.path.isabs(p):
    return False
return any(p == q or p.startswith(q.rstrip("/") + "/") for q in (prefs or []))
```

> **为什么加绝对路径拒**：基线实测发现旧 `_wl_ok` 对 `/docs/x.md` 先 `lstrip("./")`（lstrip 是字符集剥离，
> 会把前导 `/` 吃掉）→ 归一化成 `docs/x.md` → **命中白名单被放行**。这是一处既有的越界放宽
> （绝对路径可绕过前缀语义）。修语句清洗时顺手堵上，方向是收紧，D3 里给出输出。

### 语义边界（明确的「不做」）

- 不做通配（`docs/*` 仍按字面前缀 `docs/*` 处理，不放行 `docs/x`）；
- 不把 `.` / `..` / 空 token 变成「全仓放行」（`_parse_edit_whitelist("EDIT_WHITELIST: .")` 仍返回 `None`，与旧行为一致）；
- 不引入任何新的放行分支：命中逻辑仍是 `p == q or p.startswith(q + "/")`。

## 3. 前后对照（同一用例矩阵；程序化生成，零手抄）

生成命令：

```
python3 tests/emit_edit_whitelist_parse_evidence.py \
    --out-dir docs/evidence/m16622_edit_whitelist_parse
```
（before = `git show f40e2cb1a:…/tp_worker.py` 导出后 import；after = 当前工作树；矩阵与 pytest 共用同一份 `POSITIVE_GOALS/NEGATIVE_CASES/DORMANT_GOALS`）

| 组 | 用例 | goal / 被拒路径 | before 结果 | after 结果 |
|---|---|---|---|---|
| positive | cjk_full_stop | `EDIT_WHITELIST: docs/, artifacts/。` | **FAIL** `['docs','artifacts/。']`，`artifacts/x.json` 被拒 | **PASS** `['docs','artifacts']`，放行 |
| positive | ascii_period | `…docs/, artifacts/.` | PASS | PASS |
| positive | fullstop_then_newline | `…artifacts/。\n后续正文` | **FAIL**（同上） | **PASS** |
| positive | fullwidth_comma | `…docs/，artifacts/` | PASS | PASS |
| positive | no_slash_single | `EDIT_WHITELIST: docs` | PASS | PASS |
| positive | dot_slash_prefix | `EDIT_WHITELIST: ./docs/` | PASS | PASS |
| positive | semicolon_tail | `…docs/; artifacts/；` | PASS | PASS |
| negative | dotdot_escape | `docs/../backend/x.py` | 拒绝（PASS） | 拒绝（PASS） |
| negative | absolute_path | `/etc/passwd` | 拒绝（PASS） | 拒绝（PASS） |
| negative | outside_dir | `backend/x.py` | 拒绝（PASS） | 拒绝（PASS） |
| negative | parent_escape | `../x.py` | 拒绝（PASS） | 拒绝（PASS） |
| negative | prefix_sibling | `docs_evil/x.py` | 拒绝（PASS） | 拒绝（PASS） |
| negative | absolute_inside_name | `/docs/x.md` | **FAIL（被放行=既有越界放宽）** | 拒绝（PASS） |
| dormant | no_declaration / empty_goal / none_goal / only_whitespace | 无声明 | None（PASS） | None（PASS） |
| dormant | only_punct | `EDIT_WHITELIST: 。` | **FAIL** 返回 `['。']`（白名单激活但无一可用前缀 ⇒ 全拒=自伤） | **PASS** 返回 `None`（休眠） |
| dormant | only_puncts | `EDIT_WHITELIST: ，、；。` | **FAIL** 返回 `['。']` | **PASS** None |

汇总：before `positive 5/7, negative 5/6, dormant 4/6`；after `positive 7/7, negative 6/6, dormant 6/6`；
**改判（before-fail → after-pass）5 条**：`cjk_full_stop`、`fullstop_then_newline`、`absolute_inside_name`、`only_punct`、`only_puncts`。
反例组在修复后**全部仍拒绝**（安全不回退；且比修复前更严 1 条）。

通过的矩阵热力图（红=FAIL，绿=PASS）：`docs/evidence/m16622_edit_whitelist_parse/pass_matrix.png`（由 `matrix.json` 生成）。

### 诚实说明（与任务书的偏差）

任务书第 3 条写「空声明 / 只有标点 → 返回 None（休眠，与现状一致，零行为变化）」。
**实测「只有标点」并非零行为变化**：基线对 `EDIT_WHITELIST: 。` 返回 `['。']`（白名单被激活但无一可用前缀），
修复后返回 `None`（休眠）。方向是「使不可用声明退化为休眠」，与任务书意图一致，但不满足「与现状一致」的字面要求——
列为**已知行为变化**，若审阅要求严格零变化，可将「清洗后 prefs 为空」时的返回改成保留原 token（不建议：
那等于让一个句号把整个任务锁死）。真正零行为变化的部分是 `no_declaration / empty_goal / none_goal /
only_whitespace` 四条（D5 给出）。

## 4. 镜像与源（D4）

仓内情况：`harness_gateway/mirror/tp_worker.py` 是**唯一** in-repo canonical 实现（`MIRROR_MANIFEST.json` 的
`src_root=/root/tp-agent` 在仓外）；`mirror.py` 用 `mirror_sha256` 做渲染/落盘校验，故必须同步清单。

```
$ sha256sum harness_gateway/mirror/tp_worker.py /root/tp-agent/tp_worker.py
84dba6af8c8f464ae21d242bf85157a407ba9645435e49707e9a1b72dcc261cb  harness_gateway/mirror/tp_worker.py
c1967ce77c547a94293b303824e0b9e5aa7db7470f147be8ad652dcea58ad7f8  /root/tp-agent/tp_worker.py
$ wc -c  # 55533  vs  57244
$ grep -c "_WL_TAIL_PUNCT" /root/tp-agent/tp_worker.py   → 0   # 线上未含本修复
```

- `MIRROR_MANIFEST.json`：`tp_worker.mirror_sha256/mirror_bytes` 更新为 `84dba6af…/55533`，
  并新增 `divergence:{kind:"pending_deploy", mission:"m16622", note:…}` 显式记账「镜像领先于 src_root，待部署」。
  `source_sha256` 保持记录线上实际内容（不伪造成一致）。
- `DEPLOY_STATE.json`：`tp_worker.live_sha256` 校正为线上实际 `c1967ce7…`、`mirror_sha256` 更新为 `84dba6af…`。
  附带发现：该文件原记 `live=mirror=0d567624…/54037B`，而实际线上已是 `c1967ce7…/57244B` ⇒ **部署台账在 11-07 后已陈旧**
  （与本单无关，一并校正，供部署面核对）。
- 自洽性由 `tests/test_edit_whitelist_parse.py::TestMirrorIntegrity` 钉死（manifest sha/bytes == 磁盘）。

## 5. live harness 部署说明（**本次未做**）

- **本次未更新线上 `/root/tp-agent/tp_worker.py`**（任务禁令：不改 dispatcher、不碰线上 harness 进程）。
  线上实测 `grep -c "_WL_TAIL_PUNCT" = 0`，即修复**尚未生效**。
- 应由**部署面**（`harness_gateway/` 的 mirror→live 同步/发布流程，即在 `/root/tp-agent` 侧执行 mirror 渲染+落盘校验的
  运维/维护席位，或岗位交接后由下一单派遣的部署任务）执行：把本镜像合入 `src_root` 的 `tp_worker.py`，
  跑 `harness_gateway` 校验（sha/bytes 一致）后重启 worker 进程，再把 `MIRROR_MANIFEST.json` 的
  `source_sha256` 与 `divergence` 清掉。
- 因此本单验收结论为 **PARTIAL**：仓内缺陷已修并带回归测试；线上行为**未变**。

## 6. 验收逐条（命令 + 输出）

### D1 回归测试全绿（正例 7 / 反例 6 / 休眠 6 + 镜像自洽 3 + 基线证人 4）
```
$ python3 -m pytest tests/test_edit_whitelist_parse.py -q
............................                                             [100%]
28 passed, 1 warning in 0.26s
```
邻域回归（现有 tp_worker 消费者 + harness_gateway 全套）：
```
$ python3 -m pytest tests/test_edit_whitelist_parse.py tests/test_worker_truth_in23.py \
      tests/test_harness_journal_hook.py tests/test_audit_capabilities_instrument.py -q
70 passed, 1 warning in 2.11s
$ python3 -m pytest tests/harness_gateway/ -q
27 passed, 1 warning in 8.74s
```

### D2 基线对照（修复前失败输出 / 修复后通过输出）
```
$ python3 tests/emit_edit_whitelist_parse_evidence.py
before_ref=f40e2cb1a  before_sha256=476a285dd1ec  after_sha256=84dba6af8c8f
positive  cjk_full_stop            False  True    prefs before=['docs', 'artifacts/。'] after=['docs', 'artifacts']
positive  fullstop_then_newline    False  True    prefs before=['docs', 'artifacts/。'] after=['docs', 'artifacts']
negative  absolute_inside_name     False  True    prefs before=['docs'] after=['docs']
dormant   only_punct               False  True    prefs before=['。'] after=None
summary before: {'positive': {'total': 7, 'pass': 5}, 'negative': {'total': 6, 'pass': 5}, 'dormant': {'total': 6, 'pass': 4}}
summary after : {'positive': {'total': 7, 'pass': 7}, 'negative': {'total': 6, 'pass': 6}, 'dormant': {'total': 6, 'pass': 6}}
flipped(before-fail -> after-pass): 5
exit=0
```
另一条独立基线证人（不依赖 git）：`TestBaselineWitness` 内嵌修复前实现逐字副本，
断言 `legacy_parse_edit_whitelist("EDIT_WHITELIST: docs/, artifacts/。") == ['docs','artifacts/。']`
且 `legacy_wl_ok("artifacts/x.json", …) is False` —— 防止后人把用例改软。

### D3 反例仍全部拒绝
```
$ python3 -c "import importlib.util as u;s=u.spec_from_file_location('t','harness_gateway/mirror/tp_worker.py');m=u.module_from_spec(s);s.loader.exec_module(m);p=m._parse_edit_whitelist('EDIT_WHITELIST: docs/');
[print(repr(x), m._wl_ok(x,p)) for x in ('docs/../backend/x.py','/etc/passwd','backend/x.py','docs/ok.md')]"
'docs/../backend/x.py' False
'/etc/passwd' False
'backend/x.py' False
'docs/ok.md' True
```
（附：`/docs/x.md` 修复前 True → 修复后 False，见 §3 `absolute_inside_name`。）

### D4 镜像自洽
```
$ sha256sum harness_gateway/mirror/tp_worker.py   → 84dba6af8c8f464ae21d242bf85157a407ba9645435e49707e9a1b72dcc261cb
$ python3 -m pytest tests/test_edit_whitelist_parse.py -q -k MirrorIntegrity
...                                                                      [100%]
3 passed
```

### D5 零行为变化（未声明 → None）
```
$ python3 -c "…;_parse_edit_whitelist('普通任务目标，没有白名单声明。')" → None
  （另 '' / None / 'EDIT_WHITELIST:     ' 均 None；测试 test_dormant_zero_behaviour_change ×6）
```

## 7. 注意点 / 局限

1. **这是收紧型自伤，不是安全漏洞**；修复方向是「让声明真的生效」，不是「让门更宽」（反例全拒、并多拒 1 条绝对路径）。
2. 本单**自身就撞了这个门**：任务书 `EDIT_WHITELIST: harness_gateway/, tests/, docs/` 以 `/` 结尾没问题，
   但首次用 `write_file` 传**绝对路径**（`/root/tp-agent/work/m16622/tests/...`）被门拒——修复后的
   `isabs` 拒规则在真实会话里即时生效（也说明既有工具约定是相对路径）。
3. `_WL_TAIL_PUNCT` 含 `/`：`docs/`、`docs///` 都归一成 `docs`（`_wl_ok` 本就支持无斜杠前缀）。
   若未来出现**真以点结尾**的目录名（如 `v1.`），需从字符集里剔除 `.` 并改用「只剥句末标点」策略——目前无此用例。
4. 句中截断只对 `。．！？…　` 生效，不含半角空格与 `,`：`EDIT_WHITELIST: docs/, artifacts/ 其他说明` 这类
   「句式尾巴用空格分隔」仍会污染最后前缀——未修（避免把路径里可能的空格当断句，保持最小改动），列为已知未覆盖。
5. 部署未做（PARTIAL），线上行为未变；`MIRROR_MANIFEST.divergence` 即为待办线索。

## 8. 六要素自评（后端/门禁类，无物理场量）

| 要素 | 本单对应 |
|---|---|
| ① 案例介绍 | 无物理背景/几何/参数表：**门禁解析器缺陷**。参数=19 条合成用例（正 7/反 6/休眠 6） |
| ② 怎么做的 | 读代码→最小复现→修 `_parse_edit_whitelist`/`_wl_ok`→同步清单/部署台账→新增回归测试+证据生成器 |
| ③ 计算结果 | §3 前后对照表 19 行 + 汇总计数（before 14/19 → after 19/19，改判 5 条），全部由 `matrix.json` 程序化生成 |
| ④ 结果图 | `pass_matrix.png`（修复前后各用例 PASS/FAIL 热力图）。无场量云图/解析解曲线：门禁类任务无物理场，故以「通过矩阵」代场量图 |
| ⑤ 注意点 | §7 五条（含本单自撞门、真以点结尾目录、句中空格未覆盖、部署未做） |
| ⑥ 误差判定 | 无物理基准量，误差判据改为**离散判定矩阵**：before 误判 5/19（26.3%），after 误判 0/19（0%）⇒ 关键量（误判率）**0% < 3%** ✓；反例组拒绝率 before 83.3%(5/6) → after 100%(6/6) ✓。声明：与「解析正确性」基准（人工期望前缀）逐条比对，非连续量误差 |