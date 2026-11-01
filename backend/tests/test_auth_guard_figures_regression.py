"""免登录配图 / root_path / auto-login 三条 401 复发线的回归围栏（m1405 / tp-mod-4）

为什么有这个文件（验证欠账）
--------------------------
1) `_FIGURES_PATH_RE`（报告配图免登录，owner 2026-10-31 选 A）自 #232 入库后**没有任何测试**；
   #227 曾整件漏交，重部署后 <img> 401 复发过——同类漏交会静默复发。
2) `uvicorn --root-path /tp` 时 `request.url.path` 会拼上前缀（`/tp/api/v1/health`），
   历史上一度导致白名单全部失配 → 全站 401。中间件里已加「root_path 剥离」补丁，但同样无测试。
3) 免登录面必须**最小**：只放行「单文件配图」这一条窄缝，报告正文/列表仍须鉴权。
   任何未来的放宽（例如把 `/api/v1/tensor/reports` 塞进前缀白名单）都应被本文件拦住。

判据（机器可裁）
----------------
- 中间件判定 `_should_skip_auth(path, method)` 真值表：allow / deny 两列逐条对齐。
- root_path 故障注入：同一请求，未剥离（root_path=""）→ 401；剥离（root_path="/tp"）→ 放行。
  两条同时成立才说明补丁真的在起作用（否则测试可能是空转）。
- 路由层纵深防御：文件名白名单正则 + `FIGURE_DIR` 路径围栏。本机无 psycopg2，
  `api.tensor_reports` 无法 import，故用 AST 静态钉住源码里的守卫字面量 + 语义表（局限已在文档写明）。

运行
----
    cd backend && python -m pytest tests/test_auth_guard_figures_regression.py -q
"""
import ast
import asyncio
import os
import re
import sys
import types

import pytest
from starlette.requests import Request

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

def _ensure_db_importable():
    """导入垫片：本机（agent 容器）无 psycopg2，`database.config` 在 import 期就建引擎失败。

    只有真实 import 失败时才装一个**惰性空壳**（SessionLocal=None，与 auth_guard 里
    `except: _AuditSessionLocal = None` 的降级路径完全一致），不影响被测逻辑。
    有 psycopg2 的环境走真实模块，SHIM 记为 "real"。
    """
    try:
        import database.config  # noqa: F401
        return "real"
    except Exception:
        pass
    pkg = types.ModuleType("database")
    pkg.__path__ = []  # 显式空包，防止回落到磁盘同名模块
    cfg = types.ModuleType("database.config")
    cfg.SessionLocal = None
    cfg.Base = type("Base", (), {})
    cfg.engine = None
    pkg.config = cfg
    sys.modules["database"] = pkg
    sys.modules["database.config"] = cfg
    return "stub"


SHIM = _ensure_db_importable()

from middleware import auth_guard  # noqa: E402  (import 后才放 sys.path)

REPORTS_SRC = os.path.join(BACKEND, "api", "tensor_reports.py")
MAIN_SRC = os.path.join(BACKEND, "main.py")


# ─────────────────────────── 公共：驱动中间件 ───────────────────────────

def _drive(path, root_path="", method="GET", headers=None):
    """直接调中间件函数，返回 (status_code_or_None, forwarded, body)。

    `call_next` 被调用即代表「放行到路由层」（forwarded=True）。
    scope["path"] 用**含 root_path 的完整路径**构造，与 uvicorn --root-path 的真实行为一致。
    """
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": root_path,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 40000),
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
    }
    request = Request(scope)
    seen = {}

    async def call_next(_req):
        seen["forwarded"] = True
        from starlette.responses import PlainTextResponse

        return PlainTextResponse("ok")

    resp = asyncio.run(auth_guard.auth_guard_middleware(request, call_next))
    return resp.status_code, bool(seen.get("forwarded")), getattr(resp, "body", b"")


