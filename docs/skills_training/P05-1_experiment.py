#!/usr/bin/env python3
"""
P05-1 Experimental Design: Caching Strategy Comparison
Compares LRU+fixed-TTL vs LFU+adaptive-TTL on a fixed request trace.
Generates all metrics, latency distributions, and analysis data.
"""

import json
import math
import random
import time
import hashlib
from collections import OrderedDict, Counter
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

# ============================================================
# CONFIGURATION (predeclared before any results inspection)
# ============================================================
SEED = 42
TRACE_LENGTH = 50_000          # number of requests in fixed trace
UNIQUE_KEYS = 2_000            # cardinality of key space
CACHE_CAPACITY = 500           # max entries per cache
ZIPF_ALPHA = 1.2               # skew parameter for request distribution
FIXED_TTL = 300                # seconds, LRU strategy
ADAPTIVE_TTL_MIN = 60          # seconds, LFU strategy floor
ADAPTIVE_TTL_MAX = 600         # seconds, LFU strategy ceiling
FRESHNESS_THRESHOLD = 120      # seconds; hits older than this = stale
BASE_LATENCY_MS = 2.0          # cache hit latency (ms)
MISS_LATENCY_MS = 45.0         # cache miss latency (ms, fetch from origin)
STALE_PENALTY_MS = 8.0         # extra latency for stale-data validation
WARMUP_REQUESTS = 5_000        # excluded from metrics
N_REPETITIONS = 5              # repeated runs with different noise seeds
DECISION_HITRATE_DELTA = 0.03  # <3% hit rate difference = equivalent
DECISION_P99_INCREASE = 0.10   # >10% P99 increase = reject
DECISION_STALE_REDUCTION = 0.20 # ≥20% stale reduction = prefer LFU

random.seed(SEED)

# ============================================================
# 1. FIXED REQUEST TRACE GENERATION
# ============================================================
def generate_trace(length: int, n_keys: int, alpha: float, seed: int) -> List[int]:
    """Generate Zipf-distributed request trace with temporal locality bursts."""
    rng = random.Random(seed)
    # Zipf weights
    weights = [1.0 / (k ** alpha) for k in range(1, n_keys + 1)]
    total = sum(weights)
    weights = [w / total for w in weights]
    
    trace = []
    keys = list(range(n_keys))
    
    # Add temporal locality: occasional bursts of recent keys
    burst_prob = 0.15
    recent_window = 50
    
    for i in range(length):
        if rng.random() < burst_prob and len(trace) >= recent_window:
            # Burst: pick from recent keys
            recent = trace[-recent_window:]
            trace.append(rng.choice(recent))
        else:
            trace.append(rng.choices(keys, weights=weights, k=1)[0])
    
    return trace

# ============================================================
# 2. CACHING STRATEGIES
# ============================================================
@dataclass
class CacheEntry:
    key: int
    value: str
    created_at: float
    last_access: float
    access_count: int = 1

class LRUCache:
    """LRU cache with fixed TTL."""
    def __init__(self, capacity: int, ttl: float):
        self.capacity = capacity
        self.ttl = ttl
        self.cache: OrderedDict[int, CacheEntry] = OrderedDict()
        self.clock = 0.0
    
    def get(self, key: int, now: float) -> Tuple[bool, bool]:
        """Returns (hit, is_stale)."""
        self.clock = now
        if key in self.cache:
            entry = self.cache[key]
            age = now - entry.created_at
            self.cache.move_to_end(key)
            entry.last_access = now
            entry.access_count += 1
            if age > self.ttl:
                del self.cache[key]
                return False, False  # expired = miss
            stale = age > FRESHNESS_THRESHOLD
            return True, stale
        return False, False
    
    def put(self, key: int, now: float):
        self.clock = now
        if key in self.cache:
            self.cache.move_to_end(key)
            self.cache[key].last_access = now
            self.cache[key].access_count += 1
        else:
            if len(self.cache) >= self.capacity:
                self.cache.popitem(last=False)
            self.cache[key] = CacheEntry(
                key=key, value=f"v_{key}", created_at=now,
                last_access=now, access_count=1
            )

