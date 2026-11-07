#!/usr/bin/env python3
"""IN27-H5 票箱采集：向 ≥3 个不同模型族并行收取独立票，落 artifacts/in27_h5/ballots.jsonl。

票箱与裁决分离的理由：票一旦落盘即可复算（``in27_h5_replay.py --offline-ballots``），
裁决器本身不依赖网络；票的独立性靠「一族一票 + 独立 HTTP 调用 + 互不可见」保证。

用法：python3 scripts/in27_h5_ballots.py [--families 4] [--timeout 90]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("in27_h5_replay", ROOT / "scripts/in27_h5_replay.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)  # type: ignore[union-attr]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--out", default="artifacts/in27_h5/ballots.jsonl")
    args = ap.parse_args()

    env = replay.load_env(Path("/root/tp-agent/agent.env"))
    base, key = env["LLM_BASE_URL"], env["LLM_API_KEY"]
    models = replay.relay_models(base, key)
    families = replay.pick_families(models)[: args.families]
    if len(families) < 3:
        raise SystemExit(f"独立模型族不足 3 个（实际 {families}），无法满足多族表决门槛")

    charter = replay.load_charter(replay.CHARTER)
    classifier = replay.load_redline_lexicon(replay.LEXICON)
    cases = json.loads(replay.CASES.read_text(encoding="utf-8"))["cases"]

    jobs = []
    for case in cases:
        facts = {}
        for entry in case["facts"]:
            if entry["metric"] == "red_line_categories_hit":
                hits = classifier.classify(
                    " ".join(str(case.get(k, "")) for k in ("question", "scope", "disputed_action"))
                )
                facts[entry["metric"]] = len({h["category"] for h in hits})
            else:
                value, _ = replay.extract_fact(entry, classifier)
                facts[entry["metric"]] = value
        prompt = replay.ballot_prompt(case, facts, charter.cite(case["charter_citations"]))
        jobs.append((case["case_id"], prompt))

    def one(job):
        case_id, family, model, prompt = job
        rec = {"case_id": case_id, "family_id": family, "model_id": model, "ok": False}
        try:
            resp = replay.call_model(base, key, model, prompt, timeout=args.timeout)
            verdict, refs, rationale = replay.parse_verdict(resp["content"])
            rec.update(
                verdict=verdict,
                criteria_refs=refs,
                rationale=rationale,
                latency_ms=resp["latency_ms"],
                response_sha256=resp["response_sha256"],
                cast_at=replay.utc_now(),
                ok=bool(verdict and rationale),
            )
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    wave = [(cid, fam, mdl, prompt) for cid, prompt in jobs for fam, mdl in families]
    with ThreadPoolExecutor(max_workers=len(wave)) as pool:
        ballots = list(pool.map(one, wave))

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for b in ballots:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")
    print(json.dumps(
        {
            "families": families,
            "ballots": len(ballots),
            "valid": sum(1 for b in ballots if b["ok"]),
            "verdicts": {b["case_id"] + "|" + b["family_id"]: b.get("verdict") for b in ballots},
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())