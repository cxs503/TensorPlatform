"""报告归属门/配图门测试夹具（共享，不含任何领域语义）。

设计要点（为什么这么搭）
------------------------
* **真 SQL、真 HTTP**：用 sqlite 文件库 + FastAPI ``TestClient`` 打真实端点，
  而不是直接调用纯函数——「门写好了但没接线」是本仓历史事故（P34-N7 交付的
  ``services/report_ownership.py`` 在合并时被门禁滤除，``api/tensor_reports.py``
  仍 import 它 → 整个报告 API 无法导入）。纯函数测试对这类事故完全免疫。
* **身份走请求头**：``require_auth`` 被覆盖成「按 ``X-Test-User`` 取身份」，
  于是并发用例里 8 个线程可以各自扮演不同智能体，且不需要全局可变状态
  （全局 USER 变量在并发下会串号——那正是被测事故本身）。
* **sqlite 兼容层**：生产 SQL 用了 Postgres 专有的 ``CAST(:tags AS jsonb)`` /
  ``jsonb`` 列，sqlite 既不认 jsonb 亲和性（JSON 文本会变 ``0``）、也不会把
  文本列回读成 list。兼容层只做两件等价变换：写入时剥掉 ``CAST(... AS jsonb)``，
  读取时把 ``tags`` / ``figures`` 文本解回 JSON（Postgres jsonb 的本义）。
  其余 SQL 与业务代码一个字不动，因此门判据与实际落库走的都是生产同一份代码。
* **修复前基线来自 git**：``legacy_source()`` 从 ``git show <commit>^`` 取
  修改前的 ``api/tensor_reports.py`` 原文并按路径加载，红测试因此是**同一份
  请求打同一份历史代码**，不是「照着想象写个假旧实现」。
"""
from __future__ import annotations

import base64
import datetime as _dt
import importlib.util
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.deps import require_auth  # noqa: E402  (需先注入 sys.path)
from database.config import get_db  # noqa: E402

ORG = 1
JSON_COLUMNS = ("tags", "figures")

SCHEMA = [
    "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)",
    """CREATE TABLE tensor_dev_missions (
        id INTEGER PRIMARY KEY, organization_id INTEGER, repo TEXT, goal TEXT,
        status TEXT, result TEXT, claimed_by TEXT, claimed_user_id INTEGER,
        created_at TEXT)""",
    """CREATE TABLE tensor_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER, title TEXT,
        summary TEXT, scenario_key TEXT, mission_id INTEGER, stage TEXT, tags TEXT,
        markdown TEXT, figures TEXT, status TEXT, created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        qa_status TEXT, qa_rejection_reason TEXT, qa_checked_at TEXT)""",
]


