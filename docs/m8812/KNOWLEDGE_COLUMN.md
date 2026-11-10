# 知识专栏（Knowledge Column）— FEAT-TK-ASSET-OPS / m8812

> owner 11-10 三问之二：「如何在前端让人直观感受到」。本专栏把 TensorKnowledge 的
> 知识资产（Knowledge Object：claim / evidence / provenance / review 四契约记录）
> 变成控制台里可看、可下钻、可溯源、可降级的一等页面。

## 1. 页面结构（三 tab，骨架复用 /papers）

| tab | 内容 | 数据来源 |
|---|---|---|
| 总览 | 对象总数 / 四态信任漏斗 / 领域分布 / 按日增长 / 被引最多 / 最近被证伪 | `GET /query/stats`、`GET /ko`、`GET /query/by-evidence-relation` |
| 知识地图 | 领域 → 对象类型 → claim 下钻；证据链面板（claim → evidence → 源文件:行号@commit） | `GET /ko`、`GET /query/by-evidence-relation`、`GET /query/chain` |
| 检索与训练资产 | KE `/search` 即时检索 + 溯源面板；导出三件套 manifest 摘要 | `GET /search`；导出工件目录的 `*_manifest.json` |

视觉与 `/papers`（`PapersView.vue`）一致：同一 `tf-page` / `tf-card` / `HeroPanel` 骨架、
同一 `pp-stats` 指标卡与 `pp-h` 小标题、同一 `el-table + el-tag` 语言。未另起炉灶，
没有引入新的图表库（增长曲线用纯 CSS 柱，避免为一个页面加 300KB 依赖）。

路由 `/knowledge`（`router/index.ts`）+ 顶部导航「知识」（`ConsoleLayout.vue`）+ 客户端
`knowledgeApi`（`frontend-console/src/api/index.ts`）。

## 2. 数据纪律：单一契约源

* 所有知识数字经 **KE API**（`KE_API_BASE`，默认 `http://127.0.0.1:8021`）取；
  平台侧 `backend/api/knowledge_hub.py` **不打开 TK 库**（不连 SQLite/PG、不解析知识包取数）。
* 唯一本地文件面是**证据直读**（见 §3），读的是 TK 仓库里的文件，不是库。
* 训练资产 manifest 属**导出工件**（`python -m ke.cli export` 的产物，`KE_ASSET_DIR` 可配），
  不是知识对象表；KE API 当前没有 assets 端点，故按工件读文件并实测 sha256。
  这是本单唯一偏离「一切经 API」的地方，理由与边界写在 `knowledge_hub.py::knowledge_assets` 的 docstring。

### 平台侧端点（`backend/api/knowledge_hub.py`，prefix `/api/v1/tensor/knowledge`）

| 端点 | 作用 | 是否需要 JWT |
|---|---|---|
| `GET /status` | 引擎存活（`/healthz` 透传） | 是 |
| `GET /overview` | tab1 全量装配（含漏斗口径常量 `_ASSESSED_EXCLUDE`） | 是 |
| `GET /map` | tab2 领域 → 类型 → claim 树 | 是 |
| `GET /chain?claim_id=` | 证据链 + 每条 link 的 `record_source`（本地记录文件:行号@commit） | 是 |
| `GET /search?q=` | `/search` 透传 + 服务端脱敏 | 是 |
| `GET /assets` | 导出三件套 manifest 摘要 + sha256 前 8 位 | 是 |
| `GET /source/{path}?line=&context=` | 证据直读单文件窗口（`middleware/auth_guard` 免鉴权） | **否**（仅 GET/HEAD） |

## 3. 证据直读通道（复用 papers 免鉴权模式，坑在中间件层）

`/tensor/reports/{id}/figures/{name}` 的历史经验：**白名单必须写在
`backend/middleware/auth_guard.py`**。写在路由层会被守卫先拒（#227 曾因此重部署后 401 复发）。
本单新增：

```python
_KNOWLEDGE_SOURCE_PATH_RE = re.compile(
    r"^/api/v1/tensor/knowledge/source/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$")

def _is_knowledge_source_path(path: str) -> bool:
    """正则命中且不含 "." / ".." 路径段（拒绝穿越）"""
```

* 只对 `GET/HEAD/OPTIONS` 放行；写方法仍要 JWT。
* 路由层纵深防御：扩展名白名单（`.json/.md/.py/...`）+ 解析后路径必须落在 TK 仓库内
  （`relative_to` 围栏）+ 2MB 体积预算。
* 页面里两个入口：抽屉内直读（带 token，走平台）与「新窗口免鉴权直读 ↗」
  （`/api/v1/tensor/knowledge/source/...`，无 Authorization 也能 200，已实测）。

