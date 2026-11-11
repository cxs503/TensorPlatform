"""Injection calibration: prove the scanner has teeth, without storing secrets.

Two corpora are generated **at runtime from a seed** (hashlib expansion, so no
``random`` version dependence and no literals in the repo):

* 20 synthetic secrets — 4 of them deliberately *bare* high-entropy keys with no
  vendor prefix at all (the case a regex-only scanner cannot see), the rest
  shaped like the leaks that actually happen (PEM block, JWT, DB URL with an
  inline password, cloud keys, auth headers, kv assignments);
* 20 benign samples — the four admissible whitelist classes (sha256 / git commit
  / UUID / fixture) plus the look-alikes that kill naive scanners: base64 of
  prose, long camelCase identifiers, paths, versions, timestamps, SQL, code.

The metric pair is (detection rate, false-positive rate). A scanner tuned until
detection is 100% by flagging everything is worthless; calibration exists to make
both numbers falsifiable.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_SEED = "tp-secret-scan-calibration-v1"


def _stream(seed: str, label: str, length: int) -> bytes:
    """Deterministic byte stream from (seed, label) — no PRNG state, no literals."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        digest = hashlib.sha256("{}|{}|{}".format(seed, label, counter).encode("utf-8")).digest()
        out.extend(digest)
        counter += 1
    return bytes(out[:length])


def _b64(seed: str, label: str, nbytes: int, *, urlsafe: bool = True, pad: bool = False) -> str:
    raw = _stream(seed, label, nbytes)
    text = (base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)).decode("ascii")
    text = text.rstrip("=") if not pad else text
    return _ensure_mixed_digit(text, seed, label)


