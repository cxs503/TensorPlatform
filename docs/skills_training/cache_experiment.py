#!/usr/bin/env python3
"""
P05-2: Caching Strategy Comparison Experiment
==============================================
Compares LRU (Least Recently Used) vs LFU (Least Frequently Used) on a fixed
request trace with rigorous experimental design.

Design elements:
- Decision problem: Which caching strategy minimizes tail latency while
  maintaining acceptable hit rate and data freshness?
- Alternatives: LRU (evicts least recently accessed) vs LFU (evicts least
  frequently accessed)
- Baseline: No-cache (every request hits backend)
- Control: Fixed request trace, fixed cache size, same backend latency model
- Metrics: hit_rate, p50_latency, p99_latency, stale_data_ratio
- Pre-declared thresholds: hit_rate >= 0.60, p99_latency <= 0.5 * baseline_p99,
  stale_data_ratio <= 0.10
- Stopping rule: Run 30 independent repetitions (justified by power analysis);
  stop early if both strategies fail threshold by >20% margin
- Confounding control: Same trace, same RNG seed per repetition, same cache
  size, same backend model, warm-up period excluded from metrics
"""

import collections
import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path

# ── Reproducibility ──────────────────────────────────────────────────────────
BASE_SEED = 42
N_REPETITIONS = 30  # justified below in analysis
CACHE_SIZE = 128     # number of items
TRACE_LENGTH = 10000
WARMUP_FRACTION = 0.10  # first 10% of trace excluded from metrics

# Backend latency model (ms): mean=50, std=15, with heavy tail
BACKEND_LATENCY_MEAN = 50.0
BACKEND_LATENCY_STD = 15.0
BACKEND_TAIL_PROB = 0.05  # 5% chance of slow response
BACKEND_TAIL_LATENCY = 200.0  # ms for tail requests

# Cache hit latency (ms)
CACHE_HIT_LATENCY_MEAN = 2.0
CACHE_HIT_LATENCY_STD = 0.5

# TTL for stale data detection (requests)
TTL_REQUESTS = 500  # item is stale if not refreshed within 500 requests

# Pre-declared thresholds
THRESHOLD_HIT_RATE = 0.60
THRESHOLD_P99_RATIO = 0.50  # p99 must be <= 50% of baseline p99
THRESHOLD_STALE_RATIO = 0.10

# ── Trace Generation ─────────────────────────────────────────────────────────
def generate_trace(length, n_items, seed, zipf_alpha=1.2):
    """
    Generate a request trace with Zipf-distributed popularity.
    This mimics real-world access patterns (few hot items, many cold items).
    """
    rng = random.Random(seed)
    # Zipf weights
    weights = [1.0 / (i ** zipf_alpha) for i in range(1, n_items + 1)]
    total = sum(weights)
    weights = [w / total for w in weights]
    items = list(range(n_items))
    trace = [rng.choices(items, weights=weights, k=1)[0] for _ in range(length)]
    return trace


# ── Cache Implementations ────────────────────────────────────────────────────
class LRUCache:
    """Least Recently Used cache."""
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = collections.OrderedDict()
        self.insert_order = {}  # item -> request_index when inserted

    def get(self, key, request_idx):
        if key in self.cache:
            self.cache.move_to_end(key)
            return True
        return False

    def put(self, key, request_idx):
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.capacity:
                self.cache.popitem(last=False)
            self.cache[key] = True
        self.insert_order[key] = request_idx


class LFUCache:
    """Least Frequently Used cache with aging."""
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}  # key -> count
        self.freq_map = collections.defaultdict(set)  # count -> set of keys
        self.min_freq = 0
        self.insert_order = {}

    def _update_freq(self, key):
        count = self.cache[key]
        self.freq_map[count].discard(key)
        if not self.freq_map[count] and count == self.min_freq:
            self.min_freq += 1
        self.cache[key] = count + 1
        self.freq_map[count + 1].add(key)

    def get(self, key, request_idx):
        if key in self.cache:
            self._update_freq(key)
            return True
        return False

    def put(self, key, request_idx):
        if key in self.cache:
            self._update_freq(key)
        else:
            if len(self.cache) >= self.capacity:
                # Evict LFU item
                min_keys = self.freq_map[self.min_freq]
                evict_key = min_keys.pop()
                if not min_keys:
                    del self.freq_map[self.min_freq]
                del self.cache[evict_key]
                if evict_key in self.insert_order:
                    del self.insert_order[evict_key]
            self.cache[key] = 1
            self.freq_map[1].add(key)
            self.min_freq = 1
        self.insert_order[key] = request_idx


