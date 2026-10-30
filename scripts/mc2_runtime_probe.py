#!/usr/bin/env python3
"""
M-C2 P1: Runtime SDAA Op Fallback Probe
Actually runs ops on SDAA card and detects CPU fallback warnings.
"""

import sys
import json
import warnings
import traceback
from pathlib import Path
from contextlib import contextmanager

# Capture warnings
captured_warnings = []

@contextmanager
def capture_warnings():
    """Capture all warnings during execution."""
    global captured_warnings
    captured_warnings = []
    
    def warning_handler(message, category, filename, lineno, file=None, line=None):
        captured_warnings.append({
            "message": str(message),
            "category": category.__name__,
            "filename": filename,
            "lineno": lineno
        })
    
    old_showwarning = warnings.showwarning
    warnings.showwarning = warning_handler
    warnings.simplefilter("always")
    
    try:
        yield
    finally:
        warnings.showwarning = old_showwarning


def test_basic_ops_on_sdaa():
    """Test basic tensor ops on SDAA."""
    import torch
    import torch_sdaa
    
    device = torch.device("sdaa:0")
    results = {
        "device": str(device),
        "device_name": torch.sdaa.get_device_name(0),
        "ops_tested": [],
        "fallback_detected": False,
        "warnings": []
    }
    
    ops = [
        ("matmul", lambda: torch.randn(128, 256, device=device) @ torch.randn(256, 64, device=device)),
        ("linear", lambda: torch.nn.functional.linear(torch.randn(32, 128, device=device), torch.randn(64, 128, device=device))),
        ("conv2d", lambda: torch.nn.functional.conv2d(torch.randn(1, 3, 32, 32, device=device), torch.randn(16, 3, 3, 3, device=device))),
        ("relu", lambda: torch.relu(torch.randn(64, 128, device=device))),
        ("softmax", lambda: torch.softmax(torch.randn(32, 10, device=device), dim=1)),
        ("batch_norm", lambda: torch.nn.functional.batch_norm(torch.randn(8, 16, 32, 32, device=device), torch.randn(16, device=device), torch.randn(16, device=device))),
    ]
    
    with capture_warnings():
        for op_name, op_func in ops:
            try:
                # Clear cache before each op
                torch.sdaa.empty_cache()
                
                # Run op
                result = op_func()
                
                # Force sync to catch async errors
                torch.sdaa.synchronize()
                
                # Check result device
                result_device = str(result.device)
                
                op_result = {
                    "op": op_name,
                    "status": "SUCCESS",
                    "result_device": result_device,
                    "on_sdaa": "sdaa" in result_device,
                    "warnings": []
                }
                
                # Check for fallback warnings
                for warn in captured_warnings:
                    if "cpu" in warn["message"].lower() or "fallback" in warn["message"].lower():
                        op_result["warnings"].append(warn)
                        op_result["status"] = "FALLBACK_DETECTED"
                        results["fallback_detected"] = True
                
                results["ops_tested"].append(op_result)
                captured_warnings.clear()
                
            except Exception as e:
                results["ops_tested"].append({
                    "op": op_name,
                    "status": "ERROR",
                    "error": str(e),
                    "traceback": traceback.format_exc()
                })
    
    results["warnings"] = captured_warnings
    return results


