"""合成夹具（域中立）：派单日志 DGP + 蒸馏产物骨架，用于测试与审计灵敏度。

为什么合成
----------
真实平台日志在仓库里不落盘（体积+隐私），实时拉取又不可复算，所以测试用**已知真值**的
数据生成过程（DGP）驱动：验收概率是写死的 logistic 函数（``TRUE_COEF``），成本是写死的
对数线性函数，标签是显式 Bernoulli 抽样。这样"模型学到东西"和"模型作弊"都能被区分：
学对了 → precision@20% 明显高于基线；作弊（读事后字段）→ 指标跳变被泄漏审计抓到。

DGP（每条记录）::

    z    = b0 + b_repo * (repo_quality - 0.55) + b_nov * novelty + b_len * (log_len - 6.8)
    y    ~ Bernoulli(sigmoid(z))
    cost = exp(c0 + c_budget * log_budget + c_len * log_len + N(0, 0.4))

名字全部是 ``r00…`` / ``t0001`` 这类合成 token，不含任何物理域词。
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = ["REPOS", "TRUE_COEF", "make_raw", "make_distill_bundle", "dump_bundle"]

REPOS: tuple[str, ...] = tuple(f"r{i:02d}" for i in range(6))
#: 各仓的"基础验收质量"（真值；模型只能通过历史通过率间接看到它）
REPO_QUALITY: dict[str, float] = {
    "r00": 0.80, "r01": 0.68, "r02": 0.55, "r03": 0.45, "r04": 0.32, "r05": 0.22,
}
_WORDS: tuple[str, ...] = (
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi",
)

#: 真值系数（测试断言用）
TRUE_COEF: dict[str, float] = {
    "intercept": -0.30,
    "b_repo": 3.2,
    "b_nov": 1.6,
    "b_len": -0.30,
    "c0": 4.2,
    "c_budget": 0.55,
    "c_len": 0.22,
}


def make_raw(
    n: int = 400,
    *,
    seed: int = 3,
    start: datetime | None = None,
) -> list[dict[str, Any]]:
    """生成合成派单日志（``build_records`` 的原样输入形状）。

    主题机制让"新颖性"可被文本代理看见：55% 的候选复用最近 5 条里出现过的主题（真值新颖性
    0.2，文本与历史高度重合），45% 开新主题（真值新颖性 1.0）；标签由真值新颖性 + repo 历史
    通过率 + 长度项按 :data:`TRUE_COEF` 抽样。
    """
    rng = random.Random(seed)
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo_state: dict[str, list[int]] = {r: [] for r in REPOS}
    last_words: list[str] = []
    last_topic = -1
    rows: list[dict[str, Any]] = []
    next_topic = 0
    for i in range(n):
        repo = rng.choice(REPOS)
        hist = repo_state[repo]
        repo_pass = (sum(hist) + 2.0) / (len(hist) + 4.0)
        if last_words and rng.random() < 0.55:
            # 复用上一条的词表（真值新颖性 0.2；文本与历史高度重合，代理应能看见）
            words = list(last_words)
            topic = last_topic
            novelty_true = 0.2
        else:
            topic = next_topic
            next_topic += 1
            words = [f"w{topic}_{k}" for k in range(8)]
            novelty_true = 1.0
        goal = f"t{i:04d} topic{topic} " + " ".join(words[: rng.randint(4, 8)])
        log_len = math.log1p(len(goal))
        log_budget = round(math.log1p(rng.choice([80, 120, 200, 400, 800])), 4)
        z = (
            TRUE_COEF["intercept"]
            + TRUE_COEF["b_repo"] * (REPO_QUALITY[repo] - 0.55)
            + TRUE_COEF["b_nov"] * novelty_true
            + TRUE_COEF["b_len"] * (log_len - 6.8)
        )
        p = 1.0 / (1.0 + math.exp(-z))
        passed = rng.random() < p
        cost = math.exp(
            TRUE_COEF["c0"]
            + TRUE_COEF["c_budget"] * log_budget
            + TRUE_COEF["c_len"] * log_len
            + rng.gauss(0.0, 0.4)
        )
        created = start + timedelta(hours=i * 0.5)
        rows.append(
            {
                "id": i + 1,
                "repo": repo,
                "goal": goal,
                "budget": {"max_tool_calls": int(round(math.exp(log_budget) - 1))},
                "co_repos": [],
                "parent_mission_id": None,
                "created_at": created.isoformat(),
                "finished_at": (created + timedelta(minutes=rng.randint(5, 120))).isoformat(),
                "status": "succeeded" if passed else "failed",
                "result": {
                    "accepted": passed,
                    "tool_calls": round(float(cost), 2),
                    "headline": "ok" if passed else "crash",
                    "fail_stage": None if passed else "gate",
                },
            }
        )
        last_words = words
        last_topic = topic
        repo_state[repo].append(1 if passed else 0)
    return rows


def make_distill_bundle(
    *,
    n_seeds: int = 3,
    mission_ids: list[int] | None = None,
    novelty: list[float] | None = None,
    seed_dir: str = "synthetic/out",
) -> dict[str, Any]:
    """合成蒸馏产物骨架（``load_distill_bundle`` 的返回值形状）。"""
    ids = mission_ids or [1, 2, 3, 4, 5]
    nov = novelty or [0.7, 0.62, 0.9]
    seeds = []
    for k in range(n_seeds):
        src = ids[k % len(ids)]
        quote = f"synthetic gap {k}"
        seeds.append(
            {
                "seed_id": f"seed-{k:04d}",
                "cluster_id": k,
                "title": f"candidate {k}",
                "gap_description": quote * 2,
                "size": 1 + k,
                "kind_counts": {"limitation": 1 + k},
                "sources": [
                    {
                        "node_key": str(src),
                        "mission_id": src,
                        "kind": "limitation",
                        "source_path": f"synthetic/{src}/report.json",
                        "source_sha256": f"{src:064d}",
                        "pointer": "$.markdown#section[0]",
                        "quote": quote,
                        "quote_sha256": f"{k:064d}",
                    }
                ],
                "gate": {
                    "novelty": nov[k % len(nov)],
                    "max_sim": 1.0 - nov[k % len(nov)],
                    "mean_sim": 0.01,
                    "match_ref_id": "synthetic/ref#0",
                    "match_sha256": "0" * 64,
                },
            }
        )
    return {
        "dir": seed_dir,
        "seeds_sha256": "1" * 64,
        "run_sha256": "2" * 64,
        "seeds": {"seeds": seeds, "accepted": seeds, "gate": {"passed": n_seeds}},
        "run": {},
    }


def dump_bundle(bundle: dict[str, Any], out_dir: str) -> str:  # pragma: no cover - IO
    from pathlib import Path

    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    (base / "seeds.json").write_text(
        json.dumps(bundle["seeds"], ensure_ascii=False), encoding="utf-8"
    )
    (base / "run.json").write_text(json.dumps(bundle["run"], ensure_ascii=False), encoding="utf-8")
    return str(base)