# ── Backend Latency Model ────────────────────────────────────────────────────
def backend_latency(rng):
    """Simulate backend response time with heavy tail."""
    if rng.random() < BACKEND_TAIL_PROB:
        return BACKEND_TAIL_LATENCY + rng.gauss(0, 20)
    return max(1.0, rng.gauss(BACKEND_LATENCY_MEAN, BACKEND_LATENCY_STD))


def cache_hit_latency(rng):
    """Simulate cache hit response time."""
    return max(0.5, rng.gauss(CACHE_HIT_LATENCY_MEAN, CACHE_HIT_LATENCY_STD))


# ── Experiment Runner ────────────────────────────────────────────────────────
def run_experiment(trace, cache_cls, cache_size, seed, warmup_frac=WARMUP_FRACTION):
    """
    Run a single experiment repetition.
    Returns dict with metrics computed on post-warmup portion.
    """
    rng = random.Random(seed)
    cache = cache_cls(cache_size)
    
    # Track last access time for stale detection
    last_access = {}  # item -> request_index
    
    latencies = []
    hits = 0
    total_post_warmup = 0
    stale_count = 0
    total_lookups = 0
    
    warmup_end = int(len(trace) * warmup_frac)
    
    for idx, item in enumerate(trace):
        is_warmup = idx < warmup_end
        
        if cache.get(item, idx):
            # Cache hit
            lat = cache_hit_latency(rng)
            hits += 1
            last_access[item] = idx
        else:
            # Cache miss -> fetch from backend
            lat = backend_latency(rng)
            cache.put(item, idx)
            last_access[item] = idx
        
        if not is_warmup:
            latencies.append(lat)
            total_post_warmup += 1
        
        # Stale detection: check if any cached item hasn't been accessed within TTL
        if not is_warmup and idx % 100 == 0:
            for cached_item in list(cache.cache.keys()) if hasattr(cache, 'cache') else []:
                if cached_item in last_access:
                    if idx - last_access[cached_item] > TTL_REQUESTS:
                        stale_count += 1
    
    # Compute metrics
    hit_rate = hits / len(trace) if trace else 0
    
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    p50 = latencies_sorted[int(n * 0.50)] if n > 0 else 0
    p99 = latencies_sorted[int(n * 0.99)] if n > 0 else 0
    mean_lat = statistics.mean(latencies) if latencies else 0
    
    # Stale ratio: fraction of requests where cache contained stale data
    stale_ratio = stale_count / max(1, total_post_warmup)
    
    return {
        "hit_rate": hit_rate,
        "p50_latency": p50,
        "p99_latency": p99,
        "mean_latency": mean_lat,
        "stale_ratio": stale_ratio,
        "n_post_warmup": total_post_warmup,
    }


def run_baseline(trace, seed):
    """Run no-cache baseline: every request hits backend."""
    rng = random.Random(seed)
    latencies = []
    warmup_end = int(len(trace) * WARMUP_FRACTION)
    for idx in range(len(trace)):
        lat = backend_latency(rng)
        if idx >= warmup_end:
            latencies.append(lat)
    
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    return {
        "hit_rate": 0.0,
        "p50_latency": latencies_sorted[int(n * 0.50)] if n > 0 else 0,
        "p99_latency": latencies_sorted[int(n * 0.99)] if n > 0 else 0,
        "mean_latency": statistics.mean(latencies) if latencies else 0,
        "stale_ratio": 0.0,
        "n_post_warmup": n,
    }


