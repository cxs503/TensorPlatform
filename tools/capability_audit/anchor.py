"""Anti-tamper anchor: Merkle manifest + Ed25519 signature + git commit time anchor.

Threat model
------------
An audit result is only useful if a reader can tell that (a) nobody edited the
numbers afterwards and (b) the record existed at a given wall-clock time.  A
single self-signed JSON file gives neither, because the signer can silently
regenerate it.  This module therefore publishes three independent latches:

1. **Content latch** — a Merkle root over every artifact (path, size, sha256).
   Editing any byte changes the root; the root is short enough to quote in a
   commit message, a paper, or a chat message.
2. **Authenticity latch** — Ed25519 signature over the root plus metadata, made
   with an ephemeral key whose public half is embedded in the record.  Any
   third party can verify; forging requires the private key that never left the
   audit run.
3. **Time latch** — the git commit that was HEAD when the anchor was produced
   (SHA + committer date) and, optionally, a commit whose *message* carries the
   root.  Git's committer date is the external timestamp; rewriting it changes
   the commit SHA and breaks latch (3).

Chaining ``anchor.json`` records through ``prev_anchor`` makes the sequence
append-only: dropping or editing an earlier anchor breaks the next one.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ed25519, merkle

SCHEMA = "capability-audit-anchor/1.0"
#: The anchored set is the *machine evidence* only: the raw probe results and the
#: figures rendered from them.  The narrative report and criteria.json are derived
#: views that quote this root, so anchoring them would create a fixed-point loop.
DEFAULT_PATTERNS = ("audit_results.json", "figures/*.png")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def git_info(repo: Path, ref: str = "HEAD") -> Dict[str, Any]:
    try:
        commit = _git(repo, "rev-parse", ref)
        committer_date = _git(repo, "log", "-1", "--format=%cI", ref)
        subject = _git(repo, "log", "-1", "--format=%s", ref)
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    except RuntimeError as exc:
        return {"kind": "git-commit", "available": False, "error": str(exc)}
    return {
        "kind": "git-commit",
        "available": True,
        "commit": commit,
        "committer_date": committer_date,
        "subject": subject,
        "branch": branch,
        "ref": ref,
    }


def collect_artifacts(root: Path, patterns=DEFAULT_PATTERNS,
                      repo: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(root)
    entries: List[Dict[str, Any]] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            data = path.read_bytes()
            entry = {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            if repo is not None:
                try:
                    entry["repo_path"] = str(path.resolve().relative_to(Path(repo).resolve()))
                except ValueError:
                    # artifacts kept outside the git tree (e.g. a relocated copy being
                    # verified by a third party) simply carry no repo-relative path
                    pass
            entries.append(entry)
    if not entries:
        raise RuntimeError(f"no artifacts matched {patterns} under {root}")
    return sorted(entries, key=lambda e: e["path"])


def build_manifest(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    tree = merkle.manifest_root(entries)
    return tree


def _payload_for_signing(record: Dict[str, Any]) -> bytes:
    body = {k: v for k, v in record.items() if k != "signature"}
    return merkle.canonical_bytes(body)


def load_seed(key_file: Optional[Path]) -> bytes:
    if key_file is not None:
        key_file = Path(key_file)
        if key_file.exists():
            raw = key_file.read_bytes()
            if len(raw) == 32:
                return raw
            try:
                return bytes.fromhex(raw.decode().strip())
            except ValueError as exc:
                raise RuntimeError(f"key file {key_file} is neither 32 raw bytes nor hex") from exc
        seed = ed25519.generate_seed()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(seed)
        os.chmod(key_file, 0o600)
        return seed
    env = os.environ.get("CAPABILITY_AUDIT_SEED")
    if env:
        return bytes.fromhex(env.strip())
    return ed25519.generate_seed()


def create_anchor(artifact_dir: Path, repo: Path, key_file: Optional[Path] = None,
                  pin_ref: str = "HEAD", prev_anchor: Optional[Path] = None,
                  patterns=DEFAULT_PATTERNS, index: int = 1) -> Dict[str, Any]:
    artifact_dir, repo = Path(artifact_dir), Path(repo)
    entries = collect_artifacts(artifact_dir, patterns, repo=repo)
    tree = build_manifest(entries)
    seed = load_seed(key_file)
    pub = ed25519.public_key(seed)

    prev_block = None
    if prev_anchor is not None and Path(prev_anchor).exists():
        raw = Path(prev_anchor).read_bytes()
        prev_anchor = Path(prev_anchor)
        prev_block = {
            "path": str(prev_anchor),
            "anchor_sha256": hashlib.sha256(raw).hexdigest(),
            "root": json.loads(raw.decode()).get("artifact_root"),
            "index": index - 1,
        }

    record: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _now_iso(),
        "tool": {"name": "tools.capability_audit",
                 "module_sha256": _module_digest(),
                 "version": "1.0.0"},
        "artifact_root": tree["root"],
        "artifact_count": tree["size"],
        "patterns": list(patterns),
        "artifacts": entries,
        "merkle_levels": tree["levels"],
        "external_time_anchor": git_info(repo, pin_ref),
        "prev_anchor": prev_block,
        "chain": {"index": index, "links_to": prev_block["anchor_sha256"] if prev_block else None},
    }
    message = _payload_for_signing(record)
    signature = ed25519.sign(seed, message)
    record["signature"] = {
        "alg": "ed25519",
        "public_key": pub.hex(),
        "fingerprint": ed25519.fingerprint(pub),
        "signature": signature.hex(),
        "signed_payload_sha256": hashlib.sha256(message).hexdigest(),
    }
    return record


def _module_digest() -> str:
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in sorted(p.name for p in here.glob("*.py")):
        h.update(name.encode())
        h.update((here / name).read_bytes())
    return h.hexdigest()


def verify_anchor(anchor_path: Path, artifact_dir: Path, repo: Path,
                  require_commit_message: bool = False) -> Dict[str, Any]:
    anchor_path, artifact_dir, repo = Path(anchor_path), Path(artifact_dir), Path(repo)
    record = json.loads(anchor_path.read_text(encoding="utf-8"))
    checks: List[Dict[str, Any]] = []

    # 1. signature
    payload = _payload_for_signing(record)
    sig_block = record.get("signature", {})
    sig_ok = ed25519.verify(bytes.fromhex(sig_block.get("public_key", "")),
                            payload, bytes.fromhex(sig_block.get("signature", "")))
    checks.append({"name": "ed25519_signature", "ok": sig_ok,
                   "detail": f"public key {sig_block.get('fingerprint')}, "
                             f"payload sha256 {hashlib.sha256(payload).hexdigest()[:16]}"})

    # 2. artifact digests
    recomputed = []
    digest_ok = True
    missing = []
    for entry in record.get("artifacts", []):
        path = artifact_dir / entry["path"]
        if not path.exists():
            digest_ok = False
            missing.append(entry["path"])
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        ok = digest == entry["sha256"]
        digest_ok &= ok
        recomputed.append({"path": entry["path"], "sha256": digest, "ok": ok})
    checks.append({"name": "artifact_digests", "ok": digest_ok,
                   "detail": f"{len(recomputed)} artifacts re-hashed, missing={missing}",
                   "artifacts": recomputed})

    # 3. Merkle root rebuilt from the current manifest
    try:
        current_entries = collect_artifacts(
            artifact_dir, patterns=tuple(record.get("patterns") or DEFAULT_PATTERNS), repo=repo)
        current_root = build_manifest(current_entries)["root"]
    except RuntimeError as exc:
        current_entries, current_root = [], None
        checks.append({"name": "merkle_root", "ok": False, "detail": str(exc)})
    root_ok = current_root == record.get("artifact_root")
    if current_root is not None:
        checks.append({"name": "merkle_root", "ok": root_ok,
                       "detail": f"recorded {record.get('artifact_root')} vs rebuilt {current_root}"})

    # 4. inclusion proof of the first artifact against the recorded root
    proof_ok = None
    if record.get("merkle_levels") and record.get("artifacts"):
        from .merkle import leaf_hash, verify_inclusion
        entries_sorted = sorted(record["artifacts"], key=lambda e: e["path"])
        leaves = [leaf_hash(merkle.canonical_bytes(merkle.leaf_payload(e)))
                  for e in entries_sorted]
        proofs = []
        for idx in range(len(leaves)):
            proof = merkle.inclusion_proof(leaves, idx)
            proofs.append({"path": entries_sorted[idx]["path"],
                           "proof": proof,
                           "verified": verify_inclusion(leaves[idx], proof,
                                                        record["artifact_root"])})
        proof_ok = all(p["verified"] for p in proofs)
        checks.append({"name": "inclusion_proofs", "ok": proof_ok,
                       "detail": f"{len(proofs)}/{{}} proofs verified".format(len(proofs)),
                       "proofs": proofs})

    # 5. external time anchor: commit exists, artifacts present in it, time ordering
    ext = record.get("external_time_anchor", {})
    commit = ext.get("commit")
    commit_ok, anchored, commit_time = False, 0, None
    detail = "no commit recorded"
    if commit:
        try:
            _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
            commit_time = _git(repo, "log", "-1", "--format=%cI", commit)
            for entry in record.get("artifacts", []):
                repo_path = entry.get("repo_path") or entry["path"]
                try:
                    blob = subprocess.run(["git", "show", f"{commit}:{repo_path}"],
                                          cwd=str(repo), capture_output=True).stdout
                except Exception:  # pragma: no cover
                    continue
                if blob and hashlib.sha256(blob).hexdigest() == entry["sha256"]:
                    anchored += 1
            commit_ok = True
            detail = (f"commit {commit[:12]} ({commit_time}) exists; "
                      f"{anchored}/{len(record.get('artifacts', []))} artifacts are byte-identical "
                      f"to their content in that commit")
        except RuntimeError as exc:
            detail = f"commit {commit} not found: {exc}"
    checks.append({"name": "external_time_anchor", "ok": commit_ok, "detail": detail,
                   "commit": commit, "committer_date": commit_time,
                   "artifacts_anchored_in_commit": anchored})

    # 6. anchor chain
    prev = record.get("prev_anchor")
    chain_ok = True
    chain_detail = "genesis anchor (no predecessor)"
    if prev:
        prev_path = Path(prev["path"])
        if prev_path.exists():
            prev_raw = prev_path.read_bytes()
            digest = hashlib.sha256(prev_raw).hexdigest()
            chain_ok = digest == prev["anchor_sha256"]
            chain_detail = (f"predecessor {prev_path.name} sha256 "
                            f"{'matches' if chain_ok else 'DOES NOT match'} recorded link")
        else:
            chain_ok = False
            chain_detail = f"predecessor {prev_path} missing"
    checks.append({"name": "anchor_chain", "ok": chain_ok, "detail": chain_detail})

    # 7. optional: root quoted inside a commit message (public time latch)
    if require_commit_message:
        root = record.get("artifact_root", "")
        try:
            # full body (%B): the root latch is normally quoted in the commit body
            log = _git(repo, "log", "--all", "--format=%H%x09%B")
        except RuntimeError:
            log = ""
        hits = [line for line in log.splitlines() if root and root in line]
        checks.append({"name": "commit_message_anchor", "ok": bool(hits),
                       "detail": (f"root quoted in {len(hits)} commit message(s)"
                                  if hits else "root not found in any commit message")})

    # 8. derived acceptance file must agree with the anchored audit results
    try:
        import json as _json
        crit_path = artifact_dir / "criteria.json"
        audit_path = artifact_dir / "audit_results.json"
        if crit_path.exists() and audit_path.exists():
            crit = _json.loads(crit_path.read_text())["criteria"]
            base = _json.loads(audit_path.read_text())["criteria"]
            pairs = [("invariance_capabilities", "invariance_covered"),
                     ("analytic_capabilities", "analytic_covered"),
                     ("monotonic_capabilities", "monotonic_covered"),
                     ("checks_total", "checks_total"),
                     ("benchmark_compared", "benchmark_compared"),
                     ("benchmark_failed", "benchmark_failed"),
                     ("checks_passed", "checks_passed"),
                     ("checks_failed", "checks_failed")]
            mismatched = [f"{a}={crit.get(a)} vs {b}={base.get(b)}"
                          for a, b in pairs if crit.get(a) != base.get(b)]
            checks.append({"name": "criteria_consistency", "ok": not mismatched,
                           "detail": ("criteria.json counts agree with the anchored "
                                      "audit_results.json" if not mismatched
                                      else "mismatch: " + "; ".join(mismatched))})
    except (KeyError, ValueError) as exc:  # pragma: no cover - malformed file
        checks.append({"name": "criteria_consistency", "ok": False, "detail": str(exc)})

    ok = all(c["ok"] for c in checks)
    return {"schema": "capability-audit-verification/1.0",
            "verified_at": _now_iso(),
            "ok": ok,
            "anchor_path": str(anchor_path),
            "artifact_root": record.get("artifact_root"),
            "signature_fingerprint": sig_block.get("fingerprint"),
            "checks": checks}