def test_gnn_on_sdaa():
    """Test GNN (GCN) forward pass on SDAA - adapted from experiment_algorithms_gnn_gan.py"""
    import torch
    import torch_sdaa
    
    device = torch.device("sdaa:0")
    results = {
        "test": "GNN_GCN_Forward",
        "device": str(device),
        "status": "UNKNOWN",
        "fallback_detected": False,
        "iterations_completed": 0
    }
    
    try:
        # Create small GNN fixture data on SDAA
        node_count = 10
        feature_dim = 3
        hidden_dim = 16
        class_count = 2
        
        features = torch.randn(node_count, feature_dim, device=device)
        labels = torch.randint(0, class_count, (node_count,), device=device)
        
        # Create adjacency matrix on SDAA
        adjacency = torch.zeros((node_count, node_count), dtype=torch.float32, device=device)
        edges = [(0,1), (1,2), (2,3), (3,4), (4,0), (5,6), (6,7), (7,8), (8,9), (9,5), (4,5)]
        for i, j in edges:
            adjacency[i, j] = 1.0
            adjacency[j, i] = 1.0
        adjacency.fill_diagonal_(1.0)
        
        # Normalize adjacency
        degree = adjacency.sum(dim=1).clamp_min(1.0)
        inv_sqrt = degree.pow(-0.5)
        norm_adj = inv_sqrt[:, None] * adjacency * inv_sqrt[None, :]
        
        # Build simple GCN
        class GCN(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.input_layer = torch.nn.Linear(feature_dim, hidden_dim)
                self.output_layer = torch.nn.Linear(hidden_dim, class_count)
            
            def forward(self, x, adj):
                h = adj @ x
                h = torch.relu(self.input_layer(h))
                h = adj @ h
                return self.output_layer(h)
        
        model = GCN().to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
        criterion = torch.nn.CrossEntropyLoss()
        
        # Train for 3 iterations
        with capture_warnings():
            for epoch in range(3):
                optimizer.zero_grad()
                output = model(features, norm_adj)
                loss = criterion(output, labels)
                loss.backward()
                optimizer.step()
                
                # Check all tensors are on SDAA
                for param in model.parameters():
                    if "sdaa" not in str(param.device):
                        results["fallback_detected"] = True
                        results["status"] = "FALLBACK_DETECTED"
                        break
                
                torch.sdaa.synchronize()
                results["iterations_completed"] += 1
            
            # Check for fallback warnings
            for warn in captured_warnings:
                if "cpu" in warn["message"].lower() or "fallback" in warn["message"].lower():
                    results["fallback_detected"] = True
                    results["status"] = "FALLBACK_DETECTED"
        
        if results["iterations_completed"] == 3 and not results["fallback_detected"]:
            results["status"] = "SUCCESS_NO_FALLBACK"
        
    except Exception as e:
        results["status"] = "ERROR"
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
    
    return results


def test_cv_training_step_on_sdaa():
    """Test CV training step on SDAA - adapted from cv_training.py"""
    import torch
    import torch_sdaa
    
    device = torch.device("sdaa:0")
    results = {
        "test": "CV_Training_Step",
        "device": str(device),
        "status": "UNKNOWN",
        "fallback_detected": False,
        "iterations_completed": 0
    }
    
    try:
        # Simple CNN
        class SimpleCNN(torch.nn.Module):
            def __init__(self, num_classes=10):
                super().__init__()
                self.conv1 = torch.nn.Conv2d(3, 16, 3, padding=1)
                self.conv2 = torch.nn.Conv2d(16, 32, 3, padding=1)
                self.fc = torch.nn.Linear(32 * 8 * 8, num_classes)
                self.pool = torch.nn.MaxPool2d(2, 2)
            
            def forward(self, x):
                x = self.pool(torch.relu(self.conv1(x)))
                x = self.pool(torch.relu(self.conv2(x)))
                x = x.view(-1, 32 * 8 * 8)
                x = self.fc(x)
                return x
        
        model = SimpleCNN(num_classes=10).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
        criterion = torch.nn.CrossEntropyLoss()
        
        # Dummy data
        images = torch.randn(4, 3, 32, 32, device=device)
        labels = torch.randint(0, 10, (4,), device=device)
        
        # Train for 3 iterations
        with capture_warnings():
            for epoch in range(3):
                optimizer.zero_grad()
                outputs = model(images)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()
                
                # Check all tensors are on SDAA
                for param in model.parameters():
                    if "sdaa" not in str(param.device):
                        results["fallback_detected"] = True
                        results["status"] = "FALLBACK_DETECTED"
                        break
                
                torch.sdaa.synchronize()
                results["iterations_completed"] += 1
            
            # Check for fallback warnings
            for warn in captured_warnings:
                if "cpu" in warn["message"].lower() or "fallback" in warn["message"].lower():
                    results["fallback_detected"] = True
                    results["status"] = "FALLBACK_DETECTED"
        
        if results["iterations_completed"] == 3 and not results["fallback_detected"]:
            results["status"] = "SUCCESS_NO_FALLBACK"
        
    except Exception as e:
        results["status"] = "ERROR"
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
    
    return results


def main():
    print("=" * 60)
    print("M-C2 P1: Runtime SDAA Op Fallback Probe")
    print("=" * 60)
    
    results = {
        "mission": "M-C2-P1",
        "target": "SDAA Card 12 (/dev/tcaicard2)",
        "probe_type": "runtime",
        "tests": {}
    }
    
    # Test 1: Basic ops
    print("\n[Test 1] Basic tensor ops on SDAA...")
    try:
        results["tests"]["basic_ops"] = test_basic_ops_on_sdaa()
        print(f"  ✓ {len(results['tests']['basic_ops']['ops_tested'])} ops tested")
        print(f"  Fallback detected: {results['tests']['basic_ops']['fallback_detected']}")
    except Exception as e:
        results["tests"]["basic_ops"] = {"status": "ERROR", "error": str(e)}
        print(f"  ✗ Error: {e}")
    
    # Test 2: GNN forward pass
    print("\n[Test 2] GNN (GCN) training on SDAA...")
    try:
        results["tests"]["gnn_training"] = test_gnn_on_sdaa()
        print(f"  ✓ Iterations: {results['tests']['gnn_training']['iterations_completed']}")
        print(f"  Status: {results['tests']['gnn_training']['status']}")
    except Exception as e:
        results["tests"]["gnn_training"] = {"status": "ERROR", "error": str(e)}
        print(f"  ✗ Error: {e}")
    
    # Test 3: CV training step
    print("\n[Test 3] CV (CNN) training on SDAA...")
    try:
        results["tests"]["cv_training"] = test_cv_training_step_on_sdaa()
        print(f"  ✓ Iterations: {results['tests']['cv_training']['iterations_completed']}")
        print(f"  Status: {results['tests']['cv_training']['status']}")
    except Exception as e:
        results["tests"]["cv_training"] = {"status": "ERROR", "error": str(e)}
        print(f"  ✗ Error: {e}")
    
    # Summary
    results["summary"] = {
        "total_tests": len(results["tests"]),
        "successful_tests": sum(
            1 for t in results["tests"].values()
            if t.get("status") == "SUCCESS_NO_FALLBACK"
        ),
        "fallback_tests": sum(
            1 for t in results["tests"].values()
            if t.get("fallback_detected", False)
        ),
        "error_tests": sum(
            1 for t in results["tests"].values()
            if t.get("status") == "ERROR"
        )
    }
    
    # Write output
    output_path = Path("/srv/tensor/agent/work/m303/docs/council/op_fallback_probe_runtime.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print("\n" + "=" * 60)
    print(f"✓ Runtime probe complete: {output_path}")
    print(f"  Tests run: {results['summary']['total_tests']}")
    print(f"  Successful (no fallback): {results['summary']['successful_tests']}")
    print(f"  Fallback detected: {results['summary']['fallback_tests']}")
    print(f"  Errors: {results['summary']['error_tests']}")
    print("=" * 60)
    
    return results


if __name__ == "__main__":
    main()
