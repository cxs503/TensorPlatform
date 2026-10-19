"""挂起看门狗故障注入验证：LLM 流永不出块（litellm 吞读超时的实证场景）
→ 看门狗应在 LLM_STALL_EXIT_SECONDS+宽容窗内 os._exit(137)。

父进程口径：子进程 returncode==137 且墙钟 < 90s = PASS。
用法：python3 test_stall_watchdog.py [--child]
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(os.path.abspath(__file__))

CHILD = r'''
import os, sys, time
os.environ["LLM_STALL_EXIT_SECONDS"] = "4"
sys.path.insert(0, "{h}")
import native_harness as nh
import litellm

class _R:
    model = "qwen3.7-plus"
    stop_reason = None
    class _B:
        action_count = 0
        def emit(self, *a, **k): pass
    bridge = _B()

h = nh.NativeHarness(_R(), "/tmp", "/tmp/stall_test_state")

def _hang(*a, **k):
    class _Resp:
        def __iter__(self):
            while True:
                time.sleep(600)   # 永不出块：2026-10-19 13:06 事故形态
        def close(self): pass
    return _Resp()

litellm.completion = _hang
# 经 run() 真实路径（2026-10-19 修复后 init 安全默认不武装，run() 进入才
# 武装看门狗——直调 _stream_completion 会因 _conv_done=True 先退役）
h._run_loop = lambda: h._stream_completion(
    {{"model": "openai/x", "messages": [], "tools": None, "stream": True}})
h.run()
print("FAIL: 挂死流却正常返回", flush=True)
'''.format(h=HARNESS)


def main() -> int:
    t0 = time.time()
    p = subprocess.run([sys.executable, __file__, "--child"],
                       capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHONPATH": HARNESS})
    dt = time.time() - t0
    out = (p.stdout + p.stderr).strip().splitlines()
    print(f"returncode={p.returncode} wall={dt:.1f}s")
    for line in out[-3:]:
        print("  |", line)
    ok = p.returncode == 137 and dt < 90
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


# ── T2（2026-10-19 m52 事故回归）：干净收尾后看门狗必须退役 ──
# m51 正常交付（纯文本收尾→request_acceptance，stop_reason=None）后遗留
# 看门狗线程 _last_progress 冻结，900s 后 os._exit(137) 杀掉守护与下一个
# 在飞 mission（m52 陪葬）。run() 返回必须退役看门狗。
CHILD2 = r'''
import os, sys, time
os.environ["LLM_STALL_EXIT_SECONDS"] = "4"
sys.path.insert(0, "{h}")
import native_harness as nh

class _R:
    model = "qwen3.7-plus"
    stop_reason = None
    class _B:
        action_count = 0
        def emit(self, *a, **k): pass
    bridge = _B()

h = nh.NativeHarness(_R(), "/tmp", "/tmp/stall_test_state2")
h._run_loop = lambda: None      # 纯文本干净收尾：run() 立即返回
h.run()
assert h._conv_done is True, "run() 返回未置 _conv_done"
print("run() returned, conv_done=True; sleeping past stall window...", flush=True)
time.sleep(4 + 35 + 35)         # 越过两个 30s 巡检窗：退役线程应已 return
print("SURVIVED: watchdog retired after clean finish", flush=True)
'''.format(h=HARNESS)


def main2() -> int:
    t0 = time.time()
    p = subprocess.run([sys.executable, __file__, "--child2"],
                       capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHONPATH": HARNESS})
    dt = time.time() - t0
    out = (p.stdout + p.stderr).strip().splitlines()
    print(f"T2 returncode={p.returncode} wall={dt:.1f}s")
    for line in out[-3:]:
        print("  |", line)
    ok = p.returncode == 0 and any("SURVIVED" in x for x in out)
    print("T2 PASS" if ok else "T2 FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        exec(CHILD)
    elif len(sys.argv) > 1 and sys.argv[1] == "--child2":
        exec(CHILD2)
    elif len(sys.argv) > 1 and sys.argv[1] == "--t2":
        sys.exit(main2())
    else:
        rc1 = main()
        rc2 = main2()
        sys.exit(rc1 or rc2)