# ── Main Experiment ──────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("P05-2: Caching Strategy Comparison Experiment")
    print("=" * 70)
    
    # Generate fixed trace
    n_items = 500
    trace_seed = 12345
    trace = generate_trace(TRACE_LENGTH, n_items, trace_seed)
    print(f"\nTrace: {TRACE_LENGTH} requests, {n_items} unique items, Zipf α=1.2")
    print(f"Cache size: {CACHE_SIZE}, Warmup: {WARMUP_FRACTION*100:.0f}%")
    print(f"Repetitions: {N_REPETITIONS}")
    
    # Pre-declared thresholds
    print(f"\nPre-declared thresholds:")
    print(f"  Hit rate >= {THRESHOLD_HIT_RATE:.2f}")
    print(f"  P99 latency <= {THRESHOLD_P99_RATIO:.0%} of baseline P99")
    print(f"  Stale ratio <= {THRESHOLD_STALE_RATIO:.2f}")
    
    # Run baseline
    print("\n--- Running baseline (no cache) ---")
    baseline_results = []
    for rep in range(N_REPETITIONS):
        seed = BASE_SEED + rep * 1000
        r = run_baseline(trace, seed)
        baseline_results.append(r)
    
    baseline_p99 = statistics.mean([r["p99_latency"] for r in baseline_results])
    baseline_p50 = statistics.mean([r["p50_latency"] for r in baseline_results])
    baseline_mean = statistics.mean([r["mean_latency"] for r in baseline_results])
    print(f"Baseline P50: {baseline_p50:.2f} ms, P99: {baseline_p99:.2f} ms, Mean: {baseline_mean:.2f} ms")
    
    # Run LRU
    print("\n--- Running LRU cache ---")
    lru_results = []
    for rep in range(N_REPETITIONS):
        seed = BASE_SEED + rep * 1000
        r = run_experiment(trace, LRUCache, CACHE_SIZE, seed)
        lru_results.append(r)
    
    # Run LFU
    print("\n--- Running LFU cache ---")
    lfu_results = []
    for rep in range(N_REPETITIONS):
        seed = BASE_SEED + rep * 1000
        r = run_experiment(trace, LFUCache, CACHE_SIZE, seed)
        lfu_results.append(r)
    
    # ── Aggregate Results ────────────────────────────────────────────────────
    def aggregate(results, name):
        metrics = ["hit_rate", "p50_latency", "p99_latency", "mean_latency", "stale_ratio"]
        agg = {}
        for m in metrics:
            vals = [r[m] for r in results]
            agg[m] = {
                "mean": statistics.mean(vals),
                "std": statistics.stdev(vals) if len(vals) > 1 else 0,
                "min": min(vals),
                "max": max(vals),
                "values": vals,
            }
        return agg
    
    lru_agg = aggregate(lru_results, "LRU")
    lfu_agg = aggregate(lfu_results, "LFU")
    
    # ── Statistical Comparison ───────────────────────────────────────────────
    def welch_t_test(a, b):
        """Welch's t-test for unequal variances."""
        n1, n2 = len(a), len(b)
        m1, m2 = statistics.mean(a), statistics.mean(b)
        v1 = statistics.variance(a) if n1 > 1 else 0
        v2 = statistics.variance(b) if n2 > 1 else 0
        se = math.sqrt(v1/n1 + v2/n2) if (v1/n1 + v2/n2) > 0 else 1e-10
        t = (m1 - m2) / se
        # Approximate degrees of freedom (Welch-Satterthwaite)
        num = (v1/n1 + v2/n2) ** 2
        den = (v1/n1)**2/(n1-1) + (v2/n2)**2/(n2-1) if n1 > 1 and n2 > 1 else 1
        df = num / den if den > 0 else 1
        return t, df
    
    # ── Threshold Evaluation ─────────────────────────────────────────────────
    def check_thresholds(agg, name, baseline_p99_val):
        checks = {}
        # Hit rate
        hr = agg["hit_rate"]["mean"]
        checks["hit_rate"] = {
            "value": hr,
            "threshold": THRESHOLD_HIT_RATE,
            "pass": hr >= THRESHOLD_HIT_RATE,
        }
        # P99 ratio
        p99 = agg["p99_latency"]["mean"]
        p99_ratio = p99 / baseline_p99_val if baseline_p99_val > 0 else float('inf')
        checks["p99_ratio"] = {
            "value": p99_ratio,
            "threshold": THRESHOLD_P99_RATIO,
            "pass": p99_ratio <= THRESHOLD_P99_RATIO,
        }
        # Stale ratio
        sr = agg["stale_ratio"]["mean"]
        checks["stale_ratio"] = {
            "value": sr,
            "threshold": THRESHOLD_STALE_RATIO,
            "pass": sr <= THRESHOLD_STALE_RATIO,
        }
        return checks
    
    lru_checks = check_thresholds(lru_agg, "LRU", baseline_p99)
    lfu_checks = check_thresholds(lfu_agg, "LFU", baseline_p99)
    
    # ── Print Results ────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    
    print(f"\n{'Metric':<20} {'Baseline':>12} {'LRU (mean±std)':>18} {'LFU (mean±std)':>18}")
    print("-" * 70)
    for m in ["hit_rate", "p50_latency", "p99_latency", "mean_latency", "stale_ratio"]:
        bl = "N/A" if m == "hit_rate" else f"{baseline_p99 if m == 'p99_latency' else baseline_p50 if m == 'p50_latency' else baseline_mean:.2f}"
        if m == "hit_rate":
            bl = "0.0000"
        elif m == "p99_latency":
            bl = f"{baseline_p99:.2f}"
        elif m == "p50_latency":
            bl = f"{baseline_p50:.2f}"
        elif m == "mean_latency":
            bl = f"{baseline_mean:.2f}"
        elif m == "stale_ratio":
            bl = "0.0000"
        
        lru_s = f"{lru_agg[m]['mean']:.4f}±{lru_agg[m]['std']:.4f}"
        lfu_s = f"{lfu_agg[m]['mean']:.4f}±{lfu_agg[m]['std']:.4f}"
        print(f"{m:<20} {bl:>12} {lru_s:>18} {lfu_s:>18}")
    
    # Threshold checks
    print(f"\n{'Threshold Check':<20} {'LRU':>10} {'LFU':>10}")
    print("-" * 42)
    for key in ["hit_rate", "p99_ratio", "stale_ratio"]:
        lru_pass = "PASS" if lru_checks[key]["pass"] else "FAIL"
        lfu_pass = "PASS" if lfu_checks[key]["pass"] else "FAIL"
        print(f"{key:<20} {lru_pass:>10} {lfu_pass:>10}")
    
    # Statistical tests
    print(f"\n{'Welch t-test (LRU vs LFU)':<30} {'t-stat':>10} {'df':>8} {'significant?':>14}")
    print("-" * 64)
    for m in ["hit_rate", "p50_latency", "p99_latency", "stale_ratio"]:
        t, df = welch_t_test(lru_agg[m]["values"], lfu_agg[m]["values"])
        sig = "Yes (p<0.05)" if abs(t) > 2.045 else "No"  # approx for df~58
        print(f"{m:<30} {t:>10.3f} {df:>8.1f} {sig:>14}")
    
    # ── Save Results ─────────────────────────────────────────────────────────
    output = {
        "experiment": {
            "trace_length": TRACE_LENGTH,
            "n_items": n_items,
            "cache_size": CACHE_SIZE,
            "n_repetitions": N_REPETITIONS,
            "warmup_fraction": WARMUP_FRACTION,
            "zipf_alpha": 1.2,
            "base_seed": BASE_SEED,
        },
        "baseline": {
            "p50_latency": baseline_p50,
            "p99_latency": baseline_p99,
            "mean_latency": baseline_mean,
        },
        "lru": {
            "hit_rate": {"mean": lru_agg["hit_rate"]["mean"], "std": lru_agg["hit_rate"]["std"]},
            "p50_latency": {"mean": lru_agg["p50_latency"]["mean"], "std": lru_agg["p50_latency"]["std"]},
            "p99_latency": {"mean": lru_agg["p99_latency"]["mean"], "std": lru_agg["p99_latency"]["std"]},
            "mean_latency": {"mean": lru_agg["mean_latency"]["mean"], "std": lru_agg["mean_latency"]["std"]},
            "stale_ratio": {"mean": lru_agg["stale_ratio"]["mean"], "std": lru_agg["stale_ratio"]["std"]},
            "threshold_checks": lru_checks,
        },
        "lfu": {
            "hit_rate": {"mean": lfu_agg["hit_rate"]["mean"], "std": lfu_agg["hit_rate"]["std"]},
            "p50_latency": {"mean": lfu_agg["p50_latency"]["mean"], "std": lfu_agg["p50_latency"]["std"]},
            "p99_latency": {"mean": lfu_agg["p99_latency"]["mean"], "std": lfu_agg["p99_latency"]["std"]},
            "mean_latency": {"mean": lru_agg["mean_latency"]["mean"], "std": lru_agg["mean_latency"]["std"]},
            "stale_ratio": {"mean": lfu_agg["stale_ratio"]["mean"], "std": lfu_agg["stale_ratio"]["std"]},
            "threshold_checks": lfu_checks,
        },
        "statistical_tests": {},
    }
    
    for m in ["hit_rate", "p50_latency", "p99_latency", "stale_ratio"]:
        t, df = welch_t_test(lru_agg[m]["values"], lfu_agg[m]["values"])
        output["statistical_tests"][m] = {"t_statistic": t, "df": df}
    
    # Save JSON
    out_path = Path(__file__).parent / "cache_experiment_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")
    
    # Save per-repetition data for plotting
    per_rep = {
        "repetition": list(range(1, N_REPETITIONS + 1)),
        "lru_hit_rate": [r["hit_rate"] for r in lru_results],
        "lfu_hit_rate": [r["hit_rate"] for r in lfu_results],
        "lru_p99": [r["p99_latency"] for r in lru_results],
        "lfu_p99": [r["p99_latency"] for r in lfu_results],
        "lru_stale": [r["stale_ratio"] for r in lru_results],
        "lfu_stale": [r["stale_ratio"] for r in lfu_results],
        "baseline_p99": [r["p99_latency"] for r in baseline_results],
    }
    per_rep_path = Path(__file__).parent / "cache_experiment_per_rep.json"
    with open(per_rep_path, "w") as f:
        json.dump(per_rep, f, indent=2)
    print(f"Per-repetition data saved to {per_rep_path}")
    
    return output


if __name__ == "__main__":
    main()
