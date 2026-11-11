# 秘密泄露扫描门（三出口 · m12525 · IN387-PLAT02）

**结论摘要**：三出口（提交 / 报告 / 工件）秘密卫生门已在 TensorPlatform 落地，引擎、脱敏器、
已知值通路、注入标定全部自带测试。标定结果：**检出率 20/20 = 100%**（判据 C5 ≥95%）、
**误报率 0/20 = 0%**（判据 C6 ≤5%）、**三出口 3/3 生效**（C7）、**脱敏幂等 20/20**（C8）。
仓库基线扫描另给出真实世界口径：5862 个文件里 22 个命中（0.375%），其中 16 处是**既有
硬编码开发库口令**（真实发现），9 处是 base64/长字面量（误报面，已在局限中列明）。

---

## 1. 问题：门只覆盖了三分之一

仓内既有 `tools/write_gate/secrets.py` 只判**提交面**（staged 变更），判据是"模式 + 熵闸 +
行级豁免"；报告出口（`:8010` 报告 payload，含 `figures[].png_base64`）与工件出口
（`artifacts/**` 写盘）**没有任何秘密判据**。一次报告泄露或一个工件泄露，等于把凭证直接
写进可归档、可分发的产物里。

本单要补的不是"再来一个正则列表"，而是**一台引擎 + 三个出口 + 可量化标定**。

## 2. 架构

```
config/secret_hygiene/rules.json      # 规则库（PEM/JWT/DB URL/Bearer/云密钥/前缀令牌/赋值式/熵闸）——数据
config/secret_hygiene/known_values.json  # 已知值注册表：只存 sha256 摘要，仓内不存明文
tools/secret_hygiene/engine.py        # 匹配 + 豁免 + 熵闸 + 已知值摘要匹配（返回区间，供脱敏）
tools/secret_hygiene/redact.py        # <REDACTED:type:len> 幂等改写（文本 + 结构化载荷）
tools/secret_hygiene/outlets.py       # 三出口统一 Verdict：precommit / report / artifact
tools/secret_hygiene/integration.py   # 真实挂接：git 索引快照、ReportGuard、write_artifact
tools/secret_hygiene/calibrate.py     # 20 合成秘密 + 20 良性样本 → C5~C8
scripts/pre_commit_secret_gate.sh     # 提交钩子（ln -sf 到 .git/hooks/pre-commit）
```

设计要点（每条都有对应测试）：

* **规则是数据**：代码里没有一条具体凭证样例；语料生成器连 PEM 头都由字符串拼接生成，
  否则门会把自己的标定代码判成泄漏（自指误报，实测踩过）。
* **脱敏格式固定** `<REDACTED:rule_id:length>`：判决 JSON 可安全归档（只回显类型与长度），
  且**幂等**——标记本身不含任何规则能命中的字符组合。
* **判决三态**：`block`（默认拦）/ `redact`（放行脱敏件）/ 不可判定（环境缺失 → 交回调用方）。
* **豁免三层**：行级 allow（`data:image/`、`${...}`、`<your-x>`、字母表字面量）、规则级 allow
  （`os.environ`、`****`、函数调用式值）、熵闸（长随机串才判红，纯 hex 摘要与 camelCase 长标识符不判）。
* **已知值通路**：按 token 的 sha256 与注册表比对，覆盖"不符合任何模式"的组织内自定义口令。

## 3. 标定结果

### 3.1 注入标定（C5/C6/C7/C8）

| 指标 | 基线（首轮规则集） | 本轮（补规则+豁免） | 判据 |
|---|---|---|---|
| 检出率（20 合成秘密） | 17/20 = 0.850 | **20/20 = 1.000** | C5 ≥0.95 ✅ |
| 误报率（20 良性样本） | 1/20 = 0.050 | **0/20 = 0.000** | C6 ≤0.05 ✅ |
| 三出口生效 | 未覆盖 | **3/3** | C7 =100% ✅ |
| 脱敏幂等 | 未覆盖 | **20/20** | C8 ✅ |
| 已知值通路 | 未实现 | 命中（摘要精确匹配） | 附加 ✅ |

首轮基线漏检 3 类，本轮补齐（每一条都是**真实能力缺口**，不是调参过拟合）：

1. **JWT 带 base64 padding**：`eyJ…==.…==` 的 `=` 不被旧字符集接受 → 规则允许 `={0,2}`；
2. **`mongodb+srv://` / `postgresql://` 的凭证 URL**：原本只覆盖通用 URL 形态 →
   改为按 scheme 枚举；
3. **良性样本误报**：`AKIAIOSFODNN7EXAMPLE` 这类"文档示例键"因 `\bexample\b` 词边界失效
   （`7E` 之间无边界）而漏放 → 改为不带词边界的 `(?i)example`。

