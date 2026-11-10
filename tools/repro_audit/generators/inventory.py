"""repro_audit.generators.inventory —— 生成器盘点器。

对 36 节点样本静态 + 动态解析：在提交树内查找写该 JSON 路径的脚本
（AST 扫描 open()/json.dump/Path.write_text），输出 node→generator 映射与缺口清单。

用法::

    from tools.repro_audit.generators.inventory import scan_inventory, report_gaps
    
    inventory = scan_inventory(work_root="/root/tp-agent/work", n_sample=36)
    gaps = report_gaps(inventory)
    print(f"覆盖: {inventory['covered']}/{inventory['total']}")
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from typing import Any

# JSON 工件路径模式
_ARTIFACT_JSON_RE = re.compile(r"artifacts/.*\.json$")
_GENERATOR_DIR = "tools/repro_audit/generators"


def _find_json_writers_ast(tree: ast.AST, source_file: str) -> list[dict[str, Any]]:
    """AST 扫描：找所有写 JSON 的位置（json.dump / Path.write_text / open(...'w')）。"""
    writers = []
    
    class JsonWriteVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            # json.dump(obj, file)
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "dump" and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "json":
                        writers.append({
                            "file": source_file,
                            "line": node.lineno,
                            "method": "json.dump",
                            "col": node.col_offset
                        })
                # Path.write_text()
                elif node.func.attr == "write_text":
                    writers.append({
                        "file": source_file,
                        "line": node.lineno,
                        "method": "Path.write_text",
                        "col": node.col_offset
                    })
            # open(..., 'w')
            elif isinstance(node.func, ast.Name) and node.func.id == "open":
                if len(node.args) >= 2:
                    mode_arg = node.args[1]
                    if isinstance(mode_arg, ast.Constant) and "w" in str(mode_arg.value):
                        writers.append({
                            "file": source_file,
                            "line": node.lineno,
                            "method": "open(write)",
                            "col": node.col_offset
                        })
            self.generic_visit(node)
    
    JsonWriteVisitor().visit(tree)
    return writers


def _find_json_writers_regex(source: str, source_file: str) -> list[dict[str, Any]]:
    """正则后备：AST 解析失败时用正则找 json.dump / write_text。"""
    writers = []
    for i, line in enumerate(source.splitlines(), 1):
        if "json.dump" in line or ".write_text(" in line:
            writers.append({
                "file": source_file,
                "line": i,
                "method": "json.dump" if "json.dump" in line else "write_text",
                "col": 0
            })
    return writers


def scan_file_for_writers(file_path: str) -> list[dict[str, Any]]:
    """扫描单个 Python 文件，返回所有 JSON 写入点。"""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except OSError:
        return []
    
    try:
        tree = ast.parse(source, filename=file_path)
        return _find_json_writers_ast(tree, file_path)
    except SyntaxError:
        return _find_json_writers_regex(source, file_path)


def scan_repo_for_generators(repo_root: str, max_files: int = 5000) -> list[dict[str, Any]]:
    """扫描仓库内所有 Python 文件，找 JSON 写入点。"""
    writers = []
    count = 0
    
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # 跳过隐藏目录和常见非源码目录
        dirnames[:] = [d for d in dirnames if not d.startswith(".") 
                       and d not in ("node_modules", "__pycache__", ".git", "venv")]
        
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            if count >= max_files:
                break
            
            fpath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(fpath, repo_root)
            
            found = scan_file_for_writers(fpath)
            for w in found:
                w["file"] = rel_path  # 转为相对路径
            writers.extend(found)
            count += 1
    
    return writers


def extract_artifact_paths(source: str, writers: list[dict[str, Any]]) -> list[str]:
    """从源码里提取工件路径（正则匹配 artifacts/*.json）。"""
    paths = set()
    
    # 从 writer 附近上下文提取路径
    lines = source.splitlines()
    for w in writers:
        line_idx = w["line"] - 1
        # 看前后 5 行
        for i in range(max(0, line_idx - 5), min(len(lines), line_idx + 5)):
            for m in _ARTIFACT_JSON_RE.finditer(lines[i]):
                paths.add(m.group(0))
    
    # 全局扫描 artifacts/ 路径
    for m in _ARTIFACT_JSON_RE.finditer(source):
        paths.add(m.group(0))
    
    return sorted(paths)


def _get_generator_dir() -> str:
    """获取生成器目录路径。"""
    # 当前文件在 tools/repro_audit/generators/inventory.py
    # 生成器目录就是当前目录
    return os.path.dirname(os.path.abspath(__file__))


def scan_inventory(work_root: str, n_sample: int = 36, 
                   seed: str = "repro-audit") -> dict[str, Any]:
    """盘点生成器覆盖度：扫描 work_root 下前 n_sample 个 mission 目录。
    
    返回：
    {
        "total": 36,
        "covered": 12,
        "uncovered": 24,
        "nodes": [
            {"mission_id": 123, "artifacts": [...], "generators": [...]},
            ...
        ],
        "generator_registry": {
            "artifacts/foo.json": "tools/repro_audit/generators/gen_foo.py",
            ...
        }
    }
    """
    # 收集 mission 目录
    missions = []
    for entry in sorted(os.listdir(work_root)):
        if entry.startswith("m") and entry[1:].isdigit():
            missions.append(entry)
        if len(missions) >= n_sample:
            break
    
    # 扫描生成器目录
    gen_dir = _get_generator_dir()
    
    generator_registry = {}
    if os.path.isdir(gen_dir):
        for fname in os.listdir(gen_dir):
            if fname.startswith("gen_") and fname.endswith(".py"):
                fpath = os.path.join(gen_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        source = f.read()
                    # 提取 OUTPUT_PATH 常量
                    for m in re.finditer(r'OUTPUT_PATH\s*=\s*["\']([^"\']+)["\']', source):
                        generator_registry[m.group(1)] = f"tools/repro_audit/generators/{fname}"
                except OSError:
                    pass
    
    # 盘点每个 mission
    nodes = []
    covered = 0
    
    for mdir in missions:
        mid = int(mdir[1:])
        mpath = os.path.join(work_root, mdir)
        
        # 找 artifacts/*.json
        artifacts = []
        art_dir = os.path.join(mpath, "artifacts")
        if os.path.isdir(art_dir):
            for root, _, files in os.walk(art_dir):
                for f in files:
                    if f.endswith(".json"):
                        rel = os.path.relpath(os.path.join(root, f), mpath)
                        artifacts.append(rel)
        
        # 检查是否有对应生成器
        generators = []
        for art in artifacts:
            if art in generator_registry:
                generators.append(generator_registry[art])
        
        if generators:
            covered += 1
        
        nodes.append({
            "mission_id": mid,
            "artifacts": artifacts[:10],  # 限制输出大小
            "generators": generators,
            "covered": len(generators) > 0
        })
    
    return {
        "total": len(nodes),
        "covered": covered,
        "uncovered": len(nodes) - covered,
        "coverage_rate": covered / len(nodes) if nodes else 0,
        "nodes": nodes,
        "generator_registry": generator_registry
    }


def report_gaps(inventory: dict[str, Any]) -> dict[str, Any]:
    """生成缺口报告：列出未覆盖的节点和缺失的生成器。"""
    gaps = []
    for node in inventory["nodes"]:
        if not node["covered"] and node["artifacts"]:
            gaps.append({
                "mission_id": node["mission_id"],
                "missing_artifacts": node["artifacts"],
                "suggestion": f"Create tools/repro_audit/generators/gen_m{node['mission_id']}.py"
            })
    
    return {
        "total_gaps": len(gaps),
        "gaps": gaps[:50],  # 限制输出
        "coverage_rate": inventory["coverage_rate"]
    }
