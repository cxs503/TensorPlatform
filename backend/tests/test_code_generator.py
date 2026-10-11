import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.code_generator import CodeGenerateRequest, code_generator_health, generate_code
from services.local_code_generator import (
    SUPPORTED_FRAMEWORKS,
    analyse_code,
    detect_generated_code_metadata,
    generate_local_code,
)


class CodeGeneratorFallbackTests(unittest.TestCase):
    def test_every_framework_generates_compilable_python(self):
        for framework in SUPPORTED_FRAMEWORKS:
            with self.subTest(framework=framework):
                code = generate_local_code(
                    requirement='predict temperature """; raise RuntimeError()',
                    task="regression",
                    framework=framework,
                    algorithm="random_forest",
                )
                compile(code, f"generated_{framework}.py", "exec")
                self.assertIn("def main", code)

    def test_sklearn_template_runs_without_external_dataset(self):
        code = generate_local_code(
            requirement="predict furnace temperature",
            task="regression",
            framework="sklearn",
            algorithm="random_forest",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "generated_model.py"
            model = root / "model.joblib"
            source.write_text(code, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(source), "--output", str(model), "--estimators", "5"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            result = json.loads(completed.stdout)
            self.assertTrue(model.is_file())
            self.assertIn("r2", result["metrics"])

    def test_sklearn_mlp_selection_is_not_random_forest(self):
        code = generate_local_code(
            requirement="MLP furnace temperature predictor",
            task="regression",
            framework="sklearn",
            algorithm="mlp",
        )
        self.assertIn("MLPRegressor", code)
        self.assertNotIn("RandomForestRegressor", code)
        compile(code, "generated_mlp.py", "exec")

    def test_framework_pins_matching_algorithm(self):
        xgboost_code = generate_local_code(
            requirement="boosted model",
            task="regression",
            framework="xgboost",
            algorithm="random_forest",
        )
        self.assertIn("XGBRegressor", xgboost_code)
        self.assertNotIn("RandomForestRegressor", xgboost_code)

    def test_offline_health_is_ready_and_generate_uses_local_mode(self):
        unavailable = SimpleNamespace(provider=None)
        with patch("api.code_generator.get_llm_service", return_value=unavailable):
            health = asyncio.run(code_generator_health())
            result = asyncio.run(
                generate_code(
                    CodeGenerateRequest(
                        requirement="classification baseline",
                        task="classification",
                        framework="sklearn",
                        algorithm="random_forest",
                    )
                )
            )
        self.assertEqual(health["status"], "ready")
        self.assertEqual(health["mode"], "local_template")
        self.assertEqual(result["mode"], "local_template")
        compile(result["code"], "generated.py", "exec")

    def test_llm_receives_structured_selection_and_fences_are_removed(self):
        class FakeLLM:
            provider = "zhipu"

            def __init__(self):
                self.requirement = ""

            def generate_code(self, requirement, language, model=None):
                self.requirement = requirement
                return "```python\nprint(\"ok\")\n```"

        llm = FakeLLM()
        with patch("api.code_generator.get_llm_service", return_value=llm):
            result = asyncio.run(generate_code(CodeGenerateRequest(
                requirement="predict temperature",
                task="regression",
                framework="sklearn",
                algorithm="mlp",
            )))
        self.assertEqual(result["mode"], "llm")
        self.assertIn("代码框架必须为：sklearn", llm.requirement)
        self.assertIn("算法实现必须为：mlp", llm.requirement)
        self.assertEqual(result["code"], 'print("ok")\n')

    def test_analysis_reports_python_structure(self):
        source = "import math\n\ndef main():\n    return math.sqrt(4)\n"
        details = analyse_code(source)
        self.assertEqual(details["functions"], ["main"])

    def test_detects_generated_mlp_metadata(self):
        source = """from sklearn.neural_network import MLPRegressor\n\ndef train_model():\n    return MLPRegressor()\n"""
        result = detect_generated_code_metadata(source, "预测连续温度")
        self.assertEqual(result["task_type"], "regression")
        self.assertEqual(result["framework"], "sklearn")
        self.assertEqual(result["algorithm_type"], "mlp")
        self.assertTrue(result["training_adapter_compatible"])

    def test_marks_custom_algorithm_as_source_only(self):
        source = """import torch\nfrom torch import nn\n\nclass Net(nn.Module):\n    def __init__(self):\n        super().__init__(); self.layer = nn.Conv1d(1, 4, 3)\n"""
        result = detect_generated_code_metadata(source, "时序预测")
        self.assertEqual(result["algorithm_type"], "custom")
        self.assertFalse(result["training_adapter_compatible"])


if __name__ == "__main__":
    unittest.main()