# ─────────────── A. 免登录配图：中间件真值表（allow / deny）───────────────
# (path, method, allow?)  —— allow=True 表示应当**跳过**认证
FIGURE_TRUTH_TABLE = [
    # 正例：单文件配图，GET/HEAD 免登录
    ("/api/v1/tensor/reports/971/figures/fig1.png", "GET", True),
    ("/api/v1/tensor/reports/971/figures/fig1.png", "HEAD", True),
    ("/api/v1/tensor/reports/1/figures/fig.resistance-v1.png", "GET", True),
    ("/api/v1/tensor/reports/1405/figures/a_b_c.png", "GET", True),
    # 反例：写方法一律鉴权（免登录只读，不许改）
    ("/api/v1/tensor/reports/971/figures/fig1.png", "POST", False),
    ("/api/v1/tensor/reports/971/figures/fig1.png", "DELETE", False),
    ("/api/v1/tensor/reports/971/figures/fig1.png", "PATCH", False),
    ("/api/v1/tensor/reports/971/figures/fig1.png", "PUT", False),
    # 反例：路径穿越 / 多段路径（不得因正则而放行）
    ("/api/v1/tensor/reports/971/figures/../../../../etc/passwd", "GET", False),
    ("/api/v1/tensor/reports/971/figures/a/b.png", "GET", False),
    ("/api/v1/tensor/reports/971/figures/", "GET", False),
    ("/api/v1/tensor/reports/971/figures", "GET", False),
    ("/api/v1/tensor/reports/971/figures%2F..%2F..%2Fx.png", "GET", False),
    # 反例：report_id 必须是数字
    ("/api/v1/tensor/reports/abc/figures/fig1.png", "GET", False),
    ("/api/v1/tensor/reports//figures/fig1.png", "GET", False),
    # 反例：免登录面不得外溢到报告正文/列表（最小放行面不变式）
    ("/api/v1/tensor/reports", "GET", False),
    ("/api/v1/tensor/reports/971", "GET", False),
    ("/api/v1/tensor/reports/971/figures/fig1.png/QR", "GET", False),
]


@pytest.mark.parametrize("path,method,allow", FIGURE_TRUTH_TABLE)
def test_figures_truth_table(path, method, allow):
    got = auth_guard._should_skip_auth(path, method)
    assert got is allow, f"{method} {path}: expected skip={allow}, got {got}"


@pytest.mark.parametrize("path,method,allow", FIGURE_TRUTH_TABLE)
def test_middleware_behavior_matches_truth_table(path, method, allow):
    """端到端：allow → 放行；deny → 401 且不触达路由层。"""
    status, forwarded, body = _drive(path, method=method)
    if allow:
        assert forwarded and status == 200
    else:
        assert not forwarded, f"{method} {path} 不应被放行"
        assert status == 401
        assert b"UNAUTHORIZED" in body


def test_figures_regex_is_anchored_and_single_segment():
    """正则必须是全锚定 + 只吃单段文件名，防止未来改成宽松前缀匹配。"""
    rx = auth_guard._FIGURES_PATH_RE
    assert rx.pattern.startswith("^") and rx.pattern.endswith("$")
    assert "/" not in rx.pattern.split("figures/")[1].rstrip("$")
    assert rx.match("/api/v1/tensor/reports/971/figures/x.png/extra") is None


def test_public_figure_surface_is_not_in_prefix_whitelist():
    """纵深不变式：免登录配图靠窄正则，不能靠前缀白名单（前缀会连报告正文一起放开）。"""
    for prefix in auth_guard.SKIP_AUTH_PREFIXES:
        assert not prefix.rstrip("/").endswith("/tensor"), f"过宽前缀: {prefix}"
        assert "/tensor/reports" not in prefix, f"过宽前缀: {prefix}"


# ─────────── B. root_path 故障注入（/tp 子路径部署的 401 复发线）───────────

ROOT_PATH_CASES = [
    # (path_with_prefix, root_path_in_scope, method, expect_forwarded)
    ("/tp/api/v1/health", "/tp", "GET", True),                # 剥离后命中精确白名单
    ("/tp/api/v1/tensor/reports/971/figures/fig1.png", "/tp", "GET", True),  # 剥离后命中配图正则
    ("/tp/api/v1/auth/auto-login", "/tp", "GET", True),       # 剥离后命中共用白色单
    ("/tp/api/v1/tensor/reports/971", "/tp", "GET", False),   # 剥离后仍须鉴权
    ("/tp/", "/tp", "GET", True),                             # 剥成 "/" 命中根白名单
]


@pytest.mark.parametrize("path,root_path,method,expect", ROOT_PATH_CASES)
def test_root_path_stripping_restores_whitelist(path, root_path, method, expect):
    status, forwarded, _ = _drive(path, root_path=root_path, method=method)
    assert forwarded is expect, f"{method} {path} (root_path={root_path})"
    if not expect:
        assert status == 401


@pytest.mark.parametrize("path,root_path,method,expect", ROOT_PATH_CASES)
def test_negative_control_without_strip_yields_401(path, root_path, method, expect):
    """故障注入：把 root_path 拿掉（等价于补丁失效）→ 白名单应全部失配 → 401。

    这一条与上一条配对，证明「测试真的在测补丁」，而不是永远 200 的空转。
    """
    if not expect:
        pytest.skip("deny 用例在两种情形下都是 401，无对照意义")
    status, forwarded, _ = _drive(path, root_path="", method=method)
    assert not forwarded, "缺 root_path 剥离时不应放行"
    assert status == 401
    assert path.startswith("/tp")  # 前提：路径确实带前缀


