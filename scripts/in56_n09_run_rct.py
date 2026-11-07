"""IN56-N09 评分器行为稳健性 RCT 执行器。

三个子命令（可由 ``all`` 一次跑完，其中每次重复都在**独立子进程**里跑）：

    prereg   生成预注册表（分配表+种子+MDE+裁决规则），并自校验哈希可复现
    run      --pass {0,1,2}  跑一遍判级（=一次重复），写 runs/m6180/pass_<n>.json
    analyze  合并三遍 → 翻转率/Cohen d/95%CI/MDE/负控一致率 → artifacts + 图 + 报告 JSON

用法:
    TENSOR_DSN=... python3 scripts/in56_n09_run_rct.py all
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluation_ops import perturbation_rct as rct  # noqa: E402

ART = ROOT / "artifacts/IN56_N09"
RUNS = ROOT / "runs/m6180"
CORPUS = ART / "base_corpus.json"


def load_corpus(path: Path = CORPUS) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def base_sandbox_root() -> Path:
    return RUNS / "sandbox"


def cmd_prereg(args) -> int:
    bases = load_corpus()
    prereg = rct.build_preregistration(bases, seed=args.seed, reps=args.reps)
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "preregistration.json").write_text(
        json.dumps(prereg, ensure_ascii=False, indent=1), encoding="utf-8")
    # 重跑一致性：同种子重建分配表，哈希必须相等
    again = rct.build_preregistration(load_corpus(), seed=args.seed, reps=args.reps)
    same = again["assignment_sha256"] == prereg["assignment_sha256"]
    # 检出面物化（受控条件，见模块 docstring）
    root = base_sandbox_root()
    n_files = 0
    for b in bases:
        d = rct.materialize_sandbox(b, root)
        n_files += sum(1 for p in d.rglob("*") if p.is_file())
    summary = {
        "seed": prereg["seed"], "reps": prereg["reps"], "n_bases": prereg["n_bases"],
        "n_gradings": prereg["n_gradings"],
        "assignment_sha256": prereg["assignment_sha256"],
        "prereg_sha256": prereg["prereg_sha256"],
        "regenerate_hash_equal": bool(same),
        "sandbox_files": n_files,
    }
    (ART / "preregistration_check.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if same else 1


def cmd_run(args) -> int:
    bases = load_corpus()
    prereg = json.loads((ART / "preregistration.json").read_text(encoding="utf-8"))
    rows = rct.run_pass(bases, prereg["assignment"], sandbox_root=base_sandbox_root(),
                        rep=args.pass_n, n_seats=args.seats)
    # 落盘前压缩：理由正文只留长度与 sha1（正文可由同一输入重放复现），控制提交体积
    for r in rows:
        r["reason_chars"] = len(r.pop("reason", "") or "")
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"pass_{args.pass_n}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"pass {args.pass_n}: {len(rows)} gradings -> {out}")
    return 0


def cmd_analyze(args) -> int:
    rows: list[dict] = []
    for i in range(args.reps):
        p = RUNS / f"pass_{i}.json"
        if not p.exists():
            raise SystemExit(f"missing {p}; run --pass {i} first")
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    bases = load_corpus()
    stratum_of = {int(b["mission_id"]): str(b["stratum"]) for b in bases}
    separation = rct.separation_report(bases)
    impure = [arm for arm, v in separation["arms"].items() if not v["pure"]]
    if impure:
        raise SystemExit(f"separation gate failed (impure arms: {impure}): {separation}")
    stats = rct.analyze(rows, seed=args.seed, B=args.bootstrap, stratum_of=stratum_of)
    stats["separation"] = separation
    stats["strata_labels"] = {"report": "有实质报告的提交（20 条）",
                              "summary": "只交执行摘要的提交（20 条）"}
    stats["inputs"] = {
        "base_corpus": str(CORPUS.relative_to(ROOT)),
        "preregistration": str((ART / "preregistration.json").relative_to(ROOT)),
        "passes": [str((RUNS / f"pass_{i}.json").relative_to(ROOT)) for i in range(args.reps)],
        "tools": ["tools/evaluation_ops/perturbation_rct.py",
                  "scripts/in56_n09_run_rct.py",
                  "scripts/in56_n09_harvest_corpus.py"],
    }
    (ART / "analysis.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps({k: stats[k] for k in ("n_bases", "n_gradings", "replay", "verdicts")},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_all(args) -> int:
    cmd_prereg(args)
    for i in range(args.reps):
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "run", "--pass", str(i),
             "--reps", str(args.reps), "--seats", str(args.seats)],
            check=True, cwd=str(ROOT),
        )
    return cmd_analyze(args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("prereg", "run", "analyze", "all"))
    ap.add_argument("--seed", type=int, default=rct.DEFAULT_SEED)
    ap.add_argument("--reps", type=int, default=rct.DEFAULT_REPS)
    ap.add_argument("--seats", type=int, default=3)
    ap.add_argument("--bootstrap", type=int, default=10000)
    ap.add_argument("--pass", dest="pass_n", type=int, default=0)
    args = ap.parse_args(argv)
    return {"prereg": cmd_prereg, "run": cmd_run, "analyze": cmd_analyze,
            "all": cmd_all}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())