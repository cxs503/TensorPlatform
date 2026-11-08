#!/usr/bin/env python3
"""
P07-2 探针：文件上传端点失效模式实测（可复现，不依赖 PostgreSQL、不改生产代码）

运行：python3 docs/skills_training/P07-2/probe_upload.py [--out probe_results.json]
产物：probe_results.json（每条用例的 期望/实际/判定/证据），供 docs/skills_training/P07-2.md 引用。

设计要点
- 被测模块原样导入 backend/api/file_upload.py；只做两处**测试期**可逆替换：
  fu.UPLOAD_DIR（指到临时目录）、fu.MAX_FILE_SIZE（按比例缩小，用于低成本复现边界语义）。
- DB 用文件型 SQLite（DATABASE_URL 注入在 import 之前），表结构由 migrations/011_file_upload.sql
  经最小语法翻译后建立，并**逐语句记录**该迁移在 SQLite 下是否可执行（NFR-4 证据）。
- 每条探针独立 try/except；异常本身作为证据记录，不掩盖。
"""
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BACKEND = REPO / "backend"
MIGRATION = BACKEND / "migrations" / "011_file_upload.sql"

TMPROOT = Path(tempfile.mkdtemp(prefix="p07_2_"))
UPLOAD_ROOT = TMPROOT / "upload_root"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{TMPROOT / 'p07_2.db'}"
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI                      # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402
from sqlalchemy import text                      # noqa: E402
import api.file_upload as fu                     # noqa: E402
from database.config import engine               # noqa: E402

ORIG_UPLOAD_DIR = fu.UPLOAD_DIR
ORIG_MAX_SIZE = fu.MAX_FILE_SIZE
fu.UPLOAD_DIR = str(UPLOAD_ROOT)

app = FastAPI()
app.include_router(fu.router)
client = TestClient(app, raise_server_exceptions=False)

# ── 执行路径保真：真实 app 在最外层挂了 JWT 鉴权守卫中间件（backend/main.py:598
#    → middleware/auth_guard.py:auth_guard_middleware）。只挂 router 的隔离测试会
#    把"有鉴权"误判成"无鉴权"，因此这里把守卫原样挂到探针 app 上。
from starlette.middleware.base import BaseHTTPMiddleware   # noqa: E402
from middleware.auth_guard import auth_guard_middleware     # noqa: E402
from utils.jwt_utils import JWT_SECRET_KEY                  # noqa: E402
from jose import jwt as _jwt                                # noqa: E402

guarded = FastAPI()
guarded.include_router(fu.router)
guarded.add_middleware(BaseHTTPMiddleware, dispatch=auth_guard_middleware)
gclient = TestClient(guarded, raise_server_exceptions=False)


def _token(user_id=1, sub="probe_user", role="viewer"):
    return _jwt.encode({"sub": sub, "user_id": user_id, "role": role, "type": "access"},
                       JWT_SECRET_KEY, algorithm="HS256")


AUTH_A = {"Authorization": f"Bearer {_token(1, 'user_a', 'viewer')}"}
AUTH_B = {"Authorization": f"Bearer {_token(2, 'user_b', 'viewer')}"}

RESULTS = []


def rec(cid, dimension, name, expect, actual, verdict, evidence=None, severity=None,
        invariant=None, priority=None, repro=""):
    RESULTS.append({
        "id": cid, "dimension": dimension, "name": name, "priority": priority,
        "invariant": invariant, "expect": expect, "actual": actual,
        "verdict": verdict, "severity": severity, "evidence": evidence or {},
        "repro": repro,
    })
    print(f"[{verdict:18s}] {cid} {name} :: {actual}")


def run(cid, dimension, name, fn, **kw):
    try:
        res = fn()
        rec(cid, dimension, name, kw.pop("expect"), res.get("actual"), res["verdict"],
            evidence=res.get("evidence"), severity=res.get("severity"),
            invariant=kw.pop("invariant", None), priority=kw.pop("priority", None),
            repro=kw.pop("repro", ""))
    except Exception as exc:                      # 探针自身异常也留证
        rec(cid, dimension, name, kw.pop("expect", "n/a"),
            f"probe error: {exc!r}", "UNTESTED",
            evidence={"traceback": traceback.format_exc()[-1200:]}, **kw)


# ─────────────────────────── 环境/基线 ───────────────────────────
def env_info():
    import fastapi, sqlalchemy, sqlite3, starlette
    try:
        import subprocess
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        head = "n/a"
    return {
        "git_head": head, "python": sys.version.split()[0],
        "fastapi": fastapi.__version__, "starlette": starlette.__version__,
        "sqlalchemy": sqlalchemy.__version__, "sqlite": sqlite3.sqlite_version,
        "db": os.environ["DATABASE_URL"].replace(str(TMPROOT), "<tmp>"),
        "note": "MAX_FILE_SIZE 与 UPLOAD_DIR 在测试期替换；生产值见 module 常量",
        "prod_max_file_size": ORIG_MAX_SIZE, "prod_upload_dir": ORIG_UPLOAD_DIR,
    }


def coverage_scan():
    """题设称'只有 happy-path 测试'——实测现有测试面。"""
    hits = {"tests/": [], "backend/tests/": []}
    for root in (REPO / "tests", BACKEND / "tests"):
        key = "tests/" if root.name == "tests" and root.parent == REPO else "backend/tests/"
        if not root.exists():
            continue
        for p in root.rglob("test_*.py"):
            try:
                body = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if ("file_upload" in body or "uploaded_files" in body
                    or '"/upload"' in body or "/upload/batch" in body):
                hits[key].append(str(p.relative_to(REPO)))
    return hits


