# Chronicle Report Line v2：冻结快照 + 数字溯源 + 防造假门

**Ten-section journal framework with machine-checkable provenance for platform evidence audits**

作者/责任主体：TensorPlatform chronicle line (IN18-T3)  
战役节点：IN18-T3  
证据快照：`f46ab6f316a2c10c1c38b67594643eb0483c0e7cbe7d67c7e20dfcb374f9f572`（采集于 2026-11-07T00:50:00+00:00）  
签发：2026-11-07

> 所有位于 `prov:begin/end` 块内的数字均带 `{{prov:<id>}}` 溯源标记，对应 `provenance.json` 中的 SQL 与快照哈希；校验器重跑 SQL 比对，不符即 FAIL。

## 1. Abstract

<!-- prov:begin -->

本报告给出一条可机器复查的顶刊报告生产线：证据先冻结为快照，数字再经 SQL 注入并内联溯源标记，最后由校验器重跑查询与哈希比对裁定 PASS/FAIL。首个实例的快照覆盖 21 {{prov:n-004}} 项注册能力、10 {{prov:n-005}} 条证据行、10 {{prov:n-006}} 个领域。其中证据等级被对账为 REAL 的能力占 1 {{prov:n-007}} 项（4.8 {{prov:n-008}} %），宣称与证据一致的仅 19.0 {{prov:n-009}} %。负例测试显示：任意一位数字被改写、快照被触碰、图件被替换，校验器均判 FAIL。

<!-- prov:end -->

## 2. Introduction

论文生产线 v2 的前两稿因同一根因被否（W2 判例）：稿中数字无法回溯到任何工件，且图件数据在图脚本内合成。事后补的文字级规则（PG1–PG8 路径/引用/算术校验、PG9 执行式图件溯源）各自有效，但二者都**假定被检验的数据有一个不动的状态**。而实际流水线中，报告引用的工件随时被后续节点重建，因此『数字对得上』这件事缺少可复现的基准面。

本节点的差距陈述是可判定的：需要 (i) 冻结的、带哈希的证据快照；(ii) 数字与快照查询/图件哈希之间的强制绑定；(iii) 一个能对伪造数字说 FAIL 的校验器，并以负例测试证明它真的会说 FAIL。三项均在本仓 `tools/chronicle/report/` 内实现。

贡献（逐条可被本报告的机器产物复核）：

1. `snapshot.py`：工件 JSON → SQLite 快照 + SHA-256 锚定（可检测事后篡改）。
2. `provenance.py`：`{{prov:<id>}}` 标记协议与记录库，数字与 SQL/快照 ts 哈希一一绑定。
3. `skeleton.py`：十节顶刊骨架与结构校验（缺节/乱序/空节即 FAIL）。
4. `figures.py`：三图管线（饱和曲线/覆盖度热图/成功漏斗），PDF+PNG 双出，图件哈希入账。
5. `verify.py`：重跑 SQL + 哈希比对的双向防造假门，负例样例 MUST FAIL。


## 3. System Architecture

数据流（单向，无回写）：

```
artifacts/capability_ledger/capability_ledger_resolved.json   (真实平台工件)
        |  ingest_capability_ledger()
        v
artifacts/chronicle_report/snapshot/report.sqlite  --sha256-->  snapshot_hash
        |  SQL 查询（只读连接 mode=ro）
        +--> ProvenanceStore.add_number(value, sql, snapshot_hash, snapshot_ts)
        |                                        |
        v                                        v
   figures/*.png|pdf  --sha256-->  provenance.json  -->  manuscript.md 内联 token
                                                         |
                                            verify.py 重跑 SQL + 重算哈希
                                                         v
                                              verify.json (PASS/FAIL)
```

契约与版本：`chronicle-snapshot/1`、`chronicle-provenance/1`、`chronicle-verify/1`、`chronicle-skeleton/1`。四个 schema_version 均为本节点新增，未改动任何既有 API 面或表结构（冻结纪律：只增不改，新工具落在 `tools/chronicle/` 独立目录）。

失败模式与对策：

| 失败模式 | 检测点 | 判定码 |
|---|---|---|
| 数字被改写 | 印刷值 vs 记录值 | FABRICATED_NUMBER |
| 记录被改指 | 记录值 vs 重跑 SQL | SOURCE_DRIFT |
| 快照被编辑 | sqlite 字节哈希 | SNAP_HASH_MISMATCH |
| 图件被替换 | 图件 sha256 | FIGURE_HASH_MISMATCH |
| 数字漏标 | 强制块内裸数字 | UNMARKED_NUMBER |
| 标记悬空 | token 无记录 | DANGLING_TOKEN |


