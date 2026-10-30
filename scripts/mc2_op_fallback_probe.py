#!/usr/bin/env python3
"""
M-C2 P1: Op Fallback Probe for SDAA Card 12
Scans TensorPlatform backend for CPU fallback patterns and device awareness.
Produces: op_fallback_probe.json with (fallback_ops, hot_ops) tables.
"""

import ast
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

# Compute-intensive services to probe
COMPUTE_FILES = [
    "backend/services/experiment_algorithms_gnn_gan.py",
    "backend/services/real_training_service.py",
    "backend/services/compression_service.py",
    "backend/api/cv_training.py",
    "backend/api/audio_training.py",
    "backend/api/gan_training.py",
    "backend/api/gnn_training.py",
    "backend/services/model_training_service.py",
]

class DevicePatternAnalyzer(ast.NodeVisitor):
    """AST visitor to detect device-related patterns."""
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.cpu_hardcoded: List[Dict] = []
        self.device_aware: List[Dict] = []
        self.fallback_patterns: List[Dict] = []
        self.hot_ops: List[Dict] = []
        self.current_function = None
        self.current_class = None
        
    def visit_FunctionDef(self, node):
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func
        
    def visit_ClassDef(self, node):
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class
        
    def visit_Call(self, node):
        # Detect .to("cpu") or .to(device="cpu")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "to":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "cpu":
                        self.cpu_hardcoded.append({
                            "line": node.lineno,
                            "pattern": ".to('cpu')",
                            "function": self.current_function,
                            "class": self.current_class,
                            "severity": "HIGH"
                        })
                for keyword in node.keywords:
                    if keyword.arg == "device":
                        if isinstance(keyword.value, ast.Constant) and keyword.value.value == "cpu":
                            self.cpu_hardcoded.append({
                                "line": node.lineno,
                                "pattern": ".to(device='cpu')",
                                "function": self.current_function,
                                "class": self.current_class,
                                "severity": "HIGH"
                            })
            
            # Detect .cpu()
            if node.func.attr == "cpu" and not node.args:
                self.cpu_hardcoded.append({
                    "line": node.lineno,
                    "pattern": ".cpu()",
                    "function": self.current_function,
                    "class": self.current_class,
                    "severity": "MEDIUM"
                })
            
            # Detect torch.device("cuda" if ... else "cpu")
            if node.func.attr == "device":
                self.device_aware.append({
                    "line": node.lineno,
                    "pattern": "torch.device()",
                    "function": self.current_function,
                    "class": self.current_class,
                    "severity": "INFO"
                })
        
        # Detect torch.as_tensor(..., device="cpu")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ["as_tensor", "zeros", "randn", "tensor"]:
                for keyword in node.keywords:
                    if keyword.arg == "device":
                        if isinstance(keyword.value, ast.Constant) and keyword.value.value == "cpu":
                            self.cpu_hardcoded.append({
                                "line": node.lineno,
                                "pattern": f"torch.{node.func.attr}(device='cpu')",
                                "function": self.current_function,
                                "class": self.current_class,
                                "severity": "HIGH"
                            })
        
        # Detect map_location="cpu"
        for keyword in node.keywords:
            if keyword.arg == "map_location":
                if isinstance(keyword.value, ast.Constant) and keyword.value.value == "cpu":
                    self.fallback_patterns.append({
                        "line": node.lineno,
                        "pattern": "map_location='cpu'",
                        "function": self.current_function,
                        "class": self.current_class,
                        "severity": "LOW",
                        "note": "Legitimate for model deserialization"
                    })
        
        # Detect hot ops: matmul, conv, linear, etc.
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ["matmul", "conv2d", "linear", "relu", "softmax"]:
                self.hot_ops.append({
                    "line": node.lineno,
                    "op": node.func.attr,
                    "function": self.current_function,
                    "class": self.current_class
                })
        
        self.generic_visit(node)


