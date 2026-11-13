#!/usr/bin/env python3
"""compound_meter — 复利仪表+防倒退闸（owner 2026-11-11「如何把控进入复利状态」令）

复利回路四组件（沉淀/注入/验证/负知识）的仪表化把控：
  M1 沉淀率   knowledge_base 行数 7d 增量（回路生息之本）
  M2 负知识   negative_knowledge_ledger 行数 7d 增量（防负复利）
  M3 验证兑账 tensor_repro_verdicts / TensorBench 结果行数（信任分级之源）
  M4 能力增量 capability_deltas 非 no_substantive_delta 占比（=r 的直接读数）
  M5 回路接线 P0/P2 载体单在飞状态（m14354-57, m14428-31）
  M9 主线占比 REPRO-/XVAL-/JOUR- 锚点任务占 7d 新建任务的比（FEAT-ANCHOR-FEEDER 验收②：
     主线锚无长设供给 → 自驱成默认主粮的结构失衡必须可读；<3% 只告警不熔断——只计量不改进）

两态判定（诚实区分「未建成」与「建了又断」）：
  wiring  = 载体单未落地/表未建 —— 绿灯放行（建设期正常态）
  armed   = 载体单已 succeeded 后进入武装态 —— 此时指标归零=回路断了 → 红线告警
防倒退闸（G2，armed 态才武装）：M1 或 M4 连续 3 轮（18h）零增量而有任务在跑 →
  chronicle 告警+日志红线（v1 不自动节流，报 owner 裁）；门必须红队可失败（quality-bar-v1 教义）。

周期 6h；结果写 compound_metrics 表（自建）+ /var/log/tp-compound-meter.log。
"""
import json
import pathlib
import subprocess
import sys
import time

PG = ["psql", "-U", "postgres", "-d", "tensorplatform", "-h", "/var/run/postgresql",
      "-t", "-A", "-F", "|", "-c"]
INTERVAL = 6 * 3600
LOG = "/var/log/tp-compound-meter.log"
WIRING_MISSIONS = [14354, 14355, 14356, 14357, 14428, 14429, 14430, 14431]
STALE_ROUNDS = 3  # G2：连续 3 轮零增量（18h）→ 红线
MAINLINE_REDLINE = 3.0   # M9：7d 主线占比低于此值 → 告警（只计量不改进，对齐 kpi_meter 纪律）
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:                      # 主线占比仪表（FEAT-ANCHOR-FEEDER 验收②）
    import mainline_share
except Exception:         # 仪表缺装 → M9 降级为 None，不影响 M1-M8
    mainline_share = None


class _MetricQ:
    """把 psql 通道包成 mainline_share 需要的 db.q(sql)->rows 接口。

    记录分隔符用 \x02（不用换行）：mission goal 是多行文本，按 \n 切会把字段切错位。
    """

    def q(self, sql):
        args = ["psql", "-U", "postgres", "-d", "tensorplatform", "-h", "/var/run/postgresql",
                "-t", "-A", "-F", "\x01", "-R", "\x02", "-v", "ON_ERROR_STOP=1", "-c", sql]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:120])
        return [rec.split("\x01") for rec in r.stdout.strip().split("\x02") if rec.strip()]
_state_path = pathlib.Path("/root/tp-agent/compound_meter_state.json")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def q(sql):
    r = subprocess.run(PG + [sql], capture_output=True, text=True)
    if r.returncode != 0:
        return None  # 表不存在等 → wiring 态
    return r.stdout.strip()


def q_int(sql, default=None):
    v = q(sql)
    if v is None or v == "":
        return default
    try:
        return int(v.split("\n")[0])
    except ValueError:
        return default


def load_state():
    try:
        return json.loads(_state_path.read_text())
    except Exception:
        return {"zero_streak": {}}


def save_state(st):
    _state_path.write_text(json.dumps(st))