## 4. Methods

证据采集与快照：工件以原始字节哈希（`source.sha256`）入库，随后把能力行与证据行导入 SQLite 并 `VACUUM`，对库文件字节取 SHA-256 作为 `snapshot_hash`。任何后续写操作改变文件字节即被 `Snapshot.integrity()` 判 FAIL；连接以 `mode=ro` 打开，校验器在物理上无法修改自己的证据。

溯源标记协议：数字在文本中的唯一合法形态是 `数值 {{prov:<id>}}`，记录包含 `value / decimals / locator / source{sql, params, snapshot_hash, snapshot_ts}`。强制范围为 `prov:begin`/`prov:end` 注释块；块内出现无标记数字即 FAIL。本报告选择只强制 Abstract 与 Results 两块，并在校验输出中显式报告强制块数量，不以『全文覆盖』的措辞掩盖实际覆盖范围。

判定规则与容差：印刷值允许误差取自记录精度——半量级（`0.5 x 10^-decimals`），即四舍五入显示不算伪造，而任一有效位被改写必然超预算。SQL 重跑采用严格相等（相对预算 1e-9），因为计数类结果不应有浮点漂移。


## 5. Experimental Setup

- 数据源：`/root/tp-agent/work/m4976/artifacts/capability_ledger/capability_ledger_resolved.json`（源哈希 `7adb9835d8489f4a…`）
- 快照哈希：`f46ab6f316a2c10c1c38b67594643eb0483c0e7cbe7d67c7e20dfcb374f9f572`（2026-11-07T00:50:00+00:00，共 21 能力行 / 10 证据行）
- 运行环境：Python 3.11.6 / matplotlib 3.10.8 / numpy 2.4.6 / Linux loongarch64

运行命令（工作树内可直接执行）：

```
python3 -m tools.chronicle.report.demo                     # 全链构建 + 校验
python3 -m pytest tests/test_chronicle_report_gate.py -q   # 门禁单测（含负例）
```

负例（对抗）测试设计——每例都必须是『预期 FAIL』，若 PASS 则视为门禁失效：

| 负例 | 注入动作 | 预期判定码 |
|---|---|---|
| N1 伪造数字 | 把 Results 表中某数字 +1 而保留 token | FABRICATED_NUMBER |
| N2 记录改指 | 改 provenance.json 中记录的 value | SOURCE_DRIFT |
| N3 快照篡改 | 向 sqlite 追加一行 | SNAP_HASH_MISMATCH |
| N4 图件替换 | 覆盖 PNG 字节 | FIGURE_HASH_MISMATCH |
| N5 漏标 | 块内插入裸数字 | UNMARKED_NUMBER |


## 6. Results

<!-- prov:begin -->

**表 1 快照一览（全部数值由 SQL 注入，零手抄）**

| 指标 | 数值 | 说明 |
|---|---|---|
| 注册能力总数 | 21 {{prov:n-010}} 项 | `capability` 行数 |
| 证据行总数 | 10 {{prov:n-011}} 行 | `evidence` 行数 |
| 带证据能力数 | 3 {{prov:n-012}} 项 | `evidence_count > 0` |
| 宣称 REAL | 18 {{prov:n-013}} 项 | `claimed_level = REAL` |
| 对账后 REAL | 1 {{prov:n-014}} 项 （4.8 {{prov:n-015}} %） | `reconciled_level = REAL` |
| 宣称与证据一致 (OK) | 4 {{prov:n-016}} 项 （19.0 {{prov:n-017}} %） | `divergence = OK` |
| 过度宣称 (OVERCLAIM) | 2 {{prov:n-018}} 项 （9.5 {{prov:n-019}} %） | `divergence = OVERCLAIM` |
| 未验证 (UNVERIFIED) | 15 {{prov:n-020}} 项 | `divergence = UNVERIFIED` |
| 证据获取饱和比 | 0.300 {{prov:n-021}} | 有证据能力数 / 证据行数 |
| 领域数 / 证据等级数 | 10 {{prov:n-022}} / 3 {{prov:n-023}} | `COUNT(DISTINCT ...)` |

**图 1–3**（每张图的每个数组均来自一条已入账 SQL；图件 SHA-256 已写入 provenance）

![证据获取饱和曲线](figures/fig1_saturation.png) {{prov:fig-001}}

