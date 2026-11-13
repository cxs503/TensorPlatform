#!/usr/bin/env python3
"""compute_gate.install —— 机制化部署（可回滚，幂等）：

  1. 把本包同步到 /root/tp-agent/cluster/compute_gate（tp_worker 的部署位）
  2. patch /root/tp-agent/tp_worker.py：
       a. 新增工具 run_compute（取代 run_cluster v2 的**递交语义**；run_cluster 保留为兼容别名）
       b. run_shell 前置硬拦：本地重计算（≥8 并行 / 后台逃逸 / 长阻塞）→ 工具级拒绝并指向 run_compute
     每一步：备份 .bak_compute_gate_<ts>、锚点校验、失败不动原文、`--check` 只体检不改
  3. 安装 systemd 单元 tp-compute-guard（常驻扫描；不依赖任何人类会话）
       systemd 不可用 → 退化为 nohup 守护并写 PID 文件（仍然机制化）

用法：
  python3 -m tools.compute_gate.install --check        # 只体检（锚点/已装状态）
  python3 -m tools.compute_gate.install --apply        # 同步+打补丁（备份）
  python3 -m tools.compute_gate.install --rollback     # 回滚到最近备份
"""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import time

HERE = pathlib.Path(__file__).resolve().parent
DEPLOY_DIR = pathlib.Path("/root/tp-agent/cluster/compute_gate")
WORKER = pathlib.Path("/root/tp-agent/tp_worker.py")
UNIT_SRC = HERE.parents[1] / "deploy" / "systemd" / "tp-compute-guard.service"
UNIT_DST = pathlib.Path("/etc/systemd/system/tp-compute-guard.service")

TOOL_SPEC = '''    {"type": "function", "function": {
        "name": "run_compute", "description":
        "计算递交门（owner 2026-11-13 计算调度令，取代 run_cluster v2）：agent 的一切计算**递交**给调度系统，"
        "跑在哪里是调度系统的事。submit 立即返回 job_id+排队位次；status/fetch 轮询取工件；每份回执附 "
        "local vs queued 用量。本机只准 <8 核、<10min 的短测试——重计算会被硬拦并自动迁移。",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["submit", "status", "fetch", "nodes", "usage", "kill"],
                        "description": "submit=递交(默认)；status=查状态；fetch=取工件(--wait 可等)；usage=用量回执"},
            "repo": {"type": "string", "description": "仓库名（如 TensorBEM）"},
            "cmd": {"type": "string", "description": "单命令（在 repo 检出内执行）"},
            "cmd_tpl": {"type": "string", "description": "扫描命令模板，{i}/{n} 会替换"},
            "shards": {"type": "integer", "description": "分片总数 1-64"},
            "cores": {"type": "integer", "description": "资源请求核数（准入记账；不指定节点）"},
            "ref": {"type": "string", "description": "git ref，默认 main"},
            "timeout": {"type": "integer", "description": "秒，上限 7200"},
            "stage_dir": {"type": "string", "description": "本地目录 tar 直达节点（测自己的工作树用这个）"},
            "job_id": {"type": "string", "description": "cjN 作业号（status/fetch/kill 用）"},
            "wait_seconds": {"type": "integer", "description": "fetch 时最多等多少秒到终态"}},
            "required": ["action"]}}},
'''

SHIM = '''
# ── 计算递交门（compute_gate，owner 2026-11-13 计算调度令）──────────────────
# 摘除/回滚：删掉本块 + run_compute 工具项与 handler 分支（或 install.py --rollback）。
def _cg_module():
    """装载部署位 compute_gate；不可用返回 None（降级=老行为，不阻断 worker）。"""
    try:
        import importlib.util, sys as _s
        key = "_tp_compute_gate"
        if key in _s.modules:
            return _s.modules[key]
        pkg_dir = os.path.join("/root/tp-agent/cluster/compute_gate", "__init__.py")
        if not os.path.exists(pkg_dir):
            return None
        spec = importlib.util.spec_from_file_location(
            "compute_gate", pkg_dir, submodule_search_locations=[os.path.dirname(pkg_dir)])
        mod = importlib.util.module_from_spec(spec)
        _s.modules["compute_gate"] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def tool_run_compute(mission, **kw):
    """run_compute 工具实现：递交语义 + local vs queued 用量回执。"""
    cg = _cg_module()
    if cg is None:
        return "REJECTED: 计算递交门未部署（/root/tp-agent/cluster/compute_gate 缺失）——先跑 install --apply"
    cfg = cg.load_config()
    kw["mission_id"] = kw.get("mission_id") or (mission or {}).get("id")
    r = cg.run_compute(cfg, **kw)
    return json.dumps(r, ensure_ascii=False)[:12000]


def tool_run_shell_gated(root, command):
    """run_shell 前置硬拦：静态可判的本机重计算/后台逃逸 → 工具级拒绝并指向 run_compute。"""
    cg = _cg_module()
    if cg is not None:
        try:
            from compute_gate import preflight as _pf
            res = _pf.check_local_command(command)
            if res.get("verdict") == "redirect":
                return _pf.format_redirect(res, tool_hint="run_compute")
        except Exception:
            pass
    return tool_run_shell(root, command)

'''

HANDLER_OLD = '''                elif name == "run_shell":
                    result = tool_run_shell(root, args["command"])'''
HANDLER_NEW = '''                elif name == "run_shell":
                    result = tool_run_shell_gated(root, args["command"])'''
HANDLER_RC = '''                elif name == "run_compute":
                    result = tool_run_compute(mission, **args)'''
ANCHOR_FINISH = '    {"type": "function", "function": {\n        "name": "finish"'


