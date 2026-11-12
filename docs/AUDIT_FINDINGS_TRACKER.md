# 交差审计发现的结构化追踪（AUDIT-FINDINGS-TRACKER v1，mission 16224）

## 为什么要有这东西

交差审计（如 15311/15312 双审）产出的「发现」长期是自然语言：**同一类问题在不同报告里
叫不同名字**（fake pass / 假通过 / 假绿），汇总靠人抄（手抄风险），标签与证据等级错位
（声明 REAL 实际无探针）无法被机器抓住。结果就是「审计做了，但台账里查不到，下一轮
重犯」。本机制把发现变成**闭词表 + 结构化记录 + 退出码**三件套。

## 机制契约（可直接抄用）

| 契约 | 落地 |
|---|---|
| 五类闭词表 | `fake_pass` / `hand_copy` / `label_mismatch` / `undefined_criteria` / `other` |
| 类别来源 | 结构化分类字段优先；缺字段才退文本字段，且标注 `match_scope="text"` |
| 未识别 | 降级 `WITHHELD` + 原因码，**总判 FAIL**（不许静默通过） |
| 解析失败 | `PARSE_ERROR` / `NO_FINDINGS_STRUCTURE` → FAIL；显式空列表才是合法零发现 |
| 反手抄 | 报告自述 `counts` 与实测计数不符 → 派生 `label_mismatch`（`DECLARED_COUNT_MISMATCH`） |
| 反标签错位 | 声明类别与其它分类字段矛盾 → 派生 `label_mismatch`（`LABEL_CONFLICT`） |
| 可溯源 | 报告/schema `sha256` 入输出；无时钟无随机 → 同输入逐字节同 |
| 零领域语义 | 类别词表/容器键/字段名全在 schema 工件；代码与测试夹具用合成名 `node-alpha…` |

## 本次自证数字（全部由程序现算，见 `artifacts/audit_findings_tracker/out/`）

| 样本 | layout | total | 逐类计数 | status / rc |
|---|---|---|---|---|
| `report_15311.json` | explicit (`$.findings`) | 5 | 五类各 1 | PASS / 0 |
| `report_15312.json` | explicit (`$.report.data.issues`) | 5 | 五类各 1 | PASS / 0 |
| `report_15311_counts_drift.json` | explicit | 4 | fake_pass 1, hand_copy 1, label_mismatch 2(派生) | PASS / 0 |
| `report_15312_withheld.json` | explicit (`$.issues`) | 2 | fake_pass 1, WITHHELD 1 | FAIL / 1 |

两张异构报告（顶层 `findings` vs 深层 `report.data.issues`，dict/string 混排）都被同一
分类法解析成同一五类计数 —— 这就是「格式不统一时容错解析」的可复算证据。

## 证据图

`artifacts/audit_findings_tracker/fig_audit_findings_tracker.png`
（A：样本 × 五类计数；B：recognized/WITHHELD 分布与 PASS/FAIL 判定）。
生成脚本 `python3 -m tools.audit_findings_tracker.make_figures`，图中每个数字都由
`core.analyze_report` 现算，零手抄。

## 注意点 / 局限

- **不替审计做判断**：本工具只做「归类 + 计数 + 拦截」，一条 `fake_pass` 是否成立仍由
  审计证据链裁决；把自然语言误解成类别的风险由 `match_scope` / `ambiguous` / `detail`
  暴露，可审计不隐瞒。
- **文本兜底是弱证据**：`match_scope="text"` 的条目来自标题/描述等自由文本，词表误命中
  可能在所难免；需要强判时请让报告提供结构化分类字段。
- **别名表是平台审计词汇**（fake pass / hand copy / 标签错位 …），不含任何学科词条；
  `tools/evidence_domain_scan.py` 对本包扫描命中 0。
- **计数不一致只报不改**：派生 `label_mismatch` 是给上游的账，不修正原报告。
- 本任务无连续量基准（计数类判据），「误差 <3%」不适用；能给的误差面是**分类一致性**：
  两份异构报告对同一批合成发现给出完全相同的五类计数（差值 0）。

## 回归守卫

- `tests/test_audit_findings_tracker.py`（31 例）含：五类逐一反例、四类容器布局、启发式
  定位、显式空列表、`WITHHELD` 两种来源、`PARSE_ERROR`/`SCHEMA_INVALID`/`REPORT_MISSING`、
  声明计数漂移、标签冲突、必需类别门、确定性与 `sha256`、CLI 子进程退出码与 `--out`。
- 任何「未识别静默算通过」的回归都会被 `test_unrecognized_text_is_withheld_not_silently_passed`
  与 `test_unsupported_entry_is_withheld_and_fails` 立刻打红。