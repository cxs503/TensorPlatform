# tools/audit_findings_tracker —— 交差审计发现的结构化追踪与自动拦截

把「审计报告里的发现」变成机器可读台账：**五类闭词表**（`fake_pass` / `hand_copy` /
`label_mismatch` / `undefined_criteria` / `other`）+ **确定性输出** + **PASS/FAIL 退出码**。
平台共性机制，**零领域语义**：类别词表/容器键/分类字段全部由 schema 工件注入。

## 用法

```bash
# 结构化一份审计报告（默认用包内 schema）
python3 -m tools.audit_findings_tracker --report R.json --schema tools/audit_findings_tracker/audit_findings.v1.schema.json \
    --out artifacts/audit_findings_tracker/out/R.findings.json

# 要求报告必须覆盖全部五类（缺任一 → FAIL）
python3 -m tools.audit_findings_tracker --report R.json --require-categories

# 证据图（数字全部由 analyze_report 现算）
python3 -m tools.audit_findings_tracker.make_figures
```

退出码：`0` = PASS（发现已结构化）/ `1` = FAIL（解析失败、schema 无效、发现未识别、
无发现结构、缺必需类别）。`2` = 命令行用法错误。

## 输出结构（关键字段）

| 字段 | 含义 |
|---|---|
| `status` / `exit_code` | 总判与退出码（机器裁） |
| `layout.mode` | 发现容器定位方式：`explicit` / `explicit_empty` / `root_list` / `heuristic` / `none` |
| `counts` | 逐类别计数 + `WITHHELD` + `total`（派生条目也计入） |
| `findings[]` | `finding_id` / `origin`(report/derived) / `status`(RECOGNIZED/WITHHELD) / `category` / `source_pointer` / `match_scope` / `declared_category` / `severity` / `subject` / `reason_code` / `detail` |
| `reasons[]` | 机器可读原因码（`PARSE_ERROR` / `NO_FINDINGS_STRUCTURE` / `UNRECOGNIZED_CATEGORY` / `UNSUPPORTED_ENTRY` / `DECLARED_COUNT_MISMATCH` / `LABEL_CONFLICT` / `MISSING_REQUIRED_CATEGORY` / `SCHEMA_INVALID` …） |
| `inputs` | 报告与 schema 的路径 + `sha256`（可溯源，零手抄） |

## 四条反造假规矩

1. **R1 缺就写缺**：找不到发现容器 → `FAIL`，不许把「没解析到」当「没发现」；
   显式 `"findings": []` 才是合法的「本次零发现」。
2. **R2 归类不冒充强证据**：优先结构化分类字段；只有缺字段才退文本字段，并记
   `match_scope="text"`。
3. **R3 未识别降级**：归不进五类的条目 → `WITHHELD` + 原因码，且总判 `FAIL`。
4. **R4/R5 派生发现**：报告自述计数与实测不符 → 派生 `label_mismatch`（防手抄汇总数）；
   同一发现的声明类别与其它分类字段矛盾 → 派生 `label_mismatch`（防标签错位）。
   派生条目**不覆盖**原始发现的类别（如实记录优先）。

## schema 工件

`audit_findings.v1.schema.json` 是分类法而不是严格 JSON Schema：`categories` /
`category_aliases` / `finding_list_keys` / `classification_fields` / `text_fields` /
`required_categories` / `count_container_keys` / `withheld_category` 均可配置。
要求 `categories` 必须覆盖五类发现，否则 `SCHEMA_INVALID`。

## 测试

```bash
python3 -m pytest tests/test_audit_findings_tracker.py -q   # 31 passed
```