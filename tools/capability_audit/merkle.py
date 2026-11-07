"""Merkle tree over audit artifacts (RFC 6962 style, SHA-256).

Design notes
------------
* Canonical encoding: UTF-8 JSON with sorted keys and no whitespace, so that
  the same logical object always yields the same bytes (and therefore the
  same leaf hash) regardless of the producing process.
* Domain separation: leaves are ``H(0x00 || data)`` and internal nodes are
  ``H(0x01 || left || right)`` (RFC 6962 §2.1).  This makes second-preimage
  attacks between leaf and node space impossible.
* Odd-sized levels promote the last node unchanged (RFC 6962 uses a split
  point rather than duplicating the last leaf; promotion is the equivalent
  bottom-up formulation).

The tree exposes inclusion proofs so a third party can verify one artifact
against a published root without re-reading the whole artifact set.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Sequence, Tuple


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic JSON encoding used for every hashable payload."""
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def build_tree(leaves: Sequence[bytes]) -> Dict[str, Any]:
    """Return {'root': hex, 'levels': [[hex,...],...], 'size': n}."""
    if not leaves:
        raise ValueError("cannot build a Merkle tree from an empty leaf set")
    level = [bytes(x) for x in leaves]
    levels: List[List[bytes]] = [level]
    while len(level) > 1:
        nxt: List[bytes] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(node_hash(level[i], level[i + 1]))
        if len(level) % 2:
            nxt.append(level[-1])
        levels.append(nxt)
        level = nxt
    return {
        "root": levels[-1][0].hex(),
        "size": len(leaves),
        "levels": [[h.hex() for h in lvl] for lvl in levels],
    }


def inclusion_proof(leaves: Sequence[bytes], index: int) -> List[Tuple[str, str]]:
    """RFC 6962 audit path: list of (side, hex) with side in {'left','right'}."""
    if not 0 <= index < len(leaves):
        raise IndexError("leaf index out of range")
    level = [bytes(x) for x in leaves]
    idx = index
    proof: List[Tuple[str, str]] = []
    while len(level) > 1:
        if idx % 2 == 0:
            sibling = idx + 1
            if sibling < len(level):
                proof.append(("right", level[sibling].hex()))
        else:
            proof.append(("left", level[idx - 1].hex()))
        nxt: List[bytes] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(node_hash(level[i], level[i + 1]))
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
        idx //= 2
    return proof


def root_from_proof(leaf: bytes, proof: Iterable[Tuple[str, str]]) -> str:
    h = leaf
    for side, sib_hex in proof:
        sib = bytes.fromhex(sib_hex)
        h = node_hash(h, sib) if side == "right" else node_hash(sib, h)
    return h.hex()


def verify_inclusion(leaf: bytes, proof: Iterable[Tuple[str, str]], root_hex: str) -> bool:
    try:
        return root_from_proof(leaf, proof) == root_hex
    except (ValueError, TypeError):
        return False


#: Only these fields enter the leaf payload.  Anything else carried on a manifest
#: entry (e.g. the convenience ``repo_path``) is metadata and must stay out of the
#: hash, otherwise a third party holding a relocated copy of the same bytes could
#: not reproduce the root.
LEAF_FIELDS = ("path", "sha256", "bytes")


def leaf_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: entry[k] for k in LEAF_FIELDS}


def manifest_root(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a tree over [{'path':..., 'sha256':..., 'bytes':...}, ...].

    The leaf payload is the canonical JSON of the entry projection above, so both
    the set of artifacts and their contents are covered by the root.
    """
    entries = sorted(entries, key=lambda e: e["path"])
    leaves = [leaf_hash(canonical_bytes(leaf_payload(e))) for e in entries]
    tree = build_tree(leaves)
    tree["entries"] = [
        dict(e, leaf_sha256=leaves[i].hex()) for i, e in enumerate(entries)
    ]
    return tree