"""派发前可行性先验 —— 时间留出背测 + shuffle 负控。

为什么必须时间留出
------------------
战役是一波一波播的：先验会漂移（早期波次节点多、成熟度低；后期波次偏深水区）。
随机切分会把同一波次的节点同时塞进训练与测试，给出乐观的假分数。因此**按 plan 的
创建时刻切分**：训练窗（早）→ 校准窗（中）→ 测试窗（晚），三窗**计划不相交**，
并且校准窗晚于训练窗（Platt/isotonic 的拟合数据也必须在训练窗之后，否则校准本身污染）。

校准数据从哪来（本节点最重要的设计决定，实测驱动的）
----------------------------------------------------
最初方案是「校准窗（中段计划）上拟合 Platt/isotonic」。实测证伪：中段窗口的原始分数
**局部判别力≈0**（calib 窗 raw AUC 0.498），20~30 个负例上的 2 参拟合把斜率学成负数，
把测试窗排名整个翻转（AUC 0.673 → 0.327）。修法不是调超参，而是换校准数据来源：
**训练窗内的 K 折（按计划分组）out-of-fold 原始分数**——样本量 3 倍、判别力真实存在、
且每条分数都由没见过它的模型产生（无泄漏）。中段窗口保留为**独立监控窗**，专门报告
「窗口局部判别力」以暴露漂移；单窗拟合作为诊断变体 ``*_window`` 保留在账本里，不粉饰。

先验漂移修正（第二个实测驱动的设计决定）
----------------------------------------
校准之后仍有系统性等比偏差：训练窗验收率 0.84、测试窗 0.90，概率整体压低，
Brier 反而输给气候基准（0.110 vs 0.092）。做法是标准的 **prior shift 修正**
（Saerens 等 2002 的对数几率平移）：形状（斜率/单调映射）来自 OOF，**水平**来自
「最近一个已完整结局的窗口」的先验 ``π_cal``：
``p = σ(a·s + b + Δ)``，``Δ = logit(π_cal) − logit(π_train)``。
``π_cal`` 用校准窗标签估，窗内节点在测试窗之前全部结束 → 派发时刻可得，不偷看未来。
保序变体用同样的对数几率平移（单调性不变）。

shuffle 负控
------------
把标签在样本内随机置换（破坏特征-标签相关性，保留类别比例与特征分布），整套
「标准化 → 逻辑回归 → Platt/isotonic 校准 → 测试窗评估」重跑 N 次：若无标签泄漏，
测试窗 AUC 应落在 0.5 附近、Brier skill ≤ 0。观测值相对置换零分布的右尾概率即
经验 p 值 ``(1 + #{null ≥ obs}) / (N + 1)``。这是「模型不是靠随机/泄漏变好」的最强
机器可验反驳。
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Iterable, Sequence

import numpy as np

from . import calibrate as cal
from .features import FEATURE_NAMES, FeatureSet

VARIANTS = ("climatology", "raw", "platt", "isotonic", "platt_prior", "isotonic_prior")
#: 诊断变体：只在中段校准窗上拟合（记录「单窗校准会翻排名」这一失败模式）
DIAGNOSTIC_VARIANTS = ("platt_window", "isotonic_window")


# ───────────────────────────── 切分 ─────────────────────────────

@dataclasses.dataclass
class Split:
    train: list[int]
    calib: list[int]
    test: list[int]
    detail: dict[str, Any]

    def to_dict(self) -> dict:
        return {"sizes": {"train": len(self.train), "calib": len(self.calib),
                          "test": len(self.test)}, **self.detail}


def _plan_order(fs: FeatureSet, indices: Sequence[int]) -> list[int]:
    """计划按 (plan_created_at, plan_id) 升序；只保留在 indices 里出现过的计划。"""
    first_seen: dict[int, tuple[str, int]] = {}
    for i in indices:
        pid = fs.plan_ids[i]
        key = (fs.plan_created_at[i], pid)
        if pid not in first_seen or key < first_seen[pid]:
            first_seen[pid] = key
    return [pid for pid, _ in sorted(first_seen.items(), key=lambda kv: kv[1])]


def _has_both_classes(fs: FeatureSet, indices: Iterable[int]) -> bool:
    ys = {fs.labels[i] for i in indices}
    return 0 in ys and 1 in ys


def time_split(fs: FeatureSet, train_frac: float = 0.6, calib_frac: float = 0.2,
               min_class: int = 3) -> Split:
    """按计划时间切三窗；在候选边界里挑「每窗两类别都够 min_class」且最接近目标比例的组合。

    复杂度：计划数 P 的 O(P²) 候选枚举，但类别计数走前缀和，单次 O(1)（P~300 时毫秒级）。
    """
    idx = fs.labeled_indices()
    if len(idx) < 20:
        raise ValueError(f"labeled samples too few for backtest: {len(idx)}")
    plans = _plan_order(fs, idx)
    by_plan: dict[int, list[int]] = {p: [] for p in plans}
    for i in idx:
        by_plan[fs.plan_ids[i]].append(i)

    n = len(idx)
    pos_cum = [0]                 # pos_cum[k] = 前 k 个计划的累计正例
    cnt_cum = [0]
    for p in plans:
        sub = by_plan[p]
        pos = sum(1 for i in sub if fs.labels[i] == 1)
        pos_cum.append(pos_cum[-1] + pos)
        cnt_cum.append(cnt_cum[-1] + len(sub))

    def counts(lo: int, hi: int) -> tuple[int, int]:
        """计划区间 [lo, hi) 的 (正, 负)。"""
        pos = pos_cum[hi] - pos_cum[lo]
        total = cnt_cum[hi] - cnt_cum[lo]
        return pos, total - pos

    target_train = train_frac * n
    target_calib = calib_frac * n
    best: tuple[float, int, int] | None = None
    P = len(plans)
    for i in range(1, P - 1):
        if min(counts(0, i)) < min_class:
            continue
        for j in range(i + 1, P):
            if min(counts(i, j)) < min_class:
                continue
            if min(counts(j, P)) < min_class:
                continue
            cost = (abs(cnt_cum[i] - target_train) / n
                    + abs(cnt_cum[j] - cnt_cum[i] - target_calib) / n)
            if best is None or cost < best[0]:
                best = (cost, i, j)
            # 计划区间一旦超过目标量级，继续右移只会更差（单调性剪枝）
            if cnt_cum[j] - cnt_cum[i] > target_calib + n * 0.2:
                break

    if best is None:
        raise ValueError("no valid time split found (need both classes with "
                         f">= {min_class} samples in train/calib/test)")
    _, i, j = best
    train = [k for p in plans[:i] for k in by_plan[p]]
    calib_ = [k for p in plans[i:j] for k in by_plan[p]]
    test = [k for p in plans[j:] for k in by_plan[p]]
    detail = {
        "criterion": "按计划 created_at 切分：train(早) / calib(中) / test(晚)，计划互不相交",
        "plans": {"train": len(plans[:i]), "calib": len(plans[i:j]), "test": len(plans[j:])},
        "window": {
            "train": _window(fs, train), "calib": _window(fs, calib_), "test": _window(fs, test),
        },
        "class_counts": {"train": counts(0, i), "calib": counts(i, j), "test": counts(j, P)},
        "plan_boundaries": {"train_last_plan": plans[i - 1], "calib_first_plan": plans[i],
                            "calib_last_plan": plans[j - 1], "test_first_plan": plans[j]},
        "split_cost": round(best[0], 6),
    }
    return Split(train=train, calib=calib_, test=test, detail=detail)


def _window(fs: FeatureSet, indices: Sequence[int]) -> dict[str, str | None]:
    times = [fs.created_at[i] for i in indices]
    plans = [fs.plan_created_at[i] for i in indices]
    return {
        "node_created_at": f"{min(times)} … {max(times)}" if times else None,
        "plan_created_at": f"{min(plans)} … {max(plans)}" if plans else None,
    }


# ───────────────────────────── 管线 ─────────────────────────────

@dataclasses.dataclass
class Pipeline:
    """标准化 → 逻辑回归（原始分数）→ Platt / isotonic 校准。

    校准映射拟合在**训练窗 K 折 out-of-fold 原始分数**上（按计划分组，避免同计划泄漏）；
    ``window_platt`` / ``window_isotonic`` 是「只在中段校准窗上拟合」的诊断变体（保留以记录失败模式）。
    """

    standardizer: cal.Standardizer
    base: cal.LogisticModel
    platt: cal.PlattCalibrator
    isotonic: cal.IsotonicCalibrator
    prior: float                       # 训练窗验收率（气候基准）
    window_platt: cal.PlattCalibrator | None = None
    window_isotonic: cal.IsotonicCalibrator | None = None
    oof: dict[str, Any] | None = None
    prior_calib: float | None = None       # 中段（最近完整结局）窗口的验收率
    prior_shift: float | None = None       # Δ = logit(π_cal) − logit(π_train)
    feature_names: tuple[str, ...] = FEATURE_NAMES

    # -- 拟合 --------------------------------------------------------
    @classmethod
    def fit(cls, fs: FeatureSet, split: Split, l2: float = 1.0,
            n_folds: int = 5) -> "Pipeline":
        Xtr = fs.matrix(split.train)
        ytr = _labels(fs, split.train)
        std = cal.Standardizer().fit(Xtr)
        base = cal.LogisticModel(l2=l2).fit(std.transform(Xtr), ytr)

        oof_scores = oof_raw_scores(fs, split.train, l2=l2, n_folds=n_folds)
        platt = cal.PlattCalibrator().fit(oof_scores, ytr)
        iso = cal.IsotonicCalibrator().fit(oof_scores, ytr)

        Xca = fs.matrix(split.calib)
        yca = _labels(fs, split.calib)
        raw_ca = base.decision_function(std.transform(Xca))
        w_platt = cal.PlattCalibrator().fit(raw_ca, yca)
        w_iso = cal.IsotonicCalibrator().fit(raw_ca, yca)

        w_platt_free = cal.PlattCalibrator(ridge=0.0,
                                           slope_bounds=(-1e9, 1e9)).fit(raw_ca, yca)
        oof = {
            "n_oof": int(len(oof_scores)),
            "n_folds": int(n_folds),
            "fold_unit": "plan（同计划节点同折，避免同波次泄漏）",
            "auc_oof": cal.auc(oof_scores, ytr),
            "mean_oof": float(np.mean(oof_scores)),
            "std_oof": float(np.std(oof_scores)),
            "calib_window_raw_auc": cal.auc(raw_ca, yca),
            "calib_window_pos_rate": float(yca.mean()),
            "platt_slope_oof": float(platt.a),
            "platt_slope_window": float(w_platt.a),
            "platt_slope_window_unconstrained": float(w_platt_free.a),
            "window_fit_degenerate": bool(w_platt_free.a <= 0 or abs(w_platt_free.a) > 20),
            "diagnosis": (
                "OOF 校准斜率为正且保序；中段单窗无约束拟合斜率 %.3f → %s"
                % (w_platt_free.a,
                   "退化/反号（会把预测排名翻转），故校准数据必须走 OOF"
                   if (w_platt_free.a <= 0 or abs(w_platt_free.a) > 20)
                   else "与 OOF 同号但水平拟合更差（见 *_window 诊断变体）")),
        }
        prior_calib = float(yca.mean())
        delta = float(cal.logit(np.array([prior_calib]))[0]
                      - cal.logit(np.array([float(ytr.mean())]))[0])
        oof["prior_train"] = float(ytr.mean())
        oof["prior_calib"] = prior_calib
        oof["prior_shift_logodds"] = delta
        return cls(standardizer=std, base=base, platt=platt, isotonic=iso,
                   prior=float(ytr.mean()), window_platt=w_platt,
                   window_isotonic=w_iso, oof=oof,
                   prior_calib=prior_calib, prior_shift=delta)

    # -- 预测 --------------------------------------------------------
    def raw(self, X) -> np.ndarray:
        return self.base.decision_function(self.standardizer.transform(np.asarray(X, dtype=float)))

    def predict(self, X) -> dict[str, np.ndarray]:
        X = np.asarray(X, dtype=float)
        raw = self.raw(X)
        out = {
            "climatology": np.full(len(X), self.prior),
            "raw": cal.sigmoid(raw),
            "platt": self.platt.predict(raw),
            "isotonic": self.isotonic.predict(raw),
        }
        if self.prior_shift is not None:
            shape_platt = cal.logit(self.platt.predict(raw))
            shape_iso = cal.logit(self.isotonic.predict(raw))
            out["platt_prior"] = cal.sigmoid(shape_platt + self.prior_shift)
            out["isotonic_prior"] = cal.sigmoid(shape_iso + self.prior_shift)
        if self.window_platt is not None:
            out["platt_window"] = self.window_platt.predict(raw)
        if self.window_isotonic is not None:
            out["isotonic_window"] = self.window_isotonic.predict(raw)
        return out

    # -- 序列化 ------------------------------------------------------
    def to_dict(self) -> dict:
        d = {
            "feature_names": list(self.feature_names),
            "standardizer": self.standardizer.to_dict(),
            "base_logistic": self.base.to_dict(),
            "platt": self.platt.to_dict(),
            "isotonic": self.isotonic.to_dict(),
            "prior": self.prior,
            "prior_calib": self.prior_calib,
            "prior_shift": self.prior_shift,
            "oof": self.oof,
        }
        if self.window_platt is not None:
            d["window_platt"] = self.window_platt.to_dict()
        if self.window_isotonic is not None:
            d["window_isotonic"] = self.window_isotonic.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Pipeline":
        return cls(
            standardizer=cal.Standardizer.from_dict(d["standardizer"]),
            base=cal.LogisticModel.from_dict(d["base_logistic"]),
            platt=cal.PlattCalibrator.from_dict(d["platt"]),
            isotonic=cal.IsotonicCalibrator.from_dict(d["isotonic"]),
            prior=float(d["prior"]),
            window_platt=(cal.PlattCalibrator.from_dict(d["window_platt"])
                          if d.get("window_platt") else None),
            window_isotonic=(cal.IsotonicCalibrator.from_dict(d["window_isotonic"])
                             if d.get("window_isotonic") else None),
            oof=d.get("oof"),
            prior_calib=d.get("prior_calib"),
            prior_shift=d.get("prior_shift"),
            feature_names=tuple(d.get("feature_names") or FEATURE_NAMES),
        )

    def coefficients(self) -> list[dict]:
        rows = [{"feature": n, "coef": float(c)}
                for n, c in zip(self.feature_names, self.base.coef)]
        rows.sort(key=lambda r: (-abs(r["coef"]), r["feature"]))
        return rows


def oof_raw_scores(fs: FeatureSet, train_idx: Sequence[int], l2: float = 1.0,
                   n_folds: int = 5) -> np.ndarray:
    """训练窗内按计划分组的 K 折 out-of-fold 原始分数（每条分数来自没见过它的模型）。"""
    train_idx = list(train_idx)
    plans = _plan_order(fs, train_idx)
    fold_of_plan = {p: k % n_folds for k, p in enumerate(plans)}
    out = np.full(len(train_idx), np.nan)
    plan_ids = [fs.plan_ids[i] for i in train_idx]
    y = _labels(fs, train_idx)
    for k in range(n_folds):
        test_pos = [j for j, p in enumerate(plan_ids) if fold_of_plan[p] == k]
        train_pos = [j for j, p in enumerate(plan_ids) if fold_of_plan[p] != k]
        if not test_pos or not train_pos:
            continue
        Xtr = fs.matrix([train_idx[j] for j in train_pos])
        ytr = y[train_pos]
        if len(set(ytr.tolist())) < 2:      # 折内单类：用先验 logit 兜底
            out[test_pos] = 0.0
            continue
        std = cal.Standardizer().fit(Xtr)
        m = cal.LogisticModel(l2=l2).fit(std.transform(Xtr), ytr)
        Xte = fs.matrix([train_idx[j] for j in test_pos])
        out[test_pos] = m.decision_function(std.transform(Xte))
    if np.isnan(out).any():                 # pragma: no cover - 防御
        out = np.nan_to_num(out, nan=0.0)
    return out


def _labels(fs: FeatureSet, indices: Sequence[int]) -> np.ndarray:
    return np.asarray([fs.labels[i] for i in indices], dtype=float)


# ───────────────────────────── 评估 ─────────────────────────────

def evaluate(pipe: Pipeline, fs: FeatureSet, split: Split, n_bins: int = 10) -> dict:
    """测试窗评估（薄包装，保留 split 语义）。"""
    return evaluate_indices(pipe, fs, split.test, n_bins=n_bins)


def evaluate_indices(pipe: Pipeline, fs: FeatureSet, indices: Sequence[int],
                     n_bins: int = 10) -> dict:
    """在给定样本上对四个主变体 + 两个诊断变体算全套指标（同一批样本，可直接比）。"""
    X = fs.matrix(indices)
    y = _labels(fs, indices)
    preds = pipe.predict(X)
    out: dict[str, Any] = {"n": int(len(y)), "pos_rate": float(y.mean()),
                           "variants": {}, "diagnostic_variants": {}}
    for name in VARIANTS + DIAGNOSTIC_VARIANTS:
        if name not in preds:
            continue
        p = preds[name]
        e_u = cal.ece(p, y, n_bins=n_bins, strategy="uniform")
        e_q = cal.ece(p, y, n_bins=n_bins, strategy="quantile")
        out["variants" if name in VARIANTS else "diagnostic_variants"][name] = {
            "brier": cal.brier(p, y),
            "brier_skill_vs_train_prior": cal.brier_skill(p, y, pipe.prior),
            "auc": cal.auc(p, y),
            "log_loss": cal.log_loss(p, y),
            "ece_uniform10": {k: v for k, v in e_u.items() if k != "table"},
            "ece_quantile10": {k: v for k, v in e_q.items() if k != "table"},
            "mean_pred": float(np.mean(p)),
            "reliability_uniform": e_u["table"],
            "reliability_quantile": e_q["table"],
        }
    return out


# ───────────────────────────── 负控 ─────────────────────────────

def permuted_labels(fs: FeatureSet, y_perm: np.ndarray) -> FeatureSet:
    """返回浅拷贝：labeled 位置的标签替换为 y_perm（特征列不变）。"""
    labels = list(fs.labels)
    for pos, i in enumerate(fs.labeled_indices()):
        labels[i] = int(y_perm[pos])
    return dataclasses.replace(fs, labels=labels)


#: 进程池全局（避免每次置换都 pickle 一次特征集）
_WORKER: dict[str, Any] = {}


def _perm_seeds(seed: int, n_perm: int) -> list[int]:
    """每个置换一个独立子种子：串行/并行结果完全一致（可复算的关键）。"""
    rng = np.random.default_rng(seed)
    return [int(v) for v in rng.integers(0, 2 ** 31 - 1, size=n_perm)]


def _init_worker(fs: FeatureSet, split: Split, variant: str) -> None:
    _WORKER["fs"] = fs
    _WORKER["split"] = split
    _WORKER["variant"] = variant


def _one_perm(perm_seed: int) -> tuple[float, float]:
    fs: FeatureSet = _WORKER["fs"]
    split: Split = _WORKER["split"]
    variant: str = _WORKER["variant"]
    rng = np.random.default_rng(perm_seed)
    idx = fs.labeled_indices()
    y = _labels(fs, idx)
    fs_p = permuted_labels(fs, rng.permutation(y))
    pipe = Pipeline.fit(fs_p, split)
    Xte = fs_p.matrix(split.test)
    yte = _labels(fs_p, split.test)
    p = pipe.predict(Xte)[variant]
    return cal.auc(p, yte), cal.brier_skill(p, yte, pipe.prior)


def shuffle_control(fs: FeatureSet, observed_test: dict, n_perm: int = 50,
                    seed: int = 20261110, variant: str = "platt_prior",
                    train_frac: float = 0.6, calib_frac: float = 0.2,
                    jobs: int = 1) -> dict:
    """标签置换负控：整套管线重跑 N 次，给观测 AUC/BSS 的经验 p 值。

    ``jobs > 1`` 走进程池；每个置换用 ``seed`` 派生的独立子种子，因此**串并行结果一致**。
    """
    split = time_split(fs, train_frac=train_frac, calib_frac=calib_frac)
    seeds = _perm_seeds(seed, n_perm)
    if jobs > 1 and n_perm > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(jobs, n_perm),
                                 initializer=_init_worker,
                                 initargs=(fs, split, variant)) as ex:
            results = list(ex.map(_one_perm, seeds, chunksize=max(1, n_perm // (jobs * 4))))
    else:
        _init_worker(fs, split, variant)
        results = [_one_perm(s) for s in seeds]

    null_auc = [a for a, _ in results if not math.isnan(a)]
    null_bss = [b for _, b in results]
    obs_auc = observed_test["variants"][variant]["auc"]
    obs_bss = observed_test["variants"][variant]["brier_skill_vs_train_prior"]
    null_auc_arr = np.asarray(null_auc)
    null_bss_arr = np.asarray(null_bss)
    p_auc = (1 + int(np.sum(null_auc_arr >= obs_auc))) / (len(null_auc_arr) + 1)
    p_bss = (1 + int(np.sum(null_bss_arr >= obs_bss))) / (len(null_bss_arr) + 1)
    return {
        "criterion": ("标签在样本内随机置换后整套管线重跑；无泄漏则应 AUC≈0.5、BSS≤0，"
                      "观测值落在置换零分布右尾 → 经验 p 值"),
        "variant": variant,
        "n_perm": int(n_perm),
        "seed": int(seed),
        "jobs": int(jobs),
        "null_auc": {"mean": float(null_auc_arr.mean()) if len(null_auc_arr) else None,
                     "std": float(null_auc_arr.std(ddof=1)) if len(null_auc_arr) > 1 else None,
                     "min": float(null_auc_arr.min()) if len(null_auc_arr) else None,
                     "max": float(null_auc_arr.max()) if len(null_auc_arr) else None,
                     "values": [round(float(v), 6) for v in null_auc]},
        "null_bss": {"mean": float(null_bss_arr.mean()),
                     "std": float(null_bss_arr.std(ddof=1)) if len(null_bss_arr) > 1 else None,
                     "max": float(null_bss_arr.max()),
                     "values": [round(float(v), 6) for v in null_bss]},
        "observed": {"auc": obs_auc, "brier_skill": obs_bss},
        "p_value_auc": float(p_auc),
        "p_value_bss": float(p_bss),
        "null_auc_within_0p05_of_half": bool(abs(float(null_auc_arr.mean()) - 0.5) <= 0.05)
        if len(null_auc_arr) else None,
        "null_bss_max_le_0p05": bool(float(null_bss_arr.max()) <= 0.05),
    }


def auc_array(p: np.ndarray, y: np.ndarray) -> float:
    return cal.auc(p, y)

# ───────────────────────────── 对照实验 ─────────────────────────────

def ablation_calibration_source(fs: FeatureSet, split: Split, l2: float = 1.0) -> dict:
    """校准数据来源对照：中段单窗（无正则 / 有正则）vs 训练窗 OOF，同一测试窗比。

    这是本节点把「设计决定」变成可复现证据的开关：单窗无约束拟合斜率会反号（AUC 被翻转
    成 1−AUC），正则化只稳住排名，水平拟合要靠先验平移补齐。
    """
    Xtr = fs.matrix(split.train)
    ytr = _labels(fs, split.train)
    std = cal.Standardizer().fit(Xtr)
    base = cal.LogisticModel(l2=l2).fit(std.transform(Xtr), ytr)
    Xca = fs.matrix(split.calib)
    yca = _labels(fs, split.calib)
    raw_ca = base.decision_function(std.transform(Xca))
    Xte = fs.matrix(split.test)
    yte = _labels(fs, split.test)
    raw_te = base.decision_function(std.transform(Xte))

    prior_train = float(ytr.mean())
    prior_calib = float(yca.mean())
    delta = float(cal.logit(np.array([prior_calib]))[0]
                  - cal.logit(np.array([prior_train]))[0])
    maps = {
        "window_unregularized": cal.PlattCalibrator(ridge=0.0,
                                                    slope_bounds=(-1e9, 1e9)).fit(raw_ca, yca),
        "window_regularized": cal.PlattCalibrator().fit(raw_ca, yca),
        "oof": cal.PlattCalibrator().fit(
            oof_raw_scores(fs, split.train, l2=l2), ytr),
    }
    variants: dict[str, Any] = {}
    for name, mapping in maps.items():
        base_p = mapping.predict(raw_te)
        for suffix, pr in (("", base_p), ("_prior_shift",
                                          cal.sigmoid(cal.logit(base_p) + delta))):
            variants[name + suffix] = {
                "slope": float(mapping.a), "intercept": float(mapping.b),
                "prior_shift": delta if suffix else 0.0,
                "test_brier": cal.brier(pr, yte),
                "test_brier_skill": cal.brier_skill(pr, yte, prior_train),
                "test_auc": cal.auc(pr, yte),
                "test_ece_uniform10": cal.ece(pr, yte)["ece"],
                "rank_inverted": bool(mapping.a <= 0),
            }
    inverted = variants["window_unregularized"]["rank_inverted"]
    return {
        "schema": "pre_dispatch_prior/ablation/v1",
        "question": "校准映射的数据来源：中段单窗 vs 训练窗 OOF（同一测试窗比）",
        "criterion": "事后 AUC 应保持约 0.65~0.70；斜率≤0 = 排名被校准翻转",
        "n": {"train": len(split.train), "calib": len(split.calib), "test": len(split.test)},
        "class_counts": split.detail["class_counts"],
        "variants": variants,
        "verdict": (
            "单窗无正则拟合斜率反号（AUC 被翻转）：校准数据来源必须用 OOF；"
            "单窗正则化只稳住排名，水平拟合靠先验平移补齐"
            if inverted else
            "单窗未退化，但水平拟合精度仍低于 OOF+先验平移（见 brier/skill 两列）"),
    }


# ───────────────────────────── 派发前实盘 ─────────────────────────────

def pending_priors(pipe: Pipeline, nodes: Sequence[Any], variant: str = "platt_prior",
                   top_k: int = 5) -> dict:
    """对**当前仍在 pending 的节点**算派发前先验（真实用法：这些节点还没有结局）。

    逐节点按因果序推进 ``HistoryState``：算到某 pending 节点时，历史里只有比它更早的节点，
    结局标签只有已完成的那些 —— 与真实派发时刻看到的信息量一致。
    """
    from . import features as ft

    state = ft.HistoryState()
    rows: list[dict[str, Any]] = []
    for n in sorted(nodes, key=lambda x: x.sort_key):
        if n.status == "pending":
            x = np.asarray(state.features_for(n), dtype=float)
            pr = pipe.predict(x.reshape(1, -1))[variant][0]
            rows.append({
                "node_id": n.node_id, "plan_id": n.plan_id,
                "created_at": n.created_at,
                "p_accept": float(pr),
                "criteria_n": len(ft.criteria_items(n.metadata)),
                "dep_count": len(n.dependencies),
                "repo": n.repo,
            })
        state.observe(n)
    if not rows:
        return {"n": 0, "note": "当前没有 pending 节点"}
    ps = np.asarray([r["p_accept"] for r in rows])
    ordered = sorted(rows, key=lambda r: r["p_accept"])
    return {
        "n": len(rows),
        "variant": variant,
        "note": "pending 节点无结局；此表是派发前先验（只能用更早节点信息），不是验收结论",
        "p_mean": float(ps.mean()),
        "p_p10": float(np.quantile(ps, 0.10)),
        "p_p50": float(np.quantile(ps, 0.50)),
        "p_p90": float(np.quantile(ps, 0.90)),
        "lowest": ordered[:top_k],
        "highest": ordered[-top_k:][::-1],
    }