def read_metrics():
    m = {}
    m["kb_rows"] = q_int("SELECT count(*) FROM knowledge_base")
    m["neg_rows"] = q_int("SELECT count(*) FROM negative_knowledge_ledger")
    m["repro_rows"] = q_int("SELECT count(*) FROM tensor_repro_verdicts")
    m["bench_rows"] = q_int("SELECT count(*) FROM tensorbench_results")
    # capability_deltas 真增量占比（r 直接读数）
    raw = q("SELECT count(*) FILTER (WHERE coalesce(category,'') NOT IN ('','no_substantive_delta')),"
            " count(*) FROM tensor_capability_deltas")
    try:
        real, total = raw.split("|")[0], raw.split("|")[1]
        m["delta_real"], m["delta_total"] = int(real), int(total)
    except Exception:
        m["delta_real"] = m["delta_total"] = None
    # 载体单状态 + 功能总账进度（M6 声明态：done/in_progress 由建结单方写，验证绑定待 tb-eval-loop）
    m["ledger_done"] = q_int("SELECT count(*) FROM feature_ledger WHERE status='done'")
    m["ledger_total"] = q_int("SELECT count(*) FROM feature_ledger")
    # M7 本职空转：in_progress 总账无任何在飞单挂锚的条数（owner「本职不能没人做」令）
    m["ledger_orphans"] = q_int(
        "SELECT count(*) FROM feature_ledger f WHERE f.status='in_progress' AND NOT EXISTS ("
        "SELECT 1 FROM tensor_dev_missions i WHERE i.status IN ('queued','running','claimed') "
        "AND i.goal LIKE '%'||f.slug||'%')")
    rows = q("SELECT id, status FROM tensor_dev_missions WHERE id IN (%s)"
             % ",".join(map(str, WIRING_MISSIONS)))
    wiring = {"succeeded": 0, "in-flight": 0, "dead": 0}
    if rows is not None:
        for line in rows.split("\n"):
            if "|" not in line:
                continue
            _, st = line.split("|")
            if st == "succeeded":
                wiring["succeeded"] += 1
            elif st in ("failed", "revoked", "stopped"):
                wiring["dead"] += 1
            else:
                wiring["in-flight"] += 1
    m["wiring"] = wiring
    m["missions_running"] = q_int("SELECT count(*) FROM tensor_dev_missions WHERE status='running'")
    # M8 TK 回路（owner 2026-11-11「成果落入 TK 反哺平台」令）：能力条目/注入/引用 + 反文件夹红线
    m["kb_cap"] = q_int("SELECT count(*) FROM knowledge_base WHERE ktype='capability'")
    m["inj_7d"] = q_int("SELECT count(*) FROM knowledge_injections WHERE injected_at::timestamptz > now()-interval '7 days'  -- 11-13 修：injected_at 是 text，裸比较抛异常被吞成 None→M8 假警报")
    m["cited_total"] = q_int("SELECT coalesce(sum(cited_count),0) FROM knowledge_base WHERE ktype='capability'")
    # M9 主线类占比（REPRO-/XVAL-/JOUR- vs 自驱/知识工厂/算例池）：结构失衡读数
    if mainline_share is not None:
        try:
            m.update(mainline_share.mainline_metrics(_MetricQ()))
        except Exception as e:
            log(f"M9 主线占比取数失败: {type(e).__name__} {e}")
            m["mainline_24h_pct"] = m["mainline_7d_pct"] = None
    else:
        m["mainline_24h_pct"] = m["mainline_7d_pct"] = None
    return m