class LFUCache:
    """LFU cache with adaptive TTL based on access frequency."""
    def __init__(self, capacity: int, ttl_min: float, ttl_max: float):
        self.capacity = capacity
        self.ttl_min = ttl_min
        self.ttl_max = ttl_max
        self.cache: Dict[int, CacheEntry] = {}
        self.freq: Counter = Counter()
        self.clock = 0.0
    
    def _adaptive_ttl(self, entry: CacheEntry) -> float:
        """TTL scales with access frequency: more popular = longer TTL."""
        count = entry.access_count
        # Logarithmic scaling
        ratio = math.log(1 + count) / math.log(1 + 100)  # normalize
        ratio = max(0, min(1, ratio))
        return self.ttl_min + ratio * (self.ttl_max - self.ttl_min)
    
    def get(self, key: int, now: float) -> Tuple[bool, bool]:
        self.clock = now
        if key in self.cache:
            entry = self.cache[key]
            age = now - entry.created_at
            ttl = self._adaptive_ttl(entry)
            entry.last_access = now
            entry.access_count += 1
            self.freq[key] += 1
            if age > ttl:
                del self.cache[key]
                self.freq.pop(key, None)
                return False, False
            stale = age > FRESHNESS_THRESHOLD
            return True, stale
        return False, False
    
    def put(self, key: int, now: float):
        self.clock = now
        if key in self.cache:
            self.cache[key].last_access = now
            self.cache[key].access_count += 1
            self.freq[key] += 1
        else:
            if len(self.cache) >= self.capacity:
                # Evict least frequent
                lfu_key = min(self.cache, key=lambda k: self.freq.get(k, 0))
                del self.cache[lfu_key]
                self.freq.pop(lfu_key, None)
            self.cache[key] = CacheEntry(
                key=key, value=f"v_{key}", created_at=now,
                last_access=now, access_count=1
            )
            self.freq[key] = 1

# ============================================================
# 3. EXPERIMENT RUNNER
# ============================================================
def simulate_request(key: int, cache, now: float, rng: random.Random) -> Dict:
    """Simulate one request, return metrics."""
    hit, stale = cache.get(key, now)
    
    if hit:
        latency = BASE_LATENCY_MS + (STALE_PENALTY_MS if stale else 0)
        # Add jitter
        latency += rng.gauss(0, 0.3)
        latency = max(0.1, latency)
        return {"hit": True, "stale": stale, "latency_ms": latency}
    else:
        cache.put(key, now)
        latency = MISS_LATENCY_MS + rng.gauss(0, 3.0)
        latency = max(1.0, latency)
        return {"hit": False, "stale": False, "latency_ms": latency}

def run_experiment(trace: List[int], cache_class, cache_kwargs: dict,
                   noise_seed: int, warmup: int = WARMUP_REQUESTS) -> Dict:
    """Run one repetition of the experiment."""
    rng = random.Random(noise_seed)
    cache = cache_class(**cache_kwargs)
    
    latencies = []
    hits = 0
    stale_hits = 0
    misses = 0
    total = 0
    
    # Simulate time: each request spaced by ~0.5s average
    sim_time = 0.0
    
    for i, key in enumerate(trace):
        sim_time += rng.expovariate(2.0)  # avg 0.5s between requests
        result = simulate_request(key, cache, sim_time, rng)
        
        if i >= warmup:
            latencies.append(result["latency_ms"])
            total += 1
            if result["hit"]:
                hits += 1
                if result["stale"]:
                    stale_hits += 1
            else:
                misses += 1
    
    # Compute percentiles
    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(0.50 * n)]
    p95 = latencies[int(0.95 * n)]
    p99 = latencies[int(0.99 * n)]
    p999 = latencies[min(int(0.999 * n), n - 1)]
    mean_lat = sum(latencies) / n
    
    hit_rate = hits / total if total > 0 else 0
    stale_rate = stale_hits / hits if hits > 0 else 0
    
    return {
        "hit_rate": hit_rate,
        "stale_rate": stale_rate,
        "mean_latency_ms": mean_lat,
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "p99_latency_ms": p99,
        "p999_latency_ms": p999,
        "total_requests": total,
        "hits": hits,
        "misses": misses,
        "stale_hits": stale_hits,
        "latency_distribution": latencies,
    }

