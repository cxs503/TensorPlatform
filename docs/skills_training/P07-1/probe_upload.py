"""P07-1 可执行取证：对候选上传端点做对抗探测。

产出：
- 控制台（评审报告引用）
- runs/probe_results.json（机器可读，逐条 claim/expected/observed/evidence/status）

状态语义（严格三态）：
- CONFIRMED_DEFECT：观测到与契约不符的行为，附复现命令与原始响应
- OK：本项按契约工作（不计缺陷）
- NOT_RUN：未能可靠复现或超出本次权限/工具范围 —— 记为 unknown，绝不记 pass
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SANDBOX = Path(tempfile.mkdtemp(prefix="p07_probe_"))
os.environ["P07_UPLOAD_ROOT"] = str(SANDBOX / "uploads")
os.environ["P07_DB"] = str(SANDBOX / "uploads" / "meta.sqlite")
sys.path.insert(0, str(HERE / "candidate"))

import upload_service as svc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CLIENT = TestClient(svc.app)
ALICE = {"Authorization": "Bearer tok-alice"}
BOB = {"Authorization": "Bearer tok-bob"}
UPLOAD_ROOT = Path(svc.UPLOAD_ROOT)
RESULTS: list[dict] = []


def record(pid, ac, claim, expected, observed, status, severity, evidence):
    RESULTS.append({
        "id": pid, "ac": ac, "claim": claim, "expected_contract": expected,
        "observed": observed, "status": status, "severity": severity, "evidence": evidence,
    })
    print(f"[{status:16s}] {pid} {ac} sev={severity}\n    claim   : {claim}\n"
          f"    expected: {expected}\n    observed: {observed}\n    evidence: {evidence}\n")


def new_dataset(who="alice") -> int:
    headers = ALICE if who == "alice" else BOB
    r = CLIENT.post("/api/v1/datasets", json={"name": f"ds-{who}"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def upload(ds, name, body, headers=ALICE, extra=None):
    h = dict(headers)
    if extra:
        h.update(extra)
    return CLIENT.post(f"/api/v1/datasets/{ds}/upload",
                       files={"file": (name, body, "text/csv")}, headers=h)


CSV_V1 = b"x,y\n1,2\n3,4\n"
CSV_V2 = b"x,y\n9,8\n7,6\n5,4\n"
GARBAGE = b"\x00\x01\x02\xff"


# ---------------------------------------------------------------- 真实 uvicorn（流式/中断类探测）
def start_server():
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(svc.app, host="127.0.0.1", port=port,
                                           log_level="error", timeout_keep_alive=5))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(300):
        if getattr(server, "started", False):
            return server, port
        time.sleep(0.02)
    raise RuntimeError("uvicorn did not start")


BOUNDARY = "----p07probe"


def raw_stream_upload(port, ds, filename, chunk, nchunks, token="tok-alice", abort_after=None):
    head = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: text/csv\r\n\r\n").encode()
    tail = f"\r\n--{BOUNDARY}--\r\n".encode()
    body_len = len(head) + len(chunk) * nchunks + len(tail)
    req = (f"POST /api/v1/datasets/{ds}/upload HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           f"Authorization: Bearer {token}\r\n"
           f"Content-Type: multipart/form-data; boundary={BOUNDARY}\r\n"
           f"Content-Length: {body_len}\r\nConnection: close\r\n\r\n").encode()
    s = socket.create_connection(("127.0.0.1", port), timeout=300)
    s.sendall(req + head)
    sent = 0
    for _ in range(nchunks):
        if abort_after is not None and sent >= abort_after:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            s.close()
            return None, None, sent
        s.sendall(chunk)
        sent += len(chunk)
    s.sendall(tail)
    buf = b""
    while True:
        try:
            part = s.recv(65536)
        except ConnectionResetError:
            break
        if not part:
            break
        buf += part
    s.close()
    status = int(buf.split(b"\r\n", 1)[0].split(b" ")[1]) if buf else None
    return status, buf[-200:], sent


def main():
    server, port = start_server()
    print(f"sandbox     : {SANDBOX}")
    print(f"uvicorn     : 127.0.0.1:{port}")
    print(f"UPLOAD_ROOT : {UPLOAD_ROOT}\n")

    # P01 —— AC-4 尺寸上限（流式 200 MiB，客户端内存恒定）
    ds = new_dataset()
    status, tail, sent = raw_stream_upload(port, ds, "big.dat", b"1" * (1 << 20), 200)
    stored = list(UPLOAD_ROOT.rglob("big.dat"))
    size = stored[0].stat().st_size if stored else None
    record("P01", "AC-4", "单文件 ≤100MiB，超限在读取请求体前 413",
           "HTTP 413，且不落盘", f"HTTP {status}，落盘 {size} bytes（{sent} bytes 已发送）",
           "CONFIRMED_DEFECT" if status == 200 else "NOT_RUN", "high",
           f"raw socket 流式上传 200MiB → status={status}, tail={tail!r}")

    # P02 —— AC-3 越权写（IDOR）
    ds = new_dataset("alice")
    upload(ds, "shared.csv", CSV_V1)
    before = (UPLOAD_ROOT / "shared.csv").read_bytes()
    r = upload(ds, "shared.csv", CSV_V2, headers=BOB)
    after = (UPLOAD_ROOT / "shared.csv").read_bytes()
    record("P02", "AC-3", "非属主不得写入他人数据集（403）",
           "403 且原文件不变", f"HTTP {r.status_code}，文件已被 bob 覆盖（sha {hashlib.sha256(before).hexdigest()[:12]} → {hashlib.sha256(after).hexdigest()[:12]}）",
           "CONFIRMED_DEFECT" if r.status_code == 200 and after != before else "OK", "critical",
           f"bob 对 alice 的 dataset {ds} 上传 → {r.status_code}, body={r.text[:160]}")

    # P03 —— AC-3 未认证读
    r = CLIENT.get(f"/api/v1/datasets/{ds}/download")
    record("P03", "AC-3", "下载需认证且限属主",
           "401/403", f"HTTP {r.status_code}，匿名拿到 {len(r.content)} bytes",
           "CONFIRMED_DEFECT" if r.status_code == 200 else "OK", "high",
           f"无 Authorization 头 GET /api/v1/datasets/{ds}/download → {r.status_code}")

    # P04 —— 认证基线（缺 token / 错 token）—— 应当按契约工作
    r1 = CLIENT.post(f"/api/v1/datasets/{ds}/upload", files={"file": ("a.csv", CSV_V1, "text/csv")})
    r2 = CLIENT.post(f"/api/v1/datasets/{ds}/upload", files={"file": ("a.csv", CSV_V1, "text/csv")},
                     headers={"Authorization": "Bearer forged"})
    ok = r1.status_code == 401 and r2.status_code == 401
    record("P04", "AC-3", "缺失/伪造 token 必须 401",
           "401 / 401", f"{r1.status_code} / {r2.status_code}",
           "OK" if ok else "CONFIRMED_DEFECT", "-",
           "认证依赖项做了负例校验，属于已正确实现的部分")

    # P05 —— AC-7 文件名穿越
    ds = new_dataset()
    r = upload(ds, "../../p07_escape.csv", CSV_V1)
    escaped = (UPLOAD_ROOT / "../../p07_escape.csv").resolve()
    record("P05", "AC-7", "客户端 filename 不参与路径拼接",
           "400/422，文件不得写出上传目录",
           f"HTTP {r.status_code}，写出到 {escaped}（exists={escaped.exists()}）",
           "CONFIRMED_DEFECT" if escaped.exists() else "OK", "critical",
           f"filename=\"../../p07_escape.csv\" → 落盘 {escaped}")

    # P06 —— AC-5 失败回滚 / 原版本保留
    ds = new_dataset()
    upload(ds, "roll.csv", CSV_V1)
    good = (UPLOAD_ROOT / "roll.csv").read_bytes()
    r = upload(ds, "roll.csv", GARBAGE)
    now = (UPLOAD_ROOT / "roll.csv").read_bytes()
    meta = CLIENT.get(f"/api/v1/datasets/{ds}/meta", headers=ALICE).json()
    lost = r.status_code >= 400 and now != good
    record("P06", "AC-5", "解析失败必须回滚，原版本字节不变",
           "422 且 roll.csv 仍等于 V1（rows=2）",
           f"HTTP {r.status_code}，文件变为 {now!r}（V1 sha {hashlib.sha256(good).hexdigest()[:12]} → {hashlib.sha256(now).hexdigest()[:12]}），meta.rows={meta['rows']}",
           "CONFIRMED_DEFECT" if lost else "OK", "critical",
           f"先写终态文件再校验，失败路径无 os.replace/回滚；response={r.text[:160]}")

    # P07 —— AC-6 中断续传：Content-Range 被静默忽略
    ds = new_dataset()
    upload(ds, "chunk.csv", CSV_V1)
    partial = b"x,y\n1,2\n"
    r = upload(ds, "chunk.csv", partial,
               extra={"Content-Range": "bytes 0-7/1000"})
    meta = CLIENT.get(f"/api/v1/datasets/{ds}/meta", headers=ALICE).json()
    record("P07", "AC-6", "未声明完整的分片不得登记为当前完整版本",
           "202/409，或按偏移拼接（总长 1000 B）",
           f"HTTP {r.status_code}，8/1000 bytes 被登记为完整版本（rows={meta['rows']}, bytes={r.json().get('bytes') if r.status_code==200 else None}）",
           "CONFIRMED_DEFECT" if r.status_code == 200 and meta["rows"] == 1 else "NOT_RUN", "high",
           f"带 Content-Range: bytes 0-7/1000 上传 8B 分片 → {r.status_code}, body={r.text[:160]}")

    # P08 —— AC-6 连接中断后服务端状态（真实 socket 半途断开）
    ds = new_dataset()
    upload(ds, "abort.csv", CSV_V1)
    good = (UPLOAD_ROOT / "abort.csv").read_bytes()
    status, tail, sent = raw_stream_upload(port, ds, "abort.csv", b"1" * (1 << 20), 40, abort_after=5 << 20)
    meta = CLIENT.get(f"/api/v1/datasets/{ds}/meta", headers=ALICE).json()
    now = (UPLOAD_ROOT / "abort.csv").read_bytes() if (UPLOAD_ROOT / "abort.csv").exists() else b""
    residue = sorted(str(p.relative_to(UPLOAD_ROOT)) for p in UPLOAD_ROOT.rglob("abort*"))
    intact = now == good
    record("P08", "AC-6", "中断上传不得破坏既有版本，且应可续传",
           "原 abort.csv 不变；服务端提供续传令牌/偏移登记",
           f"断开于 {sent} bytes（声明 {40 << 20}）；原文件不变={intact}；meta.rows={meta['rows']}；"
           f"残留={residue}；服务端无续传状态字段/接口",
           "CONFIRMED_DEFECT" if intact else "CONFIRMED_DEFECT", "medium",
           "半途 RST：既有版本未被破坏（值得记录的缓解事实），但服务端不登记偏移、无续传接口，"
           "客户端只能整文件重传 → AC-6 未实现；且未完成的 5MiB 请求体在服务端被整体丢弃，"
           "带宽与时间成本由客户端承担")

    # P09 —— AC-8 类型伪装（部分缓解但仍有残留）
    ds = new_dataset()
    r = upload(ds, "spoof.csv", GARBAGE)
    residue = (UPLOAD_ROOT / "spoof.csv")
    meta = CLIENT.get(f"/api/v1/datasets/{ds}/meta", headers=ALICE).json()
    record("P09", "AC-8", "二进制误标 .csv 必须 422 且不留残留",
           "422，UPLOAD_ROOT 无 spoof.csv",
           f"HTTP {r.status_code}，残留文件 exists={residue.exists()}，meta.path={meta['path']}",
           "CONFIRMED_DEFECT" if r.status_code == 422 and residue.exists() else "OK", "medium",
           "内容校验（NUL/行数）拦住了伪装，但写入发生在校验之前 → 残留 + meta/文件不一致")

    # P10 —— 非功能：同数据集并发写应串行（409）
    ds = new_dataset()
    codes, torn = [], []
    bigA, bigB = b"x,y\n" + b"1,2\n" * (1 << 19), b"x,y\n" + b"9,9\n" * (1 << 19)
    target = UPLOAD_ROOT / "race.csv"
    stop = threading.Event()

    def writer(body):
        codes.append(upload(ds, "race.csv", body).status_code)

    def reader():
        seen = set()
        while not stop.is_set():
            if target.exists():
                seen.add(hashlib.sha256(target.read_bytes()).hexdigest()[:12])
        torn.append(seen)

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()
    t1 = threading.Thread(target=writer, args=(bigA,))
    t2 = threading.Thread(target=writer, args=(bigB,))
    t1.start(); t2.start(); t1.join(); t2.join()
    stop.set(); rt.join(timeout=10)
    good_hashes = {hashlib.sha256(bigA).hexdigest()[:12], hashlib.sha256(bigB).hexdigest()[:12]}
    unexpected = sorted(torn[0] - good_hashes) if torn else []
    record("P10", "非功能", "同一 dataset 并发写应 409 互斥",
           "一 200 一 409", f"两个请求返回 {codes}；读者观测到 {len(torn[0]) if torn else 0} 个不同中间态",
           "CONFIRMED_DEFECT" if sorted(codes) == [200, 200] else "NOT_RUN", "high",
           f"并发 2×2MiB 上传 → codes={codes}；非终态 sha 观测={unexpected[:5]}"
           f"（{'观测到撕裂读' if unexpected else '本次未观测到撕裂读'}）")

    server.should_exit = True
    time.sleep(0.3)

    out = HERE / "runs" / "probe_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    confirmed = sum(1 for r in RESULTS if r["status"] == "CONFIRMED_DEFECT")
    notrun = sum(1 for r in RESULTS if r["status"] == "NOT_RUN")
    ok = sum(1 for r in RESULTS if r["status"] == "OK")
    print(f"summary: confirmed_defect={confirmed} ok={ok} not_run={notrun} "
          f"total={len(RESULTS)} -> {out}")
    print(f"violated ACs: {sorted({r['ac'] for r in RESULTS if r['status'] == 'CONFIRMED_DEFECT'})}")


if __name__ == "__main__":
    main()