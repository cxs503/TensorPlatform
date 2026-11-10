"""零领域语义守卫：把"引擎里不许出现应用域词条"从评审习惯变成机器检查。

铁律（TensorPlatform 平台仓）：域词条/域判据/域算例一律经 API / 配置 / 工件注入，
不得硬编码进平台代码。benchmark 引擎是平台机制（谁都能用），所以它必须可证明地
不含任何应用域词汇——**证明方式是扫描，不是自证**。

实现取向（刻意保持"零领域语义"自身成立）
----------------------------------------
* 词表**不在本模块里**：它是外部配置（缺省 ``tools/evidence_domain_lexicon.json``，
  即仓库唯一允许出现域词的豁免文件），路径可由 ``--lexicon`` 注入；
* 扫描器**复用**平台既有的 :mod:`tools.evidence_domain_scan`（同一份词表、同一套
  词边界规则），避免"两套扫描两种结论"；
* 缺词表 → 明确报 ``status="no_lexicon"`` 且 ``ok=False``（fail-closed：扫不了 ≠ 干净）。

CLI::

    python3 -m tools.benchmark_runner.domain_guard            # 扫描引擎包
    python3 -m tools.benchmark_runner.domain_guard --json
    python3 -m tools.benchmark_runner.domain_guard tools/benchmark_runner/configs

退出码：0 无命中；1 有命中 / 词表缺失（CI 红）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence

_DEFAULT_TARGETS = ("tools/benchmark_runner",)
_DEFAULT_LEXICON = "tools/evidence_domain_lexicon.json"


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def scan(targets: Optional[Sequence[str]] = None,
         lexicon: Optional[str | pathlib.Path] = None) -> Dict[str, Any]:
    """扫描 ``targets``（缺省=引擎包）中是否出现域词条；返回裁决 dict。"""
    root = _repo_root()
    lex_path = pathlib.Path(lexicon) if lexicon else root / _DEFAULT_LEXICON
    paths = [t if pathlib.Path(t).is_absolute() else str(root / t)
             for t in (targets or _DEFAULT_TARGETS)]
    lex_path = lex_path if lex_path.is_absolute() else root / lex_path

    base: Dict[str, Any] = {"targets": [str(p) for p in paths], "lexicon": str(lex_path),
                            "hits": [], "scanned_files": 0}
    if not lex_path.is_file():
        return {**base, "ok": False, "status": "no_lexicon",
                "reason": f"domain lexicon not found at {lex_path}: cannot prove domain-freedom"}
    try:
        from tools.evidence_domain_scan import load_lexicon, scan_paths   # 复用平台扫描器
    except Exception as exc:                                              # noqa: BLE001
        return {**base, "ok": False, "status": "scanner_unavailable",
                "reason": f"cannot import tools.evidence_domain_scan: {type(exc).__name__}: {exc}"}

    lexicon_data = load_lexicon(lex_path)
    raw = scan_paths([pathlib.Path(p) for p in paths], lexicon_data, root=root)
    hits: List[Dict[str, Any]] = raw.get("hits") or []
    return {
        **base,
        "ok": not hits,
        "status": "clean" if not hits else "domain_terms_found",
        "scanned_files": raw.get("scanned_files", 0),
        "terms": raw.get("terms"),
        "hits": hits[:200],
        "n_hits": len(hits),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="benchmark 引擎零领域语义守卫（CI gate）")
    ap.add_argument("paths", nargs="*", help=f"扫描目标（缺省 {list(_DEFAULT_TARGETS)}）")
    ap.add_argument("--lexicon", default=None, help=f"词表 JSON（缺省 {_DEFAULT_LEXICON}）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 裁决")
    args = ap.parse_args(argv)
    verdict = scan(args.paths or None, args.lexicon)
    if args.json:
        print(json.dumps(verdict, indent=2, ensure_ascii=False))
    else:
        print(f"domain_guard: status={verdict['status']} scanned={verdict['scanned_files']} "
              f"hits={verdict.get('n_hits', 0)}")
        for h in verdict.get("hits", [])[:20]:
            print(f"  {h}")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())