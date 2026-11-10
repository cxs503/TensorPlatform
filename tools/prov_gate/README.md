# prov_gate — 报告数字级 provenance 门

每份报告的每个结果数字，要么绑定到 sidecar 里可复算的出处，要么显式标注为声明值/文献值。
写作方向被翻转：**模板里没有结果数字**，发布时由渲染器从 sidecar 取值替换。

```
report.template.md  --render-->  report.md + render_manifest.json
                                    |
                             sidecar.json (query_hash | artifact_sha256, source, value, computed_at)
                                    |
                              gate -> PASS / FLAG / FAIL
```

## 快速开始

```bash
# 1) 从模板渲染（缺 key 直接拒绝发布）+ 落 manifest
python3 -m tools.prov_gate.render --template t.md --sidecar sidecar.json \
    --out report.md --manifest manifest.json

# 2) 门禁（给 --db 则重跑查询比对数值；给 --artifact-root 则重算工件哈希）
python3 -m tools.prov_gate.gate --published report.md --sidecar sidecar.json \
    --template t.md --manifest manifest.json --db evidence.sqlite \
    --artifact-root . --json-out gate.json -q

# 3) 可复现证据（合成夹具 + 三类负控 + 图 + 自检）
python3 -m tools.prov_gate.evidence
```

Python 侧（发布 SDK）:

```python
from tools.prov_gate.sdk import render_and_gate, verify_published
res = render_and_gate("t.md", "sidecar.json", out="report.md",
                      manifest_out="manifest.json", db_path="evidence.sqlite",
                      artifact_root=".")
assert res.passed, res.gate["findings"]
```

## 契约要点

- 占位符：双花括号 + `prov:` + 键名（`[A-Za-z0-9_.:-]+`）；数字渲染为 sidecar 决定的
  `decimals` 文本，工件渲染为其 sha256。
- `query_hash = sha256(canonical(sql, params))`：SQL 折叠空白去尾分号 + 参数规范 JSON。
  只有格式变化不改指纹；字面量/参数变化必改指纹。
- `computed_at`：ISO-8601 UTC，仅作顺序证据（无签名密钥，不是签名）。

## 四层判定

| 层 | 作用 | 关键失败码 |
|---|---|---|
| L1 | 正文 == 模板对 sidecar 的规范渲染；manifest 摘要 == 正文摘要 | `RENDER_DRIFT`, `DOC_SHA_MISMATCH` |
| L2 | 模板每个 key 有记录；正文无残留占位符 | `MISSING_KEY`, `UNRENDERED_PLACEHOLDER` |
| L3 | 记录 `query_hash` 可从其 `source` 重算；重跑查询值一致；工件重哈希 | `QUERY_HASH_MISMATCH`, `SOURCE_DRIFT`, `ARTIFACT_HASH_MISMATCH` |
| L4 | 裸数字比例 ≤ 阈值（reject/flag 策略） | `BARE_NUMBER_RATIO_EXCEEDED` |

## 声明范围（可审计的排除，不是暗洞）

- 代码跨度/围栏：机器内容，计入 `code_context`，占比超 `max_code_ratio` 即 FLAG。
- 结构化数字：日期、版本号、`Table 2` 式标签、标题编号、有序列表标记。
- `<!-- prov:scope:declared -->` / `<!-- prov:scope:literature -->` 区域与行尾
  `<!-- declared -->`：声明值/文献值，逐类计数；占比超 `max_region_excluded_ratio` 即 FLAG。
  代码跨度里的同名标记不生效（不是控制通道）。

## 证据工件（runs/p251w2_n04/）

`*.sqlite` 被仓库 `.gitignore` 排除，故快照内容同时镜像为 `metrics.json`（表 metric 的
name/value/decimals）与 `report_sidecar.json`（每条记录自带 source.sql 与 value）；二者任一即可
重建 `evidence.sqlite` 并单条重跑查询。合成夹具的数据集镜像为 `fixture/dataset.json`。

## 局限

- 无签名：value + query_hash + computed_at + 底层数据被一致伪造时查不出，本模块是篡改*证据*。
- `query_hash` 只证明「值来自这条查询」，不证明「查询是想要的度量」。
- 无模板/无 manifest 时退化为弱绑定（`binding_mode="value_equality"`）：字面量与某记录的可渲染
  文本逐字节相同即绑定；`require_manifest=True` 禁止该模式。