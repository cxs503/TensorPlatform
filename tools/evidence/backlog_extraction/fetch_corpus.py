#!/usr/bin/env python3
"""fetch_corpus —— 把「报告 markdown + 求解器仓 pending 终态判词」冻结成离线语料。

为什么需要冻结
--------------
本模块（P44-N09 honest-FAIL 欠账结构化提取）的验收判据全部是**离线可复算**的：
同一份语料跑两次提取器必须得到逐字节相同的 backlog.jsonl。线上 ``/tensor/reports``
是活库（新报告持续写入），若在评测时直接联网取数，召回率会随库漂移。因此取数
一次、落盘成快照、评测只读快照。

两类语料
--------
1. ``reports``  —— 战役 DAG（plan 44 / ``multi_domain_verification_p21``，即 P21 波次）
   各节点验收报告的 markdown 正文。报告里的「误差判定」表是 FAIL/WITHHELD 行的一手来源。
2. ``pending``  —— 求解器仓 ``benchmarks/pending/*/terminal_verdict*.md``（终态判词）。
   这些文件不入库（pending = 未通过/悬置），是第二类一手来源。

两条取数通道（live API / 只读 DB）
----------------------------------
* ``--source api``：走 ``/tensor/reports``（只读 GET）。实测**线上构建忽略 mission_id
  过滤参数**（``?mission_id=5345`` 仍返回最新 50 条），且 ``total`` 恒等于本页条数，
  所以它只能抓到最新 50 篇报告——对早已归档的 P21 报告无效。这条通道保留给
  「抓最新报告」场景。
* ``--source db``：直接对平台库做**只读 SELECT**（``psql -At``，无写语句）。这是取归档
  报告唯一可行的通道，故设为默认。只读性由 SQL 白名单保证（只允许 SELECT）。

用法::

    python3 -m tools.evidence.backlog_extraction.fetch_corpus \
        --source db --missions 5345-5353 --per-mission latest \
        --out artifacts/backlog_extraction/corpus
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPORT_ENDPOINT = "/tensor/reports"
DEFAULT_PENDING_ROOTS = ("/root/tensor-ecosystem",)
DB_NAME = os.environ.get("TENSOR_DB_NAME", "tensorplatform")
DB_USER = os.environ.get("TENSOR_DB_USER", "postgres")


# --------------------------------------------------------------------- API 通道
def _login(api_base: str) -> str:
    """复用平台既有只读客户端（tools.acceptance_triage）拿 Bearer token。"""
    from tools.acceptance_triage import login  # 局部导入：离线评测不需要网络

    token = os.environ.get("TOKEN", "")
    if token:
        return token
    return login(
        api_base,
        os.environ.get("TENSOR_AGENT_USER", ""),
        os.environ.get("TENSOR_AGENT_PASS", ""),
    )


def _client(api_base: str):
    from tools.acceptance_triage import HttpTransport

    return HttpTransport(api_base, _login(api_base))


def iter_reports(client: Any, page_size: int = 50, max_pages: int | None = None) -> Iterable[dict]:
    page = 1
    while True:
        batch = client.get(REPORT_ENDPOINT, params={"page": page, "page_size": page_size})
        items = batch.get("items", []) if isinstance(batch, dict) else batch
        if not items:
            return
        for it in items:
            yield it
        total = batch.get("total") if isinstance(batch, dict) else None
        if len(items) < page_size:
            return
        if total is not None and page * page_size >= int(total):
            return
        if max_pages is not None and page >= max_pages:
            return
        page += 1


# ---------------------------------------------------------------------- DB 通道
# markdown 含换行/逗号/竖线，直接做字段分隔必然被撕碎；故正文以 base64 单行传出，
# 其余字段用 \x1f（单元分隔符，正文中不出现的控制字符）分行内分隔。
_REPORT_SQL = """
SELECT id, mission_id, COALESCE(scenario_key,''), COALESCE(title,''),
       to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS'),
       translate(encode(convert_to(COALESCE(markdown,''),'UTF8'),'base64'), E'\n\r ', '')
