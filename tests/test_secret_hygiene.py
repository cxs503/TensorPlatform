"""秘密卫生门（引擎 / 三出口 / 脱敏 / 标定）— pytest 套件。

纪律
----
* 测试里**不出现**任何完整凭证字面量：合成值一律由片段拼接生成（避免测试源成为
  泄漏渠道，也避免自称"不会写死样例"的模块被自己的门拦下）。
* 断言只比对**脱敏标记 + 计数**，不回显明文。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.secret_hygiene.calibrate import build_corpus, calibrate
from tools.secret_hygiene.engine import (entropy_ok, load_config, scan_payload, scan_text,
                                         shannon_entropy)
from tools.secret_hygiene.outlets import (SecretBlocked, artifact_outlet, precommit_outlet,
                                          probe_plaintext_residual, report_outlet)
from tools.secret_hygiene.redact import redact_payload, redact_text

RULES = "config/secret_hygiene/rules.json"


@pytest.fixture(scope="module")
def cfg():
    return load_config(RULES, root=ROOT)


def _token(n: int = 32) -> str:
    """确定性合成令牌（混合大小写 + 数字，避开仓内任何真实值）。"""
    alphabet = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    # 用置换序取字符：连续字母表序列会被行级豁免（(?i)abcdefghijklmnopqrstuvwxyz）吃掉，
    # 也会让"随机串"退化成可预测样例。
    return "".join(alphabet[(i * 7 + 3) % len(alphabet)] for i in range(n))


def _pem(label: str = "RSA PRIVATE KEY") -> str:
    head = "-----" + "BEGIN "
    return f"{head}{label}-----\n{_token(32)}\n-----END {label}-----"


# --------------------------------------------------------------------- 引擎


def test_config_loads_rules_and_reports_public_summary(cfg):
    summary = cfg.to_public_dict()
    assert "private_key_block" in summary["rule_ids"]
    assert summary["entropy_enabled"] is True
    # 公开摘要里不含正则原文（可安全归档）
    assert all(isinstance(r, str) for r in summary["rule_ids"])


def test_scan_detects_pem_block_without_exposing_value(cfg):
    hits = scan_text(_pem(), cfg, path="k.pem")
    assert [h.rule_id for h in hits] == ["private_key_block"]
    payload = hits[0].to_dict()
    assert payload["redacted"].startswith("<REDACTED:private_key_block:")
    assert "BEGIN" not in json.dumps(payload)  # 判决 JSON 不含明文片段


def test_entropy_gate_rejects_lowercase_hex_digest(cfg):
    digest = hashlib.sha256(b"fixture").hexdigest()
    assert scan_text(f"checksum {digest}", cfg) == []
    assert scan_text(digest, cfg) == []  # 纯 hex 熵够但无大小写混合 → 不算秘密
    ok, bits = entropy_ok(_token(44), cfg.entropy)
    assert ok and bits >= cfg.entropy["min_entropy_bits_per_char"]
    assert shannon_entropy("aaaa") == pytest.approx(0.0)


def test_allow_line_patterns_suppress_placeholders(cfg):
    assert scan_text('password = "<your-password>"', cfg) == []
    assert scan_text("secret_key: ${PLATFORM_SECRET_KEY}", cfg) == []
    assert scan_text('api_key = os.environ["PLATFORM_API_KEY"]', cfg) == []


def test_known_value_registry_matches_by_digest(tmp_path: Path):
    value = "SYNTHETIC" + "-KNOWN-VALUE-NOT-A-REAL-CREDENTIAL"
    reg = tmp_path / "known.json"
    reg.write_text(json.dumps({"digests": [hashlib.sha256(value.encode()).hexdigest()]}),
                   encoding="utf-8")
    cfg = load_config(RULES, root=ROOT, known_registry=reg)
    hits = scan_text(f"value = {value}", cfg)
    assert [h.rule_id for h in hits] == ["known_value"]


def test_scan_payload_walks_all_string_leaves(cfg):
    payload = {"title": "ok", "figures": [{"name": "f", "png_base64": "iVBORw0KGgo="}],
               "summary": "token=" + _token(32)}
    hits = scan_payload(payload, cfg)
    assert {h.path for h in hits} == {"summary"}


# ---------------------------------------------------------------- 脱敏改写


def test_redaction_marker_format_and_idempotency(cfg):
    text = "line1\n" + "API_KEY=" + _token(32) + "\n"
    hits = scan_text(text, cfg)
    once = redact_text(text, hits)
    assert "<REDACTED:" in once
    twice = redact_text(once, scan_text(once, cfg))
    assert once == twice
    assert _token(32) not in once


def test_redact_payload_keeps_structure_and_removes_plaintext(cfg):
    payload = {"summary": "auth_token: " + _token(24), "figures": [{"name": "f1"}]}
    hits = scan_payload(payload, cfg)
    sanitized = redact_payload(payload, hits)
    assert sanitized["figures"] == [{"name": "f1"}]
    assert "<REDACTED:" in sanitized["summary"]
    assert _token(24) not in json.dumps(sanitized)


# ------------------------------------------------------------------ 三出口


def test_precommit_outlet_blocks_and_redacts(cfg):
    content = "config\npassword = '" + _token(20) + "'\n"
    verdict = precommit_outlet([("svc/settings.py", content)], cfg, mode="block")
    assert verdict.allowed is False
    assert verdict.hits
    redacted = precommit_outlet([("svc/settings.py", content)], cfg, mode="redact")
    assert redacted.allowed is True
    assert probe_plaintext_residual(redacted, [_token(20)]) == 0
    assert not scan_text(redacted.sanitized["svc/settings.py"], cfg)


def test_report_outlet_blocks_secret_in_figures_caption(cfg):
    payload = {"title": "t", "figures": [{"name": "f",
                                          "caption": "api_key=" + _token(32)}]}
    verdict = report_outlet(payload, cfg)
    assert verdict.blocked
    assert verdict.hits[0]["path"].startswith("figures[0]")
    redacted = report_outlet(payload, cfg, mode="redact")
    assert redacted.allowed


def test_artifact_outlet_blocks_write_and_writes_redacted_on_redact_mode(cfg, tmp_path: Path):
    target = tmp_path / "out" / "artifact.txt"
    with pytest.raises(SecretBlocked):
        artifact_outlet(target, "x\nauth_token: " + _token(28), cfg, mode="block")
    assert not target.exists()
    verdict = artifact_outlet(target, "x\nauth_token: " + _token(28), cfg, mode="redact")
    assert verdict.allowed
    written = target.read_text(encoding="utf-8")
    assert "<REDACTED:" in written and _token(28) not in written


def test_artifact_outlet_refuses_path_escape(cfg, tmp_path: Path):
    with pytest.raises(PermissionError):
        artifact_outlet(Path("../escape.txt"), "harmless", cfg, mode="block",
                        root=tmp_path)


# -------------------------------------------------------------------- 标定


def test_corpus_has_twenty_secrets_and_twenty_benign_without_literal_credentials():
    corpus = build_corpus()
    assert len(corpus["secrets"]) == 20
    assert len(corpus["benign"]) == 20
    # 语料由片段拼接生成：源码里不出现完整 PEM 头（自指误报护栏）
    source = (ROOT / "tools" / "secret_hygiene" / "calibrate.py").read_text(encoding="utf-8")
    assert "BEGIN RSA PRIVATE KEY-----\n" not in source


def test_calibration_meets_recall_fpr_outlet_and_idempotency_targets():
    payload = calibrate(ROOT)
    assert payload["detection"]["recall"] >= 0.95
    assert payload["false_positives"]["fpr"] <= 0.05
    assert payload["outlets"]["effective"] == 3
    assert payload["redaction"]["C8_idempotent"] is True
    assert payload["known_value_path"]["detected"] is True


def test_calibration_is_reproducible_for_same_seed():
    first = calibrate(ROOT, seed=7)
    second = calibrate(ROOT, seed=7)
    assert first["detection"] == second["detection"]
    assert first["false_positives"] == second["false_positives"]


def test_own_modules_pass_the_gate(cfg):
    """自洽性：新模块自身的源码不得被自己的门判红（否则门无法进 CI）。"""
    offenders = {}
    for rel in ("tools/secret_hygiene/engine.py", "tools/secret_hygiene/outlets.py",
                "tools/secret_hygiene/redact.py", "tools/secret_hygiene/calibrate.py",
                "tools/secret_hygiene/cli.py"):
        hits = scan_text((ROOT / rel).read_text(encoding="utf-8"), cfg, path=rel)
        if hits:
            offenders[rel] = [h.to_dict() for h in hits[:2]]
    assert offenders == {}

# ------------------------------------------------------- 三出口的真实挂接


def _init_repo(path: Path) -> Path:
    import subprocess  # nosec B404 - 夹具要造一个真 git 仓库

    path.mkdir(parents=True, exist_ok=True)
    for args in (("init", "-q"),):
        subprocess.run(["git", *args], cwd=str(path), check=True, capture_output=True)
    start = path / "notes.txt"
    start.write_text("harmless\n", encoding="utf-8")
    for args in (("add", "notes.txt"),
                 ("-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture",
                  "commit", "-q", "-m", "base")):
        subprocess.run(["git", *args], cwd=str(path), check=True, capture_output=True)
    return path


def test_precommit_outlet_hooked_to_git_index(cfg, tmp_path: Path):
    from tools.secret_hygiene.integration import scan_staged

    repo = _init_repo(tmp_path / "repo")
    (repo / "settings.py").write_text("PLATFORM_API_KEY=" + _token(32) + "\n", encoding="utf-8")
    import subprocess  # nosec B404
    subprocess.run(["git", "add", "settings.py"], cwd=str(repo), check=True,
                   capture_output=True)
    verdict = scan_staged(repo, cfg)
    assert verdict.blocked and verdict.hits
    assert verdict.hits[0]["path"] == "settings.py"


def test_precommit_outlet_allows_clean_index(cfg, tmp_path: Path):
    from tools.secret_hygiene.integration import scan_staged

    repo = _init_repo(tmp_path / "repo")
    (repo / "readme.txt").write_text("plain documentation\n", encoding="utf-8")
    import subprocess  # nosec B404
    subprocess.run(["git", "add", "readme.txt"], cwd=str(repo), check=True,
                   capture_output=True)
    verdict = scan_staged(repo, cfg)
    assert verdict.allowed and not verdict.hits


def test_report_guard_blocks_then_sanitizes(cfg):
    from tools.secret_hygiene.integration import ReportGuard
    from tools.secret_hygiene.outlets import SecretBlocked

    guard = ReportGuard(cfg, mode="block")
    payload = {"title": "t", "summary": "password: " + _token(24)}
    with pytest.raises(SecretBlocked):
        guard.assert_publishable(payload)
    clean = guard.sanitize(payload)
    assert "<REDACTED:" in clean["summary"]
    assert _token(24) not in json.dumps(clean)
    assert guard.assert_publishable({"title": "ok"}) == {"title": "ok"}


def test_artifact_writer_integration_records_audit(cfg, tmp_path: Path):
    from tools.secret_hygiene.integration import write_artifact

    target = tmp_path / "out" / "evidence.json"
    audit = tmp_path / "audit.jsonl"
    verdict = write_artifact(target, '{"token": "' + _token(28) + '"}', cfg=cfg,
                             root=tmp_path, mode="redact", audit_path=audit)
    assert verdict.allowed
    assert audit.exists() and json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert _token(28) not in target.read_text(encoding="utf-8")


# ------------------------------------------- 回归：图件 base64 不得拦整份报告


def test_report_gate_allows_binary_figure_payload_but_flags_caption(cfg):
    """实测缺陷回归：平台报告规范载荷含高熵 png base64，不得因此被拦（豁免要留痕）。"""
    import base64
    import os as _os

    png_like = base64.b64encode(_os.urandom(4096)).decode()
    payload = {"title": "t",
               "figures": [{"name": "f1", "png_base64": png_like,
                            "caption": "api_key=" + _token(32)}]}
    suppressed: list[str] = []
    hits = scan_payload(payload, cfg, suppressed=suppressed)
    assert [h.path for h in hits] == ["figures[0].caption"]
    assert "figures[0].png_base64" in suppressed  # 豁免留痕，不静默
    clean = {"title": "t", "figures": [{"name": "f1", "png_base64": png_like}]}
    assert report_outlet(clean, cfg, mode="block").allowed is True
