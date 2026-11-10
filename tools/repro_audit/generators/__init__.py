"""repro_audit.generators —— 数值通道生成器框架。

每个生成器是一个 Python 模块，暴露 ``generate() -> dict`` 函数，
返回可序列化的 JSON 结构（含 inputs、command、module_sha256、seed、result）。

设计约束：
  * **幂等**：同输入同输出（除白名单字段：timestamp、hostname、duration_sec）
  * **最小依赖**：只读模块 + 计算关键量 + 写 JSON
  * **可追溯**：每个生成器记录模块 sha256、输入参数、命令
  * **零领域语义**：生成器不认识船型/学科词，只做数值复算
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import time
from typing import Any, Callable

# 白名单字段：这些字段允许每次运行不同（时间戳、主机名、耗时）
WHITELIST_FIELDS = {"timestamp", "hostname", "duration_sec", "generated_at", "wall_time"}


def sha256_file(path: str) -> str:
    """计算文件 sha256（用于生成器模块指纹）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_generator(module_path: str) -> Callable[[], dict]:
    """动态加载生成器模块，返回 generate 函数。"""
    spec = importlib.util.spec_from_file_location("generator", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load generator from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "generate"):
        raise AttributeError(f"Generator {module_path} missing generate() function")
    return mod.generate


def run_generator(module_path: str, output_path: str | None = None) -> dict[str, Any]:
    """运行生成器，返回完整结果（含元数据）。

    结果结构：
    {
        "generator": "<module_path>",
        "module_sha256": "<sha256>",
        "inputs": {...},
        "command": "<python3 ...>",
        "seed": "<seed>",
        "timestamp": "<iso8601>",
        "hostname": "<host>",
        "duration_sec": <float>,
        "result": {...}
    }
    """
    import socket
    
    started = time.time()
    gen_func = load_generator(module_path)
    
    # 运行生成器
    result = gen_func()
    duration = time.time() - started
    
    # 计算模块 sha256
    module_sha = sha256_file(module_path)
    
    # 构建完整输出
    output = {
        "generator": os.path.basename(module_path),
        "module_sha256": module_sha,
        "inputs": getattr(gen_func, "_inputs", {}),
        "command": f"python3 {module_path}",
        "seed": getattr(gen_func, "_seed", "deterministic"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "duration_sec": round(duration, 3),
        "result": result
    }
    
    # 写输出文件（如果指定）
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, sort_keys=True)
    
    return output


def compare_outputs(expected: dict, actual: dict, whitelist: set[str] | None = None) -> dict[str, Any]:
    """比对两个生成器输出，返回差异报告。

    白名单字段（timestamp/hostname/duration_sec）自动跳过。
    返回：
    {
        "match": bool,
        "differences": [{"path": "...", "expected": ..., "actual": ...}, ...],
        "checked_paths": int
    }
    """
    whitelist = whitelist or WHITELIST_FIELDS
    
    def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
        """拍平 JSON 为 path -> value。"""
        out = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                path = f"{prefix}.{k}" if prefix else k
                if k in whitelist:
                    continue  # 跳过白名单字段
                out.update(_flatten(v, path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                out.update(_flatten(item, f"{prefix}[{i}]"))
        else:
            out[prefix] = obj
        return out
    
    exp_flat = _flatten(expected)
    act_flat = _flatten(actual)
    
    differences = []
    all_paths = sorted(set(exp_flat) | set(act_flat))
    
    for path in all_paths:
        if path not in exp_flat:
            differences.append({"path": path, "expected": None, "actual": act_flat[path]})
        elif path not in act_flat:
            differences.append({"path": path, "expected": exp_flat[path], "actual": None})
        elif exp_flat[path] != act_flat[path]:
            differences.append({"path": path, "expected": exp_flat[path], "actual": act_flat[path]})
    
    return {
        "match": len(differences) == 0,
        "differences": differences,
        "checked_paths": len(all_paths)
    }