def _hex(seed: str, label: str, nchars: int, *, upper: bool = False) -> str:
    raw = _stream(seed, label, nchars // 2 + 1).hex()[:nchars]
    return raw.upper() if upper else raw


def _ensure_mixed_digit(text: str, seed: str, label: str) -> str:
    """Guarantee a mixed-case, digit-bearing string so the corpus is well-formed."""
    digits = "0123456789"
    if not any(ch.isdigit() for ch in text):
        pos = _stream(seed, label + "|d", 1)[0] % max(1, len(text))
        text = text[:pos] + digits[_stream(seed, label + "|dv", 1)[0] % 10] + text[pos + 1:]
    if not any(ch.isupper() for ch in text):
        pos = _stream(seed, label + "|u", 1)[0] % max(1, len(text))
        text = text[:pos] + "Q" + text[pos + 1:]
    if not any(ch.islower() for ch in text):
        pos = _stream(seed, label + "|l", 1)[0] % max(1, len(text))
        text = text[:pos] + "z" + text[pos + 1:]
    return text


def _alnum_upper(seed: str, label: str, nchars: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    raw = _stream(seed, label, nchars)
    return "".join(alphabet[b % len(alphabet)] for b in raw)


def _alnum(seed: str, label: str, nchars: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    raw = _stream(seed, label, nchars)
    return "".join(alphabet[b % len(alphabet)] for b in raw)


@dataclass
class Case:
    label: str
    text: str                      # the embedding (what a scanner actually sees)
    kind: str                      # bare | kv | header | block | url | encoded | benign
    expect: Optional[str] = None   # expected detector type family (None for benign)
    note: str = ""
    value: str = ""                # the secret material itself (for no-echo checks)

    def __post_init__(self) -> None:
        if not self.value:
            self.value = self.text

    def to_dict(self) -> Dict[str, object]:
        return {"label": self.label, "kind": self.kind, "expect": self.expect, "note": self.note,
                "length": len(self.text), "value_length": len(self.value)}


@dataclass
class Corpus:
    secrets: List[Case] = field(default_factory=list)
    benign: List[Case] = field(default_factory=list)
    transport: List[Case] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "secrets": [case.to_dict() for case in self.secrets],
            "benign": [case.to_dict() for case in self.benign],
            "transport": [case.to_dict() for case in self.transport],
        }


def build_corpus(seed: str = DEFAULT_SEED) -> Corpus:
    corpus = Corpus()

    def secret(label: str, text: str, kind: str, expect: str, note: str = "", value: str = "") -> None:
        corpus.secrets.append(Case(label=label, text=text, kind=kind, expect=expect, note=note,
                                   value=value or text))

    # --- 1..4: bare high-entropy keys, no vendor prefix, no kv context --------
    bare_specs = (
        ("bare_key_32", 24, "no prefix / no context / 32-char urlsafe base64"),
        ("bare_key_43", 32, "no prefix / no context / 43-char urlsafe base64"),
        ("bare_key_24", 18, "no prefix / no context / 24-char urlsafe base64"),
        ("bare_key_64", 48, "no prefix / no context / 64-char urlsafe base64"),
    )
    for label, nbytes, note in bare_specs:
        secret(label, _b64(seed, label, nbytes), "bare", "high_entropy_string", note)

    # --- 5..20: shaped secrets ----------------------------------------------
    aws_id = "AKIA" + _alnum_upper(seed, "awsid", 16)
    secret("aws_access_key_id", aws_id, "bare", "aws_access_key_id", value=aws_id)
    aws_secret = _b64(seed, "awssec", 30, pad=True)[:40]
    secret("aws_secret_access_key",
           'aws_secret_access_key = "{}"'.format(aws_secret),
           "kv", "aws_secret_access_key", value=aws_secret)
    gcp = "AIza" + _alnum(seed, "gcp", 35)
    secret("gcp_api_key", gcp, "bare", "gcp_api_key", value=gcp)
    oai = "sk-" + _alnum(seed, "openai", 32)
    secret("openai_key", oai, "bare", "openai_key", value=oai)
    pat = "github_pat_" + _alnum(seed, "ghpat", 30)
    secret("github_pat", pat, "bare", "github_token", value=pat)
    ghp = "ghp_" + _alnum(seed, "ghp", 24)
    secret("github_classic", ghp, "bare", "github_token", value=ghp)
    slack = "xoxb-" + _alnum(seed, "slack", 24)
    secret("slack_token", slack, "bare", "slack_token", value=slack)
    stripe = "sk_live_" + _alnum(seed, "stripe", 24)
    secret("stripe_live_key", stripe, "bare", "stripe_live_key", value=stripe)
    sg = "SG.{}.{}".format(_alnum(seed, "sg1", 22), _alnum(seed, "sg2", 22))
    secret("sendgrid_key", sg, "bare", "sendgrid_key", value=sg)

    def b64url(seed_label: str, nbytes: int) -> str:
        return _b64(seed, seed_label, nbytes)

    jwt = "eyJ{}.eyJ{}.{}".format(b64url("jwt1", 18), b64url("jwt2", 24), b64url("jwt3", 30))
    secret("jwt", jwt, "bare", "jwt")

    pem_body = "\n".join(b64url("pem{}".format(i), 48) for i in range(4))
    # the PEM armour is assembled at runtime: a PEM header is a well-known marker
    # (not a credential), but keeping the literal out of the source means the
    # scanner can scan itself clean.
    pem = "{}\n{}\n{}".format("-----BEGIN " + "RSA PRIVATE KEY" + "-----", pem_body,
                               "-----END " + "RSA PRIVATE KEY" + "-----")
    secret("pem_private_key", pem, "block", "pem_private_key")

    db_pw = b64url("dbpw", 18)
    secret("postgres_url", "postgres://report_writer:{}@db.internal:5432/ledger".format(db_pw),
           "url", "db_url_credentials", value=db_pw)
    mysql_pw = b64url("mysqlpw", 16)
    secret("mysql_url", "mysql://root:{}@127.0.0.1:3306/ops".format(mysql_pw),
           "url", "db_url_credentials", value=mysql_pw)

    bearer = b64url("bearer", 30)
    secret("bearer_header", "Authorization: Bearer {}".format(bearer),
           "header", "bearer_header", value=bearer)
    basic = base64.b64encode("svc:{}".format(b64url("basicpw", 18)).encode()).decode()
    secret("basic_auth_header", "Authorization: Basic {}".format(basic), "header", "basic_auth_header",
           value=basic)
    kv_pw = b64url("kvpw", 16)
    secret("password_kv", 'password = "{}"'.format(kv_pw), "kv", "password_kv", value=kv_pw)

    # --- transport cases (encoded secrets; the report gate must see through) --
    inner = "password = \"{}\"".format(b64url("transport_pw", 18))
    corpus.transport.append(
        Case(label="base64_wrapped_kv", text=base64.b64encode(inner.encode()).decode(),
             kind="encoded", expect="decoded:password_kv", note="secret transported as base64",
             value=b64url("transport_pw", 18))
    )
    inner_bearer = "Authorization: Bearer {}".format(b64url("transport_bearer", 30))
    corpus.transport.append(
        Case(label="hex_wrapped_bearer", text=inner_bearer.encode().hex(), kind="encoded",
             expect="decoded:bearer_header", note="secret transported as hex",
             value=b64url("transport_bearer", 30))
    )

    # --- 20 benign look-alikes ---------------------------------------------
    def benign(label: str, text: str, kind: str = "benign", note: str = "") -> None:
        corpus.benign.append(Case(label=label, text=text, kind=kind, expect=None, note=note))

    benign("sha256_digest", _hex(seed, "sha256", 64), note="64-hex digest (value class sha256)")
    benign("git_commit_full", _hex(seed, "commit", 40), note="40-hex commit sha")
    benign("git_commit_abbrev", _hex(seed, "commitabbr", 12), note="abbreviated commit sha")
    uuid = "{}-{}-{}-{}-{}".format(_hex(seed, "u1", 8), _hex(seed, "u2", 4), _hex(seed, "u3", 4),
                                   _hex(seed, "u4", 4), _hex(seed, "u5", 12))
    benign("uuid4", uuid, note="canonical UUID")
    benign("uuid_upper", "{}-{}-{}-{}-{}".format(_hex(seed, "v1", 8, upper=True), _hex(seed, "v2", 4, upper=True),
                                                 _hex(seed, "v3", 4, upper=True), _hex(seed, "v4", 4, upper=True),
                                                 _hex(seed, "v5", 12, upper=True)), note="uppercase UUID")
    benign("sha256_with_prefix", "sha256:" + _hex(seed, "sha256pref", 64), note="docker-style digest")
    benign("base64_of_prose",
           base64.b64encode(b"the quick brown fox jumps over the lazy dog, twice per run").decode(),
           note="base64 of a sentence (encoded content, not a key)")
    benign("camel_case_identifier", "artifactProvenanceChainBuilderForMission12471X", note="long identifier")
    benign("kebab_slug", "report-gate-calibration-run-2026-11-10", note="kebab slug")
    benign("posix_path", "/root/tp-agent/work/m12471/tools/secret_scan/engine.py", note="path")
    benign("windows_path", "C:\\Users\\agent\\AppData\\Local\\Temp\\scan_12471.log", note="windows path")
    benign("semver", "2.11.0-rc.3+build.20261110", note="version string")
    benign("iso_timestamp", "2026-11-10T23:52:22.565698+00:00", note="timestamp")
    benign("natural_language", "The scanner blocks the commit before the secret reaches history.", note="prose")
    benign("hex_colors", "#1f77b4 #ff7f0e #2ca02c #d62728 #9467bd", note="plot palette")
    benign("math_expression", "Cp = 2*pi*Re*log(1 + 0.5*t/c)", note="formula")
    benign("python_code", "scan = Scanner(load_policy()); assert scan.clean", note="code line")
    benign("sql_query", "SELECT count(*) FROM missions WHERE status = 'blocked' LIMIT 10", note="sql")
    benign("existing_placeholder", "<REDACTED:jwt:120>", note="already-redacted marker")
    benign("docker_image_ref", "ghcr.io/tensorplatform/runtime@sha256:" + _hex(seed, "img", 64),
           note="image reference with digest")
    return corpus


def score(detected: List[bool], false_positives: List[bool]) -> Dict[str, float]:
    n_sec = len(detected)
    n_ben = len(false_positives)
    return {
        "secrets_total": n_sec,
        "secrets_detected": sum(1 for flag in detected if flag),
        "detection_rate": round(sum(1 for flag in detected if flag) / n_sec, 4) if n_sec else 0.0,
        "benign_total": n_ben,
        "benign_flagged": sum(1 for flag in false_positives if flag),
        "false_positive_rate": round(sum(1 for flag in false_positives if flag) / n_ben, 4) if n_ben else 0.0,
    }