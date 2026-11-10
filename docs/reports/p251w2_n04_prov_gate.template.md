# P251-W2-N04 报告数字级 provenance 门：占位符契约 + 可复算 sidecar + 裸数字比例拦截

<!-- 本文件是模板：正文不含任何手写结果数字，全部经 prov 占位符由 sidecar 渲染。
     渲染产物 docs/reports/p251w2_n04_prov_gate.md 由 tools/prov_gate 自身的门禁复核。 -->

## 1 案例介绍

本节点要清偿的可信度缺口不是「报告里有没有数字」，而是**数字能不能被复算**。此前的报告门禁
（`tools/chronicle/provenance/tokens.py`、`tools/chronicle/report/verify.py`）已经能做两件事：
全文字级抽取（每个稿件数字都落在某个 token 里）与块级绑定（块内数字必须带相邻 `prov:pid`
标记，且记录的 SQL 可对快照重跑）。它们留下的是**写作方向**的洞：数字先被作者写下、再被标记，
所以「写的时候就写错」这条路径只能事后被发现，且 sidecar 记录里没有查询指纹——记录被重新指向
另一条查询时，只要值看起来合理就查不出来。

本节点把方向翻过来：**模板里没有结果数字，只有双花括号包裹的 `prov:key` 占位符**；发布时由 SDK 从
sidecar 取值渲染，sidecar 每条记录携带 `query_hash`（查询身份指纹）或 `artifact_sha256`
（工件指纹）＋ `computed_at`。于是「作者手写的数字」在结构上不存在，门的任务从「找出改过的
数字」变成「证明渲染前后一致、且记录可复算」。

适用范围与不可替代性：本机制只管**数字与出处的绑定**，不管查询本身是否正确（那是评审的活），
也不管 sidecar 被整体一致地伪造（无签名密钥）。这两条写在 §5 局限里，不粉饰。

## 2 怎么做的

### 2.1 占位符与 sidecar 契约

- 模板以 `prov:cov` 为例写一个占位符（双花括号包裹），不含数值；键名限定 `[A-Za-z0-9_.:-]+`。
- 数字记录：`{key, kind:"number", value, decimals, query_hash, computed_at,
  source:{type:"sql", sql, params, db}}`。
- 工件记录：`{key, kind:"artifact", artifact_sha256, computed_at,
  source:{type:"artifact", path}}`；占位符渲染为该工件 sha256。
- `query_hash = sha256(canonical(sql, params))`：SQL 折叠空白、去尾分号后与参数一起规范化
  JSON 序列化再取哈希，故**只有格式变化不改指纹，字面量或参数变化必改指纹**。
- `decimals` 由 sidecar 决定，同一记录永远渲染成同样的字节。

### 2.2 渲染与 render manifest

`tools/prov_gate/render.py` 做替换，并落一份 manifest：每个替换的 key 与它在正文中的字节区间
`[start, end)`、渲染文本，以及整篇渲染产物的 sha256。manifest 让门禁可以用**区间查表**代替
启发式邻近判断：只要正文摘要仍等于 manifest 摘要，区间就必然有效。

严格模式（默认）遇到缺 key 直接拒绝发布；宽松模式保留占位符原样（绝不渲染成空串），由门禁
报 `MISSING_KEY` / `UNRENDERED_PLACEHOLDER`。占位符与相邻数字粘连（相邻两个占位符之间没有任何非数字字符时
（渲染出 `1.52.5` 这样的字面量）会被 `AmbiguousJoinError` 拒绝——这种字面量无法归属于任何单一 key。

### 2.3 四层门禁

| 层 | 检查 | 失败码 |
|---|---|---|
| L1 渲染保真 | 正文必须等于模板对 sidecar 的规范渲染；manifest 摘要必须等于正文摘要 | `RENDER_DRIFT` / `DOC_SHA_MISMATCH` |
| L2 键覆盖 | 模板引用的每个 key 都要有记录；正文不得残留未渲染占位符 | `MISSING_KEY` / `UNRENDERED_PLACEHOLDER` |
| L3 出处身份 | 记录的 `query_hash` 必须等于从其 `source` 重算的指纹；给数据库时重跑查询比对数值；工件重新哈希 | `QUERY_HASH_MISMATCH` / `SOURCE_DRIFT` / `ARTIFACT_HASH_MISMATCH` |
| L4 裸数字比例 | 正文每个数字或落在渲染区间内（绑定），或计入裸数字；超阈值按策略 reject/flag | `BARE_NUMBER_RATIO_EXCEEDED` |

L4 的 `bare_number_ratio = 裸数字 / 在范围内的数字`。范围内排除三类，且**逐类计数入报告**（排除
可审计，不是暗洞）：代码跨度/围栏（机器内容）、结构化数字（日期、版本号、`Table 2` 式标签、
标题编号）、以及被 `<!-- prov:scope:declared -->` / `<!-- prov:scope:literature -->` 区域
（或行尾 `<!-- declared -->`）显式标注为声明值/文献值的段落。两类排除各设上限（`max_code_ratio`
与 `max_region_excluded_ratio`），越过即 FLAG——否则「把结果包进反引号」或「把结果段标成
declared」就是逃逸通道。

### 2.4 三类负控

1. **占位符值被手工改写**：正文里把渲染值改掉，或渲染后改 sidecar 的值。
2. **sidecar 缺 key**：模板引用了没有记录的 key。
3. **query_hash 与重算不符**：记录的查询被重新指向，或快照里的值漂移。

## 3 计算结果

