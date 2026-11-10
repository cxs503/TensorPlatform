# node_acceptance —— 战役节点验收证据自动聚合引擎

把「人工写报告、人来判定」升级为「机器读工件、机器给裁定、人工只裁量分歧」。

本包是 TensorPlatform 的**共性机制**：代码里不出现任何领域词条（域词条清单即平台
`tools/evidence_domain_lexicon.json`），领域含义只经由**工件**与 `lexicon.json` 注入。
域词由平台既有的 `tools/evidence_domain_scan.py` 机检（本包已加入其 targets，命中 0）。

```
节点工件目录 ──scan_node_dir──▶ NodeEvidence (指标叶子 + sha256 审计链)
                                     │
工件内声明的判据 ──discover──▶ Criterion(text/identifier/tolerance)
                                     │
                     aggregate ──▶ NodeVerdict  (PASS/FAIL/MECHANICAL_CONFLICT/INCOMPLETE)
                                     │
              build_acceptance_report ──▶ 六要素 JSON + Markdown
                                     │
                   cross_validate ──▶ 机器 vs 人工 定源表 + 一致率
```

## 模块

| 文件 | 职责 |
|---|---|
| `model.py` | 冻结数据模型：`Criterion`/`Observation`/`CriterionOutcome`/`NodeVerdict`，裁定合成规则 `node_verdict_from_outcomes` |
| `criteria.py` | 判据解析：标识符式 `x < 1e-3`、提示式 `耗时≤30s`/`一致率≥4/5`/`误差<3%`、布尔/存在式 |
| `scanner.py` | 工件扫描：声明式 glob + 工件根，指标叶子扁平化（含 scope 命名空间），逐文件 sha256 |
| `discovery.py` | 判据发现：`criteria`/`anchors` 列表、记录内 `(tolerance, value)` 对（含方向与「已记载偏差」识别） |
| `aggregator.py` | 证据聚合：metric_name → hint → declared_tolerance 三级解析阶梯，逐判据裁定 |
| `report.py` | 六要素报告生成（案例介绍/方法/结果/图/注意点/误差判定）+ Markdown |
| `cross_validate.py` | 与人工报告交叉验证：四类关系 + 定源表（两侧 source#pointer） |
| `self_audit.py` | 预注册自审协议：确定性选点、一致率、耗时、解析正确率、域词扫描、出图 |
| `cli.py` / `__main__.py` | `python -m tools.node_acceptance {scan,parse,judge,report,cross-validate,self-audit}` |

## 裁定语义（关键设计）

| 状态 | 含义 |
|---|---|
| `PASS` | 判据有工件证据且满足阈值 |
| `FAIL` | 判据被**显式声明**（文字或记录自带人类标记）且明确不满足 |
| `MECHANICAL_CONFLICT` | 数字越界，但记录本身记载了「已接受的偏差」（人类标记=通过 + 文字说明）：**需人工裁量**，既不算 PASS 也不算 FAIL |
| `UNKNOWN` | 无可解析比较、或工件缺证据、或「容忍度↔数值」关联由引擎推断而记录未声明方向：**拒绝猜** |
| `INCOMPLETE` | 节点级：无 FAIL，但存在 UNKNOWN/CONFLICT，不能判 PASS |
| `NO_CRITERIA` | 节点未声明任何可机裁判据 |

铁律：`FAIL` 黏性（不可被 PASS 票覆盖）；`UNKNOWN` 永不升级为 `PASS`；
推断出来的关联（不是工件显式声明）触发的越界一律不判 FAIL。

## 用法

```bash
# 看一个节点目录里有什么
python -m tools.node_acceptance scan /root/tp-agent/work/m12345

# 解析判据文本
python -m tools.node_acceptance parse "一致率≥4/5" "扫描+聚合耗时≤30s" "误差<3%"

# 机器裁定 + 与人工报告交叉验证
python -m tools.node_acceptance judge /root/tp-agent/work/m12345

# 出六要素报告（JSON + Markdown）
python -m tools.node_acceptance report /root/tp-agent/work/m12345 --out-dir artifacts/node_acceptance

# 预注册自审（确定性选点，5 个节点）
python -m tools.node_acceptance self-audit --work-root /root/tp-agent/work
```

## 自审结果（`artifacts/node_acceptance/self_audit.{json,md}`）

预注册协议（先定判据再看结果）：

* **选点规则**：按 mission id 升序，按 validation_report.json **内容哈希**去重（模板副本折叠成一个槽位），
  要求报告含人工状态字段 + 数值断言 + ≥3 条可机裁判据；不足时按签名轮转取第二个副本。规则可复跑，选点数 5。
* **判据**：一致率 ≥4/5、单节点 ≤30s、解析正确率 ≥90%、域词命中 0。

| 判据 | 目标 | 实测 | 结论 |
|---|---|---|---|
| 一致率（无矛盾口径：认证 + 无矛盾 + 已记载偏差） | ≥4/5 | **5/5** | PASS |
| 机器与人工**相反**的节点数 | 0 | **0** | PASS |
| 单节点耗时（扫描+聚合，含 600 文件上限） | ≤30s | **4.4s**（最大 6.5s） | PASS |
| 判据解析正确率（10 条预注册判据） | ≥90% | **100%** | PASS |
| 零领域语义（平台域词表机检） | 0 hit | **0 hit** | PASS |
| 严格认证率（机器独立复现人工二值裁定） | — | **0/5** | **honest FAIL** |

`strict` 为 0 的原因不是误判，而是**样本工件里必然存在不可机裁的判据**（`
"κ(S) ≈ 76`、"JSON output is valid" 这类无阈值散文判据），且 4 个节点各带 1 条
「已记载偏差」（数值越界但记录自述接受）——引擎按纪律停在 `INCOMPLETE`。
两者都写进报告，不用高口径掩盖低口径。

## 局限（如实声明）

1. **同源**：机器与人工读的是同一份工件，能抓「文字结论 vs 数值」不一致，抓不到「数值本身错」。
2. **推断关联**：`(tolerance, value)` 关联由引擎推断；故其越界只记为需裁量项，不判 FAIL——代价是召回率，
   换来的是零误杀。
3. **散文判据覆盖不到**：无阈值/无标识符的判据一律 UNKNOWN，占比高时节点只能 `INCOMPLETE`。
4. **语料模板化**：本地 2294 个 `validation_report.json` 只有极少数内容哈希（模板复制），
   自审样本多样性受语料限制，规则已按内容哈希去重。
5. **不计入评测的旁证**：开发期在 `m10005`/`m10125` 上引擎给出 `PASS` 且与人工 `agree_certified`
   （事后观察，不进主口径）。

## 测试

```bash
python -m pytest tests/test_node_acceptance.py -q     # 23 passed
python tools/evidence_domain_scan.py                  # 0 hit（本包已纳入 targets）
```