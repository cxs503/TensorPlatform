"""三层记忆压缩测试（合成文档/席位，零领域语义）。"""

from tools.memory_compress import (compress_index_view, compress_seat, decay_tiers,
                                   doc_metrics, due_triggers)
from tools.memory_compress.kb_lifecycle import write_forgetting_log

DOC = """# Alpha 手册
## 判据
误差 <= 3% 才算过；样本数 128。
## 流程
先跑 tests/test_alpha.py 再报数。
### 细节
第一步 0.5s，第二步 2.5s，第三步 30s。
## 账本
记 12 单，通过 9 单。
"""


def test_global_view_keeps_structure_and_numbers():
    r = compress_index_view(DOC, "alpha.md")
    m = doc_metrics(r)
    assert m["metrics"]["headings_total"] == 5 and m["metrics"]["headings_kept"] == 4
    assert m["metrics"]["deep_headings_as_pointers"] == 1, "深标题降级为指针，不丢结构"
    assert m["metrics"]["numeric_retention"] >= 0.6
    assert m["ok"], m["violations"]
    assert "alpha.md#判据" in r["view"]


def test_seat_memory_keeps_evidence_drops_one_off():
    rec = {"seat_id": "s1", "lessons": [
        "用 tests/test_beta.py 证明修复（误差 1.2%）",
        "先读了 6 个文件才开始动手",
        "把阈值 3% 写进 config/beta.json"]}
    eps = [{"mission_id": i, "task_class": "dev", "status": "failed",
            "failure_kind": "inflated_delivery", "report_id": 100 + i} for i in range(3)]
    out = compress_seat(rec, eps, min_recur=2)
    assert len(out["kept"]["capability_evidence"]) == 2
    assert out["kept"]["recurrent_failures"][0]["pattern"] == "inflated_delivery"
    assert out["kept"]["recurrent_failures"][0]["count"] == 3
    assert out["kept"]["competence"]["dev"]["pass_rate"] == 0.0
    assert mod_dropped(out) == 1 and out["dropped"][0]["recover"].startswith("tensor_reports")


def mod_dropped(out):
    return len(out["dropped"])


def test_kb_lifecycle_tiers_and_forgetting_log(tmp_path):
    rows = [
        {"id": 1, "kb_key": "hot", "cited_count": 5, "injected_count": 9, "last_cited_at": "2026-11-10T00:00:00"},
        {"id": 2, "kb_key": "stale", "cited_count": 0, "injected_count": 1, "last_hit_at": "2026-06-01T00:00:00"},
        {"id": 3, "kb_key": "arch", "cited_count": 0, "injected_count": 0, "created_at": "2025-01-01T00:00:00"},
    ]
    tiers = {t["kb_key"]: t for t in decay_tiers(rows, now=__import__("datetime").datetime.fromisoformat("2026-11-12T00:00:00+00:00"))}
    assert tiers["hot"]["tier"] == "hot" and tiers["hot"]["decay_weight"] == 1.0
    assert tiers["stale"]["tier"] == "stale" and tiers["stale"]["action"] == "downweight"
    assert tiers["arch"]["tier"] == "archive" and tiers["arch"]["decay_weight"] == 0.0
    log = write_forgetting_log(list(tiers.values()), action="dry_run", log_dir=tmp_path)
    assert log["rows"] == 2 and (tmp_path / "forgetting_log.jsonl").exists()
    txt = (tmp_path / "forgetting_log.jsonl").read_text(encoding="utf-8")
    assert '"mode": "dry_run"' in txt and "zero_or_old_reference" in txt


def test_cadence_periodic_and_threshold():
    due = due_triggers({}, state={})
    assert due["due"] and due["triggers"][0]["trigger"] == "BOOTSTRAP"
    hot = due_triggers({"seat_memory_tokens": 5000, "kb_stale_ratio": 0.5},
                       state={"last_run_at": "2026-11-11T00:00:00+00:00"})
    names = {t["trigger"] for t in hot["triggers"]}
    assert "SEAT_MEMORY_SIZE" in names and "KB_STALE_RATIO" in names
    blocked = due_triggers({"seat_memory_tokens": 5000},
                           state={"last_run_at": "2026-11-12T05:00:00+00:00"})
    assert blocked["due"] is False and blocked["blocked_by"] == "MIN_INTERVAL"
