#!/usr/bin/env python3
"""黄金集构建器：生成 ``fixtures/golden_set.json`` + ``fixtures/golden_set.sha256``。

**只跑一次**（构建），标签与证据由本脚本按"证据原型 → 期望等级"的显式表写定，
之后冻结。重跑会得到逐字节相同文件（无时间戳、无随机量），冻结清单只应在
明确要改黄金集时更新。

用法::

    python3 -m tools.maturity_inference.fixtures.build_golden
    python3 -m tools.maturity_inference.fixtures.build_golden --check   # 只校验冻结
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from ..evidence import build_node

HERE = Path(__file__).resolve().parent
TARGET = HERE / "golden_set.json"
SHA_FILE = HERE / "golden_set.sha256"

RECORDED_AT = "2026-11-10T00:00:00+00:00"
PRODUCER = "maturity-inference-golden-builder"

#: 40 位小写 hex 的合成摘要（确定性，便于逐字节复现）
def _sha(tag: str) -> str:
    return hashlib.sha256(f"golden::{tag}".encode("utf-8")).hexdigest()


def _impl(key: str, tag: str, *, scope: str = "repo", bad_sha: bool = False) -> dict:
    return build_node(
        key, "implemented",
        kind="source", locator=f"{key.replace('.', '/')}/impl.py",
        artifact_sha256=("ZZ-not-a-sha" if bad_sha else _sha(f"impl::{tag}")),
        producer=PRODUCER, recorded_at=RECORDED_AT, scope=scope,
    )


def _tested(key: str, tag: str, *, scope: str = "repo", status: str = "passed",
            drop_cmd: bool = False) -> dict:
    rerun = {"status": status, "executed_at": RECORDED_AT,
             "log_sha256": _sha(f"log::{tag}")}
    if not drop_cmd:
        rerun["cmd"] = f"pytest -q tests/test_{tag}.py"
    return build_node(
        key, "tested",
        kind="pytest", locator=f"tests/test_{tag}.py",
        artifact_sha256=_sha(f"test::{tag}"),
        producer=PRODUCER, recorded_at=RECORDED_AT, scope=scope, rerun=rerun,
    )


def _bench(key: str, tag: str, *, scope: str = "repo", value: float = 0.5,
           ref: float = 0.5, tol: float = 0.01, drop_tol: bool = False,
           satisfied: bool = True) -> dict:
    criterion = {"name": "rel_err", "value": value, "baseline_ref": ref,
                 "baseline": "analytic", "satisfied": satisfied, "unit": "-"}
    if not drop_tol:
        criterion["tolerance"] = tol
    return build_node(
        key, "benchmark_verified",
        kind="artifact", locator=f"artifacts/{tag}/bench.json",
        artifact_sha256=_sha(f"bench::{tag}"),
        producer=PRODUCER, recorded_at=RECORDED_AT, scope=scope,
        rerun={"cmd": f"python3 -m {tag}.bench", "status": "passed",
               "executed_at": RECORDED_AT, "log_sha256": _sha(f"benchlog::{tag}")},
        criterion=criterion,
    )


def _coupled(key: str, tag: str, *, scope: str = "repo", components=None) -> dict:
    return build_node(
        key, "coupled_prototype",
        kind="artifact", locator=f"artifacts/{tag}/e2e.json",
        artifact_sha256=_sha(f"e2e::{tag}"),
        producer=PRODUCER, recorded_at=RECORDED_AT, scope=scope,
        rerun={"cmd": f"pytest -q tests/test_{tag}_e2e.py", "status": "passed",
               "executed_at": RECORDED_AT, "log_sha256": _sha(f"e2elog::{tag}")},
        criterion={"name": "rel_err", "value": 0.4, "baseline_ref": 0.4,
                   "baseline": "reference", "tolerance": 0.02, "satisfied": True,
                   "unit": "-"},
        coupling={"components": components or [f"{key}.left", f"{key}.right"],
                  "interface_digest": _sha(f"iface::{tag}"),
                  "end_to_end_rerun": f"pytest -q tests/test_{tag}_e2e.py"},
    )


def _eng(key: str, tag: str, *, scope: str = "repo", same_repeat: bool = False) -> dict:
    runs = [
        {"sha256": _sha(f"run1::{tag}" if not same_repeat else f"run::{tag}"), "verdict": "pass"},
        {"sha256": _sha(f"run2::{tag}" if not same_repeat else f"run::{tag}"), "verdict": "pass"},
    ]
    return build_node(
        key, "engineering_qualified",
        kind="artifact", locator=f"artifacts/{tag}/qualification.json",
        artifact_sha256=_sha(f"qual::{tag}"),
        producer=PRODUCER, recorded_at=RECORDED_AT, scope=scope,
        rerun={"cmd": f"pytest -q tests/test_{tag}_e2e.py", "status": "passed",
               "executed_at": RECORDED_AT, "log_sha256": _sha(f"quallog::{tag}")},
        criterion={"name": "rel_err", "value": 0.3, "baseline_ref": 0.3,
                   "baseline": "reference", "tolerance": 0.02, "satisfied": True,
                   "unit": "-"},
        coupling={"components": [f"{key}.left", f"{key}.right"],
                  "interface_digest": _sha(f"iface::{tag}"),
                  "end_to_end_rerun": f"pytest -q tests/test_{tag}_e2e.py"},
        qualification={"reviewer": "seat-independent-verifier", "repeat_runs": runs,
                       "envelope": "operating envelope: bounded inputs, n<=3",
                       "signoff_sha256": _sha(f"signoff::{tag}")},
    )


def _full_ladder(key: str, *, scope: str = "repo") -> list:
    tag = key.replace(".", "_")
    return [
        _impl(key, tag, scope=scope),
        _tested(key, tag, scope=scope),
        _bench(key, tag, scope=scope),
        _coupled(key, tag, scope=scope),
        _eng(key, tag, scope=scope),
    ]


#: 原型 → 期望等级（显式表：这是人工标签，不由引擎算出）
ARCHETYPES = {
    "claim_only": "not_supported",
    "impl_ok": "implemented",
    "impl_bad_sha": "not_supported",
    "tested_ok": "tested",
    "tested_fail": "implemented",
    "tested_missing_cmd": "implemented",
    "bench_ok": "benchmark_verified",
    "bench_out_of_tol": "tested",
    "bench_no_tol": "tested",
    "coupled_ok": "coupled_prototype",
    "coupled_single": "benchmark_verified",
    "eng_ok": "engineering_qualified",
    "eng_same_repeat": "coupled_prototype",
    "fixture_capped": "tested",
    "mock_contradiction": "implemented",
    "withheld_contradiction": "implemented",
}

COUNTS = {
    "claim_only": 3, "impl_ok": 3, "impl_bad_sha": 2, "tested_ok": 4,
    "tested_fail": 3, "tested_missing_cmd": 2, "bench_ok": 4,
    "bench_out_of_tol": 2, "bench_no_tol": 2, "coupled_ok": 3,
    "coupled_single": 2, "eng_ok": 3, "eng_same_repeat": 2,
    "fixture_capped": 2, "mock_contradiction": 2, "withheld_contradiction": 1,
}

CLAIms = {"claim_only": "REAL", "impl_ok": "MOCK", "impl_bad_sha": "REAL",
          "tested_ok": "WITHHELD", "tested_fail": "REAL", "tested_missing_cmd": "REAL",
          "bench_ok": "REAL", "bench_out_of_tol": "REAL", "bench_no_tol": "REAL",
          "coupled_ok": "REAL", "coupled_single": "REAL", "eng_ok": "REAL",
          "eng_same_repeat": "REAL", "fixture_capped": "REAL",
          "mock_contradiction": "REAL", "withheld_contradiction": "REAL"}


def _row(key: str, archetype: str, idx: int) -> dict:
    tag = f"{key.replace('.', '_')}_{idx}"
    nodes: list = []
    contradictions: list = []
    if archetype == "impl_ok":
        nodes = [_impl(key, tag)]
    elif archetype == "impl_bad_sha":
        nodes = [_impl(key, tag, bad_sha=True)]
    elif archetype == "tested_ok":
        nodes = [_impl(key, tag), _tested(key, tag)]
    elif archetype == "tested_fail":
        nodes = [_impl(key, tag), _tested(key, tag, status="failed")]
    elif archetype == "tested_missing_cmd":
        nodes = [_impl(key, tag), _tested(key, tag, drop_cmd=True)]
    elif archetype == "bench_ok":
        nodes = [_impl(key, tag), _tested(key, tag), _bench(key, tag)]
    elif archetype == "bench_out_of_tol":
        nodes = [_impl(key, tag), _tested(key, tag),
                 _bench(key, tag, value=0.62, ref=0.5, tol=0.01)]
    elif archetype == "bench_no_tol":
        nodes = [_impl(key, tag), _tested(key, tag), _bench(key, tag, drop_tol=True)]
    elif archetype == "coupled_ok":
        nodes = [_impl(key, tag), _tested(key, tag), _bench(key, tag), _coupled(key, tag)]
    elif archetype == "coupled_single":
        nodes = [_impl(key, tag), _tested(key, tag), _bench(key, tag),
                 _coupled(key, tag, components=["only.one"])]
    elif archetype == "eng_ok":
        nodes = _full_ladder(key)
    elif archetype == "eng_same_repeat":
        nodes = [_impl(key, tag), _tested(key, tag), _bench(key, tag),
                 _coupled(key, tag), _eng(key, tag, same_repeat=True)]
    elif archetype == "fixture_capped":
        nodes = _full_ladder(key, scope="fixture")
    elif archetype == "mock_contradiction":
        nodes = [_impl(key, tag), _tested(key, tag), _bench(key, tag)]
        contradictions = ["MOCK"]
    elif archetype == "withheld_contradiction":
        nodes = [_impl(key, tag), _tested(key, tag), _bench(key, tag)]
        contradictions = ["WITHHELD"]
    return {
        "capability_key": key,
        "archetype": archetype,
        "expected_level": ARCHETYPES[archetype],
        "claim": CLAIms[archetype],
        "contradictions": contradictions,
        "note": _NOTE[archetype],
        "nodes": nodes,
    }


_NOTE = {
    "claim_only": "只有自述 REAL，零证据 → 不允许抬级",
    "impl_ok": "实现工件 sha256 可追溯 → implemented（自述 MOCK 不改结果）",
    "impl_bad_sha": "实现工件 sha256 形状非法 → fail-closed 到 not_supported",
    "tested_ok": "有重跑命令+通过+日志 sha → tested",
    "tested_fail": "重跑判 failed → 只能 implemented（负控 a 的强化版）",
    "tested_missing_cmd": "缺重跑命令 → 不可执行 → implemented",
    "bench_ok": "判据数值落容差内 → benchmark_verified",
    "bench_out_of_tol": "判据越差 > 容差 → 退回 tested（数字不达标不许挂 bench）",
    "bench_no_tol": "判据缺容差 → 不可判 → tested",
    "coupled_ok": "双组件耦合 + 接口摘要 + 端到端重跑 → coupled_prototype",
    "coupled_single": "耦合只 1 个组件 → 退回 benchmark_verified",
    "eng_ok": "独立复核 + 两次互异 sha 重跑 + 包线 + 签署 → engineering_qualified",
    "eng_same_repeat": "复跑 sha 相同（不独立）→ 退回 coupled_prototype",
    "fixture_capped": "全套证据但作用域=夹具 → 封顶 tested（负控 a）",
    "mock_contradiction": "矛盾证据断言 MOCK → 封顶 implemented（负控 c 的合成镜像）",
    "withheld_contradiction": "矛盾证据断言 WITHHELD → 封顶 implemented",
}


def build() -> dict:
    rows = []
    for archetype, count in COUNTS.items():
        short = archetype.replace("_", "")[:8]
        for i in range(count):
            key = f"synth.{short}{i}." + {"claim_only": "claim", "impl_ok": "impl",
                                        "impl_bad_sha": "badsha", "tested_ok": "test",
                                        "tested_fail": "fail", "tested_missing_cmd": "nocmd",
                                        "bench_ok": "bench", "bench_out_of_tol": "over",
                                        "bench_no_tol": "notol", "coupled_ok": "e2e",
                                        "coupled_single": "one", "eng_ok": "qual",
                                        "eng_same_repeat": "rep", "fixture_capped": "fix",
                                        "mock_contradiction": "mock", "withheld_contradiction": "whld"}[archetype]
            rows.append(_row(key, archetype, i))
    rows.sort(key=lambda r: r["capability_key"])
    return {
        "schema_version": "1.0",
        "title": "五级成熟度推断引擎黄金集（40 行，合成键，零领域语义）",
        "frozen_at": "2026-11-10",
        "labeling_protocol": (
            "标签在跑推断之前按 ARCHETYPES 显式表写定（证据原型 → 期望等级），"
            "由独立于引擎的判读口径给出；引擎不得反读标签。合成键（synth.*）保证零领域语义。"),
        "label_source": "人工标注（本节点作者按冻结判据表给出，标签与引擎实现分离）",
        "levels": ["not_supported", "implemented", "tested", "benchmark_verified",
                   "coupled_prototype", "engineering_qualified"],
        "rows": rows,
    }


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄金集构建/冻结校验")
    ap.add_argument("--check", action="store_true", help="只校验冻结文件与 sha256")
    args = ap.parse_args(argv)
    data = build()
    blob = _dump(data)
    digest = hashlib.sha256(blob).hexdigest()
    if args.check:
        cur = TARGET.read_bytes() if TARGET.exists() else b""
        sha_line = SHA_FILE.read_text(encoding="utf-8").strip() if SHA_FILE.exists() else ""
        ok = cur == blob and sha_line == digest
        print(f"golden_set.json sha256={digest} 冻结一致={ok}")
        return 0 if ok else 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(blob)
    SHA_FILE.write_text(digest + "\n", encoding="utf-8")
    print(f"wrote {TARGET} rows={len(data['rows'])} sha256={digest}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())