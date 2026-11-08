# 边界门对抗语料召回率度量（P22-N10）

- 生成时间 (UTC): `2026-11-08T06:45:07+00:00`
- 语料规模: 82 例（leak 62 / clean 15 / control 5）
- 合成词表（域无关）: `{"import_tokens": ["widget", "flux"], "endpoint_pattern": "widget|flux|gadget-cfd|hull-form", "hoist_constants": ["GADGET_RHO", "SPROCKET_LENGTH"]}`

## 判据

| 判据 | 目标 | 实测 | 结论 |
|---|---|---|---|
| E1 边界门对抗语料召回率 | ≥ 95% | 100.0% (62/62) | PASS |
| E1' 召回率单侧 95% 置信下界（Clopper-Pearson） | ≥ 95% | 95.3% | PASS |
| E2 漏检清单根因覆盖率 | 100% | 100.0% (39 条漏检) | PASS |
| E3 补规则后召回率 | ≥ 95% | 100.0%（补规则前 37.1%，提升 62.9%） | PASS |
| E4 负控（已知边界案例）检出 | 100% | 100.0% (5/5) | PASS |
| 特异性（clean 例不误报） | 数据 | 100.0% | 数据 |

## 逐门召回（补规则前 → 补规则后）

| 门 | leak 数 | 前召回 | 后召回 | clean 数 | 前误报 | 后误报 |
|---|---|---|---|---|---|---|
| G1 | 24 | 37.5% | 100.0% | 7 | 0 | 0 |
| G2 | 16 | 25.0% | 100.0% | 4 | 0 | 0 |
| G3 | 22 | 45.5% | 100.0% | 4 | 0 | 0 |

## 漏检清单与根因