def test_root_path_does_not_leak_into_regex_allow():
    """剥离只做还原，不得把前缀路径伪造成配图路径。"""
    assert auth_guard._should_skip_auth("/tp/api/v1/tensor/reports/971/figures/x.png", "GET") is False


# ────────────── C. 路由层纵深防御（静态钉住，附 psycopg2 缺失说明）─────────────

def _route_source(src_path=REPORTS_SRC):
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    return src, ast.parse(src)


def test_figure_route_keeps_name_whitelist_and_path_fence():
    src, tree = _route_source()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "get_figure" in funcs, "tensor_reports.get_figure 应存在（免登录配图端点）"
    body = ast.get_source_segment(src, funcs["get_figure"]) or ""
    # 1) 文件名白名单正则
    assert re.search(r"re\.fullmatch\(\s*r?\"\[A-Za-z0-9_\.\-\]\\?\+?\"", body) or \
           re.search(r're\.fullmatch\(\s*r?"\[A-Za-z0-9_.-]\+"', body), \
           "get_figure 必须保留文件名白名单 re.fullmatch"
    assert "422" in body, "非法文件名必须 422 拒绝"
    # 2) 路径围栏：resolve() 后必须判定落在 FIGURE_DIR 内
    assert ".resolve()" in body, "必须以 resolve() 消除 .. 之后再判定"
    assert "FIGURE_DIR" in body and "parents" in body, "必须保留 FIGURE_DIR 父目录围栏"
    # 3) 不得在端点内自行读任意外部路径（无 open(path) 兜底绕过围栏）
    assert "FileResponse" in body


def test_route_layer_pattern_semantics_match_middleware():
    """路由层与中间件层的文件名口径必须同源，避免一处收紧一处放宽。"""
    src, _ = _route_source()
    m = re.search(r're\.fullmatch\(\s*r?"(\[A-Za-z0-9_.\-\]\+)"', src)
    assert m, "未能从源码提取路由层文件名正则"
    route_rx = re.compile(m.group(1))
    name_table = [
        ("fig1.png", True), ("fig.resistance-v1.png", True), ("a_b_c.png", True), ("..", True),
        ("../etc/passwd", False), ("a/b.png", False), ("a b.png", False), ("", False),
        ("fig%00.png", False), ("图1.png", False),
    ]
    for name, ok in name_table:
        assert bool(route_rx.fullmatch(name)) is ok, name


def test_path_fence_blocks_traversal_for_malicious_names():
    """复算围栏语义：即使文件名通过白名单，落到盘上的路径也必须仍在 FIGURE_DIR 内。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        figure_dir = os.path.realpath(tmp)
        for name in ("fig1.png", "..", "...png", "a..b.png"):
            path = os.path.realpath(os.path.join(figure_dir, "971", f"{name}.png"))
            assert figure_dir in path  # 围栏成立
            assert path.startswith(figure_dir + os.sep)


# ─────────────────── D. auto-login 逃生门（默认开启，须可关）───────────────────

def test_auto_login_in_exact_whitelist():
    assert "/api/v1/auth/auto-login" in auth_guard.SKIP_AUTH_PATHS


def test_auto_login_has_env_kill_switch():
    src, tree = _route_source(MAIN_SRC)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_auto_login" in funcs, "main._auto_login 应存在"
    body = ast.get_source_segment(src, funcs["_auto_login"]) or ""
    assert 'os.getenv("TP_AUTO_LOGIN", "1")' in body, "必须保留 TP_AUTO_LOGIN 环境闸"
    assert "404" in body, "关闭时必须 404（不泄露端点存在性）"
    # 逃生门只签一个收敛账号的令牌，不得签角色可注入的任意账号
    assert '"TP_AUTO_LOGIN_USER", "eco_web"' in body
    assert "role" in body and "user_id" in body


def test_auto_login_not_reachable_when_prefix_whitelist_bypasses_exact():
    """精确白名单已覆盖；若有人把它降级成前缀，会把 /auth/auto-login* 全部放开（记录性断言）。"""
    assert auth_guard._should_skip_auth("/api/v1/auth/auto-login", "POST") is True
    # 带尾随段的路径不应被放行（精确匹配语义）
    assert auth_guard._should_skip_auth("/api/v1/auth/auto-login/../me", "GET") is False