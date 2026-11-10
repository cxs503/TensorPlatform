"""
Authenticated Encryption for Blind Benchmark Holdout.

Uses encrypt-then-MAC construction with stdlib-only primitives:
  - Stream cipher: SHA-256 in counter mode (key + nonce + counter → keystream)
  - Authentication: HMAC-SHA256 over (nonce + ciphertext)
  - Format: nonce(16B) || ciphertext || tag(32B)

This provides:
  - Confidentiality: prediction endpoint cannot read ground truth
  - Integrity: any tampering detected by MAC verification
  - Key separation: different keys for encryption and authentication

Security note: This is NOT AES-GCM (which requires the cryptography library).
For platform-level access control (preventing solvers from reading answers),
this construction is sufficient. The threat model is:
  - Solver has access to sealed dataset file
  - Solver does NOT have the adjudicator key
  - Goal: make ground truth unreadable without key

References:
  - Bellare & Namprempre 2008: "Authenticated encryption: relations among notions..."
  - NIST SP 800-38C: CCM mode (similar encrypt-then-MAC philosophy)
"""

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass
from typing import Optional


@dataclass
class SealedEnvelope:
    """Sealed dataset with authentication tag."""
    nonce: bytes          # 16 bytes random nonce
    ciphertext: bytes     # encrypted payload
    tag: bytes            # 32 bytes HMAC-SHA256
    metadata: dict        # unencrypted metadata (sha256 of plaintext, size, etc.)

    def to_bytes(self) -> bytes:
        """Serialize to binary format: nonce || ciphertext || tag."""
        return self.nonce + self.ciphertext + self.tag

    @classmethod
    def from_bytes(cls, data: bytes, metadata: dict) -> "SealedEnvelope":
        """Deserialize from binary format."""
        if len(data) < 48:  # 16 nonce + 32 tag minimum
            raise ValueError("Sealed data too short")
        nonce = data[:16]
        tag = data[-32:]
        ciphertext = data[16:-32]
        return cls(nonce=nonce, ciphertext=ciphertext, tag=tag, metadata=metadata)


def _derive_stream_key(enc_key: bytes, nonce: bytes, counter: int) -> bytes:
    """Derive keystream block: SHA-256(enc_key || nonce || counter)."""
    h = hashlib.sha256()
    h.update(enc_key)
    h.update(nonce)
    h.update(struct.pack(">Q", counter))
    return h.digest()


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """XOR data with SHA-256 counter-mode keystream."""
    result = bytearray()
    counter = 0
    offset = 0
    while offset < len(data):
        keystream = _derive_stream_key(key, nonce, counter)
        chunk_len = min(32, len(data) - offset)
        for i in range(chunk_len):
            result.append(data[offset + i] ^ keystream[i])
        offset += chunk_len
        counter += 1
    return bytes(result)


def _compute_mac(mac_key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """Compute HMAC-SHA256 over (nonce || ciphertext)."""
    return hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()


def seal_data(plaintext: bytes, master_key: bytes, metadata: Optional[dict] = None) -> SealedEnvelope:
    """
    Encrypt and authenticate plaintext.

    Args:
        plaintext: data to seal
        master_key: 32-byte master key (split into enc_key + mac_key)
        metadata: optional unencrypted metadata

    Returns:
        SealedEnvelope with nonce, ciphertext, tag, metadata
    """
    if len(master_key) < 32:
        raise ValueError("Master key must be at least 32 bytes")

    # Split master key into encryption and authentication keys
    # Using SHA-256 for key separation (domain separation)
    enc_key = hashlib.sha256(b"blind_benchmark_encryption" + master_key).digest()
    mac_key = hashlib.sha256(b"blind_benchmark_authentication" + master_key).digest()

    # Generate random nonce
    nonce = os.urandom(16)

    # Encrypt: XOR with keystream
    ciphertext = _xor_stream(plaintext, enc_key, nonce)

    # Authenticate: HMAC over (nonce || ciphertext)
    tag = _compute_mac(mac_key, nonce, ciphertext)

    # Compute plaintext hash for integrity verification
    plaintext_hash = hashlib.sha256(plaintext).hexdigest()

    if metadata is None:
        metadata = {}
    metadata["plaintext_sha256"] = plaintext_hash
    metadata["plaintext_size"] = len(plaintext)

    return SealedEnvelope(nonce=nonce, ciphertext=ciphertext, tag=tag, metadata=metadata)


def unseal_data(envelope: SealedEnvelope, master_key: bytes) -> bytes:
    """
    Decrypt and verify sealed data.

    Args:
        envelope: sealed data with nonce, ciphertext, tag
        master_key: same 32-byte master key used for sealing

    Returns:
        decrypted plaintext

    Raises:
        ValueError: if MAC verification fails (tampering detected)
    """
    if len(master_key) < 32:
        raise ValueError("Master key must be at least 32 bytes")

    # Derive same keys
    enc_key = hashlib.sha256(b"blind_benchmark_encryption" + master_key).digest()
    mac_key = hashlib.sha256(b"blind_benchmark_authentication" + master_key).digest()

    # Verify MAC first (authenticate-then-decrypt)
    expected_tag = _compute_mac(mac_key, envelope.nonce, envelope.ciphertext)
    if not hmac.compare_digest(expected_tag, envelope.tag):
        raise ValueError("MAC verification failed: data tampered or wrong key")

    # Decrypt
    plaintext = _xor_stream(envelope.ciphertext, enc_key, envelope.nonce)

    # Verify plaintext hash if available
    if "plaintext_sha256" in envelope.metadata:
        actual_hash = hashlib.sha256(plaintext).hexdigest()
        if actual_hash != envelope.metadata["plaintext_sha256"]:
            raise ValueError("Plaintext hash mismatch: decryption corrupted")

    return plaintext


def generate_master_key() -> bytes:
    """Generate a random 32-byte master key."""
    return os.urandom(32)