| 案例 | 门 | 变体 | 根因类别 | 根因 | 修复规则 | 开关 | 修复后检出 |
|---|---|---|---|---|---|---|---|
| G1-L06 | G1 | `dyn_import_module` | rule_missing | gate only inspected ast.Import/ImportFrom; a module path handed to importlib.import_module() is a string literal, invisible to the AST import nodes | G1-R4 | `dynamic_imports=True` | 是 |
| G1-L07 | G1 | `dyn_dunder_import` | rule_missing | same hole as dyn_import_module for __import__() | G1-R4 | `dynamic_imports=True` | 是 |
| G1-L08 | G1 | `dyn_fstring` | rule_missing | f-string module path: no AST import node, and the literal part must be folded before token matching can see it | G1-R5 | `dynamic_imports=True` | 是 |
| G1-L09 | G1 | `dyn_concat` | rule_missing | module path split by '+' concatenation defeats literal-only folding | G1-R5 | `dynamic_imports=True` | 是 |
| G1-L10 | G1 | `dyn_via_alias` | rule_missing | path bound to a module-level NAME then imported indirectly (indirect literal) | G1-R6 | `dynamic_imports=True` | 是 |
| G1-L13 | G1 | `dyn_opaque` | logic_gap | path assembled from a runtime value: not machine-checkable, so it must be surfaced as an advisory instead of silently passing | G1-R7 | `dynamic_imports=True` | 是 |
| G2-L05 | G2 | `sep_split` | logic_gap | token 'gadget-cfd' vs path '/v1/gadget_cfd': separator choice (hyphen vs underscore) defeats literal alternation | G2-R2 | `normalize_separators=True` | 是 |
| G2-L06 | G2 | `sep_removed` | logic_gap | token 'hull-form' vs path '/v1/hullform': a removed separator defeats literal alternation | G2-R2 | `normalize_separators=True` | 是 |
| G2-L07 | G2 | `meta_tag` | rule_missing | only the path string was inspected; tags/summary/description/operationId are published as domain vocabulary too | G2-R3 | `metadata_scan=True` | 是 |
| G2-L08 | G2 | `meta_summary` | rule_missing | same as meta_tag (summary field) | G2-R3 | `metadata_scan=True` | 是 |
| G2-L09 | G2 | `meta_operation_id` | rule_missing | operationId carries the vocabulary in camelCase ('computeWidget') | G2-R3 | `metadata_scan=True` | 是 |
| G3-L06 | G3 | `prefix_concat` | logic_gap | historical pattern required a leading word boundary, so a name glued to a prefix (DEEP_GADGET_RHO) did not match | G3-R3 | `embedded=True` | 是 |
| G3-L07 | G3 | `case_variant_assign` | logic_gap | matching was case-sensitive, so the lower-case spelling of the same constant sailed through | G3-R2 | `variants=True` | 是 |
| G3-L08 | G3 | `case_variant_mixed` | logic_gap | mixed-case spelling | G3-R2 | `variants=True` | 是 |
| G3-L09 | G3 | `case_variant_getattr` | logic_gap | getattr(mod, 'gadget_rho') carries the lower-case name in a string | G3-R2 | `variants=True` | 是 |
| G3-L10 | G3 | `case_variant_in_string` | logic_gap | lower-case name inside a string literal | G3-R2 | `variants=True` | 是 |
| G3-L11 | G3 | `string_concat_split` | rule_missing | name split across '+'-concatenated literals never appears contiguously on a line, so a line-based text scan cannot see it | G3-R4 | `constant_folding=True` | 是 |
| G3-L12 | G3 | `fstring_split` | rule_missing | same as string_concat_split for f-string pieces bound to a module-level bare string | G3-R4 | `constant_folding=True` | 是 |
| G1-L16 | G1 | `dyn_nested_call` | rule_missing | runtime import nested inside a lambda/comprehension body: still a Call node, invisible while only ast.Import nodes were walked | G1-R4 | `dynamic_imports=True` | 是 |
| G1-L17 | G1 | `dyn_triple_quote` | rule_missing | triple-quoted module path is a plain string constant | G1-R4 | `dynamic_imports=True` | 是 |
| G1-L18 | G1 | `dyn_keyword_arg` | logic_gap | module path passed as a keyword (name=) argument was not the first positional argument | G1-R10 | `dynamic_imports=True` | 是 |
| G1-L19 | G1 | `dyn_fromlist` | rule_missing | the path is the first positional argument, but the call node was never inspected before the runtime-import rule existed | G1-R4 | `dynamic_imports=True` | 是 |
| G1-L20 | G1 | `dyn_relative` | rule_missing | dot-lead relative runtime path: still a call node, never inspected before | G1-R4 | `dynamic_imports=True` | 是 |
| G1-L21 | G1 | `dyn_exec_string` | rule_missing | an import statement inside an exec/eval source string is not an AST import node of this module | G1-R11 | `dynamic_imports=True` | 是 |
| G1-L22 | G1 | `dyn_aliased_machinery` | logic_gap | the machinery was rebound to a local alias (from importlib import import_module as im), so the callee name was unrecognised | G1-R12 | `dynamic_imports=True` | 是 |
| G1-L23 | G1 | `dyn_getattr_machinery` | logic_gap | machinery fetched by name (getattr(importlib, 'import_module')) presents an empty callee name | G1-R13 | `dynamic_imports=True` | 是 |
| G1-L24 | G1 | `dyn_opaque_encoded` | logic_gap | path decoded from an encoded blob: unfolldable, therefore surfaced as an advisory (manual review) instead of passing silently | G1-R7 | `dynamic_imports=True` | 是 |
| G2-L10 | G2 | `meta_description` | rule_missing | same as meta_tag (description field) | G2-R3 | `metadata_scan=True` | 是 |
| G2-L11 | G2 | `meta_param_name` | rule_missing | query/path parameter *names* are published vocabulary, not just the path string or the four operation fields | G2-R5 | `metadata_scan=True` | 是 |
| G2-L12 | G2 | `path_double_sep` | logic_gap | an empty path segment splits the token ('gadget//cfd'); literal alternation cannot bridge it | G2-R2 | `normalize_separators=True` | 是 |
| G2-L13 | G2 | `path_segment_boundary` | logic_gap | the token is split across two path segments ('gadget/cfd') | G2-R2 | `normalize_separators=True` | 是 |
| G2-L14 | G2 | `doc_info_title` | rule_missing | document-level metadata (info.title/description, top-level tags) is published by /openapi.json but was outside the scan | G2-R6 | `metadata_scan=True` | 是 |
| G2-L15 | G2 | `doc_tags` | rule_missing | same as doc_info_title (top-level tags) | G2-R6 | `metadata_scan=True` | 是 |
| G2-L16 | G2 | `meta_operation_id_upper` | rule_missing | upper-case operationId | G2-R3 | `metadata_scan=True` | 是 |
| G3-L17 | G3 | `annotation_string` | logic_gap | lower-case name inside a type-annotation string | G3-R2 | `variants=True` | 是 |
| G3-L19 | G3 | `implicit_concat` | logic_gap | implicit adjacent-literal concatenation is merged by the parser into one constant whose pieces are non-contiguous in the source line | G3-R4 | `constant_folding=True` | 是 |
| G3-L20 | G3 | `bytes_literal` | logic_gap | lower-case name inside a bytes literal | G3-R2 | `variants=True` | 是 |
| G3-L21 | G3 | `dot_path_case` | logic_gap | lower-case name on an attribute path | G3-R2 | `variants=True` | 是 |
| G3-L22 | G3 | `decorator_string` | logic_gap | lower-case name in a decorator argument | G3-R2 | `variants=True` | 是 |

## 真仓 A/B（同一棵树，两种配置）

| 配置 | G1 违规 | G1 advisory | G1 扫描文件 | G3 违规 | G3 形态分布 |
|---|---|---|---|---|---|
| before | 15 | 0 | 82 | 96 | `{"exact": 48, "embedded": 48}` |
| after | 17 | 2 | 239 | 136 | `{"folded": 15, "exact": 48, "embedded": 50, "case_variant": 23}` |

## 复现

```
python3 tools/audit/boundary_gate_recall.py
python3 -m pytest -q tests/test_boundary_gate_recall.py
```
