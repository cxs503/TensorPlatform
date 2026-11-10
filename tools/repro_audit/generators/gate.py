"""repro_audit.generators.gate —— 入库门（fail-closed）。

规则：validation_report/报告引用的每个关键数字必须能在提交树内定位到生成器，
且生成器重放后 JSON 与被引用值一致（白名单字段：时间戳/主机名/耗时）。
不满足即 fail-closed。

用法::

    from tools.repro_audit.generators.gate import IngestionGate
    
    gate = IngestionGate(repo_root="/path/to/repo")
    result = gate.check_report("validation_report.json")
    if not result["passed"]:
        print(f"Gate failed: {result['reason']}")
        sys.exit(1)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Any

# 白名单字段：允许每次运行不同
WHITELIST_FIELDS = {"timestamp", "hostname", "duration_sec", "generated_at", 
                    "wall_time", "datetime", "created_at"}

# 数值路径正则（匹配 JSON 里的数字字段）
_NUMERIC_KEY_RE = re.compile(r"^(.*\.)?(value|result|mean|std|error|rate|ratio|count|total|sum|avg|min|max)$", 
                             re.IGNORECASE)


class IngestionGate:
    """入库门：验证报告里的关键数字可追溯到生成器。"""
    
    def __init__(self, repo_root: str, generator_dir: str = "tools/repro_audit/generators"):
        self.repo_root = os.path.abspath(repo_root)
        self.generator_dir = os.path.join(repo_root, generator_dir)
        self._generator_cache: dict[str, str] = {}  # artifact_path -> generator_path
        self._scan_generators()
    
    def _scan_generators(self):
        """扫描生成器目录，建立 artifact_path -> generator_path 映射。"""
        if not os.path.isdir(self.generator_dir):
            return
        
        for fname in os.listdir(self.generator_dir):
            if not fname.startswith("gen_") or not fname.endswith(".py"):
                continue
            
            fpath = os.path.join(self.generator_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    source = f.read()
                
                # 提取 OUTPUT_PATH 常量
                for m in re.finditer(r'OUTPUT_PATH\s*=\s*["\']([^"\']+)["\']', source):
                    artifact_path = m.group(1)
                    self._generator_cache[artifact_path] = fpath
                
                # 提取 TARGET_ARTIFACT 常量（别名）
                for m in re.finditer(r'TARGET_ARTIFACT\s*=\s*["\']([^"\']+)["\']', source):
                    artifact_path = m.group(1)
                    self._generator_cache[artifact_path] = fpath
            except OSError:
                pass
    
    def _extract_numeric_paths(self, obj: Any, prefix: str = "") -> list[str]:
        """递归提取 JSON 里所有数值路径。"""
        paths = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                path = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    paths.append(path)
                paths.extend(self._extract_numeric_paths(v, path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                paths.extend(self._extract_numeric_paths(item, f"{prefix}[{i}]"))
        return paths
    
    def _get_value_at_path(self, obj: Any, path: str) -> Any:
        """从 JSON 对象里按路径取值。"""
        parts = re.split(r'\.|\[(\d+)\]', path)
        parts = [p for p in parts if p]
        
        current = obj
        for part in parts:
            if part.isdigit():
                idx = int(part)
                if isinstance(current, list) and idx < len(current):
                    current = current[idx]
                else:
                    return None
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current
    
    def _find_generator_for_artifact(self, artifact_path: str) -> str | None:
        """查找能生成指定工件的生成器。"""
        # 精确匹配
        if artifact_path in self._generator_cache:
            return self._generator_cache[artifact_path]
        
        # 模糊匹配（basename）
        basename = os.path.basename(artifact_path)
        for key, gen_path in self._generator_cache.items():
            if os.path.basename(key) == basename:
                return gen_path
        
        return None
    
    def _run_generator(self, generator_path: str) -> dict[str, Any] | None:
        """运行生成器，返回输出 JSON。"""
        try:
            # 动态导入并运行
            import importlib.util
            spec = importlib.util.spec_from_file_location("gen", generator_path)
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            
            if hasattr(mod, "generate"):
                return mod.generate()
        except Exception as e:
            return None
        return None
    
    def _compare_values(self, expected: Any, actual: Any, rel_tol: float = 1e-6) -> bool:
        """比对两个值（数值用相对容差，其他用相等）。"""
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if abs(expected) < 1e-10:
                return abs(actual - expected) < 1e-10
            return abs(actual - expected) / abs(expected) <= rel_tol
        return expected == actual
    
    def check_report(self, report_path: str, rel_tol: float = 1e-6) -> dict[str, Any]:
        """检查报告：验证所有关键数字可追溯到生成器且可重放。
        
        返回：
        {
            "passed": bool,
            "reason": str | None,
            "checked": int,
            "traceable": int,
            "replayable": int,
            "failures": [{"path": ..., "reason": ...}, ...]
        }
        """
        # 加载报告
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return {"passed": False, "reason": f"Cannot load report: {e}",
                    "checked": 0, "traceable": 0, "replayable": 0, "failures": []}
        
        # 提取所有数值路径
        numeric_paths = self._extract_numeric_paths(report)
        
        # 按工件分组（假设路径里有 artifacts/ 前缀）
        artifact_groups: dict[str, list[str]] = {}
        for path in numeric_paths:
            # 尝试提取工件路径
            m = re.match(r"(artifacts/[^/]+/[^.]+\.json)", path)
            if m:
                art_path = m.group(1)
                artifact_groups.setdefault(art_path, []).append(path)
            else:
                # 顶层数值，跳过
                pass
        
        failures = []
        traceable = 0
        replayable = 0
        
        for art_path, paths in artifact_groups.items():
            # 查找生成器
            gen_path = self._find_generator_for_artifact(art_path)
            if gen_path is None:
                failures.append({
                    "artifact": art_path,
                    "reason": "no_generator_found",
                    "paths": paths[:5]
                })
                continue
            
            traceable += 1
            
            # 运行生成器
            generated = self._run_generator(gen_path)
            if generated is None:
                failures.append({
                    "artifact": art_path,
                    "generator": gen_path,
                    "reason": "generator_execution_failed",
                    "paths": paths[:5]
                })
                continue
            
            # 比对数值
            all_match = True
            for path in paths:
                # 从路径里提取相对路径（去掉 artifacts/xxx/ 前缀）
                rel_path = re.sub(r"^artifacts/[^/]+/", "", path)
                expected_val = self._get_value_at_path(report, path)
                actual_val = self._get_value_at_path(generated, rel_path)
                
                if actual_val is None:
                    failures.append({
                        "artifact": art_path,
                        "path": path,
                        "reason": "value_not_in_generated_output"
                    })
                    all_match = False
                elif not self._compare_values(expected_val, actual_val, rel_tol):
                    failures.append({
                        "artifact": art_path,
                        "path": path,
                        "reason": "value_mismatch",
                        "expected": expected_val,
                        "actual": actual_val
                    })
                    all_match = False
            
            if all_match:
                replayable += 1
        
        passed = len(failures) == 0 and traceable > 0
        
        return {
            "passed": passed,
            "reason": None if passed else f"{len(failures)} failures",
            "checked": len(artifact_groups),
            "traceable": traceable,
            "replayable": replayable,
            "failures": failures[:20]  # 限制输出
        }
    
    def check_artifact(self, artifact_path: str, rel_tol: float = 1e-6) -> dict[str, Any]:
        """检查单个工件：验证可追溯到生成器且可重放。"""
        # 查找生成器
        gen_path = self._find_generator_for_artifact(artifact_path)
        if gen_path is None:
            return {
                "passed": False,
                "reason": "no_generator_found",
                "artifact": artifact_path
            }
        
        # 加载原始工件
        full_path = os.path.join(self.repo_root, artifact_path)
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                original = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return {
                "passed": False,
                "reason": f"cannot_load_artifact: {e}",
                "artifact": artifact_path
            }
        
        # 运行生成器
        generated = self._run_generator(gen_path)
        if generated is None:
            return {
                "passed": False,
                "reason": "generator_execution_failed",
                "artifact": artifact_path,
                "generator": gen_path
            }
        
        # 比对（忽略白名单字段）
        def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
            out = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in WHITELIST_FIELDS:
                        continue
                    path = f"{prefix}.{k}" if prefix else k
                    out.update(_flatten(v, path))
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    out.update(_flatten(item, f"{prefix}[{i}]"))
            else:
                out[prefix] = obj
            return out
        
        orig_flat = _flatten(original)
        gen_flat = _flatten(generated.get("result", generated))
        
        mismatches = []
        for path in sorted(set(orig_flat) | set(gen_flat)):
            if path not in orig_flat:
                mismatches.append({"path": path, "reason": "missing_in_original"})
            elif path not in gen_flat:
                mismatches.append({"path": path, "reason": "missing_in_generated"})
            elif not self._compare_values(orig_flat[path], gen_flat[path], rel_tol):
                mismatches.append({
                    "path": path,
                    "reason": "value_mismatch",
                    "expected": orig_flat[path],
                    "actual": gen_flat[path]
                })
        
        return {
            "passed": len(mismatches) == 0,
            "reason": None if not mismatches else f"{len(mismatches)} mismatches",
            "artifact": artifact_path,
            "generator": gen_path,
            "mismatches": mismatches[:10]
        }


def gate_main():
    """命令行入口：python3 -m tools.repro_audit.generators.gate --gate <report.json>"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Ingestion gate: validate reproducibility")
    parser.add_argument("--gate", metavar="REPORT", help="Check report file")
    parser.add_argument("--artifact", metavar="PATH", help="Check single artifact")
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--tol", type=float, default=1e-6, help="Relative tolerance")
    
    args = parser.parse_args()
    gate = IngestionGate(args.repo)
    
    if args.gate:
        result = gate.check_report(args.gate, rel_tol=args.tol)
    elif args.artifact:
        result = gate.check_artifact(args.artifact, rel_tol=args.tol)
    else:
        parser.print_help()
        sys.exit(1)
    
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    gate_main()
