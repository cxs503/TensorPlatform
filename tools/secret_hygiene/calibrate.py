"""注入标定：20 个合成秘密 + 20 个良性样本，量化检出率/误报率与三出口有效性。

为什么合成而不放样例
--------------------
仓里**不放**任何真实凭证，也不放"看起来像真凭证"的字面量：那样做会让标定语料本身
变成泄漏渠道。语料全部由字符族随机拼接生成（固定种子 → 可复现），只在内存里存在。

标定口径（与战役判据 C5~C8 一一对应）
-------------------------------------
* C5 检出率 = 命中 ≥1 条规则的合成秘密数 / 20（目标 ≥95%）
* C6 误报率 = 被判红的良性样本数 / 20（目标 ≤5%）
* C7 三出口有效性 = 秘密植入各出口输入后"被拦 + 脱敏后无明文残留"的出口数 / 3
* C8 脱敏幂等 = ``redact(redact(x)) == redact(x)`` 的样本数 / 样本数
* 附加：已知值通路（按 sha256 摘要精确命中，验证"模式化规则之外的凭证"也能被发现）
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .engine import HygieneConfig, load_config, scan_text, shannon_entropy
from .outlets import (artifact_outlet, precommit_outlet, probe_plaintext_residual,
                      report_outlet)
from .redact import redact_text

SCHEMA = "secret_hygiene_calibration/1.0"
_UP = string.ascii_uppercase
_LOW = string.ascii_lowercase
_DIGITS = string.digits
_ALNUM = string.ascii_letters + string.digits
_URLSAFE = string.ascii_letters + string.digits + "-_"


@dataclass(frozen=True)
class Sample:
    sample_id: str
    family: str
    text: str
    expected_rules: Sequence[str] = ()
    benign: bool = False


def _rand(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def _b64url(rng: random.Random, n: int) -> str:
    return _rand(rng, _URLSAFE, n)


def build_corpus(seed: int = 20261111) -> Dict[str, List[Sample]]:
    """生成标定语料：20 个合成秘密 + 20 个良性样本（固定种子可复现）。"""
    rng = random.Random(seed)
    secrets: List[Sample] = []

    body = _b64url(rng, 32)
    # PEM 标记由**拼接**生成：语料生成器源码里因此不出现完整私钥块字面量，
    # 否则门会把自己的标定代码判成泄漏（自指误报）。
    begin, dash = "-----" + "BEGIN ", "-----"
    for label, sample_id in (("RSA PRIVATE KEY", "sec_pem_rsa"),
                             ("EC PRIVATE KEY", "sec_pem_ec"),
                             ("OPENSSH PRIVATE KEY", "sec_pem_openssh")):
        text = f"{begin}{label}{dash}\n{body}\n-----END {label}{dash}"
        secrets.append(Sample(sample_id, "pem", text, ("private_key_block",)))

    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": _rand(rng, _ALNUM, 8)}).encode()).decode()
    signature = _b64url(rng, 24)
    secrets.append(Sample("sec_jwt", "jwt", f"Authorization: {header}.{payload}.{signature}", ("jwt", "credential_assignment")))

    pw = _rand(rng, _ALNUM, 14)
    secrets.append(Sample("sec_db_postgres", "db_url", f"postgresql://platform:" + pw + "@db.prod.internal:5432/platform", ("db_url_credentials",)))
    secrets.append(Sample("sec_db_mysql", "db_url", f"mysql://root:" + pw + "@10.0.0.7:3306/app", ("db_url_credentials",)))
    secrets.append(Sample("sec_db_mongodb", "db_url", f"mongodb+srv://svc:" + pw + "@cluster0.prod.internal/app", ("db_url_credentials",)))
    secrets.append(Sample("sec_db_redis", "db_url", f"redis://cache:" + pw + "@redis:6379/0", ("db_url_credentials",)))

    bearer = _rand(rng, _ALNUM, 36)
    secrets.append(Sample("sec_bearer", "bearer", f"Authorization: Bearer {bearer}", ("bearer_token",)))

    secrets.append(Sample("sec_aws_key", "cloud", f"AKIA{_rand(rng, _UP + _DIGITS, 16)}", ("cloud_access_key_id",)))
    secrets.append(Sample("sec_github", "prefixed", f"ghp_{_rand(rng, _ALNUM, 36)}", ("prefixed_api_token",)))
    secrets.append(Sample("sec_gitlab", "prefixed", f"glpat-{_rand(rng, _ALNUM, 20)}", ("prefixed_api_token",)))
    secrets.append(Sample("sec_slack", "prefixed", f"xoxb-{_rand(rng, _DIGITS + _LOW, 12)}-{_rand(rng, _ALNUM, 12)}", ("prefixed_api_token",)))
    secrets.append(Sample("sec_google", "prefixed", f"AIza{_rand(rng, _ALNUM + '-_', 35)}", ("prefixed_api_token",)))
    secrets.append(Sample("sec_stripe", "prefixed", f"sk_live_{_rand(rng, _ALNUM, 24)}", ("prefixed_api_token",)))

    secrets.append(Sample("sec_env_apikey", "assignment", f"PLATFORM_API_KEY={_rand(rng, _ALNUM, 32)}", ("credential_assignment",)))
    secrets.append(Sample("sec_env_password", "assignment", f"password = '{_rand(rng, _ALNUM, 20)}'", ("credential_assignment",)))
    secrets.append(Sample("sec_env_client_secret", "assignment", f"CLIENT_SECRET={_rand(rng, _ALNUM, 28)}", ("credential_assignment",)))
    secrets.append(Sample("sec_yaml_token", "assignment", f"auth_token: {_rand(rng, _ALNUM, 24)}", ("credential_assignment",)))

    blob = _rand(rng, _LOW, 20) + _rand(rng, _UP, 12) + _rand(rng, _DIGITS, 12)
    secrets.append(Sample("sec_entropy_blob", "entropy", f"session_blob = {blob}", ("high_entropy_blob", "credential_assignment")))

    benign: List[Sample] = []
    benign.append(Sample("ben_sha256", "digest", f"artifact sha256: {_rand(rng, '0123456789abcdef', 64)}", benign=True))
    benign.append(Sample("ben_uuid", "id", "request_id=" + "-".join([_rand(rng, '0123456789abcdef', 8), _rand(rng, '0123456789abcdef', 4), "4" + _rand(rng, '0123456789abcdef', 3), "a" + _rand(rng, '0123456789abcdef', 3), _rand(rng, '0123456789abcdef', 12)]), benign=True))
    benign.append(Sample("ben_png_data_uri", "binary", "data:image/png;base64," + _rand(rng, _ALNUM + '+/=', 96), benign=True))
    benign.append(Sample("ben_env_ref", "reference", "api_key = os.environ['PLATFORM_API_KEY']", benign=True))
    benign.append(Sample("ben_placeholder", "placeholder", "password = <your-password>", benign=True))
    benign.append(Sample("ben_masked", "masked", "password = ********", benign=True))
    benign.append(Sample("ben_aws_example", "example", "aws_access_key_id = 'AKIAIOSFODNN7EXAMPLE'", benign=True))
    benign.append(Sample("ben_camel_identifier", "identifier", "internalSessionTokenForAuditTrailEnabled = True", benign=True))
    benign.append(Sample("ben_path", "path", "artifacts/capability_evidence/geometry.generate/acceptance.json", benign=True))
    benign.append(Sample("ben_image_digest", "digest", "image: registry.example.invalid/api@sha256:" + _rand(rng, '0123456789abcdef', 64), benign=True))
    benign.append(Sample("ben_url_no_creds", "url", "https://api.example.invalid/v1/calculations/resistance", benign=True))
    benign.append(Sample("ben_secret_key_env", "reference", "secret_key: ${PLATFORM_SECRET_KEY}", benign=True))
    benign.append(Sample("ben_jinja", "template", "token: {{ token }}", benign=True))
    benign.append(Sample("ben_log_line", "log", "2026-11-11 INFO connected to db:5432 latency=4ms", benign=True))
    benign.append(Sample("ben_code_accessor", "code", "def get_token(self):\n    return self._token", benign=True))
    benign.append(Sample("ben_prose", "prose", "Rotate your access token every 90 days; see runbook.", benign=True))
    benign.append(Sample("ben_table", "doc", "| password | rotated monthly | owner |", benign=True))
    benign.append(Sample("ben_merge_sha", "vcs", f"Merge commit {_rand(rng, '0123456789abcdef', 40)}", benign=True))
    benign.append(Sample("ben_requirement", "dependency", "requests==2.31.0", benign=True))
    benign.append(Sample("ben_arn", "cloud_ref", "arn:aws:iam::123456789012:role/PlatformAgentRole", benign=True))
    return {"secrets": secrets, "benign": benign}


def _first_matching_rule(text: str, cfg: HygieneConfig) -> List[str]:
    return [hit.rule_id for hit in scan_text(text, cfg)]


def calibrate(root: str | Path = ".", *, cfg: Optional[HygieneConfig] = None,
              rules_path: str = "config/secret_hygiene/rules.json",
              seed: int = 20261111) -> Dict[str, Any]:
    """跑完整标定，返回可归档的指标字典（不含任何明文）。"""
    root = Path(root)
    cfg = cfg or load_config(rules_path, root=root)
    corpus = build_corpus(seed=seed)

    detected: List[str] = []
    missed: List[str] = []
    rule_ok = 0
    for sample in corpus["secrets"]:
        found = _first_matching_rule(sample.text, cfg)
        if found:
            detected.append(sample.sample_id)
            if set(found) & set(sample.expected_rules):
                rule_ok += 1
        else:
            missed.append(sample.sample_id)

    false_positive: List[str] = []
    for sample in corpus["benign"]:
        if _first_matching_rule(sample.text, cfg):
            false_positive.append(sample.sample_id)

    total_secrets = len(corpus["secrets"])
    total_benign = len(corpus["benign"])
    recall = len(detected) / total_secrets
    fpr = len(false_positive) / total_benign

    # ---- C7 三出口有效性 -------------------------------------------------
    secret_text = "PLATFORM_API_KEY=" + _rand(random.Random(seed + 7), _ALNUM, 32)
    outlet_rows: List[Dict[str, Any]] = []

    pre = precommit_outlet([("src/app.py", f"# config\n{secret_text}\n")], cfg, mode="redact")
    outlet_rows.append(_outlet_row("precommit", pre, [secret_text.split("=", 1)[1]],
                                  rescan_clean=lambda: not scan_text(
                                      (pre.sanitized or {}).get("src/app.py", ""), cfg)))

    payload = {"title": "demo", "summary": f"key={secret_text}",
               "markdown": "text", "figures": [{"name": "f1", "png_base64": "iVBORw0KGgo="}]}
    rep = report_outlet(payload, cfg, mode="redact")
    outlet_rows.append(_outlet_row("report", rep, [secret_text.split("=", 1)[1]],
                                  rescan_clean=lambda: not scan_text(
                                      json.dumps(rep.sanitized or {}, ensure_ascii=False), cfg)))

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "artifact.txt"
        art = artifact_outlet(target, f"line1\n{secret_text}\n", cfg, mode="redact")
        written = target.read_text(encoding="utf-8") if target.exists() else ""
        outlet_rows.append(_outlet_row("artifact", art, [secret_text.split("=", 1)[1]],
                                       rescan_clean=lambda: not scan_text(written, cfg)))

    blocked_outlets = sum(1 for row in outlet_rows if row["blocked_and_redacted"])

    # ---- C8 脱敏幂等 -----------------------------------------------------
    idempotent = 0
    checked = 0
    for sample in corpus["secrets"]:
        hits = scan_text(sample.text, cfg)
        once = redact_text(sample.text, hits)
        twice = redact_text(once, scan_text(once, cfg))
        checked += 1
        if once == twice:
            idempotent += 1

    # ---- 已知值通路（摘要精确匹配；临时注册表注入） ----------------------
    # 已知值明文由拼接构造：注册表按摘要登记，仓内不出现完整字面量
    known_value = "SYNTHETIC" + "-KNOWN-VALUE-NOT-A-REAL-CREDENTIAL"
    with tempfile.TemporaryDirectory() as tmp:
        registry = Path(tmp) / "known.json"
        registry.write_text(json.dumps({"schema": "secret_hygiene_known_values/1.0",
                                        "digests": [hashlib.sha256(
                                            known_value.encode()).hexdigest()]}), encoding="utf-8")
        known_cfg = load_config(rules_path, root=root, known_registry=registry)
        known_hits = scan_text(f"value = {known_value}", known_cfg)
    known_detected = any(hit.rule_id == "known_value" for hit in known_hits)

    entropy_of_blob = shannon_entropy(next(
        s.text.split("= ", 1)[1] for s in corpus["secrets"] if s.sample_id == "sec_entropy_blob"))

    return {
        "schema": SCHEMA,
        "seed": seed,
        "config": cfg.to_public_dict(),
        "corpus": {"secrets": total_secrets, "benign": total_benign},
        "detection": {
            "detected": len(detected),
            "missed": missed,
            "recall": round(recall, 4),
            "family_match": rule_ok,
            "C5_recall_ge_95": recall >= 0.95,
        },
        "false_positives": {
            "flagged": len(false_positive),
            "samples": false_positive,
            "fpr": round(fpr, 4),
            "C6_fpr_le_5": fpr <= 0.05,
        },
        "outlets": {"rows": outlet_rows, "effective": blocked_outlets,
                    "C7_all_three_effective": blocked_outlets == 3},
        "redaction": {"checked": checked, "idempotent": idempotent,
                      "C8_idempotent": idempotent == checked},
        "known_value_path": {"detected": known_detected},
        "entropy_reference": {"synthetic_blob_bits_per_char": round(entropy_of_blob, 3)}}


def _outlet_row(name: str, verdict, tokens: Sequence[str], *, rescan_clean) -> Dict[str, Any]:
    residual = probe_plaintext_residual(verdict, tokens)
    redacted = bool(verdict.sanitized is not None)
    clean = bool(rescan_clean())
    return {
        "outlet": name,
        "would_block_in_block_mode": bool(verdict.hits),
        "hits": len(verdict.hits),
        "redaction_applied": redacted,
        "plaintext_residual": residual,
        "rescan_after_redaction_clean": clean,
        "blocked_and_redacted": bool(verdict.hits) and redacted and residual == 0 and clean,
    }


def write_calibration(payload: Dict[str, Any], out_dir: str | Path) -> Dict[str, str]:
    """落盘标定产物 + sha256 旁车。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "calibration.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out / "calibration.json.sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    return {"path": str(target), "sha256": digest}