标定还暴露两个**实现 bug**（修复前会让报告出口瞎掉）：

* `credential_assignment` 不认 JSON 键的闭合引号，`{"token": "…"}` 漏检 —— 报告 payload
  恰恰是 JSON，属致命缺口；
* 匹配区间取 `match.group(1)` 时对无捕获组的规则抛 `IndexError`（引擎崩溃而非漏检）；
* **自用自家门发现的第三个 bug**：用报告出口判本单自己的报告 payload 时，
  `figures[].png_base64`（高熵 base64 图件）被 `high_entropy_blob` 判红 —— 平台规范载荷
  每一份带图报告都会命中，等于报告出口对所有带图报告失效。修法：新增**字段级豁免**
  `binary_field_patterns`（按 JSON 指针末段判定图件字段），且豁免字段指针写入 `suppressed`
  留痕；回归测试 `test_report_gate_allows_binary_figure_payload_but_flags_caption`
  同时断言"图件放行 + caption 里的真凭证仍被拦"。

### 3.2 仓库基线扫描（真实世界口径）

| 指标 | 数值 |
|---|---|
| 扫描文件 | 5862（`*.py/*.md/*.json/*.yml/*.yaml/*.sh/*.conf/*.txt/*.js/*.ts`） |
| 命中文件 | 22（0.375%） |
| 命中分族 | `db_url_credentials` 16 · `high_entropy_blob` 9 · `credential_assignment` 5 · `bearer_token` 2 |

人工判读这 22 处：

* **16 处 `db_url_credentials` 是真实发现**：`postgresql://<user>:<dev-password>@…` 形式的
  **硬编码开发库口令**散落在 `backend/config.py`、`backend/main.py`、`kubernetes/configmap.yaml`、
  `.devcontainer/docker-compose.yml` 等处（同一口令多次出现）。这是既有债务，本单只**报**不**改**
  （改造涉及部署语义，属另一张单），已作为 debt 留在 `repo_baseline.json`。
* **9 处 `high_entropy_blob` 是误报面**：文档里的 base64 图块、字母表字面量、仓库 README 原文。
* **测试夹具**（`Bearer abc123.def456.ghi789`、`sk-abcdefghijklmnopqrstuvwxyz` 等）被判红——
  它们是"授权金丝雀"，正确做法是**策略级 allowlist（按路径）**，本单未做（见局限）。

## 4. 三出口怎么用

```bash
# 出口一：提交（钩子）
ln -sf ../../scripts/pre_commit_secret_gate.sh .git/hooks/pre-commit
python3 tools/secret_hygiene/cli.py staged --mode block        # 退出码 1 = 拦

# 出口二：报告发布前
python3 tools/secret_hygiene/cli.py gate --outlet report --payload-file /tmp/report.json

# 出口三：工件写盘前（block 抛 SecretBlocked / redact 写脱敏件并留审计 JSONL）
python3 tools/secret_hygiene/cli.py gate --outlet artifact --path out/x.json \
        --content-file /tmp/x.json --mode redact --audit artifacts/secret_hygiene/audit.jsonl
```

代码内挂接：`ReportGuard(cfg).assert_publishable(payload)` 发布前调用；
`write_artifact(path, content, cfg=cfg, root=ROOT)` 替代裸 `open(...,'w')` 写工件。

## 5. 产物与审计链

```
artifacts/secret_hygiene/calibration.json          # C5~C8 全部数字（含基线/语料/逐出口）
artifacts/secret_hygiene/calibration.json.sha256   # sha256: 52a7fc47…
artifacts/secret_hygiene/repo_baseline.json        # 仓库基线扫描逐文件脱敏命中
artifacts/secret_hygiene/repo_baseline.json.sha256
```

## 6. 局限（诚实声明）

1. **标定语料是自建的**：20+20 由本单编写并用于调规则，实测 100%/0% 是**上界**，
   不代表对未知凭证族的召回。真实覆盖需要从事件台账回填语料（已知值注册表就是为此留的接口）。
2. **真实世界误报率高于 5%**：基线扫描中 9/22 命中属 base64/长字面量误报（文件级 0.15%）。
   判据 C6 的 0% 是**标定语料口径**，两个口径已在报告里分列，不合并宣称。
3. **测试金丝雀会命中**：`tests/**` 里的合成令牌是"故意像凭证"的内容，需策略级路径 allowlist；
   本单只留了行级/规则级豁免，未做路径策略。
4. **熵闸对二进制/大文件不做判定**：`scan_file` 跳过含 NUL 的二进制与 >2MB 文件（由工件出口
   的字节判据兜底时需另加），这是性能取舍。
5. **不含运行时凭证校验**：门判"内容像不像凭证"，不验证凭证是否仍有效；吊销/轮换不在此范围。