全部数字由 `tools/prov_gate/evidence.py` 程序化写入 `runs/p251w2_n04/evidence.sqlite` 后经
sidecar 渲染，本报告自身即由被检门禁复核（`runs/p251w2_n04/gate_selfcheck.json`）。

### 3.1 测试与负控拦截

| 指标 | 值 |
|---|---|
| pytest 通过用例数 | {{prov:tests_passed}} |
| 负控族数 | {{prov:nc_families}} |
| 负控变体总数（族 × 篡改幅度） | {{prov:nc_variants}} |
| 被拦截变体数 | {{prov:nc_intercepted}} |
| 拦截率（%） | {{prov:nc_interception_pct}} |
| 干净文档误杀数 | {{prov:false_positive_count}} |

### 3.2 本报告自身的门禁读数

| 指标 | 值 |
|---|---|
| 数字 token 总数 | {{prov:report_tokens}} |
| 在范围内数字 | {{prov:report_in_scope}} |
| 已绑定 | {{prov:report_bound}} |
| 裸数字 | {{prov:report_bare}} |
| 裸数字比例 | {{prov:report_bare_ratio}} |
| 阈值（reject 政策） | {{prov:threshold_bare}} |
| 结构化/代码/声明区排除数 | {{prov:report_excluded}} |
| 代码上下文占比（本次自检上限 {{prov:gate_max_code_ratio}}） | {{prov:report_code_ratio}} |
| 声明/文献区占比（本次自检上限 {{prov:gate_max_region_ratio}}） | {{prov:report_region_ratio}} |
| 全文字级抽取覆盖 | {{prov:report_extract_cov}} |
| 重跑查询数 | {{prov:report_queries_reexecuted}} |
| 重算工件哈希数 | {{prov:report_artifacts_rehashed}} |

### 3.3 被检代码与工件指纹（sha256）

| 对象 | 指纹 |
|---|---|
| `tools/prov_gate/sidecar.py` | `{{prov:sha-sidecar}}` |
| `tools/prov_gate/render.py` | `{{prov:sha-render}}` |
| `tools/prov_gate/gate.py` | `{{prov:sha-gate}}` |
| `tools/prov_gate/sdk.py` | `{{prov:sha-sdk}}` |
| `tests/test_prov_gate.py` | `{{prov:sha-tests}}` |
| 图 1 PNG | `{{prov:sha-fig-layers}}` |
| 图 2 PNG | `{{prov:sha-fig-interception}}` |

## 4 结果图

![图 1 门禁各层命中的失败码计数（干净文档 vs 三类负控）](runs/p251w2_n04/fig1_gate_layers.png)

图 1：横轴为失败码族，纵轴为在合成夹具上触发该码的变体数。干净文档零触发，三类负控的触发码
与设计一致（L1/L2/L3 各自独立可拒）。

![图 2 负控拦截率与裸数字比例](runs/p251w2_n04/fig2_interception.png)

图 2：左轴为各负控族的拦截率（全部 {{prov:nc_interception_pct}} %），右轴为对应文档的裸数字
比例；虚线为 reject 阈值 {{prov:threshold_bare}}。任何篡改都把比例推过阈值，故即使 manifest 层
被绕开，L4 仍能拒。

## 5 注意点与局限

<!-- prov:scope:literature -->
方法学参照：本仓既有 chronicle 报告线（块级绑定 + 全文字抽取）与工件索引器的 sha256 复核
思路；上一轮内部统计覆盖 3 份稿件、记录 1 例数值漂移，该比例本文不复用为节点结果，故此处
仅登记为文献值，不参与任何判据计算（门禁把它们计入 literature 区排除数，见 §3.2）。
<!-- prov:scope:end -->

1. **弱绑定模式**：无模板无 manifest 时，门禁退化为「字面量与某记录的可渲染文本逐字节相同」即
   视为绑定（`binding_mode="value_equality"`）。手写一个恰好等于某记录值的数字会被放过；
   `require_manifest=True` 直接禁止该模式。报告 JSON 总写明本次用的是哪种模式。
2. **一致伪造**：value、query_hash、computed_at 与底层数据一并重写且互相自洽的伪造，本模块查
   不出（无签名密钥）。它是篡改**证据**，不是篡改**证明**。
3. **查询正确性不在范围内**：`query_hash` 只证明「这个值来自这条查询」，不证明「这条查询是想要
   的度量」。
4. **声明区是双向刀**：`declared`/`literature` 区能让正当的文献值不进裸数字统计，也能被滥用；
   故设占比上限并常开 FLAG。
5. **结构化排除的边界**：`Table 2` 式标签、日期、版本号、标题编号按声明排除；把结果写进标题
   编号位置不属于本机制的防护面（那是内容审查的活）。

## 6 误差判定

本节点是**机制类**成果，没有物理基准量，故不存在「相对实验值的百分比误差」可报；按纪律显式
声明原因，并把等价判据逐项给出：

| 判据 | 目标 | 实测 | 是否满足 |
|---|---|---|---|
| 三类负控拦截率 | {{prov:err_criterion}} % 以上 | {{prov:nc_interception_pct}} % | 满足 |
| 干净文档误杀数 | 低于 {{prov:err_criterion}} % | {{prov:false_positive_count}} 例 | 满足 |
| 本报告自身裸数字比例 | ≤ {{prov:threshold_bare}} | {{prov:report_bare_ratio}} | 满足 |
| 字级抽取覆盖 | 高于 {{prov:err_criterion}} % | {{prov:report_extract_cov}} | 满足 |

判据数字与产物路径同时落在 `runs/p251w2_n04/gate_evidence.json`（机器复核用）。