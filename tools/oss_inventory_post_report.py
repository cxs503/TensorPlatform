import json, os, urllib.request, urllib.error, sys

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]

here = "/root/tp-agent/work/m8298"
md = open(os.path.join(here, "docs/oss_inventory/2026-11-09_TCLB_deep_dive.md"), encoding="utf-8").read()
mb = open(os.path.join(here, "docs/oss_inventory/2026-11-09_TCLB_machine_block.txt"), encoding="utf-8").read()
markdown = md + "\n\n---\n\n## 机器块（[OSS-INV]/[OSS-SOL]，供守护线解析）\n\n```\n" + mb + "\n```\n"


def post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read().decode()


def try_login():
    body = json.dumps({"username": USER, "password": PASS}).encode()
    for path in ["/api/v1/auth/login", "/auth/login", "/api/v1/token", "/api/v1/auth/token"]:
        try:
            st, txt = post(BASE + path, body, {"Content-Type": "application/json"})
            d = json.loads(txt)
            tok = d.get("access_token") or d.get("token") or (d.get("data") or {}).get("access_token")
            if tok:
                print("login ok via", path)
                return tok
        except Exception as e:
            print("login fail", path, type(e).__name__, getattr(e, "code", ""))
    # form-encoded fallback
    import urllib.parse
    body = urllib.parse.urlencode({"username": USER, "password": PASS}).encode()
    for path in ["/api/v1/auth/login", "/auth/login"]:
        try:
            st, txt = post(BASE + path, body, {"Content-Type": "application/x-www-form-urlencoded"})
            d = json.loads(txt)
            tok = d.get("access_token") or d.get("token") or (d.get("data") or {}).get("access_token")
            if tok:
                print("login ok (form) via", path)
                return tok
        except Exception as e:
            print("login form fail", path, type(e).__name__)
    return None


tok = try_login()
if not tok:
    print("NO TOKEN -> cannot post report")
    sys.exit(3)

payload = {
    "title": "【盘点】TCLB",
    "summary": ("CFD-GO/TCLB（GPL-3.0）代码级盘点：R/rtemplate+内嵌 sympy/lbmpy 声明式模板生成 CUDA/CPU LBM 内核；"
                "多相侧伪势 EDM+Lycett-Luo、双组元 MCMP、保守 Allen-Cahn 相场+压力演化（高密度比）齐备，"
                "无 free-energy/free-surface/IBM/KBC；共 11 条 [OSS-SOL] 映射（含 GAP-LBM-MCMP-POISEUILLE）。"
                "本单为静态读码，未运行算例，误差判定 NOT_APPLICABLE。"),
    "scenario_key": "oss-inventory",
    "mission_id": 8298,
    "tags": ["oss-inventory", "LBM", "TCLB", "multiphase", "phase-field", "GPU", "codegen"],
    "markdown": markdown,
    "figures": [],
}

for path in ["/api/v1/tensor/reports", "/tensor/reports", "/api/v1/tensor_reports"]:
    try:
        st, txt = post(BASE + path, json.dumps(payload).encode(),
                       {"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        print("REPORT POST", path, st, txt[:300])
        if st < 400:
            break
    except urllib.error.HTTPError as e:
        print("post fail", path, e.code, e.read().decode()[:300])
    except Exception as e:
        print("post err", path, type(e).__name__, str(e)[:200])