def _bak_path():
    return WORKER.with_name("tp_worker.py.bak_compute_gate_%s" % time.strftime("%Y%m%d_%H%M%S"))


def check():
    out = {"worker": str(WORKER), "exists": WORKER.exists(), "deploy_dir": str(DEPLOY_DIR),
           "deploy_present": DEPLOY_DIR.exists(), "checks": []}
    if not WORKER.exists():
        out["checks"].append({"item": "worker", "ok": False, "detail": "tp_worker.py 缺失"})
        return out
    src = WORKER.read_text()
    out["checks"] += [
        {"item": "tool_spec_run_compute", "ok": '"name": "run_compute"' in src},
        {"item": "shim_present", "ok": "def tool_run_compute(" in src and "def tool_run_shell_gated(" in src},
        {"item": "handler_compute", "ok": 'name == "run_compute"' in src},
        {"item": "handler_gated_shell", "ok": 'tool_run_shell_gated(root, args["command"])' in src},
        {"item": "anchor_finish", "ok": ANCHOR_FINISH in src},
        {"item": "syntax_ok", "ok": _syntax_ok(src)},
    ]
    return out


def _syntax_ok(src):
    try:
        compile(src, str(WORKER), "exec")
        return True
    except SyntaxError:
        return False


def sync_package():
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(HERE.glob("*.py")):
        shutil.copy2(f, DEPLOY_DIR / f.name)
        n += 1
    (DEPLOY_DIR / "VERSION").write_text("compute_gate %s\n" % time.strftime("%Y-%m-%d %H:%M"))
    return n


def apply_patch():
    src = WORKER.read_text()
    orig = src
    changes = []
    if '"name": "run_compute"' not in src:
        if ANCHOR_FINISH not in src:
            raise SystemExit("ANCHOR MISS（finish 工具项）—— 拒绝改动")
        src = src.replace(ANCHOR_FINISH, TOOL_SPEC + ANCHOR_FINISH, 1)
        changes.append("tool_spec")
    if "def tool_run_shell_gated(" not in src:
        anchor = "def _parse_edit_whitelist(goal):"
        if anchor not in src:
            raise SystemExit("ANCHOR MISS（_parse_edit_whitelist）—— 拒绝改动")
        src = src.replace(anchor, SHIM.lstrip("\n") + "\n" + anchor, 1)
        changes.append("shim")
    if HANDLER_OLD in src:
        src = src.replace(HANDLER_OLD, HANDLER_NEW + "\n" + HANDLER_RC + "\n", 1)
        changes.append("handler")
    elif 'name == "run_compute"' not in src:
        raise SystemExit("ANCHOR MISS（run_shell handler）—— 拒绝改动")
    if src == orig:
        return {"changed": [], "note": "已是目标状态（幂等）"}
    if not _syntax_ok(src):
        raise SystemExit("补丁后语法检查失败 —— 拒绝落盘")
    bak = _bak_path()
    shutil.copy2(WORKER, bak)
    WORKER.write_text(src)
    return {"changed": changes, "backup": str(bak), "md5_before": __import__("hashlib").md5(
        orig.encode()).hexdigest()}


def install_unit():
    if not UNIT_SRC.exists():
        return {"unit": "missing_source", "src": str(UNIT_SRC)}
    res = {}
    try:
        shutil.copy2(UNIT_SRC, UNIT_DST)
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=60)
        r = subprocess.run(["systemctl", "enable", "--now", "tp-compute-guard"],
                           capture_output=True, text=True, timeout=120)
        res["systemd"] = {"rc": r.returncode, "out": (r.stdout + r.stderr)[-500:]}
        if r.returncode == 0:
            return res
    except Exception as e:
        res["systemd"] = {"error": "%s: %s" % (type(e).__name__, e)}
    # 退化：nohup 守护（仍机制化，PID 文件可审计/可回滚）
    pidf = DEPLOY_DIR / "guard.pid"
    if pidf.exists() and _alive(int(pidf.read_text().strip() or 0)):
        res["nohup"] = "already running pid=%s" % pidf.read_text().strip()
        return res
    p = subprocess.Popen(["python3", "-m", "compute_gate.cli", "guard", "--loop"],
                         cwd=str(DEPLOY_DIR.parent), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    pidf.write_text(str(p.pid))
    res["nohup"] = {"pid": p.pid, "pid_file": str(pidf)}
    return res


def _alive(pid):
    return pid > 0 and pathlib.Path("/proc/%d" % pid).exists()


def rollback():
    baks = sorted(WORKER.parent.glob("tp_worker.py.bak_compute_gate_*"))
    if not baks:
        return {"rollback": "no backup found"}
    shutil.copy2(baks[-1], WORKER)
    try:
        pidf = DEPLOY_DIR / "guard.pid"
        if pidf.exists():
            import os
            import signal
            try:
                os.kill(int(pidf.read_text().strip()), signal.SIGTERM)
            except Exception:
                pass
    except Exception:
        pass
    subprocess.run(["systemctl", "disable", "--now", "tp-compute-guard"], capture_output=True, timeout=60)
    return {"rollback": str(baks[-1]), "restored": str(WORKER)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="compute_gate.install")
    ap.add_argument("--check", action="store_true"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true"); ap.add_argument("--skip-unit", action="store_true")
    a = ap.parse_args(argv)
    if a.rollback:
        print(json.dumps(rollback(), ensure_ascii=False, indent=1)); return 0
    if a.check or not a.apply:
        print(json.dumps(check(), ensure_ascii=False, indent=1)); return 0
    out = {"sync_files": sync_package(), "patch": apply_patch()}
    if not a.skip_unit:
        out["unit"] = install_unit()
    out["post_check"] = check()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if all(c.get("ok", True) for c in out["post_check"]["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())