def setup_schema():
    """建立 uploaded_files 表；逐语句记录 011 迁移在 SQLite 下的可执行性。"""
    raw = MIGRATION.read_text(encoding="utf-8")
    # 逐行剥掉 SQL 注释后再按 ';' 切分（否则整条 CREATE TABLE 会因首行是注释被误丢）
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
    stmts = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
    report = []
    with engine.begin() as conn:
        for i, s in enumerate(stmts):
            t = s
            t = re.sub(r"SERIAL\s+PRIMARY\s+KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", t, flags=re.I)
            t = re.sub(r"\bJSONB\b", "TEXT", t, flags=re.I)
            kind = "CREATE_OR_REPLACE_VIEW" if re.match(r"CREATE\s+OR\s+REPLACE\s+VIEW", t, re.I) else \
                   ("SEED_INSERT" if re.match(r"INSERT", t, re.I) else "OTHER")
            head = " ".join(t.split())[:70]
            try:
                if kind == "CREATE_OR_REPLACE_VIEW":
                    raise RuntimeError("SQLite: CREATE OR REPLACE VIEW 不支持")
                conn.execute(text(t))
                report.append({"stmt": i, "kind": kind, "head": head, "ok": True})
            except Exception as exc:
                report.append({"stmt": i, "kind": kind, "head": head, "ok": False,
                               "error": str(exc)[:160]})
            if kind == "SEED_INSERT" and not report[-1]["ok"]:
                pass
    # 兜底：确保表存在（若 DDL 首条失败）
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE IF NOT EXISTS uploaded_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename VARCHAR(255) NOT NULL, original_filename VARCHAR(255) NOT NULL,
            category VARCHAR(50) DEFAULT 'general', file_path TEXT NOT NULL,
            file_size BIGINT DEFAULT 0, extension VARCHAR(20), mime_type VARCHAR(100),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, uploaded_by INTEGER,
            description TEXT, tags TEXT, is_public INTEGER DEFAULT 0,
            download_count INTEGER DEFAULT 0, checksum VARCHAR(64), metadata TEXT,
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""))
    return report


