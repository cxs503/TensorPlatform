"""P96-N8 验收：客户端载荷工装（独占文件名 + 自证 + 提交前复算）+ 路径约定 lint。

判据
----
* ``write_report_payload`` 返回自证 ``{path, mission_id, sha256, bytes, verified}``，
  且 sha256 等于磁盘回读值（写完就验）；
* 文件名不含 mission_id 令牌 → ``PayloadNameNotExclusive``（拒绝写）；
* payload 内嵌 mission_id 与入参不符 → ``PayloadMissionMismatch``（写错单在写盘前拦下）；
* 目标已存在 → ``PayloadExists``（绝不覆盖他人成果；``replace=True`` 只刷新本单）；
* 提交前 ``preflight`` 现场重算，文件被篡改/截断 → ``PayloadDrift``；
* 8 线程并发写 8 个不同单到**同一目录** → 8 个独立文件、内容零串号（客户端侧 0/8）；
* 客户端配图判据与服务端**同一份实现**（``FigureRejected`` 是同一个类对象）：
  JPEG/WebP → ``REPORT_FIGURE_NOT_PNG`` 且给出期望魔数，截断 PNG → ``REPORT_FIGURE_TRUNCATED``。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from services import report_ownership as shared
from tools import report_payload as rp
from tools.audit import report_path_convention_lint as lint
from tests.report_gate_support import JPEG_BYTES, TRUNCATED_PNG, WEBP_BYTES, b64, make_png

MISSION = 5617

#: 违规字面量在测试里拼出来：本文件自己不能成为 lint 的存量欠账（自证干净）。
BARE_TMP = "/tmp/" + "report.json"


def _payload(mission_id: int = MISSION, **extra) -> dict:
    base = {"mission_id": mission_id, "title": f"report {mission_id}",
            "markdown": "## body\n", "tags": ["gate"]}
    base.update(extra)
    return base


# ── 独占写盘 + 自证 ────────────────────────────────────────────────────────
def test_write_returns_self_proof_matching_disk(tmp_path):
    path = tmp_path / f"report_m{MISSION}.json"
    proof = rp.write_report_payload(path, MISSION, _payload())
    assert proof["mission_id"] == MISSION
    assert proof["path"] == str(path)
    assert proof["bytes"] == path.stat().st_size
    assert proof["verified"] is True
    import hashlib

    assert proof["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert json.loads(path.read_text(encoding="utf-8"))["mission_id"] == MISSION


def test_name_must_carry_mission_token(tmp_path):
    with pytest.raises(rp.PayloadNameNotExclusive):
        rp.write_report_payload(tmp_path / "report.json", MISSION, _payload())


def test_payload_mission_mismatch_rejected_before_write(tmp_path):
    target = tmp_path / f"report_m{MISSION}.json"
    with pytest.raises(rp.PayloadMissionMismatch):
        rp.write_report_payload(target, 9999, _payload(MISSION))
    assert not target.exists(), "写错单必须在写盘前拦下（不留半成品）"


def test_missing_mission_field_rejected(tmp_path):
    payload = _payload()
    payload.pop("mission_id")
    with pytest.raises(rp.PayloadMissionMismatch):
        rp.write_report_payload(tmp_path / f"report_m{MISSION}.json", MISSION, payload)


def test_never_overwrites_other_mission_file(tmp_path):
    target = tmp_path / f"report_m{MISSION}.json"
    rp.write_report_payload(target, MISSION, _payload(marker="first"))
    with pytest.raises(rp.PayloadExists):
        rp.write_report_payload(target, MISSION, _payload(marker="second"))
    assert json.loads(target.read_text(encoding="utf-8"))["marker"] == "first"
    # 显式 replace=True 只允许刷新本单自己的文件
    proof = rp.write_report_payload(target, MISSION, _payload(marker="second"), replace=True)
    assert proof["verified"] and json.loads(target.read_text(encoding="utf-8"))["marker"] == "second"


# ── 提交前本地复算 ────────────────────────────────────────────────────────
def test_preflight_recomputes_proof(tmp_path):
    target = tmp_path / f"report_m{MISSION}.json"
    proof = rp.write_report_payload(target, MISSION, _payload())
    got = rp.preflight(target, MISSION, proof)
    assert got["payload"]["mission_id"] == MISSION
    assert got["proof"]["sha256"] == proof["sha256"]
    assert got["proof"]["verified"] is True


def test_preflight_detects_tampering(tmp_path):
    target = tmp_path / f"report_m{MISSION}.json"
    proof = rp.write_report_payload(target, MISSION, _payload())
    target.write_text(json.dumps(_payload(tampered=True)), encoding="utf-8")
    with pytest.raises(rp.PayloadDrift):
        rp.preflight(target, MISSION, proof)


def test_preflight_rejects_wrong_mission(tmp_path):
    target = tmp_path / f"report_m{MISSION}.json"
    proof = rp.write_report_payload(target, MISSION, _payload())
    with pytest.raises(rp.PayloadMissionMismatch):
        rp.preflight(target, 9999, proof)


def test_preflight_missing_file(tmp_path):
    with pytest.raises(rp.PayloadDrift):
        rp.preflight(tmp_path / f"report_m{MISSION}.json", MISSION)


# ── 客户端侧 0/8：8 线程写同一目录 ────────────────────────────────────────
def test_eight_threads_same_dir_zero_overwrite(tmp_path):
    def worker(idx: int) -> dict:
        mid = 6000 + idx
        proof = rp.write_report_payload(tmp_path / f"report_m{mid}.json", mid,
                                        _payload(mid, idx=idx))
        rp.preflight(proof["path"], mid, proof)
        return {"mission_id": mid, "sha256": proof["sha256"], "path": proof["path"]}

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(worker, range(8)))

    assert len({o["mission_id"] for o in out}) == 8
    assert len({o["sha256"] for o in out}) == 8, "8 份载荷摘要必须互不相同（无覆盖）"
    for o in out:
        doc = json.loads(open(o["path"], encoding="utf-8").read())
        assert doc["mission_id"] == o["mission_id"], "文件内容与归属不一致 = 串号"


# ── 客户端配图判据 = 服务端同一份实现 ──────────────────────────────────────
@pytest.mark.parametrize("raw,expected_code", [
    (JPEG_BYTES, shared.CODE_FIGURE_NOT_PNG),
    (WEBP_BYTES, shared.CODE_FIGURE_NOT_PNG),
    (TRUNCATED_PNG, shared.CODE_FIGURE_TRUNCATED),
])
def test_client_figure_gate_uses_server_implementation(raw, expected_code):
    with pytest.raises(shared.FigureRejected) as excinfo:
        rp.validate_figure_png("fig1", b64(raw))
    assert excinfo.value.code == expected_code
    assert shared.PNG_MAGIC_HEX in excinfo.value.detail
    assert excinfo.value.status_code == 422


def test_client_figure_gate_accepts_valid_png():
    raw = make_png(seed=3)
    assert rp.validate_figure_png("fig1", b64(raw)) == raw


def test_client_and_server_share_one_module(tmp_path):
    """工具加载的模块必须与生产 import 的是同一份（否则「本地绿/服务端红」）。"""
    mod = rp.open_ownership_module()
    assert mod.PNG_MAGIC == shared.PNG_MAGIC
    assert mod.FigureRejected is shared.FigureRejected


# ── 路径约定 lint ─────────────────────────────────────────────────────────
def test_lint_flags_bare_tmp_report_json(tmp_path):
    bad = tmp_path / "dispatch.py"
    bad.write_text(f'OUT = "{BARE_TMP}"\nopen(OUT, "w").write(body)\n', encoding="utf-8")
    res = lint.scan([bad])
    assert res["counts"] == {bad.as_posix(): 1}, res["counts"]


def test_lint_allows_mission_tagged_path(tmp_path):
    good = tmp_path / "dispatch.py"
    good.write_text('OUT = "/tmp/report_m5617.json"\n'
                    'ALT = "/tmp/report_5617.json"\n', encoding="utf-8")
    assert lint.scan([good])["counts"] == {}


def test_lint_negative_control_new_violation_is_red(tmp_path):
    """负控：基线对账必须对新文件亮红灯（否则 lint 是恒真判据）。"""
    new_file = tmp_path / "new_dispatch.py"
    new_file.write_text(f'p = "{BARE_TMP}"\n', encoding="utf-8")
    res = lint.scan([new_file])
    cmp = lint.compare(res["counts"], {})
    assert cmp["ok"] is False
    assert cmp["regressions"][0]["delta"] == 1


def test_repo_has_no_new_bare_paths_beyond_baseline():
    res = lint.scan(lint.DEFAULT_ROOTS)
    baseline = lint.load_baseline()
    assert baseline, "config/report_path_baseline.json 缺失：存量欠账未登记"
    cmp = lint.compare(res["counts"], baseline)
    assert cmp["ok"], "新增裸 " + BARE_TMP + f"：{cmp['regressions']}"