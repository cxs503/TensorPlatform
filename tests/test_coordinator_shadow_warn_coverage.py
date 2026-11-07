"""IN27-H1 附带缺陷回归：``error_total`` 必须数满整个窗口，不能只看固定 600 行尾读。

口径（registry.METRICS['error_total']）=「窗口内 ERROR 行数」。实测 campaign_dispatcher.log
~12 行/分钟时 600 行只够 ~50 分钟 → 少算（2026-11-07 15:58 实测 73→61，-16%）。

判据：① 窗口内 ERROR 行数 > 600 行的合成日志，读数必须等于真实条数；
      ② 窗口外（更老）的 ERROR 不得计入（放大读取不等于放宽窗口）；
      ③ 无时间戳行仍按原语义计入 undated；
      ④ 巨大日志触上限时如实标 ``tail_capped_paths``（不静默少算）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta, timezone

REPO = pathlib.Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pu = _load("in27h1_pulse", REPO / "tools" / "coordinator_shadow" / "pulse.py")

AS_OF = datetime(2026, 11, 7, 15, 30, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _dense_log(tmp_path, n_error: int = 800, n_old: int = 40):
    """窗口内 n_error 条 ERROR + 大量 WARN 填充（把 ERROR 顶出 600 行尾读），窗口外 n_old 条。"""
    log = tmp_path / "dispatcher.log"
    lines = []
    # 窗口外（90 分钟前）：不得计入
    for i in range(n_old):
        lines.append(f"{_iso(AS_OF - timedelta(minutes=90, seconds=i))} ERROR 窗口外历史错误 #{i}")
    # 窗口内：60 分钟跨度内交替 WARN/ERROR，使 ERROR 分散在远超 600 行的日志里
    total = n_error * 3
    for i in range(total):
        ts = AS_OF - timedelta(seconds=int((total - i) * (55 * 60 / total)))
        if i % 3 == 0:
            lines.append(f"{_iso(ts)} ERROR 窗口内错误 code=E{i % 7}")
        else:
            lines.append(f"{_iso(ts)} WARN 孤儿 draft plan {i} 机器门未过不收养: 成熟化单 0 < 2")
    log.write_text("\n".join(lines) + "\n")
    return log


def test_window_count_is_not_truncated_by_tail_limit(tmp_path):
    log = _dense_log(tmp_path)
    stream = pu.read_warn_stream([log], AS_OF, window_minutes=60)
    assert stream["error_total"] == 800            # 旧实现：600 行尾读只看到约 200 条
    assert stream["tail_capped_paths"] == []       # 已覆盖窗口，无需触上限


def test_old_errors_outside_window_still_excluded(tmp_path):
    log = _dense_log(tmp_path, n_error=300, n_old=50)
    stream = pu.read_warn_stream([log], AS_OF, window_minutes=60)
    assert stream["error_total"] == 300            # 50 条 90 分钟前的 ERROR 不计


def test_small_log_semantics_unchanged_and_undated_counted(tmp_path):
    log = tmp_path / "small.log"
    lines = [f"{_iso(AS_OF - timedelta(minutes=5))} WARN 同签名 a=1",
             f"{_iso(AS_OF - timedelta(minutes=4))} WARN 同签名 a=2",
             f"{_iso(AS_OF - timedelta(minutes=3))} ERROR 池耗尽 pool=12",
             "没有时间戳之一 WARN 也算",
             f"{_iso(AS_OF - timedelta(hours=3))} WARN 窗口外老日志"]
    log.write_text("\n".join(lines) + "\n")
    stream = pu.read_warn_stream([log], AS_OF, window_minutes=60)
    assert stream["warn_total"] == 3 and stream["error_total"] == 1
    assert stream["undated_lines"] == 1
    assert stream["tail_capped_paths"] == []


def test_tail_cap_is_reported_not_silent(tmp_path):
    """极小上限（模拟超大日志）时必须如实留痕，而不是静默少算。"""
    log = _dense_log(tmp_path, n_error=200, n_old=0)
    stream = pu.read_warn_stream([log], AS_OF, window_minutes=60,
                                 tail_lines=10, max_tail_lines=10)
    assert stream["tail_capped_paths"] == [str(log)]
    assert stream["error_total"] < 200             # 触上限=读数偏低，故如实标注