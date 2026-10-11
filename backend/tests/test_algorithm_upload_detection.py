import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.algorithm_store import _infer_python_source_identity  # noqa: E402


def test_detects_pytorch_mlp_source():
    result = _infer_python_source_identity(
        "import torch\nimport torch.nn as nn\nmodel = nn.Sequential(nn.Linear(2, 8))\n"
    )
    assert result["framework"] == "pytorch"
    assert result["algorithm_type"] == "mlp"


def test_detects_sklearn_random_forest_source():
    result = _infer_python_source_identity(
        "from sklearn.ensemble import RandomForestRegressor\n"
        "model = RandomForestRegressor(n_estimators=10)\n"
    )
    assert result["framework"] == "sklearn"
    assert result["algorithm_type"] == "random_forest"


def test_unknown_python_source_remains_custom():
    result = _infer_python_source_identity("import pandas as pd\ndef train(data): return data\n")
    assert result["framework"] == "python"
    assert result["algorithm_type"] == "custom_python"
