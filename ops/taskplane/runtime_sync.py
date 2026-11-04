#!/usr/bin/env python3
"""生产树追平 GitHub main（owner 2026-11-01 批：1.2.3 往前推）。

每 10min：GitHub main tip → fetch → 隔离 detached worktree 合并 → AST+导入门 →
生产树 ff-only 翻新 → 健康复检（坏则回滚）。冲突=BLOCKED 只记不推。
绝不 push、绝不碰 main、破坏性操作前必验 cwd。
"""
import json, os, pathlib, re, subprocess, time, urllib.request

PROD = "/root/tensor-ecosystem/TensorPlatform"
WT = "/tmp/rt_sync_wt"
STATE = "/root/tp-agent/runtime_sync_state.json"
CYCLE = 600

def log(m):
    with open("/root/tp-agent/runtime_sync.log", "a") as f:
        f.write(f"{time.strftime('%F %T')} {m}\n")

def sh(cmd, timeout=180, cwd=None, env=None):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, cwd=cwd, env=env)

def main_tip():
    pat = re.search(r"//[^:@\s]+:([^@\s]+)@github\.com",
                    pathlib.Path("/root/.git-credentials").read_text()).group(1)
    r = urllib.request.Request("https://api.github.com/repos/cxs503/TensorPlatform/branches/main",
        headers={"Authorization": f"token {pat}", "Accept": "application/vnd.github+json"})
    return json.load(urllib.request.urlopen(r))["commit"]["sha"]

def health():
    try:
        return urllib.request.urlopen("http://127.0.0.1:8010/health", timeout=10).status == 200
    except Exception:
        return False

def db_url():
    pid = subprocess.run(["pgrep", "-f", "uvicorn"], capture_output=True, text=True).stdout.split()
    for p in pid:
        try:
            env = pathlib.Path(f"/proc/{p}/environ").read_bytes().decode(errors="replace").split("\0")
            for e in env:
                if e.startswith("DATABASE_URL="):
                    return e.split("=", 1)[1]
        except OSError:
            continue
    return ""

def cycle():
    if not health():
        log("API 不健康，跳过本轮"); return
    tip = main_tip()
    st = json.loads(pathlib.Path(STATE).read_text()) if pathlib.Path(STATE).exists() else {}
    head = sh("git rev-parse HEAD", cwd=PROD).stdout.strip()
    if sh(f"git merge-base --is-ancestor {tip} HEAD", cwd=PROD).returncode == 0:
        st.update({"last_seen": tip, "status": "OK"}); pathlib.Path(STATE).write_text(json.dumps(st))
        return
    f = sh("git fetch origin main", timeout=300, cwd=PROD)
    if f.returncode != 0:
        log(f"fetch 失败（网络），下轮再试: {f.stderr.strip()[:80]}"); return
    sh(f"git worktree remove --force {WT}", cwd=PROD); sh("git worktree prune", cwd=PROD)
    w = sh(f"git worktree add --detach {WT} {head}", cwd=PROD)
    if w.returncode != 0:
        log(f"worktree 建败: {w.stderr.strip()[:80]}"); return
    m = sh(f"git merge --no-edit {tip}", cwd=WT, timeout=300)
    if m.returncode != 0:
        sh("git merge --abort", cwd=WT); sh(f"git worktree remove --force {WT}", cwd=PROD)
        log(f"BLOCKED 冲突，需人工: {m.stderr.strip()[:100]}")
        st.update({"status": "BLOCKED", "pending_tip": tip})
        pathlib.Path(STATE).write_text(json.dumps(st)); return
    merged = sh("git rev-parse HEAD", cwd=WT).stdout.strip()
    bad = []
    for fn in sh(f"git diff --name-only {head}..{merged}", cwd=WT).stdout.split():
        if fn.endswith(".py"):
            s = sh(f"git show {merged}:{fn}", cwd=WT)
            try:
                import ast; ast.parse(s.stdout)
            except SyntaxError:
                bad.append(fn)
    if bad:
        log(f"AST FAIL {bad}，放弃本轮"); sh(f"git worktree remove --force {WT}", cwd=PROD); return
    env = dict(os.environ, TORCH_DEVICE_BACKEND_AUTOLOAD="0", PYTHONPATH="backend", DATABASE_URL=db_url())
    imp = sh("/root/tensor-ecosystem/TensorPlatform/.venv/bin/python -c 'import api.tensor_missions, api.domain.tensor'",
             cwd=f"{WT}/backend", env=env)
    if imp.returncode != 0:
        log(f"导入门 FAIL: {imp.stderr.strip()[-120:]}"); sh(f"git worktree remove --force {WT}", cwd=PROD); return
    os.chdir(PROD); assert os.getcwd() == PROD
    prev = head
    ff = sh(f"git merge --ff-only {merged}", cwd=PROD)
    if ff.returncode != 0:
        log(f"ff-only 失败: {ff.stderr.strip()[:80]}"); sh(f"git worktree remove --force {WT}", cwd=PROD); return
    for _ in range(3):
        if health(): break
        time.sleep(10)
    if not health():
        sh(f"git reset --hard {prev}", cwd=PROD)
        log("!! 翻新后健康检查失败，已回滚"); return
    sh(f"git worktree remove --force {WT}", cwd=PROD)
    st.update({"last_seen": tip, "last_sync": time.time(), "status": "OK", "head": merged[:10]})
    pathlib.Path(STATE).write_text(json.dumps(st))
    log(f"翻新 {prev[:8]} -> {merged[:8]} (main {tip[:8]})")

while True:
    try:
        cycle()
    except Exception as e:
        log(f"异常: {e}")
    time.sleep(CYCLE)