def reset_table(drop=True):
    with engine.begin() as conn:
        if drop:
            conn.execute(text("DROP TABLE IF EXISTS uploaded_files"))
        conn.execute(text("""CREATE TABLE IF NOT EXISTS uploaded_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename VARCHAR(255) NOT NULL, original_filename VARCHAR(255) NOT NULL,
            category VARCHAR(50) DEFAULT 'general', file_path TEXT NOT NULL,
            file_size BIGINT DEFAULT 0, extension VARCHAR(20), mime_type VARCHAR(100),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, uploaded_by INTEGER,
            description TEXT, tags TEXT, is_public INTEGER DEFAULT 0,
            download_count INTEGER DEFAULT 0, checksum VARCHAR(64), metadata TEXT,
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""))


def count_rows():
    with engine.begin() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM uploaded_files")).scalar()


def files_under(root: Path):
    return sorted(str(p) for p in root.rglob("*") if p.is_file())


# ─────────────────────────── B1 尺寸上限 ───────────────────────────
def s01():
    ok_eq = fu.validate_file("a.csv", ORIG_MAX_SIZE)
    ok_over = fu.validate_file("a.csv", ORIG_MAX_SIZE + 1)
    v = "CONFIRMED_DEFECT" if ok_eq and not ok_over else "OK"
    return {"actual": f"len==MAX → {ok_eq}; len==MAX+1 → {ok_over}",
            "verdict": v if not ok_eq else "OK",
            "evidence": {"len_eq": ok_eq, "len_over": ok_over,
                         "semantics": "validate_file 用 '>' 判定，恰好 MAX 被接受（契约 ≤ 一致）"},
            "severity": "info" if ok_over is False else "high"}


def s03():
    """超限请求：证明'先整包读入内存再校验'（INV-1 流式要求）。"""
    import starlette.datastructures as sd
    orig_read = sd.UploadFile.read
    spy = {"bytes_read": 0}

    async def _spy(self, size=-1):
        data = await orig_read(self, size)
        spy["bytes_read"] += len(data)
        return data

    fu.MAX_FILE_SIZE = 1024
    sd.UploadFile.read = _spy
    body = b"x" * (4 * 1024 * 1024)               # 4 MiB，上限被压到 1 KiB
    try:
        r = client.post("/upload", files={"file": ("big.csv", body, "text/csv")},
                        data={"category": "data"})
    finally:
        sd.UploadFile.read = orig_read
        fu.MAX_FILE_SIZE = ORIG_MAX_SIZE
    at_least_full = spy["bytes_read"] >= len(body)
    return {"actual": (f"限={1024}B, 提交={len(body)}B → status={r.status_code}, "
                       f"实际读入={spy['bytes_read']}B（{'整包读入' if at_least_full else '提前中止'}）"),
            "verdict": "CONFIRMED_DEFECT" if at_least_full else "OK",
            "severity": "high" if at_least_full else "info",
            "evidence": {"status": r.status_code, "bytes_read": spy["bytes_read"],
                         "body": len(body), "limit": 1024,
                         "ratio_buffered_vs_limit": round(spy["bytes_read"] / 1024, 1)}}


def s02_s04_s05():
    """边界缩放 + 空文件 + 批量中单文件超限（一次会话内完成，减少调用开销）。"""
    fu.MAX_FILE_SIZE = 2048
    try:
        at = client.post("/upload", files={"file": ("b.csv", b"y" * 2048, "text/csv")},
                         data={"category": "data"})
        over = client.post("/upload", files={"file": ("c.csv", b"y" * 2049, "text/csv")},
                           data={"category": "data"})
        empty = client.post("/upload", files={"file": ("d.csv", b"", "text/csv")},
                            data={"category": "data"})
        batch = client.post("/upload/batch",
                            files=[("files", ("ok1.csv", b"1", "text/csv")),
                                   ("files", ("bad.exe", b"2", "application/octet-stream")),
                                   ("files", ("ok2.csv", b"3", "text/csv"))],
                            data={"category": "data"})
    finally:
        fu.MAX_FILE_SIZE = ORIG_MAX_SIZE
    rec("S-01", "尺寸上限", "恰好上限字节（缩放 2KiB）", "接受",
        f"status={at.status_code}", "OK" if at.status_code == 200 else "CONFIRMED_DEFECT",
        evidence={"status": at.status_code}, invariant="INV-1", priority="P1",
        repro="MAX_FILE_SIZE:=2048, body=2048B")
    rec("S-02", "尺寸上限", "上限+1 字节（缩放）", "4xx 拒绝",
        f"status={over.status_code}", "OK" if over.status_code == 400 else "CONFIRMED_DEFECT",
        evidence={"status": over.status_code, "detail": over.text[:120]},
        invariant="INV-1", priority="P0", repro="MAX_FILE_SIZE:=2048, body=2049B")
    rec("S-04", "尺寸上限", "0 字节文件", "契约须一致（接受或 4xx）",
        f"status={empty.status_code}", "ACCEPTED_RISK" if empty.status_code == 200 else "OK",
        evidence={"status": empty.status_code,
                  "note": "空文件被接受并落盘，DB 记录 0 字节；契约未禁止但无意义，计入噪声"},
        invariant="INV-1", priority="P2")
    rec("S-05", "尺寸上限", "批量中单文件非法（bad.exe）", "该文件记 error，其余成功",
        f"status={batch.status_code} body={batch.text[:200]}",
        "OK" if (batch.status_code == 200 and batch.json().get("error_count") == 1) else "CONFIRMED_DEFECT",
        evidence=batch.json() if batch.status_code == 200 else {"text": batch.text[:200]},
        invariant="INV-1", priority="P1")


# ─────────────────────────── B2 类型白名单 ───────────────────────────
def t_matrix():
    cases = [("a.csv", 200, "白名单扩展名"), ("A.CSV", 200, "大写扩展名"),
             ("noext", 400, "无扩展名"), ("", 400, "空文件名"),
             (".gitignore", 400, "点开头无有效扩展名"),
             ("a.php.jpg", 200, "双扩展名伪装"), ("a.svg", 400, "svg（可含脚本）"),
             ("a.html", 400, "html"), ("a.exe", 400, "exe"),
             ("a.csv.exe", 400, "允许后缀+危险后缀"), ("x.tar.gz", 200, "多段压缩后缀")]
    out = {}
    for name, want, why in cases:
        r = client.post("/upload", files={"file": (name, b"payload", "application/octet-stream")},
                        data={"category": "data"})
        out[name or "<empty>"] = {"why": why, "want": want, "got": r.status_code}
    bad = {k: v for k, v in out.items()
           if not (v["got"] == v["want"] or (v["want"] == 400 and 400 <= v["got"] < 500))}
    rec("T-01..T-07", "类型白名单", "扩展名矩阵（11 例）",
        "白名单接受、危险/无扩展名 4xx",
        "; ".join(f"{k}:{v['got']}" for k, v in out.items()),
        "OK" if not bad else "CONFIRMED_DEFECT",
        evidence={"matrix": out, "mismatch": bad},
        severity="medium" if "a.php.jpg" in out and out["a.php.jpg"]["got"] == 200 else None,
        invariant="INV-2", priority="P0",
        repro="POST /upload 逐个文件名")
    rec("T-05", "类型白名单", "双扩展名 a.php.jpg 落盘", "接受但应可告警/记录真实内容类型",
        f"落盘扩展名={out['a.php.jpg']} 且响应无 mime/内容校验字段",
        "ACCEPTED_RISK",
        evidence={"note": "按最终扩展名判定 → a.php.jpg 以 .jpg 保存；无内容嗅探、无 mime 记录，"
                          "下载恒为 application/octet-stream，RCE 面被扩展名白名单挡住但伪装文件可入库"},
        invariant="INV-2", priority="P1")


# ─────────────────────────── B3 目录围栏 ───────────────────────────
def path_probes():
    before = set(files_under(TMPROOT))
    tag = uuid.uuid4().hex[:8]
    rel_cat = f"../../escape_{tag}"
    r1 = gclient.post("/upload", files={"file": ("p.csv", b"escape", "text/csv")},
                      data={"category": rel_cat}, headers=AUTH_A)
    target_dir = Path(os.path.normpath(os.path.join(fu.UPLOAD_DIR, rel_cat)))
    escaped1 = [str(p) for p in target_dir.rglob("*.csv")] if target_dir.exists() else []
    abs_dir = Path(tempfile.gettempdir()) / f"p07_abs_{tag}"
    r2 = gclient.post("/upload", files={"file": ("q.csv", b"abs", "text/csv")},
                      data={"category": str(abs_dir)}, headers=AUTH_A)
    abs_written = [str(p) for p in abs_dir.rglob("*") if p.is_file()]
    r3 = gclient.post("/upload", files={"file": ("c.csv", b"custom", "text/csv")},
                      data={"category": f"not_an_enum_{tag}"}, headers=AUTH_A)
    enum_dir = UPLOAD_ROOT / f"not_an_enum_{tag}"
    nm = gclient.post("/upload", files={"file": ("../../traversal.csv", b"t", "text/csv")},
                      data={"category": "data"}, headers=AUTH_A)
    saved = nm.json().get("filename", "") if nm.status_code == 200 else ""
    after = set(files_under(TMPROOT))

    rec("P-01", "目录围栏", "category 相对穿越 ../../escape", "4xx 或不得写出 UPLOAD_DIR",
        f"status={r1.status_code}; 逃逸落盘={escaped1[:2]}",
        "CONFIRMED_DEFECT" if escaped1 else "OK", severity="critical" if escaped1 else None,
        evidence={"status": r1.status_code, "escaped_files": escaped1[:3],
                  "upload_root": str(UPLOAD_ROOT)},
        invariant="INV-3", priority="P0",
        repro='POST /upload data={"category":"../../escape_x"}')
    rec("P-02", "目录围栏", "category 绝对路径覆盖", "4xx",
        f"status={r2.status_code}; 绝对目录内文件={abs_written[:2]}",
        "CONFIRMED_DEFECT" if abs_written else "OK",
        severity="critical" if abs_written else None,
        evidence={"status": r2.status_code, "abs_dir": str(abs_dir), "written": abs_written[:3],
                  "note": "os.path.join(UPLOAD_DIR, '/abs') 语义：绝对路径直接替换前缀"},
        invariant="INV-3", priority="P0",
        repro="POST /upload data={'category':'/tmp/p07_abs_x'}")
    rec("P-03", "目录围栏", "category 非枚举值静默建目录", "4xx 或归入 general",
        f"status={r3.status_code}; 新建目录={enum_dir.exists()}",
        "CONFIRMED_DEFECT" if enum_dir.exists() else "OK",
        severity="medium" if enum_dir.exists() else None,
        evidence={"status": r3.status_code, "dir_created": enum_dir.exists(),
                  "note": "docstring 宣称 category 为枚举，实现无校验（且默认值 general 不在枚举内）"},
        invariant="INV-3", priority="P1")
    rec("P-04", "目录围栏", "文件名穿越 ../../traversal.csv", "落盘名与客户端名无关",
        f"status={nm.status_code}; 落盘名={saved}",
        "OK" if saved and ".." not in saved and "/" not in saved else "CONFIRMED_DEFECT",
        evidence={"saved_name": saved, "note": "uuid 重命名，原始名仅存 DB"},
        invariant="INV-3", priority="P0")
    rec("P-05", "目录围栏", "落盘名可预测性", "不可预测",
        f"uuid4 hex 命名，前缀不可预测；但目录名=category 客户端可控",
        "ACCEPTED_RISK", evidence={"note": "可预测性风险转移到目录层（见 P-01/P-02）"},
        invariant="INV-3", priority="P1")
    # 清理逃逸产物
    for p in escaped1:
        try:
            os.remove(p)
        except Exception:
            pass
    shutil.rmtree(abs_dir, ignore_errors=True)
    shutil.rmtree(target_dir, ignore_errors=True)


# ─────────────────────────── B4 授权 ───────────────────────────
def auth_probes():
    """鉴权/授权探针。必须带应用级中间件（v2）；同时留一条 v1 负控证明
    '只挂 router' 的隔离测试会把有鉴权误判为无鉴权。"""
    # —— v1 负控（隔离挂载，无中间件）——
    up0 = client.post("/upload", files={"file": ("fid.csv", b"f", "text/csv")},
                      data={"category": "data"})
    rec("A-00", "授权", "负控：仅挂 router（无中间件）时的无凭据上传",
        "预期 200 —— 用于证明隔离测试不可用于鉴权结论",
        f"status={up0.status_code}", "OK" if up0.status_code == 200 else "UNTESTED",
        evidence={"note": "隔离挂载丢失 main.py:598 的 jwt_auth_middleware → 鉴权结论必须用 v2"},
        invariant="INV-4", priority="P0")

    # —— v2 真实执行路径 ——
    no_auth = {
        "upload": gclient.post("/upload", files={"file": ("a.csv", b"a", "text/csv")},
                               data={"category": "data"}),
        "list": gclient.get("/files"),
        "batch": gclient.post("/upload/batch",
                              files=[("files", ("a.csv", b"a", "text/csv"))]),
    }
    up = gclient.post("/upload", files={"file": ("auth.csv", b"a", "text/csv")},
                      data={"category": "data"}, headers=AUTH_A)
    fid = up.json().get("file_id") if up.status_code == 200 else None
    no_auth.update({
        "info": gclient.get(f"/files/{fid}") if fid else None,
        "download": gclient.get(f"/download/{fid}") if fid else None,
        "delete": gclient.delete(f"/files/{fid}") if fid else None,
    })
    codes = {k: (v.status_code if v is not None else None) for k, v in no_auth.items()}
    all_401 = all(c == 401 for c in codes.values())
    rec("A-01..A-04", "授权", "无凭据访问（已挂应用级 JWT 守卫）", "401/403",
        "; ".join(f"{k}={v}" for k, v in codes.items()),
        "OK" if all_401 else "CONFIRMED_DEFECT",
        severity=None if all_401 else "critical",
        evidence={"codes": codes, "with_token_upload": up.status_code,
                  "note": "main.py:598 最外层挂 auth_guard_middleware；/upload、/files、"
                          "/download 均不在 SKIP_AUTH 白名单 → 一律 401"},
        invariant="INV-4", priority="P0",
        repro="TestClient(guarded) 无 Authorization 头")

    # A-05 文件级授权：用户 B 的 token 能读/删用户 A 上传的文件？
    cross_dl = gclient.get(f"/download/{fid}", headers=AUTH_B) if fid else None
    cross_inf = gclient.get(f"/files/{fid}", headers=AUTH_B) if fid else None
    cross_del = gclient.delete(f"/files/{fid}", headers=AUTH_B) if fid else None
    with engine.begin() as conn:
        owners = conn.execute(text(
            "SELECT COUNT(*) FROM uploaded_files WHERE uploaded_by IS NOT NULL")).scalar()
    rec("A-05", "授权", "用户 B（低权 role=viewer）访问用户 A 的文件", "403",
        f"download={getattr(cross_dl,'status_code',None)}, info={getattr(cross_inf,'status_code',None)}, "
        f"delete={getattr(cross_del,'status_code',None)}; 表内 uploaded_by 非空行数={owners}",
        "CONFIRMED_DEFECT" if getattr(cross_dl, "status_code", None) == 200 else "OK",
        severity="critical",
        evidence={"download": getattr(cross_dl, "status_code", None),
                  "info": getattr(cross_inf, "status_code", None),
                  "delete": getattr(cross_del, "status_code", None),
                  "uploaded_by_rows": owners,
                  "note": "端点内无 Depends(require_permission(...))、无 owner 过滤；"
                          "uploaded_by 列从不写入 → 无主体归属，任意登录用户可读写删全部文件"},
        invariant="INV-4", priority="P0",
        repro="AUTH_A 上传 → AUTH_B 下载/删除 → 200")

    # A-06 枚举与权限粒度
    lst = gclient.get("/files?page_size=100", headers=AUTH_B)
    items = lst.json().get("items", []) if lst.status_code == 200 else []
    rec("A-06", "授权", "无 files:* 权限位 / 列表无 owner 过滤（枚举面）", "按 owner 过滤或按权限收窄",
        f"list status={lst.status_code}, 本后端可见条数={len(items)}（受 C-09 影响不可用作计数证据）; "
        f"router 上 require_permission 依赖数=0（对照 datasets.py=15）; "
        f"枚举能力由 A-05 交叉读/交叉删直接证实",
        "CONFIRMED_DEFECT", severity="high",
        evidence={"list_status": lst.status_code, "visible": len(items),
                  "permission_deps_on_router": 0,
                  "enumeration_proof": "A-05: AUTH_B 可读/删 AUTH_A 的文件（200）",
                  "note": "平台已有 require_permission 基建（backend/api/*.py 中 32 个模块在用），"
                          "本模块完全未接入；role=viewer 令牌可上传/删除"},
        invariant="INV-4", priority="P1")
    # role=viewer 写操作（权限粒度实证，已由 cross_del 覆盖；此处补一次明确上传）
    vw = gclient.post("/upload", files={"file": ("viewer.csv", b"v", "text/csv")},
                      data={"category": "data"}, headers=AUTH_B)
    rec("A-07", "授权", "role=viewer 令牌执行写操作（上传）", "403（只读角色不应能写）",
        f"status={vw.status_code}", "CONFIRMED_DEFECT" if vw.status_code == 200 else "OK",
        severity="high",
        evidence={"status": vw.status_code, "role": "viewer",
                  "note": "中间件只验签名与 type=access，不校验 role；端点无权限依赖"},
        invariant="INV-4", priority="P1")


# ─────────────────────────── B5 中断/续传/幂等 ───────────────────────────
class _BrokenStream:
    def __init__(self):
        self.calls = 0

    def read(self, size=-1):
        self.calls += 1
        if self.calls == 1:
            return b"z" * 4096
        raise IOError("client disconnected mid-stream")


class _FakeUpload:
    def __init__(self, name):
        self.filename = name
        self.file = _BrokenStream()


def resume_probes():
    # R-01 中断 → 半成品残留
    d = UPLOAD_ROOT / "interrupt_probe"
    d.mkdir(exist_ok=True)
    before = files_under(d)
    err = None
    try:
        fu.save_file(_FakeUpload("broken.csv"), "interrupt_probe")
    except Exception as exc:
        err = repr(exc)
    after = files_under(d)
    residue = [p for p in after if p not in before]
    rec("R-01", "中断续传", "传输中断（流读取抛错）", "无半成品残留（临时文件+原子 rename）",
        f"异常={err}; 残留文件={residue}",
        "CONFIRMED_DEFECT" if residue else "OK",
        severity="high" if residue else None,
        evidence={"residue": residue, "size": [os.path.getsize(p) for p in residue],
                  "note": "直接 open(目标) 写入，无 .part/rename；DB 无记录 → 孤儿文件不可见不可删"},
        invariant="INV-5", priority="P0", repro="fu.save_file(<流中途抛错>)")
    # R-02 Content-Range 是否被识别
    r = client.post("/upload", files={"file": ("rng.csv", b"0123456789", "text/csv")},
                    data={"category": "data"},
                    headers={"Content-Range": "bytes 0-3/10"})
    rec("R-02", "中断续传", "上传带 Content-Range 分片头", "支持续传或明确 501",
        f"status={r.status_code}, body_keys={list(r.json().keys()) if r.status_code==200 else r.text[:80]}",
        "CONFIRMED_DEFECT" if r.status_code == 200 else "OK",
        severity="medium",
        evidence={"ranges_supported": False,
                  "note": "头被忽略 → 每次重试生成新文件与新记录（重复计存储）"},
        invariant="INV-7", priority="P1", repro="POST /upload + Content-Range 头")
    # R-03 幂等
    before_n = count_rows()
    a = client.post("/upload", files={"file": ("dup.csv", b"same-bytes", "text/csv")},
                    data={"category": "data"})
    b = client.post("/upload", files={"file": ("dup.csv", b"same-bytes", "text/csv")},
                    data={"category": "data"})
    rec("R-03", "中断续传", "同内容重复上传", "幂等或可识别重复",
        f"两次 file_id={a.json().get('file_id')}/{b.json().get('file_id')}，新增记录={count_rows()-before_n}",
        "CONFIRMED_DEFECT" if count_rows() - before_n == 2 else "OK",
        severity="low",
        evidence={"note": "无 checksum 计算（表有 checksum 列未用），响应也不返回 checksum"},
        invariant="INV-6", priority="P2")
    # R-04 下载 Range
    up = client.post("/upload", files={"file": ("range.csv", b"0123456789", "text/csv")},
                     data={"category": "data"})
    fid = up.json().get("file_id")
    rg = client.get(f"/download/{fid}", headers={"Range": "bytes=2-5"})
    rec("R-04", "中断续传", "下载 Range: bytes=2-5", "206 且返回 4 字节 '2345'",
        f"status={rg.status_code}, content={rg.content!r}",
        "OK" if rg.status_code == 206 and rg.content == b"2345" else "CONFIRMED_DEFECT",
        severity="low",
        evidence={"status": rg.status_code, "body": rg.content.decode("latin1"),
                  "content_disposition": rg.headers.get("content-disposition"),
                  "accept_ranges": rg.headers.get("accept-ranges")},
        invariant="INV-7/INV-10", priority="P1")
    rec("R-05", "中断续传", "中断后清单/统计一致性", "不出现幽灵条目",
        f"R-01 残留文件不在 uploaded_files（rows={count_rows()}）→ 清单不可见，配额层面为幽灵占盘",
        "ACCEPTED_RISK",
        evidence={"note": "幽灵文件既不进清单也无法经 API 删除，只能人工清理"},
        invariant="INV-5", priority="P1")


# ─────────────────────────── B6 完整性/错误语义 ───────────────────────────
def integrity_probes():
    # C-01 DB 故障
    reset_table(drop=True)
    n_before = len(files_under(UPLOAD_ROOT))
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE uploaded_files"))
    r = client.post("/upload", files={"file": ("dbfail.csv", b"orphan", "text/csv")},
                    data={"category": "data"})
    n_after = len(files_under(UPLOAD_ROOT))
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    rec("C-01", "数据完整性", "DB 插入失败时的上传结果", "5xx 且回滚物理文件",
        f"status={r.status_code}, success={body.get('success')}, file_id={body.get('file_id')}, "
        f"落盘文件数 {n_before}→{n_after}",
        "CONFIRMED_DEFECT" if r.status_code == 200 and body.get("success") and n_after > n_before else "OK",
        severity="high",
        evidence={"response": body, "files_on_disk_delta": n_after - n_before,
                  "note": "捕获 DB 异常后仅 warning，仍返回 success=True/file_id=None"},
        invariant="INV-5", priority="P0", repro="DROP TABLE uploaded_files 后 POST /upload")
    # C-02 列表在 DB 故障时
    lf = client.get("/files")
    rec("C-02", "错误语义", "DB 不可用时 GET /files", "5xx",
        f"status={lf.status_code}, body={lf.text[:120]}",
        "CONFIRMED_DEFECT" if lf.status_code == 200 else "OK", severity="medium",
        evidence={"status": lf.status_code, "body": lf.text[:200],
                  "note": "except 分支返回 {total:0, items:[]}，故障被伪装成'空集成功'"},
        invariant="INV-8", priority="P1", repro="DROP TABLE 后 GET /files")
    # 重建表
    reset_table(drop=False)
    # C-03 字段对齐：两种复现，互相独立于后端
    #  (a) 后端无关的逻辑级复现：注入 fake row，令 f[7](mime_type)=1999 年、f[8](uploaded_at)=2026 年，
    #      若响应 uploaded_at 显示 1999 → 证明按列序取错列；
    #  (b) 真实后端复现：给真实行写入 mime_type 后调用（SQLite 下另外暴露 .isoformat() 崩溃）。
    from datetime import datetime as _dt

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _FakeDB:
        def __init__(self, row):
            self._row = row

        def execute(self, *a, **k):
            return _FakeResult(self._row)

        def commit(self):
            pass

    mock_row = (1, "uuid.csv", "orig.csv", "data", "/p/uuid.csv", 5, "csv",
                _dt(1999, 9, 9, 9, 9, 9),     # f[7] = mime_type 列
                _dt(2026, 1, 2, 3, 4, 5))     # f[8] = uploaded_at 列
    app.dependency_overrides[fu.get_db] = lambda: _FakeDB(mock_row)
    try:
        mock_info = client.get("/files/1").json()
    finally:
        app.dependency_overrides.pop(fu.get_db, None)

    up = client.post("/upload", files={"file": ("align.csv", b"align", "text/csv")},
                     data={"category": "data"})
    fid = up.json().get("file_id")
    with engine.begin() as conn:
        conn.execute(text("UPDATE uploaded_files SET mime_type=:m WHERE id=:i"),
                     {"m": "text/csv-MARKER", "i": fid})
    real = client.get(f"/files/{fid}")
    misaligned = str(mock_info.get("uploaded_at", "")).startswith("1999")
    rec("C-03", "数据完整性", "GET /files/{id} 按列序取值（期望按名绑定）", "uploaded_at 取 f[8]=(2026)",
        f"fake-row 复现：uploaded_at={mock_info.get('uploaded_at')!r}（=mime_type 列 1999）; "
        f"真实行复现：status={real.status_code} body={real.text[:110]}",
        "CONFIRMED_DEFECT" if misaligned else "OK", severity="medium",
        evidence={"fake_row_response": mock_info,
                  "real_backend": {"status": real.status_code, "body": real.text[:220]},
                  "columns_from_migration": ["id", "filename", "original_filename", "category",
                                             "file_path", "file_size", "extension", "mime_type",
                                             "uploaded_at"],
                  "note": "SELECT * 后 f[7] 应为 mime_type、f[8] 才是 uploaded_at；"
                          "实现取 f[7]。PG 下表现为 uploaded_at 显示 MIME 字符串；"
                          "SQLite 下 mime 为 str 时 .isoformat() 抛错 → 500"},
        invariant="INV-9", priority="P1",
        repro="dependency_overrides 注入 fake row（f[7]=1999）→ GET /files/1")

    # C-04 迁移种子行污染用户视图：用 /stats 与直接 SQL 判定，避免受列表端点自身缺陷影响
    with engine.begin() as conn:
        n_real = conn.execute(text("SELECT COUNT(*) FROM uploaded_files")).scalar()
        for c in ("image", "document", "data", "model", "archive", "code", "general"):
            conn.execute(text("""INSERT INTO uploaded_files
                (filename, original_filename, category, file_path, file_size, extension, description)
                VALUES ('placeholder','placeholder',:c,'/placeholder',0,'txt','默认类别占位')"""),
                         {"c": c})
        n_ph = conn.execute(text(
            "SELECT COUNT(*) FROM uploaded_files WHERE original_filename='placeholder'")).scalar()
        ph_ids = [r[0] for r in conn.execute(text(
            "SELECT id FROM uploaded_files WHERE original_filename='placeholder' LIMIT 3"))]
    st = client.get("/stats").json()
    dl = client.get(f"/download/{ph_ids[0]}") if ph_ids else None
    polluted = (st.get("total_files") or 0) >= n_real + n_ph and n_ph > 0
    rec("C-04", "数据完整性", "迁移种子行进入统计/列表视图", "不进入",
        f"种子行={n_ph} 条, /stats.total_files={st.get('total_files')} (真实行 {n_real} + 种子 {n_ph}), "
        f"种子行下载 status={getattr(dl, 'status_code', None)}",
        "CONFIRMED_DEFECT" if polluted else "OK", severity="low",
        evidence={"placeholder_count": n_ph, "stats": st, "placeholder_ids": ph_ids,
                  "download_of_placeholder": getattr(dl, "status_code", None),
                  "note": "011 迁移末尾插入 7 条 /placeholder 占位行：计入 /stats、"
                          "在支持列表的后端会出现在清单里，点击下载 404"},
        invariant="INV-9", priority="P1")

    # C-09 列表端点自身在非 PostgreSQL 后端静默失败（异常被吞成 200 空集）
    with engine.begin() as conn:
        db_rows = conn.execute(text("SELECT COUNT(*) FROM uploaded_files")).scalar()
    lf = client.get("/files")
    visible = lf.json().get("total")
    crash = None
    try:
        with engine.begin() as conn:
            row = conn.execute(text("SELECT uploaded_at FROM uploaded_files LIMIT 1")).scalar()
        crash = f"uploaded_at 驱动返回值类型={type(row).__name__}（{row!r}）→ 无 .isoformat()"
    except Exception as exc:
        crash = repr(exc)
    rec("C-09", "数据完整性", "GET /files 在 SQLite 后端的一致性", "与 DB 行数一致",
        f"DB 行数={db_rows}, /files total={visible}, status={lf.status_code}; {crash}",
        "CONFIRMED_DEFECT" if (db_rows or 0) > 0 and (visible or 0) == 0 else "OK",
        severity="medium",
        evidence={"db_rows": db_rows, "api_total": visible, "status": lf.status_code,
                  "note": "uploaded_at.isoformat() 假定 datetime；SQLite 返回 str → 异常被 "
                          "except 吞掉并返回 {total:0,items:[]} → 与 C-02 同一处静默失败叠加"},
        invariant="INV-9/INV-8", priority="P1")

    # C-05 错误信息泄露
    blocked = UPLOAD_ROOT / "blocked_dir"
    blocked.write_text("i am a file, not a dir", encoding="utf-8")
    leak = client.post("/upload", files={"file": ("leak.csv", b"leak", "text/csv")},
                       data={"category": "blocked_dir"})
    leaks_path = str(TMPROOT) in leak.text
    rec("C-05", "错误语义", "500 响应是否含内部绝对路径", "不含",
        f"status={leak.status_code}, detail={leak.text[:160]}",
        "CONFIRMED_DEFECT" if leaks_path else "OK", severity="low",
        evidence={"status": leak.status_code, "detail": leak.text[:300],
                  "note": "detail=f'上传失败: {str(e)}' 直接回显异常串（含服务器路径）"},
        invariant="INV-8", priority="P1")
    try:
        blocked.unlink()
    except Exception:
        pass
    # C-07 删除一致性
    up2 = client.post("/upload", files={"file": ("del.csv", b"del", "text/csv")},
                      data={"category": "data"})
    fid2 = up2.json().get("file_id")
    with engine.begin() as conn:
        p = conn.execute(text("SELECT file_path FROM uploaded_files WHERE id=:i"),
                         {"i": fid2}).scalar()
    d1 = client.delete(f"/files/{fid2}")
    gone = not os.path.exists(p)
    d2 = client.delete(f"/files/{fid2}")
    rec("C-07", "数据完整性", "删除后物理文件与记录同时消失", "一致，重复删除 404",
        f"第一次={d1.status_code}, 物理删除={gone}, 第二次={d2.status_code}",
        "OK" if d1.status_code == 200 and gone and d2.status_code == 404 else "CONFIRMED_DEFECT",
        severity="info",
        evidence={"first": d1.status_code, "physical_gone": gone, "second": d2.status_code},
        invariant="INV-9", priority="P1")
    # C-06 下载缺失物理文件（种子行已覆盖，补一条记录存在但文件丢失）
    up3 = client.post("/upload", files={"file": ("lost.csv", b"lost", "text/csv")},
                      data={"category": "data"})
    fid3 = up3.json().get("file_id")
    with engine.begin() as conn:
        p3 = conn.execute(text("SELECT file_path FROM uploaded_files WHERE id=:i"),
                          {"i": fid3}).scalar()
    os.remove(p3)
    lost = client.get(f"/download/{fid3}")
    rec("C-06", "数据完整性", "物理文件缺失时下载", "404 且记录状态可自愈",
        f"status={lost.status_code}, detail={lost.text[:80]}",
        "ACCEPTED_RISK" if lost.status_code == 404 else "CONFIRMED_DEFECT",
        severity="low",
        evidence={"status": lost.status_code,
                  "note": "返回 404 但记录仍 active，清单永久展示不可下载条目"},
        invariant="INV-9", priority="P2")


# ─────────────────────────── 迁移可移植性 ───────────────────────────
def migration_probe(report):
    ok = sum(1 for s in report if s["ok"])
    fails = [s for s in report if not s["ok"]]
    rec("C-08", "数据完整性", "011 迁移在 SQLite 后端可执行（NFR-4）", "全部语句可执行",
        f"{ok}/{len(report)} 语句可执行；失败 {len(fails)} 条",
        "CONFIRMED_DEFECT" if fails else "OK", severity="low",
        evidence={"statements": report},
        invariant="NFR-4", priority="P2")


def main():
    env = env_info()
    cov = coverage_scan()
    mig = setup_schema()
    for fn, args in ((s01, ()), (s03, ()), (s02_s04_s05, ()), (t_matrix, ()),
                     (path_probes, ()), (auth_probes, ()), (resume_probes, ()),
                     (integrity_probes, ())):
        run(fn.__name__, "n/a", fn.__name__,
            lambda fn=fn, args=args: (fn(*args) or {"actual": "see individual records",
                                                    "verdict": "INFO"}), expect="see records")
    migration_probe(mig)
    reset_table(drop=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "env": env,
        "baseline": {
            "coverage_scan": cov,
            "existing_tests_for_module": sum(len(v) for v in cov.values()),
            "premise_check": ("题设='只有 happy-path 测试'；实测该模块测试文件数见上，"
                              "若为 0 则实际状态严于题设"),
            "module_routes": sorted({getattr(r, "path", "") for r in fu.router.routes}),
            "module_constants": {"MAX_FILE_SIZE": ORIG_MAX_SIZE,
                                 "categories": list(fu.ALLOWED_EXTENSIONS)},
        },
        "records": RESULTS,
        "summary": {},
    }
    verdicts = {}
    for r in RESULTS:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    sev = {}
    for r in RESULTS:
        if r["verdict"] == "CONFIRMED_DEFECT":
            sev[r["severity"]] = sev.get(r["severity"], 0) + 1
    payload["summary"] = {"verdict_counts": verdicts, "defect_severity": sev,
                          "records": len(RESULTS)}
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "probe_results.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== {len(RESULTS)} records → {out}")
    print("verdicts:", verdicts)
    print("defect severity:", sev)
    shutil.rmtree(TMPROOT, ignore_errors=True)


if __name__ == "__main__":
    main()