"""参数面：一切阈值/窗口/口径都是数据，不写死在逻辑里（零领域语义）。

本包把「蒸馏出的提案种子」接到「派单排序」上：所有可调量集中在 :class:`SeedDispatchParams`，
可用 ``--config <json>`` 覆盖；未知字段或越界值直接报错（不静默吞）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

__all__ = ["ConfigError", "SeedDispatchParams", "load_params"]


class ConfigError(ValueError):
    """非法配置（未知字段 / 类型不符 / 越界）。"""


@dataclass(frozen=True)
class SeedDispatchParams:
    # --- 排序口径 -------------------------------------------------------
    top_frac: float = 0.2            # precision@top_frac（判据 d 的 @20%）
    windows: int = 5                 # walk-forward 窗口数（按 created_at 时序切）
    min_train: int = 300             # 首个评估窗口之前的最小训练样本数
    ridge: float = 1.0               # L2 强度（对标准化后的设计矩阵）
    epochs: int = 400                # 逻辑回归梯度步数（确定性全批梯度）
    lr: float = 0.35                 # 学习率
    random_repeats: int = 25         # 随机基线重复次数（取均值）
    bootstrap: int = 400             # 种子簇自助抽样次数（置信区间）
    gain_floor: float = 0.05         # EVOI 的 gain 下限（防全零）

    # --- 新颖性通道（与蒸馏层同一口径）---------------------------------
    novelty_pool: int = 200          # EMA 参照池容量（历史文档数）
    novelty_alpha: float = 0.02      # EMA 衰减（每篇新文档）
    token_min_len: int = 3           # 拉丁词最小长度
    cjk_ngram: int = 2               # 中日韩字符 n-gram 阶数
    hash_bits: int = 13              # 令牌哈希桶数 = 2**bits

    # --- 行为策略（propensity 重建）-------------------------------------
    fifo_beta: float = 2.0           # 静态 FIFO 优先级 softmax 温度
    explore_eps: float = 0.05        # 平台探索预算（均匀混合下限）
    min_slate: int = 2               # 一轮 slate 的最少条目数
    slate_hours: int = 1             # slate 时间粒度（小时）

    # --- 判据阈值（任务书数字，作为数据注入）---------------------------
    min_parse_coverage: float = 0.90
    min_cluster_consistency: float = 0.80
    max_mean_sim: float = 0.40
    novelty_floor: float = 0.55
    min_precision_gain_pp: float = 10.0
    leakage_tol: float = 0.02        # 扰动替换指标允许漂移
    leakage_sensitivity: float = 0.03  # 事后字段泄漏注入必须被检出（指标至少跳这么多）
    min_coverage: float = 0.85       # 代码覆盖率判据
    min_gating_precision: float = 0.70   # 新颖性门控精度（真值口径，小样本容忍）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIELD_NAMES = {f.name for f in fields(SeedDispatchParams)}


def load_params(overrides: Mapping[str, Any] | None = None,
                path: str | Path | None = None) -> SeedDispatchParams:
    """合并 JSON 配置与显式覆盖；未知字段/越界值报错。"""
    data: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"config not found: {p}")
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - 防御
            raise ConfigError(f"config is not JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError("config must be a JSON object")
        data.update(loaded)
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})
    unknown = sorted(set(data) - _FIELD_NAMES)
    if unknown:
        raise ConfigError(f"unknown parameter(s): {unknown}")
    for key, value in data.items():
        default = getattr(SeedDispatchParams, key)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"{key} must be bool")
        elif isinstance(default, int):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigError(f"{key} must be int")
        elif isinstance(default, float):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"{key} must be a number")
            value = float(value)
        data[key] = value
    params = SeedDispatchParams(**data)
    _validate(params)
    return params


def _validate(p: SeedDispatchParams) -> None:
    if not 0.0 < p.top_frac <= 1.0:
        raise ConfigError("top_frac must be in (0, 1]")
    if p.windows < 2:
        raise ConfigError("windows must be >= 2")
    if p.min_train < 20:
        raise ConfigError("min_train must be >= 20")
    if p.min_slate < 1:
        raise ConfigError("min_slate must be >= 1")
    if not 0.0 <= p.explore_eps < 1.0:
        raise ConfigError("explore_eps must be in [0, 1)")
    if not 0.0 <= p.leakage_tol:
        raise ConfigError("leakage_tol must be >= 0")
    if p.novelty_pool < 1 or not 0.0 < p.novelty_alpha <= 1.0:
        raise ConfigError("novelty_pool >= 1 and novelty_alpha in (0, 1]")
    if not 1 <= p.cjk_ngram <= 4:
        raise ConfigError("cjk_ngram must be in [1, 4]")
    if not 6 <= p.hash_bits <= 20:
        raise ConfigError("hash_bits must be in [6, 20]")
    if not 0.0 < p.gain_floor <= 1.0:
        raise ConfigError("gain_floor must be in (0, 1]")