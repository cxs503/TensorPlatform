"""作者自带的（唯一）测试套件：只覆盖 happy path。

对应 test_checklist.md 的 T-01 / T-02 / T-03。
本文件**故意不包含**任何否定例（越权、超限、中断、穿越、回滚、并发），
这正是评审要指出的覆盖缺口，不是疏忽。
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from upload_service import app

CLIENT = TestClient(app)
ALICE = {"Authorization": "Bearer tok-alice"}
CSV_BODY = b"x,y\n1,2\n3,4\n"


def _new_dataset() -> int:
    r = CLIENT.post("/api/v1/datasets", json={"name": "demo"}, headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def test_upload_csv_happy_path():
    ds = _new_dataset()
    r = CLIENT.post(
        f"/api/v1/datasets/{ds}/upload",
        files={"file": ("demo.csv", CSV_BODY, "text/csv")},
        headers=ALICE,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dataset_id"] == ds
    assert body["bytes"] == len(CSV_BODY)
    assert body["rows"] == 2


def test_download_roundtrip():
    ds = _new_dataset()
    CLIENT.post(
        f"/api/v1/datasets/{ds}/upload",
        files={"file": ("demo.csv", CSV_BODY, "text/csv")},
        headers=ALICE,
    )
    r = CLIENT.get(f"/api/v1/datasets/{ds}/download")
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == hashlib.sha256(CSV_BODY).hexdigest()


def test_meta_reflects_upload():
    ds = _new_dataset()
    CLIENT.post(
        f"/api/v1/datasets/{ds}/upload",
        files={"file": ("demo.csv", CSV_BODY, "text/csv")},
        headers=ALICE,
    )
    r = CLIENT.get(f"/api/v1/datasets/{ds}/meta", headers=ALICE)
    assert r.status_code == 200
    assert r.json()["owner"] == "alice"
    assert r.json()["rows"] == 2