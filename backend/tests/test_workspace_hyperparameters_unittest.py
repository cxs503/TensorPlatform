import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.workspace_runtime import inspect_workspace_inputs, run_workspace_snapshot  # noqa: E402


class WorkspaceHyperparameterTests(unittest.TestCase):
    def runtime(self):
        return {
            "id": 1,
            "name": "test",
            "python_path": sys.executable,
            "actual_python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "available": True,
            "mode": "managed_python",
        }

    def test_preflight_keeps_hyperparameters_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            code = "import os\nprint(os.getenv('ODF_HYPERPARAMS_PATH'))\n"
            result = inspect_workspace_inputs(workspace, "main.py", {"main.py": code}, code)
        self.assertFalse(result["parameters"]["required"])
        self.assertIn("默认参数", result["parameters"]["reason"])

    def test_run_injects_file_through_env_and_declared_argument(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "services.workspace_runtime.get_runtime_build", return_value=self.runtime()
        ):
            workspace = Path(directory)
            code = """import argparse, json, os
parser = argparse.ArgumentParser()
parser.add_argument('--params')
args = parser.parse_args()
print(os.getenv('ODF_HYPERPARAMS_PATH') == args.params)
print(json.load(open(args.params))['epochs'])
"""
            result = run_workspace_snapshot(
                workspace=workspace,
                entry_path="main.py",
                files={"main.py": code},
                code=code,
                environment_id=1,
                input_files={
                    "hyperparameters": {
                        "filename": "params.json",
                        "content": b'{"epochs": 12}',
                    }
                },
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"].strip().splitlines(), ["True", "12"])
        self.assertGreater(result["inputs"]["hyperparameters"]["size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