def make_png(width: int = 2, height: int = 2, seed: int = 0) -> bytes:
    """造一个结构合法的最小 PNG（签名 + IHDR + IDAT + IEND，CRC 正确）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit RGB
    raw = b"".join(b"\x00" + bytes([(seed + x) % 256 for _ in range(3 * width)])
                   for x in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


#: 真实 JPEG 头（SOI + APP0 JFIF + 尾部 EOI），用来验「非 PNG 容器」拒绝
JPEG_BYTES = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
              b"\xff\xd9")
#: 真实 WebP 头（RIFF....WEBPVP8 ...），历史上最容易被改后缀混进来
WEBP_BYTES = (b"RIFF" + struct.pack("<I", 26) + b"WEBPVP8 "
              + struct.pack("<I", 14) + b"\x00" * 14)
#: 合法 PNG 被砍掉尾部 IEND（半截上传的典型形态）
TRUNCATED_PNG = make_png()[:-12]


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ── sqlite ↔ jsonb 兼容层 ───────────────────────────────────────────────────
def _decode_json_columns(row: dict) -> dict:
    """把 sqlite 的 TEXT 还原成生产列类型：jsonb → Python 对象，timestamp → datetime。"""
    out = dict(row)
    for key in JSON_COLUMNS:
        val = out.get(key)
        if isinstance(val, str) and val[:1] in ("[", "{"):
            try:
                out[key] = json.loads(val)
            except json.JSONDecodeError:
                pass
    for key, val in list(out.items()):
        if key.endswith("_at") and isinstance(val, str) and val:
            try:
                out[key] = _dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                pass
    return out


class _CompatMappings:
    """最小映射结果集（仅实现被测代码用到的 all/first）。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def all(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def first(self) -> dict | None:
        return dict(self._rows[0]) if self._rows else None

    def one(self) -> dict:
        return self.all()[0]


class _CompatResult:
    def __init__(self, result: Any):
        self._result = result

    def mappings(self) -> _CompatMappings:
        return _CompatMappings([_decode_json_columns(dict(r))
                                for r in self._result.mappings().all()])

    def __getattr__(self, item: str) -> Any:
        return getattr(self._result, item)


class _CompatSession:
    """只改写 Postgres 专有 jsonb CAST 的会话包装（见模块 docstring）。"""

    _JSONB = re.compile(r"CAST\((\:[A-Za-z_][A-Za-z0-9_]*) AS jsonb\)")

    def __init__(self, inner: Any):
        self._inner = inner

    def execute(self, stmt: Any, params: Any = None) -> _CompatResult:
        sql = str(stmt)
        if "jsonb" in sql:
            sql = self._JSONB.sub(r"\1", sql)
        return _CompatResult(self._inner.execute(text(sql), params or {}))

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


def build_engine(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        for ddl in SCHEMA:
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO organizations (id, name) VALUES (1, 'OrgAlpha')"))
    return engine


def seed_mission(engine, mission_id: int, *, owner_uid: int | None,
                 owner_name: str | None = None, status: str = "succeeded",
                 org: int = ORG) -> None:
    """插一条 mission 归属记录：claimed_user_id 绑定认证账号（0090 语义）。"""
    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO tensor_dev_missions
                        (id, organization_id, repo, goal, status, claimed_by,
                         claimed_user_id, created_at)
                    VALUES (:id, :org, 'platform-lib', 'gate-ownership',
                            :status, :by, :uid, '2026-11-06T00:00:00')"""),
            {"id": mission_id, "org": org, "status": status,
             "by": owner_name, "uid": owner_uid},
        )


def fake_user(uid: int, *, role: str = "ENGINEER", org: int = ORG,
              username: str | None = None) -> dict:
    return {"id": uid, "username": username or f"agent{uid}", "email": f"a{uid}@t.io",
            "role": role, "is_active": True, "organization_id": org}


def build_app(router_module: Any, engine, *, figure_dir: Path,
              users: dict[int, dict]) -> TestClient:
    """装一个只含报告路由的 app；身份由 ``X-Test-User`` 头选取。"""
    router_module.FIGURE_DIR = figure_dir
    Session = sessionmaker(bind=engine, future=True)
    app = FastAPI()
    app.include_router(router_module.router)

    def _user(request: Request) -> dict:
        uid = int(request.headers.get("X-Test-User", "0"))
        if uid not in users:
            raise HTTPException(status_code=401, detail="unknown test user")
        return dict(users[uid])

    def _db() -> Iterator[_CompatSession]:
        session = Session()
        try:
            yield _CompatSession(session)
        finally:
            session.close()

    app.dependency_overrides[require_auth] = _user
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def report_body(mission_id: int | None, *, title: str = "归属门验收报告",
                figures: list[dict] | None = None) -> dict:
    return {
        "title": title,
        "summary": "report payload ownership gate acceptance",
        "scenario_key": "platform-report-gate",
        "mission_id": mission_id,
        "tags": ["ownership", "payload"],
        "markdown": "## acceptance\n\nowner gate + png magic + self proof.\n",
        "figures": figures or [],
    }


def figure(name: str = "gate", raw: bytes | None = None) -> dict:
    # name 是裸文件名（服务端落盘时补 .png），与 FigureIn 契约一致
    return {"name": name, "caption": "gate evidence",
            "png_base64": b64(raw if raw is not None else make_png())}


def report_count(engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM tensor_reports")).scalar() or 0)


def stored_rows(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, mission_id, created_by, title FROM tensor_reports ORDER BY id")
        ).mappings().all()
    return [dict(r) for r in rows]


# ── 修复前基线：从 git 取历史版本（红留证，不做「想象版旧实现」） ────────────
LEGACY_REF = "2fe7f597^"  # P34-N7 归属门提交之前的一条


def legacy_source(ref: str = LEGACY_REF,
                  path: str = "backend/api/tensor_reports.py") -> str | None:
    try:
        out = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=str(REPO),
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout


def load_module_from_source(name: str, source: str, workdir: Path) -> Any:
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / f"{name}.py"
    target.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, target)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# ── C2 并发串号取证（测试与证据脚本共用同一段实现） ────────────────────────
def concurrent_ownership_round(engine, router_module, figure_root: Path, *,
                               n: int = 8, mission_base: int = 9200,
                               user_base: int = 9200) -> dict:
    """n 个线程各自用**自己的 token**提交**自己的单**，同时尝试一次越权提交。

    返回机器可裁的统计（测试与 ``artifacts/.../criteria.json`` 都从这里取数，
    不手抄）：``stored`` / ``cross_talk`` / ``cross_talk_rate`` /
    ``own_ok`` / ``intruder_blocked`` / ``figure_bytes_match``。
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tests import report_gate_support as self_mod  # 便于并发内复用常量

    missions = {mission_base + i: user_base + i for i in range(n)}
    for mid, uid in missions.items():
        seed_mission(engine, mid, owner_uid=uid)

    clients, users = {}, {}
    for idx, (mid, uid) in enumerate(missions.items()):
        users[uid] = fake_user(uid, username=f"agent-{idx}")
        # 注意：端点的 FIGURE_DIR 是**进程级**常量（生产只有一个落盘根目录），
        # 所以本轮所有客户端共用同一个 figure_root；报告 id 全局唯一，不会互相覆盖。
        clients[uid] = build_app(router_module, engine, figure_dir=figure_root,
                                 users={uid: users[uid]})

    order = list(missions.items())
    barrier = threading.Barrier(n)
    payload_registry: dict[int, bytes] = {}
    lock = threading.Lock()

    def _worker(idx: int) -> dict:
        mid, uid = order[idx]
        neighbour = order[(idx + 1) % n][0]           # 别人的单
        raw = make_png(seed=idx + 1)
        with lock:
            payload_registry[mid] = raw
        barrier.wait(timeout=30)                       # 尽量让 8 个请求同时进端点
        own = clients[uid].post("/tensor/reports",
                                json=report_body(mid, title=f"并发取证单 {mid}",
                                                 figures=[figure(f"cfig{idx}", raw)]),
                                headers={"X-Test-User": str(uid)})
        intruder = clients[uid].post("/tensor/reports",
                                     json=report_body(neighbour, title=f"越权尝试 {neighbour}"),
                                     headers={"X-Test-User": str(uid)})
        return {"mission_id": mid, "uid": uid, "own_status": own.status_code,
                "own_body": own.json() if own.status_code == 200 else own.text[:200],
                "intruder_status": intruder.status_code,
                "intruder_code": (intruder.json().get("detail", {}).get("code")
                                  if intruder.status_code != 200 else None),
                "figure_name": f"cfig{idx}"}

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = {mid: pool.submit(_worker, idx)
                   for idx, (mid, _uid) in enumerate(order)}
        results = {mid: f.result(timeout=120) for mid, f in futures.items()}

    rows = _all_report_rows(engine)
    expected = {mid: uid for mid, uid in missions.items()}
    rows_cur = [r for r in rows if r["mission_id"] in expected]
    cross = [r for r in rows_cur if expected.get(r["mission_id"]) != r["created_by"]]
    figure_ok = 0
    for mid, res in results.items():
        name = res["figure_name"]
        path = figure_root / str(res["own_body"].get("id", -1)) / f"{name}.png"
        if path.is_file() and path.read_bytes() == payload_registry[mid]:
            figure_ok += 1
    return {
        "n": n,
        "stored": len(rows_cur),
        "stored_total": len(rows),
        "cross_talk": len(cross),
        "cross_talk_rate": f"{len(cross)}/{n}",
        "own_ok": sum(1 for r in results.values() if r["own_status"] == 200),
        "intruder_blocked": sum(1 for r in results.values()
                                if r["intruder_status"] == 403
                                and r["intruder_code"] == "REPORT_MISSION_NOT_OWNED"),
        "figure_bytes_match": figure_ok,
        "missions": sorted(missions),
        "per_thread": [results[mid] for mid in sorted(results)],
    }


def _all_report_rows(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, mission_id, created_by FROM tensor_reports ORDER BY id"
        )).mappings().all()
    return [dict(r) for r in rows]


def ownership_matrix_round(engine, router_module, figure_root: Path, *,
                           n: int = 8, mission_base: int = 9400,
                           user_base: int = 9400) -> dict:
    """n×n 归属矩阵取证：每个提交者对**每一个** mission 都发一次报告。

    只有「自己的单」应 200，其余 n-1 列必须 403（``REPORT_MISSION_NOT_OWNED``）。
    返回真实状态矩阵（供报告热图直接使用，零手抄）。
    """
    from concurrent.futures import ThreadPoolExecutor

    missions = [mission_base + i for i in range(n)]
    uids = [user_base + i for i in range(n)]
    for mid, uid in zip(missions, uids):
        seed_mission(engine, mid, owner_uid=uid)

    clients = {}
    for uid in uids:
        clients[uid] = build_app(router_module, engine, figure_dir=figure_root,
                                 users={uid: fake_user(uid)})

    def _row(idx: int) -> list[dict]:
        uid = uids[idx]
        out = []
        for col, mid in enumerate(missions):
            resp = clients[uid].post("/tensor/reports",
                                     json=report_body(mid, title=f"矩阵取证 {uid}->{mid}"),
                                     headers={"X-Test-User": str(uid)})
            detail = resp.json().get("detail", {}) if resp.status_code != 200 else {}
            out.append({"row": idx, "col": col, "submitter": uid, "mission_id": mid,
                        "status": resp.status_code,
                        "code": detail.get("code")})
        return out

    with ThreadPoolExecutor(max_workers=n) as pool:
        matrix = [cell for row in pool.map(_row, range(n)) for cell in row]

    diagonal_ok = sum(1 for c in matrix if c["row"] == c["col"] and c["status"] == 200)
    off_diag_blocked = sum(1 for c in matrix
                           if c["row"] != c["col"] and c["status"] == 403
                           and c["code"] == "REPORT_MISSION_NOT_OWNED")
    return {
        "n": n,
        "attempts": len(matrix),
        "diagonal_ok": diagonal_ok,
        "off_diagonal_blocked": off_diag_blocked,
        "off_diagonal_total": n * (n - 1),
        "statuses": [c["status"] for c in matrix],
        "matrix": matrix,
        "missions": missions,
        "submitters": uids,
    }
