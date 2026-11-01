# 免登录配图 / root_path / auto-login：401 复发线的验证欠账清算（m1405）

> 席位 tp-mod-4（models）自主工时单。**性质：验证欠账补齐**（把已合并功能钉进回归围栏 + 变异检验 + 线上外部验证），不改业务语义。
> 证据源（唯一真值，禁止手抄）：`docs/audit/artifacts/mutation_check.json`
> 复算命令：`python3 docs/audit/mutation_check.py && python3 docs/audit/gen_figures.py`
> 回归测试：`cd backend && python -m pytest tests/test_auth_guard_figures_regression.py -q --noconftest`

## 1. 为什么这条欠账值得优先还

三个事实叠加，构成本单的判据：

| 事实 | 来源 | 后果 |
|---|---|---|
| `_FIGURES_PATH_RE`（报告配图免登录，owner 2026-10-31 选 A）入库后**零测试** | `git log` #232 / `backend/middleware/auth_guard.py:91` | #227 曾整件漏交，重部署后 `<img>` 401 复发过，同类漏交会静默复发 |
| `uvicorn --root-path /tp` 时 `request.url.path` 带前缀，白名单全失配 | `auth_guard.py` LOCAL PATCH 注释 + commit 86e00c8 | 历史上全站 401；补丁本身也无测试 |
| 免登录面必须**最小**（只放行单文件配图），报告正文/列表仍须鉴权 | `tensor_reports.py:164` 注释 | 任何"顺手放宽"（前缀白名单化）都会把整张报表变公开 |

已合并 ≠ 已验证。这三条都属于"功能在、判据无"的典型验证欠账，且失效方式是**静默的**（401 只在浏览器里表现为图裂）。

## 2. 做法

### 2.1 判据（先于动手，机器可裁）

真值表两列对齐：**allow**（应跳过鉴权）与 **deny**（必须 401 且不触达路由层）。
共 18 条：4 条正例（GET/HEAD 单文件、含点/横线/下划线的文件名）+ 4 条写方法 + 7 条穿越/畸形路径 + 3 条"最小面"边界（报告列表、报告正文、多段尾随）。

### 2.2 root_path 故障注入（配对设计，防"测试空转"）

同一请求两种 scope：
- `root_path="/tp"`、`path="/tp/api/v1/..."` → 剥离后应放行（补丁生效）；
- `root_path=""`、`path="/tp/api/v1/..."` → 必须 401（补丁失效的等价还原）。

只有两条同时成立，才说明测试确实在测补丁而非恒真。5 条用例覆盖 health / 配图 / auto-login / 报告正文 / 根路径。

### 2.3 变异检验（证明守卫承重）

对源码做文本手术后 `exec` 成独立内存模块，与真实模块并行跑同一批真值表：

| 变异 | 手术 | 免登录配图 | root_path | 是否被抓 |
|---|---|---|---|---|
| 基线 | 无 | 18/18 | 5/5 | — |
| **M1** | `_FIGURES_PATH_RE` → `(?!)`（永不匹配） | **14/18** | 4/5 | ✅ 抓到（4 条 allow 行翻 401） |
| **M2** | 删除 root_path 剥离块 | 18/18 | **1/5** | ✅ 抓到（4 条 allow 行翻 401） |

M1 顺带把 1 条 root_path 用例（`/tp/…/figures/…`）也打掉，说明两守卫在配图路径上串联——正当。

### 2.4 线上外部验证（只读 GET，127.0.0.1:8010）

| 探针 | 期望 | 实测 | 判读 |
|---|---|---|---|
| `GET /tensor/reports` | 401 | **401** | 列表仍鉴权 ✅ |
| `GET /tensor/reports/970` | 401 | **401** | 正文仍鉴权 ✅ |
| `GET /tensor/reports/970/figures/fig_anchors.png` | 200 PNG | **200 `image/png` 248270B** | 免登录配图线上生效 ✅ |
| `GET /tensor/reports/958/figures/fig1_dispersion.png` | 200 PNG | **200** | 第二例独立复现 ✅ |
| `GET /tensor/reports/970/figures/definitely_absent.png` | 404 | **404** | 鉴权已通过、仅文件缺失 ✅ |
| `GET .../figures/../../../../etc/passwd` | 拒绝 | **401** | 中间件正则拦截 ✅ |
| `GET .../figures/..%2f..%2fmain.py` | 拒绝 | **401** | 编码穿越同样被挡 ✅ |
| `GET /auth/auto-login` | 200（当前开启） | **200 + access_token，`auto:true`** | 逃生门开启中，见 §4 |