def analyze_file(filepath: Path) -> Dict:
    """Analyze a single Python file for device patterns."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source)
        analyzer = DevicePatternAnalyzer(str(filepath))
        analyzer.visit(tree)
        
        return {
            "file": str(filepath),
            "cpu_hardcoded": analyzer.cpu_hardcoded,
            "device_aware": analyzer.device_aware,
            "fallback_patterns": analyzer.fallback_patterns,
            "hot_ops": analyzer.hot_ops,
            "summary": {
                "cpu_hardcoded_count": len(analyzer.cpu_hardcoded),
                "device_aware_count": len(analyzer.device_aware),
                "fallback_count": len(analyzer.fallback_patterns),
                "hot_op_count": len(analyzer.hot_ops)
            }
        }
    except Exception as e:
        return {
            "file": str(filepath),
            "error": str(e),
            "summary": {"parse_error": True}
        }


def check_sdaa_awareness(filepath: Path) -> Dict:
    """Check if file has SDAA-specific code."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        sdaa_patterns = [
            r'\bsdaa\b',
            r'\bSDAA\b',
            r'torch\.sdaa',
            r'device.*sdaa',
        ]
        
        found = []
        for pattern in sdaa_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                found.append(pattern)
        
        return {
            "file": str(filepath),
            "sdaa_aware": len(found) > 0,
            "patterns_found": found
        }
    except Exception as e:
        return {"file": str(filepath), "error": str(e)}


def main():
    base_path = Path("/srv/tensor/agent/work/m303")
    
    results = {
        "mission": "M-C2-P1",
        "target": "SDAA Card 12",
        "probe_type": "static_analysis",
        "libraries": {}
    }
    
    all_fallback_ops = []
    all_hot_ops = []
    
    for rel_path in COMPUTE_FILES:
        filepath = base_path / rel_path
        if not filepath.exists():
            continue
        
        # Analyze device patterns
        analysis = analyze_file(filepath)
        
        # Check SDAA awareness
        sdaa_check = check_sdaa_awareness(filepath)
        
        # Classify library
        lib_name = filepath.stem
        has_hardcoded_cpu = analysis["summary"].get("cpu_hardcoded_count", 0) > 0
        is_device_aware = analysis["summary"].get("device_aware_count", 0) > 0
        is_sdaa_aware = sdaa_check.get("sdaa_aware", False)
        
        # Determine fallback status
        if has_hardcoded_cpu and not is_sdaa_aware:
            fallback_status = "CPU_HARDCODED"
        elif is_device_aware and not is_sdaa_aware:
            fallback_status = "CUDA_ONLY"
        elif is_sdaa_aware:
            fallback_status = "SDAA_READY"
        else:
            fallback_status = "UNKNOWN"
        
        results["libraries"][lib_name] = {
            "file": rel_path,
            "fallback_status": fallback_status,
            "sdaa_aware": is_sdaa_aware,
            "analysis": analysis,
            "sdaa_check": sdaa_check
        }
        
        # Collect fallback ops
        for op in analysis.get("cpu_hardcoded", []):
            all_fallback_ops.append({
                "library": lib_name,
                "op": op["pattern"],
                "line": op["line"],
                "function": op["function"],
                "severity": op["severity"]
            })
        
        # Collect hot ops
        for op in analysis.get("hot_ops", []):
            all_hot_ops.append({
                "library": lib_name,
                "op": op["op"],
                "line": op["line"],
                "function": op["function"]
            })
    
    results["fallback_ops"] = all_fallback_ops
    results["hot_ops"] = all_hot_ops
    
    # Summary statistics
    results["summary"] = {
        "total_libraries": len(results["libraries"]),
        "cpu_hardcoded_libraries": sum(
            1 for lib in results["libraries"].values()
            if lib["fallback_status"] == "CPU_HARDCODED"
        ),
        "cuda_only_libraries": sum(
            1 for lib in results["libraries"].values()
            if lib["fallback_status"] == "CUDA_ONLY"
        ),
        "sdaa_ready_libraries": sum(
            1 for lib in results["libraries"].values()
            if lib["fallback_status"] == "SDAA_READY"
        ),
        "total_fallback_ops": len(all_fallback_ops),
        "total_hot_ops": len(all_hot_ops)
    }
    
    # Write output
    output_path = base_path / "docs/council/op_fallback_probe.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Probe complete: {output_path}")
    print(f"  Libraries scanned: {results['summary']['total_libraries']}")
    print(f"  CPU-hardcoded: {results['summary']['cpu_hardcoded_libraries']}")
    print(f"  CUDA-only: {results['summary']['cuda_only_libraries']}")
    print(f"  SDAA-ready: {results['summary']['sdaa_ready_libraries']}")
    print(f"  Fallback ops: {results['summary']['total_fallback_ops']}")
    print(f"  Hot ops: {results['summary']['total_hot_ops']}")
    
    return results


if __name__ == "__main__":
    main()