FROM tensor_reports
WHERE {where}
ORDER BY mission_id, id
"""


def _psql_rows(sql: str) -> list[list[str]]:
    """只读 SELECT：psql -At -F 分隔，CSV 转义交给 psql。"""
    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        raise ValueError("only read-only SELECT statements are allowed")
    proc = subprocess.run(
        ["psql", "-U", DB_USER, "-d", DB_NAME, "-At", "-F", "\x1f", "-c", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:400]}")
    rows = [line.split("\x1f") for line in proc.stdout.splitlines() if line]
    return rows


def freeze_reports_from_db(
    out_dir: Path,
    missions: set[int],
    *,
    per_mission: str = "latest",
) -> list[dict]:
    if not missions:
        raise ValueError("db source needs an explicit --missions set")
    where = "mission_id IN ({})".format(",".join(str(int(m)) for m in sorted(missions)))
    rows = _psql_rows(_REPORT_SQL.format(where=where))
    picked: dict[str, tuple[str, ...]] = {}
    for rid, mid, skey, title, created, b64 in rows:
        md = base64.b64decode(b64).decode("utf-8") if b64 else ""
        if not md:
            continue
        if per_mission == "latest":
            prev = picked.get(mid)
            if prev is None or int(rid) > int(prev[0]):
                picked[mid] = (rid, mid, skey, title, created, md)
        elif per_mission == "all":
            key = f"{mid}:{rid}"
            picked[key] = (rid, mid, skey, title, created, md)
        else:
            raise ValueError(f"unknown per_mission {per_mission!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    for rec in sorted(picked.values(), key=lambda r: (int(r[1]), int(r[0]))):
        rid, mid, skey, title, created, md = rec
        name = f"r{rid}_m{mid}_{_slug(title)}.md"
        (out_dir / name).write_text(md, encoding="utf-8")
        index.append(
            {
                "kind": "report",
                "source": "db:tensor_reports",
                "report_id": rid,
                "mission_id": mid,
                "scenario_key": skey,
                "title": title,
                "created_at": created,
                "file": name,
                "chars": len(md),
            }
        )
    return index


# ------------------------------------------------------------------ 报表 markdown
def _slug(text: str, limit: int = 48) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in text]
    s = re.sub(r"-{2,}", "-", "".join(keep)).strip("-")
    return s[:limit] or "report"


def freeze_reports(
    client: Any,
    out_dir: Path,
    missions: set[int],
    *,
    max_pages: int | None = None,
) -> list[dict]:
    """从 live API 抓报告 markdown（注意：线上构建忽略 mission_id 过滤，见模块 docstring）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    seen: set[str] = set()
    for rep in iter_reports(client, max_pages=max_pages):
        mid = str(rep.get("mission_id") or "")
        if missions and (not mid.isdigit() or int(mid) not in missions):
            continue
        rid = str(rep.get("id"))
        if rid in seen:
            continue
        detail = client.get(f"{REPORT_ENDPOINT}/{rid}")
        md = detail.get("markdown") or detail.get("content") or detail.get("body") or ""
        if not md:
            continue
        seen.add(rid)
        name = f"r{rid}_m{mid}_{_slug(str(rep.get('title') or ''))}.md"
        (out_dir / name).write_text(md, encoding="utf-8")
        index.append(
            {
                "kind": "report",
                "source": "api:/tensor/reports",
                "report_id": rid,
                "mission_id": mid,
                "title": rep.get("title"),
                "scenario_key": rep.get("scenario_key"),
                "created_at": rep.get("created_at"),
                "file": name,
                "chars": len(md),
            }
        )
    return index


# ----------------------------------------------------------------- pending 判词
def freeze_pending(roots: Iterable[str], out_dir: Path) -> list[dict]:
    """拷出 ``benchmarks/pending/**/terminal_verdict*.md``（终态判词）。

    只拷贝判词本身（不拷 solver 代码），文件名 ``<repo>__<文件名>``，索引保留
    repo/benchmark 归属与 ``source_path``（原始绝对路径，供审计链回溯）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    for root in roots:
        rp = Path(root)
        if not rp.is_dir():
            continue
        for path in sorted(rp.glob("*/benchmarks/pending/**/terminal_verdict*.md")):
            rel = path.relative_to(rp)
            repo = rel.parts[0]
            name = f"{repo}__{path.name}"
            shutil.copyfile(path, out_dir / name)
            index.append(
                {
                    "kind": "pending_verdict",
                    "source": "fs:benchmarks/pending",
                    "repo": repo,
                    "benchmark": path.parent.name,
                    "file": name,
                    "source_path": str(path),
                    "chars": len(path.read_text(encoding="utf-8", errors="replace")),
                }
            )
    return index


@dataclass
class CorpusIndex:
    reports: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "n_reports": len(self.reports),
            "n_pending": len(self.pending),
            "reports": self.reports,
            "pending": self.pending,
        }


def parse_mission_spec(spec: str) -> set[int]:
    """``"5345-5353,5360"`` → {5345..5353, 5360}；空串 → 空集（不过滤）。"""
    out: set[int] = set()
    for chunk in (c.strip() for c in spec.split(",")):
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(chunk))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="freeze FAIL/WITHHELD evidence corpus")
    ap.add_argument(
        "--api-base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    )
    ap.add_argument("--source", choices=("db", "api"), default="db")
    ap.add_argument("--out", default="artifacts/backlog_extraction/corpus")
    ap.add_argument("--missions", default="", help="5345-5353；空=不过滤")
    ap.add_argument("--per-mission", choices=("latest", "all"), default="latest")
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--pending-root", action="append", default=None)
    ap.add_argument("--no-pending", action="store_true")
    ap.add_argument("--no-reports", action="store_true")
    args = ap.parse_args(argv)

    out = Path(args.out)
    idx = CorpusIndex()
    missions = parse_mission_spec(args.missions)
    if not args.no_reports:
        if args.source == "db":
            idx.reports = freeze_reports_from_db(out / "reports", missions, per_mission=args.per_mission)
        else:
            idx.reports = freeze_reports(
                _client(args.api_base), out / "reports", missions, max_pages=args.max_pages
            )
    if not args.no_pending:
        roots = args.pending_root or list(DEFAULT_PENDING_ROOTS)
        idx.pending = freeze_pending(roots, out / "pending")
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(
        json.dumps(idx.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"[corpus] reports={len(idx.reports)} pending={len(idx.pending)} -> {out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())