关键区分度：**404 ≠ 401**。404 证明请求已穿过鉴权闸门进到路由层，这正是"免登录生效"的直接证据；而 401 说明仍被拦。基线图见 `docs/audit/figures/fig1_auth_guard_decision_matrix.png`（A/B/C 三联：真值表、故障注入、线上探针，全部从 JSON 程序化取数）。

## 3. 局限与适用范围

1. **路由层无法动态测**：本机（agent 容器）缺 `psycopg2`/`cryptography`，`api.tensor_reports` 与 `main` 无法 import。故路由层守卫（文件名 `re.fullmatch` + `FIGURE_DIR` 父目录围栏 + 422/404）改用 **AST 静态钉住 + 语义复算**：从源码提取正则字面量，跑 10 条文件名真值表；再用 `os.path.realpath` 复算围栏语义。局限如实说明：这能挡住"正则被改宽/围栏被删"，但挡不住"守卫在、调用顺序错"这类动态缺陷——需有完整依赖的 CI 环境补一条 `TestClient` 级 e2e。
2. **导入垫片**：测试在真实 `database.config` 不可用时装 `SessionLocal=None` 的空壳（与 `auth_guard` 内 `except: _AuditSessionLocal=None` 的降级路径同构）。`SHIM == "stub"` 会在结果 JSON 里标注，不做静默降级。
3. **线上探针的时间点**：证据为 2026-11-01 采集；服务重部署后需重跑 `mutation_check.py` 复核。
4. **未覆盖**：`/console`、`/factory` 前缀白名单的同类最小面断言；本次只收口配图这条历史故障线，未扩大范围。
5. **观察到的、本次未定性的异常**：报告 967 的图元数据登记了 `suboff_resistance_verification`，线上 `GET .../967/figures/suboff_resistance_verification.png` 返回 **404**（其余报告 200）。本工作树看不到服务的 `FIGURE_DIR`（默认 `uploads/tensor_reports`，相对服务 CWD），无法根因定性 —— 记为 **WITHHELD**，交后续单核查（可能是元数据登记名与实际落盘名不一致，或落盘后被清理）。这正是"报告页图片裂"的用户可见形态。

## 4. 风险记录（不属本单改动范围，如实上报）

`GET /api/v1/auth/auto-login` 当前返回 200 并为匿名访问者签发 `eco_web` 令牌（`"auto": true"`）。这是 owner 2026-11-01「先开免密」的显式决定，且代码注释已声明"公网环境请勿开启"。本单**不改变**该策略，只补了两条守卫断言：`TP_AUTO_LOGIN != "1"` → 404 的逃生闸必须在、账号必须收敛到 `TP_AUTO_LOGIN_USER`（默认 `eco_web`）。建议在对外暴露前将其置 0。

## 5. 交付物

| 文件 | 作用 |
|---|---|
| `backend/tests/test_auth_guard_figures_regression.py` | 回归围栏：18 条配图真值表 ×2（判定层/中间件层）+ 5 条 root_path 故障注入（含负对照）+ 路由层静态守卫 + auto-login 逃生闸 |
| `docs/audit/mutation_check.py` | 变异检验 + 线上探针，产出唯一真值 JSON |
| `docs/audit/artifacts/mutation_check.json` | 全部数字的机器可读证据（含 shim 标注） |
| `docs/audit/gen_figures.py` | 从 JSON 生成报告图，零手抄 |
| `docs/audit/figures/fig1_auth_guard_decision_matrix.png` | 三联证据图 |

**验收判据**：`pytest` 全绿（54 passed / 1 skipped，skip 为 deny 用例无对照意义的显式跳过）+ M1/M2 双变异均被抓 + 线上 8 条探针与期望逐条一致。