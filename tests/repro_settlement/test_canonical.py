"""规范化/指纹层：幂等与易变字段剥离。"""
import pytest

from tools.repro_settlement.canonical import (canonical, sha256_obj, sha256_text,
                                              stable_digest, strip_volatile)


def test_canonical_is_key_order_invariant():
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_canonical_is_stable_across_whitespace_side_effects():
    assert canonical({"a": [1, 2, 3]}) == '{"a":[1,2,3]}'


def test_sha256_obj_matches_text_digest():
    obj = {"x": 1}
    assert sha256_obj(obj) == sha256_text(canonical(obj))


def test_sha256_is_hex64():
    assert len(sha256_text("abc")) == 64


def test_strip_volatile_drops_timestamp_recursively():
    got = strip_volatile({"a": 1, "generated_at": "t", "nested": {"host": "h", "b": 2}})
    assert got == {"a": 1, "nested": {"b": 2}}


def test_stable_digest_ignores_volatile_fields():
    a = {"v": 1, "generated_at": "2026-01-01", "wall_seconds": 3.0}
    b = {"v": 1, "generated_at": "2026-02-02", "wall_seconds": 9.5}
    assert stable_digest(a) == stable_digest(b)


def test_stable_digest_detects_real_change():
    assert stable_digest({"v": 1}) != stable_digest({"v": 2})
