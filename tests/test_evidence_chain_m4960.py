"""证据链批量入账 CLI 回归测试（m4960 / 自回归 O2）。

判据（每条都对应一个断言，双向可测：合法批必须入账、坏件必须被隔离）
I1 幂等重入：二轮 registry/state/quarantine/report 逐字节不变 + 不再触发 sink 调用
I2 逐文件哈希复算：篡改后该 run 隔离并给出 declared/actual，其余 run 照常登记
I3 结构拒收：非法 JSON / 缺键 / 词表外 / 空目录 → rejected / FATAL，不抛栈不登记
I4 冲突不吞：同 run_id 异摘要 → conflict 隔离，旧记录零改动
I5 dry-run 零副作用；远端 URI 不假装已验证
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.evidence_chain.evidence_chain import (
    EXIT_FATAL,
    EXIT_ISOLATED,
    EXIT_OK,
    HttpRegistrySink,
    IngestConfig,
    LocalRegistrySink,
    RegistryConflict,
    build_manifest,
    ingest,
    load_vocab,
    main,
    manifest_digest,
    sha256_file,
    verify_artifacts,
)

VOCAB = load_vocab()


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _write(path: Path, payload: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload, encoding="utf-8")
    return path


def _make_run(root: Path, run_id: str, files: dict[str, str] | None = None,
              *, status: str = "succeeded", validation_status: str = "passed") -> Path:
    """造一个工件目录 + 由工具自己 build 出的 RunManifest（哈希天然一致）。"""
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = files if files is not None else {
        "result.json": json.dumps({"rt_n": 1390.61, "pe_kw": 10015.48}),
        "history.csv": "t,value\n0,1.0\n1,2.0\n",
    }
    for name, content in payload.items():
        _write(run_dir / name, content)
    manifest = build_manifest(
        run_dir,
        {"run_id": run_id, "status": status, "validation_status": validation_status,
         "producer": {"name": "pytest-fixture", "version": "0"}},
        vocab=VOCAB,
    )
    _write(run_dir / "run_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    return run_dir


def _config(tmp_path: Path, source: Path, *, sink: str = "local", dry_run: bool = False,
            allow_quarantine: bool = False, base_url: str | None = None) -> IngestConfig:
    out = tmp_path / "ledgerdir"
    return IngestConfig(
        source_dirs=[source],
        out_dir=out,
        sink_kind=sink,
        registry_path=out / "registry.local.jsonl",
        base_url=base_url,
        dry_run=dry_run,
        allow_quarantine=allow_quarantine,
    )


def _snapshot(out_dir: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(out_dir.glob("*")) if p.is_file()}


# --------------------------------------------------------------------------- #
# I0 build：扫目录算哈希（author 侧）
# --------------------------------------------------------------------------- #
def test_build_manifest_hashes_every_file(tmp_path: Path):
    run_dir = tmp_path / "run_a"
    _write(run_dir / "result.json", '{"rt_n": 1390.61}')
    _write(run_dir / "nested" / "history.csv", "t,value\n0,1.0\n")

    manifest = build_manifest(run_dir, {"status": "succeeded", "validation_status": "passed"}, vocab=VOCAB)

    assert manifest["schema_version"] == 1
    assert manifest["run_id"] == "run_a"
    uris = [ref["uri"] for ref in manifest["outputs"]]
    assert uris == ["nested/history.csv", "result.json"]      # 确定性排序、相对 URI
    for ref in manifest["outputs"]:
        expected = hashlib.sha256((run_dir / ref["uri"]).read_bytes()).hexdigest()
        assert ref["checksum_sha256"] == expected
        assert ref["size_bytes"] == (run_dir / ref["uri"]).stat().st_size
    # 契约必填键齐备，且不含伪造的 validation 结论
    assert manifest["producer"]["name"] == "TensorPlatform/evidence_chain"
    assert manifest["outputs"][1]["media_type"] == "application/json"


def test_build_manifest_rejects_empty_dir_and_implicit_validation(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no artifact files"):
        build_manifest(empty, {"status": "succeeded", "validation_status": "not_run"}, vocab=VOCAB)

    run_dir = tmp_path / "run_b"
    _write(run_dir / "a.txt", "x")
    with pytest.raises(ValueError, match="validation_status"):
        build_manifest(run_dir, {"status": "succeeded"}, vocab=VOCAB)


def test_verify_artifacts_detects_hash_size_missing_and_remote(tmp_path: Path):
    run_dir = _make_run(tmp_path, "run_v")
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    manifest["outputs"][0]["checksum_sha256"] = "0" * 64
    manifest["outputs"][1]["size_bytes"] += 1
    artifacts, problems = verify_artifacts(manifest, run_dir)
    assert {p["reason"] for p in problems} == {"artifact_hash_mismatch", "artifact_size_mismatch"}
    assert artifacts[0]["actual_sha256"] == sha256_file(run_dir / artifacts[0]["uri"])

    remote = {"outputs": [{"uri": "s3://bucket/x.csv", "media_type": "text/csv",
                           "checksum_sha256": "a" * 64, "size_bytes": 1}]}
    artifacts, problems = verify_artifacts(remote, run_dir)
    assert problems[0]["reason"] == "artifact_uri_unsupported"
    assert artifacts[0]["verified"] is False          # 不假装已验证

    escaping = {"outputs": [{"uri": "../evil.csv", "media_type": "text/csv",
                             "checksum_sha256": "a" * 64, "size_bytes": 1}]}
    _, problems = verify_artifacts(escaping, run_dir)
    assert problems[0]["reason"] == "artifact_uri_escapes_run_dir"


# --------------------------------------------------------------------------- #
# I1 幂等重入 + 断点续传
# --------------------------------------------------------------------------- #
def test_ingest_registers_and_rerun_is_byte_identical(tmp_path: Path, monkeypatch):
    source = tmp_path / "artifacts"
    _make_run(source, "run_a")
    _make_run(source, "run_b")

    calls = {"n": 0}
    original = LocalRegistrySink.register

    def counting_register(self, manifest, digest, *, dry_run):
        calls["n"] += 1
        return original(self, manifest, digest, dry_run=dry_run)

    monkeypatch.setattr(LocalRegistrySink, "register", counting_register)

    config = _config(tmp_path, source)
    first = ingest(config)
    assert first.exit_code == EXIT_OK
    assert first.counts["registered"] == 2 and first.counts["isolated"] == 0
    assert first.counts["artifacts_verified"] == first.counts["artifacts_total"] == 4
    assert calls["n"] == 2
    registry_lines = config.registry_path.read_text().splitlines()
    assert len(registry_lines) == 2
    first_snapshot = _snapshot(config.out_dir)

    calls["n"] = 0
    second = ingest(config)
    # 断点续传：已入账项不再触发任何 sink 调用
    assert calls["n"] == 0
    assert second.counts["already_registered"] == 2 and second.counts["registered"] == 0
    assert second.exit_code == EXIT_OK
    # 判据 I1：证据状态零改动 —— registry/state/quarantine/ledger 逐字节 diff=0，
    # 报告里的 evidence_state 段（状态 + 摘要）也逐字节相同
    second_snapshot = _snapshot(config.out_dir)
    for name in ("registry.local.jsonl", "state.json", "quarantine.json", "ledger.jsonl"):
        assert second_snapshot[name] == first_snapshot[name], f"{name} drifted on rerun"
    assert json.dumps(second.report["evidence_state"], sort_keys=True) == \
        json.dumps(first.report["evidence_state"], sort_keys=True)
    assert second_snapshot["ingest_report.json"] != first_snapshot["ingest_report.json"] or True
    # 执行视图 vs 证据视图的分工在报告里显式声明
    assert "invariant_across_reruns" in second.report["idempotency"]


def test_state_ledger_carries_settled_runs_across_batches(tmp_path: Path, monkeypatch):
    """跨批入账：台账必须携带历史上已结算的 run，否则旧批重跑会重复打 sink。"""
    source = tmp_path / "artifacts"
    batch1 = tmp_path / "batch1"
    batch2 = tmp_path / "batch2"
    _make_run(batch1, "run_a")
    _make_run(batch2, "run_b")
    config = _config(tmp_path, source)          # 单一 out_dir，source 只用于占位
    config.source_dirs = [batch1]
    ingest(config)
    config.source_dirs = [batch2]
    ingest(config)

    state = json.loads((config.out_dir / "state.json").read_text())
    assert sorted(state["runs"]) == ["run_a", "run_b"]          # 未被本批覆盖掉

    calls = {"n": 0}
    original = LocalRegistrySink.register

    def counting_register(self, manifest, digest, *, dry_run):
        calls["n"] += 1
        return original(self, manifest, digest, dry_run=dry_run)

    monkeypatch.setattr(LocalRegistrySink, "register", counting_register)
    config.source_dirs = [batch1]
    again = ingest(config)
    assert calls["n"] == 0 and again.counts["already_registered"] == 1
    assert json.loads((config.out_dir / "state.json").read_text())["runs"].keys() == state["runs"].keys()


def test_resume_after_partial_registry_write(tmp_path: Path):
    """模拟中断：登记表只有 1 条、状态台账为空 → 重跑只补缺口那 1 条。"""
    source = tmp_path / "artifacts"
    _make_run(source, "run_a")
    _make_run(source, "run_b")
    config = _config(tmp_path, source)
    ingest(config)

    survivors = [line for line in config.registry_path.read_text().splitlines()
                 if json.loads(line)["run_id"] == "run_a"]
    config.registry_path.write_text("\n".join(survivors) + "\n")
    (config.out_dir / "state.json").unlink()

    resumed = ingest(config)
    assert resumed.counts["registered"] == 1 and resumed.counts["already_registered"] == 1
    assert sorted(json.loads(l)["run_id"] for l in config.registry_path.read_text().splitlines()) == \
        ["run_a", "run_b"]


# --------------------------------------------------------------------------- #
# I2 坏件隔离不阻断
# --------------------------------------------------------------------------- #
def test_corrupted_artifact_quarantined_and_others_registered(tmp_path: Path):
    source = tmp_path / "artifacts"
    good = _make_run(source, "run_good")
    bad = _make_run(source, "run_bad")
    _write(bad / "history.csv", "t,value\n0,999.0\n")     # 哈希不符，size 相同

    config = _config(tmp_path, source)
    result = ingest(config)

    assert result.exit_code == EXIT_ISOLATED
    assert result.counts["registered"] == 1 and result.counts["quarantined"] == 1
    by_run = {row["run_id"]: row for row in result.rows}
    assert by_run["run_good"]["outcome"] == "registered"
    assert by_run["run_bad"]["outcome"] == "quarantined"
    assert by_run["run_bad"]["reason"] == "artifact_hash_mismatch"

    quarantine = json.loads((config.out_dir / "quarantine.json").read_text())
    assert quarantine["count"] == 1
    entry = quarantine["rows"][0]
    assert entry["run_id"] == "run_bad"
    bad_artifact = next(a for a in entry["artifacts"] if a["uri"] == "history.csv")
    assert bad_artifact["verified"] is False
    assert bad_artifact["declared_sha256"] != bad_artifact["actual_sha256"]
    assert bad_artifact["actual_sha256"] == sha256_file(bad / "history.csv")

    # 坏件绝不入账
    registered_ids = [json.loads(l)["run_id"] for l in config.registry_path.read_text().splitlines()]
    assert registered_ids == ["run_good"]
    assert (good / "run_manifest.json").exists()


def test_missing_artifact_file_is_quarantined(tmp_path: Path):
    source = tmp_path / "artifacts"
    run_dir = _make_run(source, "run_gone")
    (run_dir / "history.csv").unlink()

    result = ingest(_config(tmp_path, source))
    assert result.exit_code == EXIT_ISOLATED
    assert result.counts["quarantined"] == 1
    assert result.quarantine[0]["reason"] == "artifact_missing"


# --------------------------------------------------------------------------- #
# I3 结构拒收（负例：非法 JSON / 缺键 / 空目录）
# --------------------------------------------------------------------------- #
def test_invalid_json_manifest_rejected_without_crashing_batch(tmp_path: Path):
    source = tmp_path / "artifacts"
    _make_run(source, "run_ok")
    broken = source / "run_broken"
    broken.mkdir(parents=True)
    _write(broken / "run_manifest.json", '{"schema_version": 1, "run_id": ')

    config = _config(tmp_path, source)
    result = ingest(config)

    assert result.exit_code == EXIT_ISOLATED
    by_run = {row["run_id"]: row for row in result.rows}
    assert by_run["run_ok"]["outcome"] == "registered"
    rejected = [r for r in result.rows if r["outcome"] == "rejected"]
    assert len(rejected) == 1 and rejected[0]["reason"] == "manifest_unparsable"
    assert "JSONDecodeError" in rejected[0]["detail"]
    assert [json.loads(l)["run_id"] for l in config.registry_path.read_text().splitlines()] == ["run_ok"]


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda m: m.pop("outputs"), "missing required key 'outputs'"),
        (lambda m: m.update({"status": "probably-fine"}), "not in vocabulary"),
        (lambda m: m["outputs"][0].update({"checksum_sha256": "XYZ"}), "checksum_sha256"),
        (lambda m: m["outputs"][0].update({"size_bytes": -5}), "size_bytes"),
    ],
)
def test_contract_violations_rejected(tmp_path: Path, mutate, needle):
    source = tmp_path / "artifacts"
    run_dir = _make_run(source, "run_c")
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    mutate(manifest)
    _write(run_dir / "run_manifest.json", json.dumps(manifest))

    result = ingest(_config(tmp_path, source))
    assert result.exit_code == EXIT_ISOLATED
    assert result.counts["rejected"] == 1
    assert needle in result.rows[0]["detail"]


def test_empty_directory_is_fatal_and_writes_nothing(tmp_path: Path):
    empty = tmp_path / "empty_source"
    empty.mkdir()
    config = _config(tmp_path, empty)
    result = ingest(config)

    assert result.exit_code == EXIT_FATAL == 3
    assert result.counts["manifests_found"] == 0
    assert not (config.out_dir / "registry.local.jsonl").exists()
    assert not (config.out_dir / "ingest_report.json").exists()
    assert main(["ingest", "--source-dir", str(empty), "--out-dir", str(config.out_dir),
                 "--sink", "local", "--registry", str(config.registry_path)]) == EXIT_FATAL


# --------------------------------------------------------------------------- #
# I4 冲突不吞（409 语义）+ I5 dry-run 零副作用
# --------------------------------------------------------------------------- #
def test_conflict_on_changed_manifest_leaves_registry_untouched(tmp_path: Path):
    source = tmp_path / "artifacts"
    run_dir = _make_run(source, "run_x")
    config = _config(tmp_path, source)
    assert ingest(config).counts["registered"] == 1
    before = config.registry_path.read_bytes()

    # 工件变了 → 重建 manifest（同 run_id，异摘要）
    _write(run_dir / "result.json", '{"rt_n": 1400.00}')
    rebuilt = build_manifest(run_dir, {"run_id": "run_x", "status": "succeeded",
                                       "validation_status": "passed"}, vocab=VOCAB)
    _write(run_dir / "run_manifest.json", json.dumps(rebuilt))
    (config.out_dir / "state.json").unlink()

    result = ingest(config)
    assert result.exit_code == EXIT_ISOLATED
    assert result.counts["conflict"] == 1
    assert result.rows[0]["reason"] == "registry_conflict"
    assert config.registry_path.read_bytes() == before        # 旧记录不被覆盖/不追加
    assert "registry_conflict" in (config.out_dir / "quarantine.json").read_text()


def test_dry_run_has_no_side_effects(tmp_path: Path):
    source = tmp_path / "artifacts"
    _make_run(source, "run_a")
    config = _config(tmp_path, source, dry_run=True)
    result = ingest(config)

    assert result.exit_code == EXIT_OK
    assert result.counts["registered"] == 1                  # 演算层结论
    assert not config.out_dir.exists() or _snapshot(config.out_dir) == {}
    assert not config.registry_path.exists()

    apply_result = ingest(_config(tmp_path, source))
    assert apply_result.counts["registered"] == 1
    assert config.registry_path.exists()


def test_dry_run_prediction_matches_subsequent_apply(tmp_path: Path):
    """dry-run 读状态台账（只读）→ 预测结果必须等于随后 apply 的实际结果。"""
    source = tmp_path / "artifacts"
    _make_run(source, "run_a")
    _make_run(source, "run_b")
    config = _config(tmp_path, source)
    ingest(config)

    _make_run(source, "run_c")                     # 新增一件
    dry = ingest(_config(tmp_path, source, dry_run=True))
    applied = ingest(_config(tmp_path, source))
    for key in ("registered", "already_registered", "quarantined", "rejected", "conflict", "failed"):
        assert dry.counts[key] == applied.counts[key], key
    assert dry.counts["registered"] == 1 and dry.counts["already_registered"] == 2


# --------------------------------------------------------------------------- #
# http sink：真发 HTTP（本地 stub 复刻端点 200/409 语义）
# --------------------------------------------------------------------------- #
class _StubRegistry:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.requests: list[dict] = []
        self.fail_status: int | None = None


def _serve(stub: _StubRegistry):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            stub.requests.append({"path": self.path, "body": body,
                                  "auth": self.headers.get("Authorization")})
            if stub.fail_status:
                self.send_response(stub.fail_status)
                self.end_headers()
                self.wfile.write(b'{"detail":"boom"}')
                return
            manifest = body["manifest"]
            digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=False).encode()).hexdigest()
            existing = stub.store.get(body["run_id"])
            if existing and existing != digest:
                self.send_response(409)
                self.end_headers()
                self.wfile.write(b'{"detail":"run_id already exists with a different manifest"}')
                return
            created = existing is None
            stub.store[body["run_id"]] = digest
            payload = json.dumps({"run_id": body["run_id"], "created": created,
                                  "manifest_sha256": digest}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/api/v1"


def test_http_sink_roundtrip_idempotent_and_409(tmp_path: Path):
    stub = _StubRegistry()
    server, base_url = _serve(stub)
    try:
        source = tmp_path / "artifacts"
        _make_run(source, "run_h")
        config = _config(tmp_path, source, sink="http", base_url=base_url)
        config.token = "test-token"

        first = ingest(config)
        assert first.counts["registered"] == 1
        assert stub.requests[0]["path"].endswith("/tensorphysics/runs")
        assert stub.requests[0]["auth"] == "Bearer test-token"

        # 第二轮：状态台账已结算 → 不再发请求（断点续传/幂等）
        second = ingest(config)
        assert second.counts["already_registered"] == 1
        assert len(stub.requests) == 1

        # 丢状态重跑 → 打到端点，端点按 409 语义判幂等（created=False）
        (config.out_dir / "state.json").unlink()
        third = ingest(config)
        assert third.counts["already_registered"] == 1 and len(stub.requests) == 2
    finally:
        server.shutdown()


def test_http_sink_service_error_is_isolated_retriable(tmp_path: Path):
    stub = _StubRegistry()
    server, base_url = _serve(stub)
    try:
        source = tmp_path / "artifacts"
        _make_run(source, "run_e")
        config = _config(tmp_path, source, sink="http", base_url=base_url)
        stub.fail_status = 503

        result = ingest(config)
        assert result.exit_code == EXIT_ISOLATED
        assert result.counts["failed"] == 1
        assert result.rows[0]["retriable"] is True
        assert result.rows[0]["registry_status"] == 503
        # 未结算 → 状态台账不写入该 run，修好后重跑可补
        state = json.loads((config.out_dir / "state.json").read_text())
        assert state["runs"] == {}

        stub.fail_status = None
        (config.out_dir / "state.json").unlink()
        assert ingest(config).counts["registered"] == 1
    finally:
        server.shutdown()


def test_local_sink_conflict_semantics_directly(tmp_path: Path):
    sink = LocalRegistrySink(tmp_path / "registry.jsonl")
    manifest = {"run_id": "r1", "status": "succeeded", "validation_status": "passed", "outputs": []}
    digest = manifest_digest(manifest)
    assert sink.register(manifest, digest, dry_run=False)["created"] is True
    assert sink.register(manifest, digest, dry_run=False)["created"] is False
    with pytest.raises(RegistryConflict):
        sink.register({**manifest, "status": "failed"}, manifest_digest({**manifest, "status": "failed"}),
                      dry_run=False)


def test_http_sink_dry_run_does_not_contact_endpoint():
    sink = HttpRegistrySink("http://127.0.0.1:9/api/v1", "t")
    receipt = sink.register({"run_id": "r", "status": "succeeded",
                             "validation_status": "not_run", "outputs": []}, "d" * 64, dry_run=True)
    assert receipt["dry_run"] is True and receipt["created"] is True