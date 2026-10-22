#!/usr/bin/env python3
"""benchmark 闭环派发器（owner 2026-10-19 指令：所有 benchmark 做闭环）。

每轮（cron */10，flock 串行）：
  1. OFF 开关 → 跳过；
  2. operator 登录，拉 mission 列表，同步本线终态（succeeded → 自动 push+PR+
     代发论文级平台报告；gateway_unavailable → 计数重派 ≤3；其他终态 → 人工位）；
  3. 车道上限（P2a）：gpu 道串行（GPU_CAP=1）；cpu 道隔离条目（figdoc 类）
     并行至 CPU_CAP=3，非隔离条目须空道且不与非隔离在飞混跑；
  4. 中继健康门（GET /v1/models）不通 → 跳过（relay 宕机期不派发）；
  5. GPU 门：GPU5_BUSY 存在 → 只考虑 gpu:false 条目（收割/文档类）；
  6. 派发 backlog 中下一个就绪条目（wait_for 满足、未完成未失败）。

环境（cron 已 source）：agent.env（LLM_BASE_URL/LLM_API_KEY）+
operator.env（OPERATOR_USER/PASS）。push 令牌：GH_PUSH_TOKEN 或 /tmp/gh_token。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pr_hygiene import merge_main_before_push  # PR 卫生：推送前并 main

ECO = Path("/srv/tensor/shared/tensor_eco")
WORK = ECO / "agent_work" / "benchmark_closure"
RUNS = Path("/srv/tensor/shared/runs/bm_close")
HELPER = ECO / "TensorPlatform" / "tools" / "agent" / "dispatch_helper.py"
PY = "/srv/tensor/shared/venvs/tensor_eco/bin/python"
BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1")
GH_API = "https://api.github.com"
REPO_SLUG = "cxs503/TensorLBM"

MARK = "BENCH-CLOSE:"
TERMINAL = ("succeeded", "failed", "stopped")
INFLIGHT = ("queued", "claimed", "running", "awaiting_acceptance")
MAX_RETRIES = 3
# 车道上限（P2a）：gpu 串行；cpu 允许隔离条目（figdoc 类，只碰自己案目录）
# 并行至 3——非隔离条目（可能碰共享索引/README）仍严格串行
GPU_CAP = 1
CPU_CAP = 3
LOG = WORK / "benchmark_dispatch.log"


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {msg}\n")


def api(method: str, path: str, body: dict | None = None, tok: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{method} {path} 传输错误: {exc!r}") from exc


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_state(state: dict) -> None:
    tmp = WORK / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, WORK / "state.json")


def relay_ok() -> bool:
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "")
    if not base:
        log("ERROR: LLM_BASE_URL 未设置（cron 须 source agent.env）")
        return False
    req = urllib.request.Request(
        base + "/models", headers={"Authorization": "Bearer " + key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def gh_token() -> str:
    tok = os.environ.get("GH_PUSH_TOKEN", "")
    if not tok:
        p = Path("/tmp/gh_token")
        if p.exists():
            tok = p.read_text().strip()
    return tok


def gpu5_actually_busy() -> bool | None:
    """GPU5 真实占用检测（P0b）：nvidia-smi index→bus_id 与 compute-apps 交叉。
    True=真占；False=标记失活（occupant 已退/从未占）；None=查询失败（保守尊重标记）。"""
    try:
        g = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,gpu_bus_id", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20)
        a = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_bus_id",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20)
        if g.returncode != 0 or a.returncode != 0:
            return None
        bus5 = ""
        for ln in g.stdout.splitlines():
            f = [x.strip() for x in ln.split(",")]
            if len(f) >= 2 and f[0] == "5":
                bus5 = f[1].upper()
        if not bus5:
            return None          # 查不到 GPU5 行（索引漂移？）——保守尊重标记
        apps = {x.strip().upper() for x in a.stdout.splitlines() if x.strip()}
        return bus5 in apps
    except Exception:            # noqa: BLE001  nvidia-smi 缺失/超时 ≠ GPU 空闲
        return None


def respect_gpu5_marker(counts: dict) -> None:
    """GPU5_BUSY 标记作用于 gpu 道计数（P0b：失活标记忽略+WARN，不再饿死 GPU 条目）。"""
    if not (RUNS / "GPU5_BUSY").exists() or counts["gpu"] >= GPU_CAP:
        return
    actually = gpu5_actually_busy()
    if actually is False:
        log("WARN: GPU5_BUSY 为陈旧标记（GPU5 无 compute app）→ 忽略，本轮照派 GPU 条目")
        return
    if actually is None:
        log("NOTE: GPU5_BUSY 存在且占用状态不可查（nvidia-smi 异常）→ 保守尊重标记")
    else:
        log("NOTE: GPU5_BUSY 存在（GPU5 实占）→ 本轮仅考虑非 GPU 条目")
    counts["gpu"] = GPU_CAP


def pr_merged(num: int) -> bool:
    """wait_for_pr 门：目标 PR 是否已并入 main（GitHub API；断网/无令牌
    保守视为未合，条目继续等）。"""
    tok = gh_token()
    if not tok:
        log(f"WARN: wait_for_pr #{num} 无令牌可查 → 本轮视为未合")
        return False
    req = urllib.request.Request(
        f"{GH_API}/repos/{REPO_SLUG}/pulls/{num}",
        headers={"Authorization": "Bearer " + tok,
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return bool(json.loads(resp.read() or b"null").get("merged"))
    except Exception as exc:  # noqa: BLE001  只记类型
        log(f"WARN: wait_for_pr #{num} 查询失败 {type(exc).__name__} → 视为未合")
        return False


def pr_body(mission: dict, case: str, branch: str) -> dict:
    """纯函数：PR 请求体。mission result 是扁平结构（headline/detail，无 summary
    键），两层取法都兼容；非 dict 时安全退回默认标题。"""
    result = mission.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    summ = result.get("summary") or result
    if not isinstance(summ, dict):
        summ = {}
    headline = (summ.get("headline")
                or f"benchmarks: {case} 闭环").replace("\n", " ")[:120]
    detail = (summ.get("detail") or "").replace("\n", " ")[:600]
    title = (headline if headline.startswith("benchmarks")
             else f"benchmarks: {case} 闭环——{headline}")
    return {"title": title, "head": branch, "base": "main",
            "body": (f"{headline}\n\n{detail}\n\n"
                     "验收与判据数字见 mission 摘要与各案 README PROVENANCE 块"
                     "（值-文件-键直读工件，零手抄）。")}


def push_and_pr(item: dict, mid: int, mission: dict,
                gates_required: bool = False,
                paper: bool = False) -> str | None:
    """合格自动推送（commit 在 agent 分支 + mission succeeded）：直连 URL 推 +
    ls-remote 验证 + 开 PR。返回 PR URL / None（瞬时失败，下轮重试）/
    "GATES_FAILED"（机械门禁不过，终态）/"SUITE_FAILED"（本地套件门不过，终态）。
    paper=True 走六门（含案例细节+图件）。"""
    wt = ECO / "agent_work" / f"m{mid}" / "TensorLBM"
    branch = f"agent/m{mid}"
    # PR 卫生：门禁前并 main（白名单机械解；不推带冲突标记的分支）
    okm, howm = merge_main_before_push(str(wt), "benchmark_dispatch")
    if not okm:
        log(f"m{mid}: 并 main 未成（{howm}）→ 本轮不推，下轮重试")
        return None
    if gates_required:
        g = subprocess.run(
            [PY, str(WORK / "gates_check.py"),
             "--worktree", str(wt), "--case", item["case"],
             "--profile", "paper" if paper else "classic"],
            capture_output=True, text=True, timeout=120)
        try:
            gj = json.loads(g.stdout or "{}")
        except json.JSONDecodeError:
            gj = {"problems": [f"gates_check 输出不可解析 rc={g.returncode}"]}
        if g.returncode != 0:
            log(f"ERROR m{mid}: 机械复算不过（profile={'paper' if paper else 'classic'}）"
                f" rc={g.returncode} {str(gj.get('problems'))[:1200]} → 不推，终态")
            return "GATES_FAILED"
        log(f"OK m{mid}: gates 复算通过（{'paper 六门' if paper else 'classic 四门'}）"
            f" verdict={gj.get('verdict')} {gj.get('verified')}")
    if not (wt / ".git").exists():
        log(f"ERROR m{mid}: worktree 不存在（可能被 GC）→ 需人工推送")
        return None
    # P2b：push 前本地套件门（全库 pytest 收集 + GPU5 空闲时聚焦 Triton 测试）
    # ——CI runner 失联期间由本地机械验证兜住 #371 型合并伤损
    try:
        s = subprocess.run([PY, str(WORK / "suite_check.py"), str(wt)],
                           capture_output=True, text=True, timeout=3000)
    except subprocess.TimeoutExpired:
        s = None
    if s is None:
        log(f"ERROR m{mid}: 套件门超时（3000s）→ 不推，终态")
        return "SUITE_FAILED"
    try:
        sj = json.loads(s.stdout or "{}")
    except json.JSONDecodeError:
        sj = {"rc": 2, "error": f"suite_check 输出不可解析 rc={s.returncode}"}
    if sj.get("rc", 1) != 0:
        log(f"ERROR m{mid}: 本地套件门不过 rc={sj.get('rc')} "
            f"{str(sj.get('error'))[:600]} → 不推，终态")
        return "SUITE_FAILED"
    log(f"OK m{mid}: 套件门过 " + "; ".join(
        f"{x.get('stage')}={x.get('rc')}" + ("(skip)" if x.get("skipped") else "")
        for x in sj.get("stages", [])))
    tok = gh_token()
    if not tok:
        log("ERROR: 无 push 令牌（GH_PUSH_TOKEN//tmp/gh_token）→ 需人工推送")
        return None
    url = f"https://x-access-token:{tok}@github.com/{REPO_SLUG}.git"
    pushed = False
    for attempt in range(1, 7):
        try:
            r = subprocess.run(
                ["git", "-C", str(wt), "push", url, f"{branch}:refs/heads/{branch}"],
                capture_output=True, text=True, timeout=180,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
            if r.returncode == 0:
                pushed = True
                break
            log(f"WARN m{mid}: push 第 {attempt} 次失败：{r.stderr.strip()[:120]}")
        except subprocess.TimeoutExpired:
            log(f"WARN m{mid}: push 第 {attempt} 次超时（180s）")
        time.sleep(15 * attempt)
    if not pushed:
        log(f"ERROR m{mid}: push 6 次全败 → 下轮重试（state 未置 done）")
        return None
    try:
        v = subprocess.run(["git", "ls-remote", url, f"refs/heads/{branch}"],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        v = None
    if v is None or branch not in v.stdout:
        log(f"ERROR m{mid}: ls-remote 未见分支 → 需人工核验")
        return None

    body = pr_body(mission, item["case"], branch)
    req = urllib.request.Request(
        f"{GH_API}/repos/{REPO_SLUG}/pulls",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read())
            return out.get("html_url")
    except urllib.error.HTTPError as exc:
        log(f"ERROR m{mid}: 开 PR HTTP {exc.code} {exc.read()[:160]!r}")
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR m{mid}: 开 PR 传输错误 {exc!r}")
    return None


MAX_REPORT_FIGURES = 12  # /tensor/reports figures 列表长度上限


def publish_report(item: dict, mid: int, tok: str) -> int | None:
    """代发论文级平台报告（agent 已写 report_<case>.md 在 worktree；图件取
    worktree 根 fig_*.png 与案例目录 figures/*.png——paper 档图件在案例目录）。"""
    wt = ECO / "agent_work" / f"m{mid}" / "TensorLBM"
    md = wt / f"report_{item['case']}.md"
    if not md.exists():
        log(f"WARN m{mid}: report_{item['case']}.md 缺失 → 报告未发（PR 不受阻）")
        return None
    text = md.read_text(encoding="utf-8")
    pngs = sorted(wt.glob("fig_*.png"))
    for sub in ("verified", "pending"):
        d = wt / "benchmarks" / sub / item["case"] / "figures"
        if d.is_dir():
            pngs += sorted(d.glob("*.png"))
    figs = []
    for png in pngs:
        figs.append({"name": png.stem,
                     "caption": png.stem.replace("fig_", "").replace("_", " "),
                     "png_base64": base64.b64encode(png.read_bytes()).decode()})
    if len(figs) > MAX_REPORT_FIGURES:  # 平台 schema 上限 12，超发=422 整单丢失
        log(f"WARN m{mid}: 图件 {len(figs)} 超平台上限，截前 {MAX_REPORT_FIGURES} 张")
        figs = figs[:MAX_REPORT_FIGURES]
    payload = report_payload(text, figs, mid, item["case"])
    code, out = api("POST", "/tensor/reports", payload, tok=tok)
    if code not in (200, 201):
        log(f"ERROR m{mid}: 报告发布 HTTP {code} {str(out)[:160]}")
        return None
    log(f"OK m{mid}: 报告 id={out.get('id')} figures={len(figs)}")
    return out.get("id")


def sync_terminal(missions: dict[int, dict], state: dict, tok: str) -> None:
    items = state.setdefault("items", {})
    for key, rec in list(items.items()):
        mid = rec.get("mission")
        if not mid or rec.get("done") or rec.get("failed"):
            continue
        m = missions.get(mid)
        if not m or m.get("status") not in TERMINAL:
            if m and "gw_since" in rec:
                rec.pop("gw_since", None)   # 回到在飞=平台已重排，撤销等待钟
            continue
        status = m["status"]
        rec["status"] = status
        if status == "succeeded":
            item = next((i for i in BACKLOG["items"] if i["key"] == key), None)
            if item:
                try:
                    pr = push_and_pr(item, mid, m,
                                     gates_required=bool(rec.get("gates")),
                                     paper=bool(rec.get("paper")))
                except Exception as exc:  # noqa: BLE001  只记类型，防 argv 泄密
                    log(f"ERROR m{mid}: 收官后处理异常 {type(exc).__name__}"
                        " → state 不置 done，下轮重试")
                    continue
                if pr in ("GATES_FAILED", "SUITE_FAILED"):
                    rec["failed"] = True
                    rec["error"] = pr.lower()
                    log(f"FAILED {key}: m{mid} {pr} → 人工位（不重派）")
                    continue
                if pr is None:
                    log(f"HOLD {key}: m{mid} push/PR 未成 → 下轮重试")
                    continue
                try:
                    rid = publish_report(item, mid, tok)
                except Exception as exc:  # noqa: BLE001
                    log(f"ERROR m{mid}: 报告发布异常 {type(exc).__name__}"
                        " → PR 已开，state 不置 done 下轮补（push 幂等）")
                    continue
                rec.update(done=True, pr=pr, report=rid)
                log(f"DONE {key}: m{mid} succeeded → PR={pr} report={rid}")
        else:
            result = m.get("result") or {}
            # 网关信号可能在 error_code / headline / detail 任一处（实测 m61/m64
            # error_code 为空、headline=gateway_unavailable）
            sig = " ".join(filter(None, [
                str(result.get("error_code") or ""),
                str(result.get("headline") or ""),
                str(result.get("detail") or "")[:200]])).lower()
            if "gateway" in sig:
                # 平台通常会自动重排（m61/m64 实测回到 queued）；先等 30 分钟，
                # 仍终态才自起重派（防与平台重排撞车造重复 mission）
                if not rec.get("gw_since"):
                    rec["gw_since"] = time.time()
                    log(f"GATEWAY {key}: m{mid} 网关终态 → 等 30 分钟看平台重排")
                elif (time.time() - rec["gw_since"] < 1800):
                    pass
                elif rec.get("retries", 0) < MAX_RETRIES:
                    rec["retries"] = rec.get("retries", 0) + 1
                    rec["mission"] = None
                    rec["status"] = "retry_gateway"
                    rec["gw_since"] = None
                    log(f"RETRY {key}: m{mid} 网关终态超 30 分钟 → 重派 "
                        f"第 {rec['retries']}/{MAX_RETRIES} 次")
                else:
                    rec["failed"] = True
                    rec["error"] = "gateway_exhausted"
                    log(f"FAILED {key}: m{mid} 网关重派耗尽 → 人工位")
            else:
                err = str(result.get("error_code") or "")
                rec["failed"] = True
                rec["error"] = f"{status}:{err}"[:200]
                log(f"FAILED {key}: m{mid} {status} {err} → 人工位")
    save_state(state)


def lane_counts(inflight: list[dict], key_by_mid: dict, gpu_by_key: dict,
                isolated_by_key: dict) -> dict:
    """纯函数：在飞 mission 占各道数量（gpu/cpu）+ cpu 道非隔离在飞数。
    未知条目按 gpu+非隔离保守处理（无 backlog 记录的老 mission）。"""
    counts = {"gpu": 0, "cpu": 0, "cpu_dirty": 0}
    for m in inflight:
        key = key_by_mid.get(m.get("id"), "")
        if gpu_by_key.get(key, True):
            counts["gpu"] += 1
        else:
            counts["cpu"] += 1
            if not isolated_by_key.get(key, False):
                counts["cpu_dirty"] += 1
    return counts


def lane_blocked(counts: dict, lane: str, isolated: bool) -> str | None:
    """纯函数：候选条目该道是否被占（P2a 车道上限）。
    gpu 道串行（GPU_CAP=1）；cpu 道允许隔离条目并行至 CPU_CAP，但
    非隔离条目（可能碰共享索引/README）必须在空道才派，且不与
    非隔离在飞混跑——隔离=figdoc 类只碰自己案目录+自己的 report 文件。"""
    if lane == "gpu":
        return "gpu 道在飞" if counts["gpu"] >= GPU_CAP else None
    if counts["cpu"] >= CPU_CAP:
        return f"cpu 道满 {counts['cpu']}/{CPU_CAP}"
    if not isolated:
        return "cpu 道须空（候选非隔离）" if counts["cpu"] else None
    if counts["cpu_dirty"]:
        return "cpu 道有非隔离在飞"
    return None


def report_payload(text: str, figs: list[dict], mid: int, case: str) -> dict:
    """纯函数：/tensor/reports 请求体（stage 字段受 ^S[0-4]$ 约束，不放）。"""
    title = next((l.lstrip("# ").strip() for l in text.splitlines()
                  if l.startswith("# ")), f"benchmark 闭环：{case}")
    paras = [p.strip() for p in text.split("\n\n") if p.strip() and not
             p.strip().startswith(("#", "|", "-", "```"))]
    return {"title": title[:200],
            "summary": (paras[0][:300] if paras else title)[:300],
            "scenario_key": "benchmark-closure",
            "mission_id": mid,
            "tags": ["benchmark", "closure", case],
            "markdown": text, "figures": figs}


def dispatch(item: dict) -> int | None:
    env = dict(os.environ,
               REVIEW_USER=os.environ.get("OPERATOR_USER", ""),
               REVIEW_PASS=os.environ.get("OPERATOR_PASS", ""))
    tc, ws, tk = (int(x) for x in item["budget"].split(","))
    r = subprocess.run(
        [PY, str(HELPER), "--repo", "TensorLBM",
         "--base-ref", BACKLOG["base_ref"], "--model", item["model"],
         "--runner", "native", "--budget", f"{tc},{ws},{tk}",
         "--origin", "agent", "--goal-file", str(WORK / item["goal_file"]),
         "--memory-key", BACKLOG.get("memory_key", "benchmark-closure")],
        capture_output=True, text=True, timeout=180, env=env)
    if r.returncode != 0:
        log(f"ERROR {item['key']}: dispatch_helper 失败 {r.stderr.strip()[-1200:]}")
        return None
    try:
        return json.loads(r.stdout).get("id")
    except json.JSONDecodeError:
        log(f"ERROR {item['key']}: helper 输出不可解析 {r.stdout[:160]!r}")
        return None


def main() -> int:
    global BACKLOG
    if (WORK / "OFF").exists():
        log("SKIP: OFF 开关存在")
        return 0
    user, pw = os.environ.get("OPERATOR_USER"), os.environ.get("OPERATOR_PASS")
    if not (user and pw):
        log("ERROR: OPERATOR_USER/PASS 未设置（cron 须 source operator.env）")
        return 1
    BACKLOG = load_json(WORK / "backlog.json", None)
    if not BACKLOG or not BACKLOG.get("items"):
        log("ERROR: backlog.json 缺失或为空（先跑 build_backlog.py）")
        return 1
    code, body = api("POST", "/auth/login", {"username": user, "password": pw})
    if code != 200:
        log(f"ERROR: 登录失败 HTTP {code}")
        return 1
    tok = body["access_token"]
    code, body = api("GET", "/tensor/missions?limit=100", tok=tok)
    if code != 200 or not isinstance(body.get("items"), list):
        log(f"ERROR: mission 列表不可得 HTTP {code}（fail closed 不动作）")
        return 2
    # 前缀精确匹配：operator 值班 goal 的常驻记忆里也含 "BENCH-CLOSE:" 字样，
    # 子串匹配会把值班 mission 误捞进本线（m66 实测堵了 GPU 道）
    mine = {m["id"]: m for m in body["items"]
            if str(m.get("goal") or "").startswith(MARK)}

    state = load_json(WORK / "state.json", {"items": {}})
    sync_terminal(mine, state, tok)

    inflight = [m for m in mine.values() if m.get("status") in INFLIGHT]
    key_by_mid = {rec.get("mission"): key
                  for key, rec in state["items"].items() if rec.get("mission")}
    gpu_by_key = {i["key"]: bool(i.get("gpu")) for i in BACKLOG["items"]}
    isolated_by_key = {i["key"]: bool(i.get("isolated"))
                       for i in BACKLOG["items"]}
    counts = lane_counts(inflight, key_by_mid, gpu_by_key, isolated_by_key)
    if counts["gpu"] >= GPU_CAP and counts["cpu"] >= CPU_CAP:
        log(f"SKIP: 双道均满 m{','.join(str(m['id']) for m in inflight)} "
            f"(gpu={counts['gpu']} cpu={counts['cpu']})")
        return 0
    if not relay_ok():
        log("SKIP: 中继不通，本轮不派发（恢复后自动续）")
        return 0
    respect_gpu5_marker(counts)

    for item in BACKLOG["items"]:
        rec = state["items"].get(item["key"])
        if rec and (rec.get("done") or rec.get("failed") or rec.get("mission")):
            continue
        why = lane_blocked(counts, "gpu" if item.get("gpu") else "cpu",
                           bool(item.get("isolated")))
        if why:
            continue
        if item.get("wait_for") and not Path(item["wait_for"]).exists():
            continue
        if item.get("wait_for_pr") and not pr_merged(int(item["wait_for_pr"])):
            continue        # 目标 PR 未合（如同案在库 PR），避免双 PR 撞车
        mid = dispatch(item)
        if mid:
            state["items"][item["key"]] = {
                "mission": mid, "case": item["case"],
                "gates": True,      # A 改进：此后派发的条目 push 前过四门机械复算
                "paper": bool(item.get("paper")),   # paper 档=六门（细节+图件）
                "retries": (rec or {}).get("retries", 0),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
            save_state(state)
            log(f"DISPATCHED {item['key']} → m{mid} model={item['model']} "
                f"budget={item['budget']}")
        return 0          # 每轮至多派 1 个（串行）
    log("IDLE: 无可派条目（全部完成/失败/等待中）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
