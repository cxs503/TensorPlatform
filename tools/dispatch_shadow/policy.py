"""Versioned dispatch policies + a pinning registry.

A *policy* here is a pure function ``features -> score`` plus the exploration
law that turns scores into propensities (needed for off-policy weighting).  Two
levels of versioning are recorded in every decision record:

* ``policy_sha256`` -- content address of the **semantic spec** (kind, weights,
  bias, temperature, exploration floor, ``impl_version``, ``kernel_sha256``);
* ``kernel_sha256`` -- content address of the **implementation source** of the
  scoring/ranking/propensity kernels.

The second one is what makes "replay reproduced the ranking" mean something:
if somebody edits the scoring kernel without bumping ``impl_version``, the
recomputed kernel digest no longer matches the pinned one and replay refuses
(:class:`UnpinnedPolicyError`) instead of silently producing a different
ordering under the old version number.

Determinism rules obeyed by the kernels and asserted in the tests:

* features are iterated in the **config order stored in the record**, never in
  dict-insertion order;
* slates are ranked by ``(-score, candidate_id)`` -- score ties broken by name,
  never by input order;
* sums use :func:`~.canonical.fsum` (exactly rounded), propensities use the
  max-shifted softmax;
* no RNG, no clock, no I/O inside the kernels.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Sequence

from .canonical import content_digest, fsum, sha256_hex
from .schema import SchemaError

__all__ = [
    "IMPL_VERSION",
    "UnpinnedPolicyError",
    "Policy",
    "PolicyRegistry",
    "linear_score",
    "softmax_propensities",
    "rank_slate",
    "choose_action",
    "kernel_digest",
]

IMPL_VERSION = "dispatch-policy-kernel/1"


class UnpinnedPolicyError(RuntimeError):
    """The requested policy version is not registered / its kernel changed."""


# ---------------------------------------------------------------------------
# kernels (pure, deterministic, source-digested)
# ---------------------------------------------------------------------------


def linear_score(
    weights: Mapping[str, float],
    bias: float,
    features: Mapping[str, float],
    feature_names: Sequence[str],
) -> float:
    """``bias + sum_f w_f x_f`` in the fixed feature order, exactly rounded."""
    return fsum([float(bias)] + [float(weights[n]) * float(features[n]) for n in feature_names])


def softmax_propensities(
    scores: Sequence[float], temperature: float, exploration_floor: float
) -> list[float]:
    """Max-shifted softmax mixed with a uniform exploration floor.

    ``p_a = (1 - eps) * exp((s_a - s_max)/T) / sum_b exp((s_b - s_max)/T) + eps / n``

    The floor is what guarantees *positivity* for off-policy estimation: every
    action the log could ever contain has non-zero probability under this law.
    The max shift keeps ``exp`` from overflowing without changing the result.
    """
    if temperature <= 0:
        raise SchemaError("temperature must be > 0")
    if not 0.0 <= exploration_floor < 1.0:
        raise SchemaError("exploration_floor must be in [0, 1)")
    n = len(scores)
    if n == 0:
        raise SchemaError("cannot build propensities for an empty slate")
    top = max(float(s) for s in scores)
    exps = [math.exp((float(s) - top) / float(temperature)) for s in scores]
    z = fsum(exps)
    base = [e / z for e in exps]
    return [(1.0 - exploration_floor) * b + exploration_floor / n for b in base]


def rank_slate(scores: Mapping[str, float], candidate_ids: Sequence[str]) -> list[str]:
    """Deterministic ranking: score descending, then candidate id ascending."""
    return sorted(candidate_ids, key=lambda cid: (-float(scores[cid]), str(cid)))


def choose_action(scores: Mapping[str, float], candidate_ids: Sequence[str]) -> str:
    return rank_slate(scores, candidate_ids)[0]


@lru_cache(maxsize=1)
def _kernel_digest_cached() -> str:
    src = "\n".join(
        inspect.getsource(fn)
        for fn in (linear_score, softmax_propensities, rank_slate, choose_action)
    )
    return sha256_hex(IMPL_VERSION + "\n" + src)


def _candidate_id(candidate: Mapping[str, Any], field: str | None) -> str:
    """Resolve the candidate key from the injected config (never a hard-coded name)."""
    keys = [field] if field else []
    keys += ["candidate_id"]
    for key in keys:
        if key and key in candidate:
            return str(candidate[key])
    # last resort: a single *candidate_id field declared by the caller's config
    matches = [k for k in candidate if k.endswith("candidate_id")]
    if len(matches) == 1:
        return str(candidate[matches[0]])
    raise SchemaError(
        f"cannot resolve candidate id field (tried {keys}): keys={sorted(candidate)}"
    )


def kernel_digest() -> str:
    """Content address of the implementation sources above.

    Cached for the process lifetime: source text cannot change mid-run, and the
    digest is consulted on every replay record.  A test that simulates an
    implementation edit clears the cache explicitly
    (``policy._kernel_digest_cached.cache_clear()``).
    """
    return _kernel_digest_cached()


# ---------------------------------------------------------------------------
# policy object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """A linear scoring policy with a softmax+floor exploration law."""

    policy_id: str
    weights: Mapping[str, float]
    bias: float = 0.0
    temperature: float = 1.0
    exploration_floor: float = 0.05
    feature_names: tuple[str, ...] = ()
    impl_version: str = IMPL_VERSION
    kernel_sha256: str | None = None

    # -- identity -------------------------------------------------------
    def spec(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "weights": {n: float(self.weights[n]) for n in sorted(self.weights)},
            "bias": float(self.bias),
            "temperature": float(self.temperature),
            "exploration_floor": float(self.exploration_floor),
            "feature_names": list(self.feature_names),
            "impl_version": self.impl_version,
            "kernel_sha256": self.kernel_sha256 or kernel_digest(),
        }

    @property
    def sha256(self) -> str:
        return content_digest(self.spec())

    def check_kernel(self) -> None:
        pinned = self.kernel_sha256 or kernel_digest()
        live = kernel_digest()
        if pinned != live:
            raise UnpinnedPolicyError(
                "policy kernel source changed without bumping impl_version: "
                f"pinned {pinned[:12]}… live {live[:12]}…"
            )
        if self.impl_version != IMPL_VERSION:
            raise UnpinnedPolicyError(
                f"policy impl_version {self.impl_version!r} != runtime {IMPL_VERSION!r}"
            )

    # -- scoring --------------------------------------------------------
    def score(self, features: Mapping[str, float]) -> float:
        return linear_score(self.weights, self.bias, features, self.feature_names)

    def decide(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        candidate_id_field: str | None = None,
    ) -> dict[str, Any]:
        ids = [_candidate_id(c, candidate_id_field) for c in candidates]
        scores = {cid: self.score(c["features"]) for cid, c in zip(ids, candidates)}
        ranking = rank_slate(scores, ids)
        props = softmax_propensities(
            [scores[cid] for cid in ids], self.temperature, self.exploration_floor
        )
        return {
            "scores": scores,
            "ranking": ranking,
            "chosen": ranking[0],
            "propensities": {cid: p for cid, p in zip(ids, props)},
            "exploration_floor": self.exploration_floor,
        }

    def to_record_policy(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_sha256": self.sha256,
            "params": self.spec(),
        }

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> "Policy":
        need = ("policy_id", "weights", "feature_names")
        missing = [n for n in need if n not in spec]
        if missing:
            raise SchemaError(f"policy spec missing fields: {missing}")
        return cls(
            policy_id=str(spec["policy_id"]),
            weights={str(k): float(v) for k, v in spec["weights"].items()},
            bias=float(spec.get("bias", 0.0)),
            temperature=float(spec.get("temperature", 1.0)),
            exploration_floor=float(spec.get("exploration_floor", 0.05)),
            feature_names=tuple(str(n) for n in spec["feature_names"]),
            impl_version=str(spec.get("impl_version", IMPL_VERSION)),
            kernel_sha256=spec.get("kernel_sha256"),
        )

    @classmethod
    def from_record(cls, record_policy: Mapping[str, Any]) -> "Policy":
        spec = dict(record_policy.get("params") or {})
        if not spec:
            raise UnpinnedPolicyError(
                f"record policy {record_policy.get('policy_id')!r} carries no spec; "
                "it cannot be replayed"
            )
        policy = cls.from_spec(spec)
        if policy.sha256 != record_policy.get("policy_sha256"):
            raise UnpinnedPolicyError(
                f"policy spec does not reproduce its recorded sha256 "
                f"({policy.sha256[:12]}… != {record_policy.get('policy_sha256')!r})"
            )
        policy.check_kernel()
        return policy


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


class PolicyRegistry:
    """Maps ``policy_sha256 -> Policy`` and refuses ambiguous registrations."""

    def __init__(self) -> None:
        self._by_sha: dict[str, Policy] = {}
        self._by_id: dict[str, str] = {}

    def register(self, policy: Policy) -> str:
        sha = policy.sha256
        policy.check_kernel()
        existing = self._by_sha.get(sha)
        if existing is not None and existing.spec() != policy.spec():
            raise UnpinnedPolicyError(f"hash collision / spec drift for {sha[:12]}…")
        prev = self._by_id.get(policy.policy_id)
        if prev is not None and prev != sha:
            raise UnpinnedPolicyError(
                f"policy_id {policy.policy_id!r} already registered with a different "
                f"spec ({prev[:12]}…); bump the id when the policy changes"
            )
        self._by_sha[sha] = policy
        self._by_id[policy.policy_id] = sha
        return sha

    def require(self, sha256: str) -> Policy:
        try:
            return self._by_sha[sha256]
        except KeyError:
            raise UnpinnedPolicyError(
                f"policy {sha256[:12]}… is not registered; refusing to replay an "
                "unpinned policy version"
            ) from None

    def by_id(self, policy_id: str) -> Policy:
        return self.require(self._by_id[policy_id])

    def versions(self) -> dict[str, str]:
        return dict(self._by_id)

    def __len__(self) -> int:
        return len(self._by_sha)