# ============================================================
# 4. MAIN EXECUTION
# ============================================================
def main():
    print("=" * 60)
    print("P05-1: Caching Strategy Comparison Experiment")
    print("=" * 60)
    
    # Step 1: Generate fixed trace (deterministic)
    print("\n[1] Generating fixed request trace...")
    trace = generate_trace(TRACE_LENGTH, UNIQUE_KEYS, ZIPF_ALPHA, SEED)
    print(f"    Trace length: {len(trace)}, Unique keys: {UNIQUE_KEYS}")
    print(f"    Zipf alpha: {ZIPF_ALPHA}, Cache capacity: {CACHE_CAPACITY}")
    
    # Save trace hash for reproducibility
    trace_hash = hashlib.sha256(json.dumps(trace).encode()).hexdigest()[:16]
    print(f"    Trace SHA256 prefix: {trace_hash}")
    
    # Step 2: Run experiments
    print("\n[2] Running experiments...")
    
    lru_results = []
    lfu_results = []
    
    for rep in range(N_REPETITIONS):
        noise_seed = SEED + rep * 1000 + 7
        
        # LRU + fixed TTL
        r_lru = run_experiment(
            trace, LRUCache,
            {"capacity": CACHE_CAPACITY, "ttl": FIXED_TTL},
            noise_seed
        )
        lru_results.append(r_lru)
        
        # LFU + adaptive TTL
        r_lfu = run_experiment(
            trace, LFUCache,
            {"capacity": CACHE_CAPACITY, "ttl_min": ADAPTIVE_TTL_MIN, "ttl_max": ADAPTIVE_TTL_MAX},
            noise_seed
        )
        lfu_results.append(r_lfu)
        
        print(f"    Rep {rep+1}/{N_REPETITIONS}: "
              f"LRU hit={r_lru['hit_rate']:.4f} stale={r_lru['stale_rate']:.4f} p99={r_lru['p99_latency_ms']:.1f}ms | "
              f"LFU hit={r_lfu['hit_rate']:.4f} stale={r_lfu['stale_rate']:.4f} p99={r_lfu['p99_latency_ms']:.1f}ms")
    
    # Step 3: Aggregate results
    print("\n[3] Aggregating results...")
    
    def aggregate(results):
        keys = ["hit_rate", "stale_rate", "mean_latency_ms", "p50_latency_ms",
                "p95_latency_ms", "p99_latency_ms", "p999_latency_ms"]
        agg = {}
        for k in keys:
            vals = [r[k] for r in results]
            agg[k] = {
                "mean": sum(vals) / len(vals),
                "std": (sum((v - sum(vals)/len(vals))**2 for v in vals) / len(vals)) ** 0.5,
                "min": min(vals),
                "max": max(vals),
            }
        # Merge latency distributions for full distribution analysis
        all_lat = []
        for r in results:
            all_lat.extend(r["latency_distribution"])
        all_lat.sort()
        n = len(all_lat)
        agg["full_distribution"] = {
            "p50": all_lat[int(0.50 * n)],
            "p95": all_lat[int(0.95 * n)],
            "p99": all_lat[int(0.99 * n)],
            "p999": all_lat[min(int(0.999 * n), n - 1)],
            "count": n,
        }
        return agg
    
    lru_agg = aggregate(lru_results)
    lfu_agg = aggregate(lfu_results)
    
    # Step 4: Decision analysis
    print("\n[4] Decision analysis against predeclared thresholds...")
    
    hitrate_diff = abs(lru_agg["hit_rate"]["mean"] - lfu_agg["hit_rate"]["mean"])
    p99_ratio = (lfu_agg["p99_latency_ms"]["mean"] - lru_agg["p99_latency_ms"]["mean"]) / lru_agg["p99_latency_ms"]["mean"]
    stale_reduction = (lru_agg["stale_rate"]["mean"] - lfu_agg["stale_rate"]["mean"]) / lru_agg["stale_rate"]["mean"] if lru_agg["stale_rate"]["mean"] > 0 else 0
    
    print(f"    Hit rate delta: {hitrate_diff:.4f} (threshold: {DECISION_HITRATE_DELTA})")
    print(f"    P99 change: {p99_ratio*100:+.1f}% (threshold: +{DECISION_P99_INCREASE*100:.0f}%)")
    print(f"    Stale reduction: {stale_reduction*100:+.1f}% (threshold: {DECISION_STALE_REDUCTION*100:.0f}%)")
    
    decisions = {
        "hitrate_equivalent": hitrate_diff < DECISION_HITRATE_DELTA,
        "p99_acceptable": p99_ratio <= DECISION_P99_INCREASE,
        "stale_improved": stale_reduction >= DECISION_STALE_REDUCTION,
    }
    
    print(f"\n    Hit rate equivalent (<{DECISION_HITRATE_DELTA:.0%}): {decisions['hitrate_equivalent']}")
    print(f"    P99 acceptable (<={DECISION_P99_INCREASE:.0%} increase): {decisions['p99_acceptable']}")
    print(f"    Stale improved (>={DECISION_STALE_REDUCTION:.0%} reduction): {decisions['stale_improved']}")
    
    if decisions["hitrate_equivalent"] and decisions["p99_acceptable"] and decisions["stale_improved"]:
        decision = "LFU+adaptive-TTL preferred: equivalent hit rate, acceptable tail latency, significantly reduced stale data"
    elif decisions["hitrate_equivalent"] and decisions["p99_acceptable"]:
        decision = "No clear winner: equivalent hit rate and tail latency, stale data difference insufficient"
    elif not decisions["p99_acceptable"]:
        decision = "LRU+fixed-TTL preferred: LFU tail latency increase exceeds threshold"
    else:
        decision = "Inconclusive: trade-offs require domain-specific weighting"
    
    print(f"\n    DECISION: {decision}")
    
    # Step 5: Generate output JSON for artifact
    output = {
        "experiment_id": "P05-1-caching-comparison",
        "trace": {
            "length": TRACE_LENGTH,
            "unique_keys": UNIQUE_KEYS,
            "zipf_alpha": ZIPF_ALPHA,
            "cache_capacity": CACHE_CAPACITY,
            "sha256_prefix": trace_hash,
        },
        "config": {
            "fixed_ttl": FIXED_TTL,
            "adaptive_ttl_min": ADAPTIVE_TTL_MIN,
            "adaptive_ttl_max": ADAPTIVE_TTL_MAX,
            "freshness_threshold": FRESHNESS_THRESHOLD,
            "warmup_requests": WARMUP_REQUESTS,
            "n_repetitions": N_REPETITIONS,
            "seed": SEED,
        },
        "thresholds": {
            "hitrate_delta": DECISION_HITRATE_DELTA,
            "p99_increase": DECISION_P99_INCREASE,
            "stale_reduction": DECISION_STALE_REDUCTION,
        },
        "lru": {
            "per_repetition": [{k: r[k] for k in ["hit_rate", "stale_rate", "mean_latency_ms", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "p999_latency_ms", "total_requests"]} for r in lru_results],
            "aggregate": {k: v for k, v in lru_agg.items() if k != "full_distribution"},
            "full_distribution": lru_agg["full_distribution"],
        },
        "lfu": {
            "per_repetition": [{k: r[k] for k in ["hit_rate", "stale_rate", "mean_latency_ms", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "p999_latency_ms", "total_requests"]} for r in lfu_results],
            "aggregate": {k: v for k, v in lfu_agg.items() if k != "full_distribution"},
            "full_distribution": lfu_agg["full_distribution"],
        },
        "decisions": decisions,
        "decision_text": decision,
    }
    
    # Save results
    with open("/root/tp-agent/work/m6346/docs/skills_training/P05-1_results.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\n[5] Results saved to P05-1_results.json")
    
    # Generate latency distribution data for plotting
    # Create binned histogram data
    def make_histogram(latencies, n_bins=50):
        mn, mx = min(latencies), max(latencies)
        bins = [0] * n_bins
        edges = []
        width = (mx - mn) / n_bins
        for i in range(n_bins):
            lo = mn + i * width
            hi = mn + (i + 1) * width
            edges.append((lo + hi) / 2)
            for lat in latencies:
                if lo <= lat < hi:
                    bins[i] += 1
        return edges, bins
    
    lru_edges, lru_bins = make_histogram([l for r in lru_results for l in r["latency_distribution"]])
    lfu_edges, lfu_bins = make_histogram([l for r in lfu_results for l in r["latency_distribution"]])
    
    hist_data = {
        "lru": {"edges": lru_edges, "counts": lru_bins},
        "lfu": {"edges": lfu_edges, "counts": lfu_bins},
    }
    with open("/root/tp-agent/work/m6346/docs/skills_training/P05-1_histogram.json", "w") as f:
        json.dump(hist_data, f)
    
    print("[6] Histogram data saved to P05-1_histogram.json")
    return output

if __name__ == "__main__":
    main()
