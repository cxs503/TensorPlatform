"""复核带裁决通道的证据生成器：跑一遍真实判定代码 → 落证据 json + 出图。

产出（默认 ``artifacts/proposal_gate/review_band/``）：
* ``evidence.json`` —— 场景逐步结果（verdict/state/score）、四态计数 before/after、
  阈值（全部来自 ``config/proposal_gate.json``）、守卫触发记录、pupil 图 sha256；
* ``review_band_lifecycle.png`` —— 左：相似度判据轴 + 三个区间 + 场景实测落点；
  右：裁决前后四态计数对比。

纪律：数字全部由本脚本调 ``ProposalGate`` 真跑得出（零手抄）；阈值只从配置读，
脚本内不写字面量。运行：``python3 -m tools.proposal_gate.review_band_evidence``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .config import GateConfig, default_config_path, load_config  # noqa: E402
from .gate import GateTransitionError, ProposalGate  # noqa: E402
from .ledger import LedgerStore  # noqa: E402

# 场景文本（合成域名 foo/bar/widget，零领域语义）：A = 已入池提案，B = 其同义改写。
PROPOSAL_A = {
    "schema_version": "2.0",
    "title": "foo 通道 产物契约 去重器 覆盖率账本 三道机器门",
    "repo": "foo",
    "goal": {
        "objective": "为 foo 通道建立产物契约、去重器与覆盖率账本三道机器门",
        "criteria": ["重复率 < 0.15", "播种覆盖率 ≥ 0.80", "误判率 ≤ 0.05"],
    },
    "dependencies_hint": ["门-契约", "门-去重"],
    "is_maturity": True,
}
PROPOSAL_B = {
    "schema_version": "2.0",
    "title": "foo 通道 产物契约 校验 相似提案 拦截 播种核对",
    "repo": "foo",
    "goal": {
        "objective": "为 foo 通道建立相似提案拦截与播种核对两道机器闸",
        # 判据/依赖与 A 同量级：使相似度差异集中在主题陈述上（同义改写形态）
        "criteria": ["重复率 < 0.15", "播种覆盖率 ≥ 0.80", "误判率 ≤ 0.05"],
    },
    "dependencies_hint": ["门-契约", "门-去重"],
    "is_maturity": True,
}
PROPOSAL_BAD = {  # 判据无量化阈值 → 契约门 fail-closed
    "schema_version": "2.0",
    "title": "widget 通道 体验优化",
    "repo": "widget",
    "goal": {"objective": "把 widget 通道做得更好", "criteria": ["效果好", "更稳", "更省"]},
    "is_maturity": True,
}
PROPOSAL_DUP = {  # 与 A 几乎逐字相同 → 自动拒
    "schema_version": "2.0",
    "title": "foo 通道 产物契约 去重器 覆盖率账本 三道机器门",
    "repo": "foo",
    "goal": {
        "objective": "为 foo 通道建立产物契约、去重器与覆盖率账本三道机器门",
        "criteria": ["重复率 < 0.15", "播种覆盖率 ≥ 0.80", "误判率 ≤ 0.05"],
    },
    "dependencies_hint": ["门-契约", "门-去重"],
    "is_maturity": True,
}


def _synth(cfg: GateConfig) -> GateConfig:
    data = json.loads(json.dumps(dict(cfg.data)))
    data["repo_whitelist"] = ["foo", "bar", "widget"]
    return GateConfig.from_dict(data, path=cfg.path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(out_dir: Path, cfg: GateConfig | None = None) -> dict[str, Any]:
    base = cfg if cfg is not None else load_config()
    cfg = _synth(base)
    review = dict(cfg.need("review"))
    dup_threshold = float(cfg.need("dedup.dup_threshold"))
    review_threshold = float(cfg.need("dedup.review_threshold"))

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="review_band_"))
    store = LedgerStore.for_sqlite(str(tmp / "scenario.sqlite"), cfg)
    gate = ProposalGate(
        cfg, store, quarantine_dir=tmp / "quarantine", events_file=tmp / "events.jsonl"
    )

    steps: list[dict[str, Any]] = []

    def _rec(name: str, res) -> None:
        steps.append(
            {
                "step": name,
                "verdict": res.verdict,
                "state": res.state,
                "score": round(float(res.score or 0.0), 6),
                "dup_of_kind": (res.dup_of or "").split(":")[0] if res.dup_of else None,
            }
        )

    _rec("submit_A", gate.evaluate(PROPOSAL_A, mission_id=7301))
    _rec("submit_B_twin", gate.evaluate(PROPOSAL_B, mission_id=7302))
    _rec("submit_bad_contract", gate.evaluate(PROPOSAL_BAD, mission_id=7303, persist_quarantine=False))
    _rec("submit_dup_of_A", gate.evaluate(PROPOSAL_DUP, mission_id=7304))

    before = dict(store.stats().counts)
    band = gate.review_band()

    guards: list[dict[str, Any]] = []
    band_sha = band[0]["sha256"] if band else "0" * 64
    for name, kwargs in (
        ("adopt_band_row_without_review", {"mission_id": 7302, "sha256": band_sha}),
        ("adjudicate_contract_failure", None),
    ):
        try:
            if kwargs is None:
                gate.adjudicate(
                    mission_id=7303,
                    sha256=next(
                        (r.sha256 for r in store.rows(state="quarantined", limit=100) if r.verdict == "quarantined"),
                        "0" * 64,
                    ),
                    decision="admit",
                    reviewer="evidence-bot",
                    note="试图放行契约不合格提案（应被拒）",
                )
            else:
                gate.adopt(**kwargs)
            guards.append({"guard": name, "blocked": False, "code": None})
        except GateTransitionError as exc:
            guards.append({"guard": name, "blocked": True, "code": exc.code})

    admitted = gate.adjudicate(
        mission_id=7302,
        sha256=band_sha,
        decision="admit",
        reviewer="evidence-bot",
        note="复核确认目标与依赖集合不同，非同一立项",
    )
    after = dict(store.stats().counts)

    # ── 出图 ────────────────────────────────────────────────────────────────
    png = out_dir / "review_band_lifecycle.png"
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.0))

    scores = [(s["step"], s["score"], s["verdict"]) for s in steps if s["score"] is not None]
    zones = [
        (0.0, review_threshold, "#ecf0f1", "pass zone"),
        (review_threshold, dup_threshold, "#fdebd0", "review band"),
        (dup_threshold, 1.0, "#fadbd8", "auto reject"),
    ]
    for lo, hi, color, label in zones:
        ax1.axvspan(lo, hi, color=color, label=f"{label} [{lo:.2f}, {hi:.2f})")
    for name, value, color in (
        ("review_threshold", review_threshold, "#e67e22"),
        ("dup_threshold", dup_threshold, "#c0392b"),
    ):
        ax1.axvline(value, color=color, ls="--", lw=1.4)
    palette = {"accepted": "#27ae60", "near_duplicate": "#e67e22", "duplicate": "#c0392b",
               "quarantined": "#7f8c8d"}
    for i, (step, score, verdict) in enumerate(scores):
        ax1.scatter([score], [i], s=70, color=palette.get(verdict, "#34495e"), zorder=3)
        ax1.annotate(f"{step}: {verdict}\nscore={score:.4f}", (score, i),
                     textcoords="offset points", xytext=(6, 4), fontsize=7)
    ax1.set_yticks(range(len(scores)))
    ax1.set_yticklabels([s[0] for s in scores], fontsize=7)
    ax1.set_xlim(0, 1)
    ax1.set_xlabel("similarity score (char 3-gram Jaccard/containment)")
    ax1.set_title("Measured scores vs configured zones")
    ax1.legend(fontsize=7, loc="lower left")

    states = sorted(set(before) | set(after))
    idx = range(len(states))
    ax2.bar([i - 0.2 for i in idx], [before.get(s, 0) for s in states], width=0.4,
            label="before adjudication", color="#95a5a6")
    ax2.bar([i + 0.2 for i in idx], [after.get(s, 0) for s in states], width=0.4,
            label="after admit(twin)", color="#2980b9")
    ax2.set_xticks(list(idx))
    ax2.set_xticklabels(states, fontsize=8)
    ax2.set_ylabel("ledger rows")
    ax2.set_title("Four-state ledger: review band drained by explicit decision")
    ax2.legend(fontsize=8)
    for i, s in enumerate(states):
        ax2.annotate(str(before.get(s, 0)), (i - 0.2, before.get(s, 0)), ha="center",
                     va="bottom", fontsize=8)
        ax2.annotate(str(after.get(s, 0)), (i + 0.2, after.get(s, 0)), ha="center",
                     va="bottom", fontsize=8)
    fig.tight_layout()
    fig.savefig(png, dpi=150)
    plt.close(fig)

    evidence = {
        "scenario": "proposal review band lifecycle (synthetic domains foo/bar/widget)",
        "config_path": str(default_config_path()),
        "thresholds": {"review_threshold": review_threshold, "dup_threshold": dup_threshold},
        "review_config": {
            "queue_states": list(review["queue_states"]),
            "queue_verdicts": list(review["queue_verdicts"]),
            "decisions": sorted(dict(review["decisions"])),
            "min_note_length": review["min_note_length"],
            "adopt_allowed_states": list(review["adopt_allowed_states"]),
        },
        "steps": steps,
        "review_band_size": len(band),
        "review_band_top": {
            "score": band[0]["score"] if band else None,
            "dup_of": band[0]["dup_of"] if band else None,
        },
        "guards": guards,
        "adjudication": {
            "decision": "admit",
            "state_before": "quarantined",
            "state_after": admitted.state,
            "verdict_after": admitted.verdict,
            "reasons": list(admitted.reasons),
        },
        "counts": {"before": before, "after": after},
        "figure": {"path": str(png), "sha256": _sha256(png)},
    }
    (out_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    store.dispose()
    return evidence


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="复核带裁决通道证据生成")
    ap.add_argument("--out", default="artifacts/proposal_gate/review_band")
    args = ap.parse_args(argv)
    ev = run(Path(args.out))
    print(json.dumps({k: ev[k] for k in ("steps", "guards", "counts", "figure")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())