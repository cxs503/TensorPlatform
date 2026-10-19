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
h._stream_completion({{"model": "openai/x", "messages": [], "tools": None,
                       "stream": True}})
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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        exec(CHILD)
    else:
        sys.exit(main())