## 4. draft 正文不出 API（观感与安全双保险）

未发布对象（`publication_status != "published"`）在**服务端**剥离
`statement / snippet / quote / rationale / doc / notes / ...`，并打 `content_hidden: true`；
前端只显示「未发布 · 正文隐藏」。仅在渲染层隐藏是不够的——API 若照常返回正文，
观感要求就变成了一句空话，所以门在 `knowledge_hub.redact()` / `_redact_link()`，
由 `backend/tests/test_knowledge_hub.py` 逐条钉住。

引文可见性跟**证据记录自身**的发布态（`_evidence_pub_index`），不跟被引 claim：
一条已发布命题不代表它引用的证据已发布。

## 5. 降级路径

引擎不可达时，每个端点返回 `{available: false, reason: engine_offline|engine_error, detail, engine, checked_at}`，
不抛 500、不返回任何数字（避免把空值读成 0）。前端三 tab 分别渲染「引擎离线」占位 + 重试；
首屏加载与离线用 `checked_at` 区分（「加载中…」vs「引擎离线」）。
`/source` 不依赖引擎，KE 停机时证据直读仍可用。

## 6. 验证（可复现）

| 面 | 命令 | 结果 |
|---|---|---|
| 后端契约 | `cd backend && python -m pytest tests/test_knowledge_hub.py -q` | 16 passed（含 2 条真数据用例，引擎不在时自动 skip） |
| 前端构建 | `cd frontend-console && npm run build` | ✓ built（`KnowledgeView-*.js` 产出） |
| 端到端 DOM | `bash artifacts/m8812/e2e/run_dom_snapshot.sh online` / `offline` | 三 tab 真数据 DOM 快照 + 降级快照 |
| 直读工件 | `artifacts/m8812/e2e/source-direct-read.json` | 无 Authorization 头 200，`knowledge/oss-lbm/kruger/claims/claims.json:307@01a70cb` |

真实数据基线：KE 引擎（SQLite 实例）灌入 `knowledge/oss-lbm` 知识包 →
251 对象（claim 102 / evidence 142 / provenance 7）、300 证据边、0 评审事件。
DOM 首屏指标卡 `251` 与 `/query/stats.objects = 251` 精确一致。

## 7. 已知局限（诚实申报）

1. **本机 loongarch64 无浏览器**：playwright 官方无该架构包、系统无 chromium，
   因此**没有 UI 截图**。替代取证 = jsdom 挂真实 SFC + 真实后端数据的 DOM 快照
   （`artifacts/m8812/e2e/dom-*.html`）。这不是像素级视觉验收，CSS 布局未在真实浏览器里核对过。
2. **完整 `backend/main.py` 本机起不来**（缺 `cryptography`、pip 无索引），端到端用
   最小挂载 app（`harness_app.py`）替代：只替换 auto-login 与 `require_auth`，
   知识路由、数据、前端都是真实产物。生产容器里路径不变（`api.domain.tensor` 已 include）。
3. 增长曲线当前只有一天数据（知识包 2026-11-10 一次性灌入），图形退化为一根柱；
   `created_at` 口径随灌入时间，是「入库增长」而非「知识产生时间」。
4. 领域分布出现 `(未标注域)`：evidence / provenance 记录的 `scope.domain` 字段为空，
   平台不猜域，原样计数。这是知识包侧的数据质量问题，不是前端问题。
5. 评审/发布两档漏斗为 0：知识包刚灌入、尚未走 review 流程。页面如实显示 0，
   不为了「好看」补假数据。

## 8. 复现步骤

```bash
# 1) KE 引擎（TensorKnowledge 仓）
cd /root/tp-agent/work/m8801
KE_API_TOKEN=... KE_DB=/tmp/ke_m8812.db python3 -m uvicorn ke.api:app --host 127.0.0.1 --port 8021
KE_API_TOKEN=... python3 -m ke.ingest_client --base-url http://127.0.0.1:8021 \
    --package knowledge/oss-lbm --producer agent-m8801     # 251 objects

# 2) 平台侧最小挂载（本机缺依赖时的取证路径）
KE_API_BASE=http://127.0.0.1:8021 \
TENSOR_KNOWLEDGE_DIR=/root/tp-agent/work/m8801 \
KE_ASSET_DIR=/root/tp-agent/work/m8810/artifacts/ke_export \
python3 artifacts/m8812/e2e/harness_app.py                  # :8098

# 3) 前端 + DOM 取证
cd frontend-console && npm ci
bash ../artifacts/m8812/e2e/run_dom_snapshot.sh online
pkill -f "ke[.]api:app" && bash ../artifacts/m8812/e2e/run_dom_snapshot.sh offline
```