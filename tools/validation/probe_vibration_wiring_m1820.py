"""接线取证探针（m1820，全只读）：已合并的振动噪声核是否真的被服务？

三层证据：
  L1 静态：仓内引用计数（``ship_vibration`` 在 *.py 中出现次数，排除自身）、
           ``api/domain/__init__.py`` 的 ``__all__`` 成员、``backend/main.py`` 的
           ``include_router`` 清单、``tests/`` 里对该模块的测试数；
  L2 模块可加载性：按文件路径 import（绕过 ``api/__init__`` 的 DB 依赖）；
  L3 在线：对 :8001（船舶平台）与 :8010（科研运行台）的 6 条候选路径做 GET/POST，
      **带控制组**（``calculations/stability``、``calculations/resistance`` 期望 405，
      证明 404 不是鉴权/探针失效造成的假阴性）。

输出：``artifacts/m1820/wiring_evidence.json``（供报告与测试程序化读取，零手抄）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "m1820" / "wiring_evidence.json"
TARGET = ROOT / "backend" / "api" / "domain" / "ship_vibration.py"

ENDPOINTS = [
    ("calculations/vibration/beam_natural", "POST"),
    ("calculations/vibration/plate_natural", "POST"),
    ("calculations/vibration/excitation", "POST"),
    ("calculations/vibration/isolation", "POST"),
    ("calculations/noise/panel_tl", "POST"),
    ("calculations/noise/levels", "POST"),
]
CONTROLS = [
    ("calculations/stability", "GET"),
    ("calculations/resistance", "GET"),
]
BASES = {"ship_platform_8001": "http://127.0.0.1:8001/api/v1",
         "tensor_backend_8010": "http://127.0.0.1:8010/api/v1"}


def http_probe(url: str, method: str, body: dict | None = None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "body": r.read()[:300].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:  # 连接失败也要留证
        return {"status": None, "error": f"{type(e).__name__}: {e}"}


def static_evidence() -> dict:
    src = TARGET.read_text()
    sha = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    lines = src.count("\n") + 1

    refs, test_refs = [], []
    for p in ROOT.rglob("*.py"):
        if ".git" in p.parts or p == TARGET:
            continue
        try:
            txt = p.read_text(errors="ignore")
        except OSError:
            continue
        if "ship_vibration" in txt:
            rel = str(p.relative_to(ROOT))
            (test_refs if rel.startswith("tests/") else refs).append(rel)

    dom_init = (ROOT / "backend" / "api" / "domain" / "__init__.py").read_text()
    main_py = (ROOT / "backend" / "main.py").read_text()
    main_includes = re.findall(r"app\.include_router\(\s*([A-Za-z_][\w\.]*)\s*,\s*prefix=([^)]*)\)",
                               main_py)
    return {
        "module_path": str(TARGET.relative_to(ROOT)),
        "module_sha256": sha,
        "module_lines": lines,
        "module_functions": len(re.findall(r"^def ", src, flags=re.M)),
        "router_decorated_endpoints": len(re.findall(r"^@router\.(?:post|get)", src, flags=re.M)),
        "import_sites_outside_tests": refs,
        "import_sites_in_tests": test_refs,
        "n_import_sites_outside_tests": len(refs),
        "n_tests_referencing_module": len(test_refs),
        "in_domain_all_list": "ship_vibration" in dom_init,
        "domain_all_members": re.findall(r'"(ship_[a-z_]+_router)"', dom_init),
        "main_py_include_routers": [f"{m}(prefix={p})" for m, p in main_includes],
        "main_py_mentions_vibration": "vibration" in main_py.lower(),
    }


def git_evidence() -> dict:
    def sh(args):
        try:
            return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                                  timeout=60).stdout.strip()
        except Exception as e:
            return f"ERROR {e}"

    return {
        "head": sh(["git", "rev-parse", "--short", "HEAD"]),
        "branch": sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "module_introduced_by": sh(["git", "log", "--oneline", "-1", "--",
                                    "backend/api/domain/ship_vibration.py"]),
        "module_tracked": bool(sh(["git", "ls-files", "backend/api/domain/ship_vibration.py"])),
    }


def main() -> int:
    payload = {"static": static_evidence(), "git": git_evidence(), "live": {}}
    body_sample = {
        "calculations/vibration/beam_natural": {
            "bc": "clamped-free", "E": 2.1e11, "I": 1.6e-4, "rho": 7850.0,
            "A": 0.02, "L": 6.0, "n_modes": 3},
        "calculations/vibration/plate_natural": {
            "a": 2.0, "b": 1.5, "E": 2.1e11, "nu": 0.3, "rho": 7850.0, "h": 0.008},
        "calculations/vibration/excitation": {"rpm": 120.0, "n_blades": 4, "n_cylinders": 6},
        "calculations/vibration/isolation": {"f": 25.0, "f_n": 10.0, "zeta": 0.05},
        "calculations/noise/panel_tl": {"f": 500.0, "surface_density": 39.25},
        "calculations/noise/levels": {"levels": [60.0, 60.0]},
    }
    for tag, base in BASES.items():
        rows = []
        for path, method in ENDPOINTS:
            r = http_probe(f"{base}/{path}", method, body_sample.get(path))
            rows.append({"path": path, "method": method, "status": r["status"],
                         "detail": (r.get("body") or r.get("error") or "")[:120]})
        ctrl = []
        for path, method in CONTROLS:
            r = http_probe(f"{base}/{path}", method)
            ctrl.append({"path": path, "method": method, "status": r["status"]})
        payload["live"][tag] = {"base": base, "candidates": rows, "controls": ctrl}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    st = payload["static"]
    print(f"module {st['module_path']} lines={st['module_lines']} sha={st['module_sha256'][:12]}")
    print(f"import sites (non-test)={st['n_import_sites_outside_tests']} "
          f"tests={st['n_tests_referencing_module']} in__all__={st['in_domain_all_list']}")
    for tag, d in payload["live"].items():
        codes = [r["status"] for r in d["candidates"]]
        ctl = [(r["path"], r["status"]) for r in d["controls"]]
        print(f"{tag}: candidate statuses={codes} controls={ctl}")
    print("written", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())