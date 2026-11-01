"""变异检验 + 证据采集：证明 auth_guard 的两条守卫是承重的（m1405 / tp-mod-4）

做法（不改工作树里的业务代码，只对源码做**文本手术**后在内存模块里跑）：
  M1  把 `_FIGURES_PATH_RE` 换成永不匹配 → 免登录配图真值表里的 allow 行应全部翻成 401；
  M2  删掉 root_path 剥离块（还原 /tp 子路径部署时的历史 401）→ `/tp/...` 的 allow 行应翻成 401。
基线 + M1 + M2 三组结果都写进 artifacts/，供报告图表程序化取数（零手抄）。

另外抓一次**实时平台**外部证据（127.0.0.1:8010）：免登录配图是否真的 200、报告正文是否仍 401、
穿越尝试是否被挡。注意：本脚本只读 GET，不改任何数据。

运行： python3 docs/audit/mutation_check.py
输出： docs/audit/artifacts/mutation_check.json
"""
import json
import os
import re
import sys
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "tests"))

import test_auth_guard_figures_regression as T  # noqa: E402

AUTH_SRC = os.path.join(BACKEND, "middleware", "auth_guard.py")
STRIP_BLOCK = (
    '    path = request.url.path\n'
    '    _root_path = (request.scope.get("root_path") or "").rstrip("/")\n'
    '    if _root_path and path.startswith(_root_path):\n'
    '        path = path[len(_root_path):] or "/"\n'
)


def build_mutated(name, source):
    """把改动后的源码 exec 成独立模块（与真实模块互不干扰）。"""
    mod = types.ModuleType(name)
    mod.__package__ = "middleware"
    mod.__file__ = "<mutated>"
    exec(compile(source, "<mutated:%s>" % name, "exec"), mod.__dict__)
    return mod


def evaluate(mod):
    """用给定模块跑两张真值表，返回逐条判定。"""
    fig_rows = []
    for path, method, allow in T.FIGURE_TRUTH_TABLE:
        got = bool(mod._should_skip_auth(path, method))
        fig_rows.append({"path": path, "method": method, "expect_allow": allow, "got_allow": got,
                         "ok": got is allow})
    rp_rows = []
    for path, root_path, method, expect in T.ROOT_PATH_CASES:
        # _drive 固定用真实模块，这里用被测模块的中间件函数
        status, forwarded, _ = drive_with(mod, path, root_path, method)
        rp_rows.append({"path": path, "root_path": root_path, "method": method,
                        "expect_forward": expect, "got_forward": forwarded,
                        "status": status, "ok": forwarded is expect})
    return {"figures": fig_rows, "root_path": rp_rows}


def drive_with(mod, path, root_path, method):
    import asyncio
    from starlette.requests import Request

    scope = {"type": "http", "method": method, "path": path, "root_path": root_path,
             "query_string": b"", "scheme": "http", "server": ("testserver", 80),
             "client": ("127.0.0.1", 40000), "headers": []}
    seen = {}

    async def call_next(_req):
        seen["forwarded"] = True
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("ok")

    resp = asyncio.run(mod.auth_guard_middleware(Request(scope), call_next))
    return resp.status_code, bool(seen.get("forwarded")), None


def live_probe():
    """实时平台外部证据（只读 GET）。返回逐条 http 状态。"""
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    targets = [
        ("report_list_needs_auth", "tensor/reports"),
        ("report_body_needs_auth", "tensor/reports/970"),
        ("public_figure_gif_free_970", "tensor/reports/970/figures/fig_anchors.png"),
        ("public_figure_gif_free_958", "tensor/reports/958/figures/fig1_dispersion.png"),
        ("figure_missing_name", "tensor/reports/970/figures/definitely_absent.png"),
        ("figure_traversal_dotdot", "tensor/reports/970/figures/../../../../etc/passwd"),
        ("figure_traversal_encoded", "tensor/reports/970/figures/..%2f..%2fmain.py"),
        ("auto_login_escape_hatch", "auth/auto-login"),
    ]
    out = []
    for name, rel in targets:
        url = "%s/%s" % (base, rel)
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as r:
                body = r.read(4096)
                out.append({"case": name, "url": rel, "status": r.status,
                            "ctype": r.headers.get("content-type", ""),
                            "bytes": len(body)})
        except urllib.error.HTTPError as e:
            out.append({"case": name, "url": rel, "status": e.code,
                        "ctype": e.headers.get("content-type", ""), "bytes": 0})
        except Exception as exc:  # 平台不可达 → 如实记 WITHHELD
            out.append({"case": name, "url": rel, "status": None, "error": str(exc)[:120]})
    return out


def main():
    real_src = open(AUTH_SRC, encoding="utf-8").read()

    # 基线
    sys.path.insert(0, BACKEND)
    from middleware import auth_guard as base_mod
    base = evaluate(base_mod)

    # M1：免登录配图正则失效
    m1_src, n1 = re.subn(r'_FIGURES_PATH_RE = re\.compile\(r"([^"]+)"\)',
                         '_FIGURES_PATH_RE = re.compile(r"(?!)")', real_src)
    assert n1 == 1, "M1 手术点未命中"
    m1 = evaluate(build_mutated("auth_guard_m1", m1_src))

    # M2：移除 root_path 剥离块
    assert STRIP_BLOCK in real_src, "M2 手术点未命中（剥离块文本已变，请同步本脚本）"
    m2_src = real_src.replace(STRIP_BLOCK, '    path = request.url.path\n', 1)
    m2 = evaluate(build_mutated("auth_guard_m2", m2_src))

    def summary(res):
        fig_ok = sum(1 for r in res["figures"] if r["ok"])
        rp_ok = sum(1 for r in res["root_path"] if r["ok"])
        return {"figures_pass": fig_ok, "figures_total": len(res["figures"]),
                "root_path_pass": rp_ok, "root_path_total": len(res["root_path"])}

    payload = {
        "shim_mode": T.SHIM,
        "baseline": {"summary": summary(base), "detail": base},
        "mutation_M1_figures_regex_disabled": {"summary": summary(m1), "detail": m1},
        "mutation_M2_root_path_strip_removed": {"summary": summary(m2), "detail": m2},
        "caught": {
            "M1": summary(m1)["figures_pass"] < summary(base)["figures_pass"],
            "M2": summary(m2)["root_path_pass"] < summary(base)["root_path_pass"],
        },
        "live_platform": live_probe(),
    }
    out_dir = os.path.join(HERE, "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, "mutation_check.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"out": dest, "shim": T.SHIM,
                      "baseline": payload["baseline"]["summary"],
                      "M1": payload["mutation_M1_figures_regex_disabled"]["summary"],
                      "M2": payload["mutation_M2_root_path_strip_removed"]["summary"],
                      "caught": payload["caught"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()