![能力证据覆盖度热图](figures/fig2_coverage_heatmap.png) {{prov:fig-002}}

![能力存活漏斗](figures/fig3_success_funnel.png) {{prov:fig-003}}

<!-- prov:end -->

负例测试结果见 `tests/test_chronicle_report_gate.py`：五类注入全部被对应判定码拦下，任一负例若转为 PASS，该测试即红。门禁对本报告自身的裁定写在 `artifacts/chronicle_report/verify.json`。


## 7. Discussion

机器可验证的结论：在冻结快照上，(a) 数字被改写、记录被改指、快照被编辑、图件被替换、数字漏标，五类造假路径均有独立判定码命中；(b) 三张图的数组与快照查询一一对应，不存在绘图期合成路径（渲染代码只做 reshape 与坐标映射）。

作者判断（不作为 Abstract 数字）：证据覆盖缺口的主要成因并非能力缺失，而是审计记录（报告 id / 引用行）未与能力条目双向绑定——大量能力的 divergence 为 UNVERIFIED 而非 OK/OVERCLAIM，意味着**没有可判定的证据**，而不是**证据为负**。这一判断本身未经本报告的门禁验证，属解释性内容。

工程可用性：本管线是纯增量工具，不触碰任何冻结 API 面与既有表结构；其输入只有工件路径，因此可直接挂到任何后续报告节点上。


## 8. Limitations

- 覆盖范围：仅实现能力台账一种工件摄取器。其他工件类型（基准对比 JSON、冰区载荷表）需要各自 ingester，本节点未实现，不做任何覆盖声明。
- 方法学：SQLite 文件哈希提供篡改**证据**而非篡改**证明**（工作树内无私钥签名），能发现改动，不能证明改动者身份。
- 判定范围：仅 `prov:begin/end` 块内的数字被强制；块外（Introduction/Discussion）的数字与百分比为叙述性内容，不受 FABRICATED_NUMBER 约束。
- 精度：门禁判定『值是否一致』，不判定『值是否有物理意义』；物理精度仍须由各领域验证节点（对标实验/参考值 <3%/<15%）单独给出。


## 9. Threats to Validity

- 内部效度：容差取半量级显示精度，若作者以远超精度的舍入书写（例如把 4 位写成 2 位整数），存在把 0.4 的偏差合法化的可能。缓解：记录 decimals 由生成器固定，禁止手写降精度。
- 外部效度：本实例只覆盖一个工件与一种 schema；对其它仓库工件的可移植性依赖 ingester，未做跨仓验证。
- 构念效度：`{{prov}}` 证明数字来自快照查询，不证明该查询切中论文论点；指标选择本身的正确性不在门禁范围内。
- 结论效度：负例测试是对**已知**造假形态的检验（改写/改指/篡改/替换/漏标）；未知形态（例如把伪造值同时写入工件再重建快照并一致地重算哈希）可绕过，此类攻击需要外部时间戳或签名，本节点未提供。


## 10. Reproducibility

一键复跑（本仓工作树内，无需网络）：

```
# 1) 重建快照（确定性：同一源工件 -> 同一库字节）
python3 -m tools.chronicle.report.snapshot --source /root/tp-agent/work/m4976/artifacts/capability_ledger/capability_ledger_resolved.json \
        --out artifacts/chronicle_report/snapshot
# 2) 出图 + 生成稿 + 校验
python3 -m tools.chronicle.report.demo
# 3) 门禁单测（含 5 类负例）
python3 -m pytest tests/test_chronicle_report_gate.py -q
```

预期输出：步骤 2 打印 `status: PASS` 并以 exit code 0 结束（FAIL 时为 1）；门禁单测全绿。关键判据数字见 `artifacts/chronicle_report/report.json` 的 `acceptance` 段。

产物清单：

| 路径 | 内容 |
|---|---|
| `artifacts/chronicle_report/snapshot/report.sqlite` | 冻结证据库 |
| `artifacts/chronicle_report/snapshot/manifest.json` | 快照哈希与源哈希 |
| `artifacts/chronicle_report/figures/*.png|.pdf` | 三图双格式 |
| `artifacts/chronicle_report/provenance.json` | 溯源记录库 |
| `artifacts/chronicle_report/verify.json` | 门禁裁定（本报告自身） |
| `artifacts/chronicle_report/report.json` | 机器验收记录 |
| `docs/papers/chronicle-report-v2/manuscript.md` | 本稿 |