def evaluate(m, prev):
    """返回 (verdict, alerts)。verdict: wiring | armed-green | armed-red"""
    wiring = m["wiring"]
    # 载体单全灭且未 succeeded → 回路未建成（红灯但类别=wiring-fail 需 owner 重发）
    landed = wiring["succeeded"]
    if landed < 4:  # 至少 4 张（四回路/评价回路主体）落地才武装
        return "wiring", [f"wiring: 载体单 succeeded {landed}/8, in-flight {wiring['in-flight']}, dead {wiring['dead']}"]
    alerts = []
    ok = True
    if m["kb_rows"] is not None and prev.get("kb_rows") is not None and m["kb_rows"] <= prev.get("kb_rows", 0):
        alerts.append(f"M1 沉淀停滞: kb_rows {prev['kb_rows']}→{m['kb_rows']}")
        ok = False
    if m["delta_total"] and m["delta_real"] is not None and m["delta_real"] == 0:
        alerts.append(f"M4 能力增量仍全空: {m['delta_real']}/{m['delta_total']}")
        ok = False
    if m["neg_rows"] == 0:
        alerts.append("M2 负知识 0 行")
        ok = False
    if m["repro_rows"] == 0 and m["bench_rows"] in (None, 0):
        alerts.append("M3 验证兑账双零（repro/bench 均无）")
        ok = False
    # M8 反文件夹红线（owner「TK 不能变成文件夹」令）：能力条目在涨而 7d 零注入=只进不出
    if (m.get("kb_cap") or 0) > 10 and (m.get("inj_7d") or 0) == 0:
        alerts.append(f"M8 TK 只进不出: cap 条目 {m['kb_cap']} 而 7d 注入 0——文件夹病")
        ok = False
    # M9 主线占比：观察项（不改 verdict —— 结构改善是长线，熔断会制造噪声）
    if m.get("mainline_7d_pct") is not None and m["mainline_7d_pct"] < MAINLINE_REDLINE:
        alerts.append(f"M9 主线占比偏低: 7d {m['mainline_7d_pct']}%（<{MAINLINE_REDLINE}%）"
                      f"——查 anchor_feeder 供给/主线锚是否断流")
    return ("armed-green" if ok else "armed-red"), alerts


def main():
    while True:
        try:
            st = load_state()
            m = read_metrics()
            verdict, alerts = evaluate(m, st.get("last_metrics", {}))
            # G2 连续零增停滞计数（armed 态且有任务在跑才算）
            if verdict.startswith("armed") and m.get("missions_running", 0) > 0:
                z = st.setdefault("zero_streak", {})
                for key in ("kb_rows", "delta_real"):
                    cur, last = m.get(key), st.get("last_metrics", {}).get(key)
                    zkey = f"{key}:stale"
                    if last is not None and cur is not None and cur <= (last or 0):
                        z[zkey] = z.get(zkey, 0) + 1
                    else:
                        z[zkey] = 0
                stale = [k for k, v in z.items() if v >= STALE_ROUNDS]
                if stale:
                    alerts.append(f"G2 红线: {stale} 连续{STALE_ROUNDS}轮零增量而有任务在跑——复利回路断裂嫌疑")
            else:
                st["zero_streak"] = {}
            st["last_metrics"] = {k: v for k, v in m.items() if not isinstance(v, dict)}
            save_state(st)
            # 落表（自建表）
            q("CREATE TABLE IF NOT EXISTS compound_metrics (ts timestamptz DEFAULT now(),"
              " metric text, value bigint, state text)")
            for k in ("kb_rows", "neg_rows", "repro_rows", "bench_rows", "delta_real", "delta_total",
                      "ledger_done", "ledger_total", "ledger_orphans", "kb_cap", "inj_7d", "cited_total",
                      "mainline_24h_pct", "mainline_7d_pct"):
                if m.get(k) is not None:
                    q(f"INSERT INTO compound_metrics (metric, value, state) VALUES ('{k}', {int(round(float(m[k])))}, '{verdict}')")
            log(f"VERDICT={verdict} metrics={ {k: v for k, v in m.items() if not isinstance(v, dict)} }"
                f" wiring={m['wiring']} alerts={alerts if alerts else '无'}")
            if any("G2 红线" in a for a in alerts):
                q("INSERT INTO chronicle_snapshots (ts, payload) VALUES (now(), "
                  "'{\"note\":\"compound-meter-G2-red\",\"alerts\":" + json.dumps(alerts, ensure_ascii=False).replace("'", "''") + "}'::jsonb)")
        except Exception as e:
            log(f"cycle error: {type(e).__name__} {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
