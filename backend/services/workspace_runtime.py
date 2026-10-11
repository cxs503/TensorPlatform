"""Runtime selection and bounded execution for the personal code workspace."""
from __future__ import annotations

import ast
import importlib.metadata
import json
import math
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from services.sandbox_environment_service import get_runtime_build, list_runtime_builds

ENVIRONMENTS_FILE = Path(
    os.getenv("WORKSPACE_ENVIRONMENTS_FILE", "/app/uploads/algorithm_store_envs.json")
)
RUN_TIMEOUT_SECONDS = int(os.getenv("WORKSPACE_RUN_TIMEOUT_SECONDS", "300"))
MAX_SOURCE_BYTES = int(os.getenv("WORKSPACE_MAX_SOURCE_BYTES", str(5 * 1024 * 1024)))
MAX_WORKSPACE_BYTES = int(os.getenv("WORKSPACE_MAX_BYTES", str(100 * 1024 * 1024)))
MAX_OUTPUT_BYTES = int(os.getenv("WORKSPACE_MAX_OUTPUT_BYTES", str(1024 * 1024)))
MAX_RESULT_BYTES = int(os.getenv("WORKSPACE_MAX_RESULT_BYTES", str(5 * 1024 * 1024)))
MAX_RESULT_SAMPLES = 5000
DATASET_SUFFIXES = {".csv", ".tsv", ".jsonl", ".parquet", ".xlsx", ".xls", ".feather"}
HYPERPARAMETER_SUFFIXES = {".json", ".txt", ".yaml", ".yml", ".cfg", ".conf"}


class WorkspaceRuntimeError(ValueError):
    """A user-visible runtime or source validation error."""


_NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_METRIC_PATTERNS = (
    ("val_loss", r"val[_\s-]*loss|验证损失"),
    ("accuracy", r"accuracy|acc|准确率"),
    ("precision", r"precision|精确率"),
    ("recall", r"recall|召回率"),
    ("f1", r"f1(?:[_\s-]*score)?|F1值"),
    ("r2", r"r(?:2|²)|决定系数"),
    ("rmse", r"rmse|均方根误差"),
    ("mae", r"mae|平均绝对误差"),
    ("mse", r"mse|均方误差"),
    ("loss", r"loss|训练损失"),
)


def _metrics_from_line(line: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for name, label_pattern in _METRIC_PATTERNS:
        match = re.search(
            rf"(?:^|[^\w])(?:{label_pattern})(?:\s*\([^)]*\))?\s*[:=：-]\s*({_NUMBER_PATTERN})",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        value = float(match.group(1))
        if math.isfinite(value):
            values[name] = value
    return values


def analyze_run_output(
    output: str,
    error: str,
    *,
    duration_ms: float,
    returncode: int,
    status: str,
    structured_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract common experiment metrics without requiring a platform SDK."""
    metrics: dict[str, float] = {}
    series: list[dict[str, Any]] = []
    lines = (output or "").splitlines()
    for line in lines:
        line_metrics = _metrics_from_line(line)
        metrics.update(line_metrics)
        epoch_match = re.search(
            r"\bepoch\s*[\[(]?\s*(\d+)\s*(?:/\s*(\d+))?\s*[\])]?",
            line,
            flags=re.IGNORECASE,
        )
        if epoch_match and line_metrics:
            point: dict[str, Any] = {"epoch": int(epoch_match.group(1))}
            if epoch_match.group(2):
                point["total_epochs"] = int(epoch_match.group(2))
            point.update(line_metrics)
            series.append(point)

    visualization: dict[str, Any] = {}
    warnings: list[str] = []
    if structured_result:
        supplied_metrics = structured_result.get("metrics")
        if isinstance(supplied_metrics, dict):
            for name, raw_value in list(supplied_metrics.items())[:100]:
                if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                    continue
                value = float(raw_value)
                if math.isfinite(value):
                    metrics[str(name)[:80]] = value
        supplied_series = structured_result.get("series")
        if isinstance(supplied_series, list):
            safe_series = []
            for raw_point in supplied_series[:2000]:
                if not isinstance(raw_point, dict):
                    continue
                point = {}
                for name, raw_value in list(raw_point.items())[:30]:
                    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                        continue
                    value = float(raw_value)
                    if math.isfinite(value):
                        point[str(name)[:80]] = int(value) if name in {"epoch", "step"} else value
                if point:
                    safe_series.append(point)
            if safe_series:
                series = safe_series
        visualization, visualization_warnings = _prediction_visualization(structured_result)
        warnings.extend(visualization_warnings)

    return {
        "status": status,
        "duration_ms": round(float(duration_ms), 2),
        "returncode": int(returncode),
        "metrics": metrics,
        "series": series[-2000:],
        "visualization": visualization,
        "result_contract": "foundation_result_v1" if structured_result else None,
        "warnings": warnings,
        "stdout_lines": len(lines),
        "stdout_bytes": len((output or "").encode("utf-8")),
        "stderr_bytes": len((error or "").encode("utf-8")),
    }


def _sample_sequence(values: list[Any], maximum: int) -> list[tuple[int, Any]]:
    if len(values) <= maximum:
        return list(enumerate(values))
    step = len(values) / maximum
    sampled = []
    for index in range(maximum):
        position = min(len(values) - 1, int(index * step))
        sampled.append((position, values[position]))
    return sampled


def _histogram(values: list[float], bins: int = 20) -> dict[str, list[Any]]:
    if not values:
        return {"labels": [], "counts": []}
    minimum, maximum = min(values), max(values)
    if minimum == maximum:
        return {"labels": [f"{minimum:.6g}"], "counts": [len(values)]}
    width = (maximum - minimum) / bins
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, int((value - minimum) / width))] += 1
    labels = [f"{minimum + index * width:.4g}~{minimum + (index + 1) * width:.4g}" for index in range(bins)]
    return {"labels": labels, "counts": counts}


def _prediction_visualization(result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    y_true = result.get("y_true")
    y_pred = result.get("y_pred")
    if not isinstance(y_true, list) or not isinstance(y_pred, list):
        return {}, warnings
    usable = min(len(y_true), len(y_pred), MAX_RESULT_SAMPLES)
    if usable <= 0:
        return {}, warnings
    if len(y_true) != len(y_pred):
        warnings.append("y_true 与 y_pred 长度不同，已按较短序列展示")
    def scalar(value: Any) -> Any:
        while isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        return value

    actual = [scalar(value) for value in y_true[:usable]]
    predicted = [scalar(value) for value in y_pred[:usable]]
    task_type = str(result.get("task_type") or "").lower()
    numeric = all(
        not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in actual + predicted
    )
    if task_type == "classification" or not numeric:
        actual_labels = [str(value)[:120] for value in actual]
        predicted_labels = [str(value)[:120] for value in predicted]
        labels = sorted(set(actual_labels) | set(predicted_labels))[:100]
        if not labels:
            return {}, warnings
        positions = {label: index for index, label in enumerate(labels)}
        matrix = [[0 for _ in labels] for _ in labels]
        for actual_label, predicted_label in zip(actual_labels, predicted_labels):
            if actual_label in positions and predicted_label in positions:
                matrix[positions[actual_label]][positions[predicted_label]] += 1
        return {
            "task_type": "classification",
            "sample_count": usable,
            "confusion_matrix": {"labels": labels, "values": matrix},
            "class_distribution": {
                "labels": labels,
                "actual": [actual_labels.count(label) for label in labels],
                "predicted": [predicted_labels.count(label) for label in labels],
            },
        }, warnings

    actual_values = [float(value) for value in actual]
    predicted_values = [float(value) for value in predicted]
    paired_values = list(zip(actual_values, predicted_values))
    trend_sampled = _sample_sequence(paired_values, 300)
    scatter_sampled = _sample_sequence(paired_values, 500)
    residuals = [left - right for left, right in zip(actual_values, predicted_values)]
    visualization = {
        "task_type": "regression",
        "sample_count": usable,
        "prediction_trend": [[index + 1, pair[0], pair[1]] for index, pair in trend_sampled],
        "actual_vs_predicted": [[pair[0], pair[1]] for _, pair in scatter_sampled],
        "residual_histogram": _histogram(residuals),
    }
    importance = result.get("feature_importance")
    if isinstance(importance, dict):
        safe_importance = []
        for name, raw_value in list(importance.items())[:200]:
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                continue
            value = float(raw_value)
            if math.isfinite(value):
                safe_importance.append((str(name)[:120], value))
        safe_importance.sort(key=lambda item: abs(item[1]), reverse=True)
        visualization["feature_importance"] = {
            "labels": [item[0] for item in safe_importance[:50]],
            "values": [item[1] for item in safe_importance[:50]],
        }
    return visualization, warnings


def _read_structured_result(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        if path.stat().st_size > MAX_RESULT_BYTES:
            return None, "结构化结果文件超过5MB，已忽略"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None, "结构化结果必须是JSON对象，已忽略"
        return value, None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "结构化结果文件无法解析，已忽略"


def _configured_environments() -> list[dict[str, Any]]:
    try:
        payload = json.loads(ENVIRONMENTS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _requirements(packages: Any) -> Iterable[str]:
    if isinstance(packages, str):
        yield from (line.strip() for line in packages.splitlines() if line.strip())
        return
    if isinstance(packages, list):
        for package in packages:
            if isinstance(package, str) and package.strip():
                yield package.strip()
            elif isinstance(package, dict) and package.get("name"):
                version = str(package.get("version") or "").strip()
                yield f"{package['name']}=={version}" if version else str(package["name"])


def _version_key(version: str) -> tuple[tuple[int, Any], ...]:
    parts: list[tuple[int, Any]] = []
    for token in re.split(r"[._+\-]", version.lower()):
        for segment in re.findall(r"\d+|[a-z]+", token):
            parts.append((0, int(segment)) if segment.isdigit() else (1, segment))
    while parts and parts[-1] == (0, 0):
        parts.pop()
    return tuple(parts)


def _matches_version(actual: str, specifications: str) -> bool:
    for specification in (item.strip() for item in specifications.split(",") if item.strip()):
        match = re.fullmatch(r"(===|==|!=|>=|<=|>|<|~=)\s*([A-Za-z0-9.*+_-]+)", specification)
        if not match:
            return False
        operator, expected = match.groups()
        comparable_actual = actual if "+" in expected else actual.split("+", 1)[0]
        actual_key = _version_key(comparable_actual)
        if operator in {"==", "==="} and expected.endswith(".*"):
            if not comparable_actual.startswith(expected[:-1]):
                return False
            continue
        expected_key = _version_key(expected)
        comparisons = {
            "==": actual_key == expected_key,
            "===": comparable_actual == expected,
            "!=": actual_key != expected_key,
            ">=": actual_key >= expected_key,
            "<=": actual_key <= expected_key,
            ">": actual_key > expected_key,
            "<": actual_key < expected_key,
            "~=": actual_key >= expected_key and actual_key[:1] == expected_key[:1],
        }
        if not comparisons[operator]:
            return False
    return True


def _package_status(packages: Any) -> tuple[list[dict[str, str]], list[str]]:
    installed: list[dict[str, str]] = []
    problems: list[str] = []
    for raw in _requirements(packages):
        match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(.*?)\s*", raw)
        if not match:
            problems.append(f"依赖格式无效：{raw}")
            continue
        package_name, specifications = match.groups()
        try:
            actual = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"缺少依赖：{package_name}")
            continue
        installed.append({"name": package_name, "version": actual})
        if specifications and not _matches_version(actual, specifications):
            problems.append(f"{package_name} 需要 {specifications}，当前为 {actual}")
    return installed, problems


def _runtime_for(config: dict[str, Any]) -> dict[str, Any]:
    configured_python = str(config.get("python_version") or "").strip()
    actual_python = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_path = str(config.get("python_path") or sys.executable)
    problems: list[str] = []

    if not Path(python_path).is_file() or not os.access(python_path, os.X_OK):
        problems.append(f"解释器不存在：{python_path}")
    if configured_python and not actual_python.startswith(configured_python + ".") and actual_python != configured_python:
        problems.append(f"需要 Python {configured_python}，当前运行时为 {actual_python}")

    installed, package_problems = _package_status(config.get("packages"))
    problems.extend(package_problems)
    cuda = str(config.get("cuda_version") or "").strip()
    if cuda and not (Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists()):
        problems.append(f"需要 CUDA {cuda}，当前节点未发现 GPU")

    return {
        **config,
        "available": not problems,
        "unavailable_reason": "；".join(problems),
        "actual_python_version": actual_python,
        "python_path": python_path,
        "installed_packages": installed,
        "mode": "validated_shared_runtime",
    }


def list_runtimes() -> list[dict[str, Any]]:
    return list_runtime_builds()


def resolve_runtime(environment_id: int | None) -> dict[str, Any]:
    try:
        return get_runtime_build(environment_id)
    except ValueError as exc:
        raise WorkspaceRuntimeError(str(exc)) from exc


def safe_relative_path(raw_path: str) -> Path:
    path = Path(str(raw_path or "").replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkspaceRuntimeError(f"无效的工作区路径：{raw_path}")
    return path


def _string_literals(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and 0 < len(node.value) <= 500
    ]


def inspect_workspace_inputs(
    workspace: Path,
    entry_path: str,
    files: dict[str, Any] | None,
    code: str,
) -> dict[str, Any]:
    """Inspect source and workspace paths without executing user code."""
    entry = safe_relative_path(entry_path)
    overlay_paths = {safe_relative_path(path).as_posix() for path in (files or {})}
    literals = _string_literals(code)

    dataset_references: list[str] = []
    for literal in literals:
        candidate = Path(literal.strip())
        suffix = candidate.suffix.lower()
        if suffix in DATASET_SUFFIXES:
            dataset_references.append(literal)

    dataset_signal = bool(dataset_references) or bool(re.search(
        r"ODF_DATASET_PATH|['\"]--data['\"]|read_(?:csv|table|parquet|excel)|read_json",
        code,
    ))
    def reference_exists(raw: str) -> bool:
        path = Path(raw)
        if path.is_absolute():
            return False
        normalized = path.as_posix().lstrip("./")
        entry_relative = (entry.parent / path).as_posix()
        if normalized in overlay_paths or entry_relative in overlay_paths:
            return True
        return (workspace / normalized).is_file() or (workspace / entry_relative).is_file()

    dataset_found = any(reference_exists(item) for item in dataset_references)

    parameter_references = [
        literal for literal in literals
        if Path(literal.strip()).suffix.lower() in HYPERPARAMETER_SUFFIXES
        and re.search(r"param|config|setting|hyper", Path(literal.strip()).name, re.IGNORECASE)
    ]
    parameter_signal = bool(parameter_references) or bool(re.search(
        r"ODF_HYPERPARAMS_PATH|['\"]--(?:params|hyperparams|config)['\"]",
        code,
    ))
    parameters_found = any(reference_exists(item) for item in parameter_references)

    def detail(kind: str, specified: bool, found: bool, references: list[str]) -> dict[str, Any]:
        if found:
            reason = f"代码引用的{kind}文件已存在"
        elif specified:
            reason = f"代码声明了{kind}输入，但运行工作区中没有找到可用文件"
        else:
            reason = f"代码没有声明{kind}文件来源"
        return {
            "specified": specified,
            "found": found,
            "required": not found,
            "references": references[:10],
            "reason": reason,
        }

    dataset = detail("数据集", dataset_signal, dataset_found, dataset_references)
    parameters = detail("超参数", parameter_signal, parameters_found, parameter_references)
    parameters["required"] = False
    parameters["reason"] = (
        "代码已有可用的参数文件；如不覆盖将继续使用它"
        if parameters_found else
        "可选：上传或手动填写；留空时使用脚本默认参数"
    )
    return {
        "needs_inputs": dataset["required"],
        "dataset": dataset,
        "parameters": parameters,
    }


def _workspace_size(workspace: Path) -> int:
    return sum(
        item.stat().st_size
        for item in workspace.rglob("*")
        if item.is_file() and not item.is_symlink() and ".runs" not in item.parts
    )


def _prepare_run_script(
    src: str,
    target_override: str | None = None,
    feature_override: list[str] | None = None,
) -> str:
    """预处理运行副本：接入平台数据路径和模型产出路径，不改业务字段逻辑。

    ``TARGET``、特征列等属于算法源码的业务语义。只有源码主动读取
    ``ODF_FIELD_ROLES_PATH`` / ``ODF_TARGET_COLUMN`` 时，才使用界面传入的
    字段用途；除非用户明确选择覆盖目标列，否则这里不会用系统猜测值替换已有脚本常量。
    """
    import re
    # 1. 路径常量替换（按文件后缀区分 CSV/parquet）
    # .parquet 后缀 → ODF_DATASET_PARQUET（保留 read_parquet 原样）
    src = re.sub(r'(?m)^(\s*)(DATA_PATH|DATA_FILE|data_path|INPUT_PATH)\s*=\s*Path\(r?["\'].*?\.parquet["\']\)',
                 r"\1\2 = Path(os.environ.get('ODF_DATASET_PARQUET', os.environ.get('ODF_DATASET_PATH', '.')))", src)
    # 其他（.csv 或无后缀）→ ODF_DATASET_PATH
    src = re.sub(r'(?m)^(\s*)(DATA_PATH|DATA_FILE|data_path|INPUT_PATH)\s*=\s*Path\(r?["\'].*?["\']\)',
                 r"\1\2 = Path(os.environ.get('ODF_DATASET_PATH', '.'))", src)
    src = re.sub(r'(?m)^(\s*)(MODEL_PATH|MODEL_SAVE_PATH|model_path|OUTPUT_PATH|SAVE_PATH)\s*=\s*Path\(r?["\'].*?["\']\)',
                 r"\1\2 = Path(os.environ.get('ODF_MODEL_OUT_PATH', '/tmp/foundation_run_model.pkl'))", src)
    if target_override:
        src = re.sub(
            r'''(?m)^(\s*)(TARGET|target|TARGET_COLUMN|target_column)\s*=\s*['"]([^'"]*)['"]''',
            lambda match: f"{match.group(1)}{match.group(2)} = {target_override!r}",
            src,
        )
    src = re.sub(r'(?m)^\s*(MODEL_SAVE_PATH|MODEL_PATH|SAVE_PATH|OUTPUT_PATH)\.parent\.mkdir[^\n]*\n', '', src)

    if feature_override:
        feature_literal = repr(list(feature_override))
        feature_names = (
            "RAW_FEATURE_COLS|FEATURE_COLUMNS|FEATURE_COLS|INPUT_COLUMNS|X_COLS|"
            "feature_columns|feature_cols"
        )
        src = re.sub(
            rf"(?m)^(\s*)({feature_names})\s*=\s*(?:\[[^\n]*\]|\([^\n]*\))\s*$",
            lambda match: f"{match.group(1)}{match.group(2)} = {feature_literal}",
            src,
        )

    # 2. 假数据替换：np.random.rand/randn 造数据 → 读 ODF_DATASET_PATH
    src = re.sub(
        r'(?m)^(\s*)(sample_features|sample_data|df_demo|fake_data|demo_data)\s*=\s*np\.random\.(rand|randn)\(.*?\).*$',
        r"\1\2 = __import__('pandas').read_csv(os.environ['ODF_DATASET_PATH']) if os.environ.get('ODF_DATASET_PATH') else np.random.\3(200, 8)",
        src)
    src = re.sub(
        r'(?m)^(\s*)(sample_target|fake_target)\s*=\s*.*$',
        r"\1\2 = __import__('pandas').read_csv(os.environ['ODF_DATASET_PATH']).iloc[:, -1].values.reshape(-1, 1) if os.environ.get('ODF_DATASET_PATH') else \2",
        src)
    src = re.sub(
        r'(?m)^(\s*)df_demo\s*=\s*pd\.DataFrame\(np\.hstack\(\[.*?\]\)\).*$',
        r"\1df_demo = pd.read_csv(os.environ['ODF_DATASET_PATH']) if os.environ.get('ODF_DATASET_PATH') else df_demo",
        src)

    # 3. 不再替换 read_parquet → read_csv（平台同时导出 parquet，各读各的）

    # 4. 始终注入 ODF_MODEL_OUT_PATH（默认路径）+ import os
    if 'ODF_MODEL_OUT_PATH' not in src:
        src = "import os\nos.environ.setdefault('ODF_MODEL_OUT_PATH', '/tmp/foundation_run_model.pkl')\n" + src

    if not re.search(r'^import os\b', src, re.MULTILINE):
        src = 'import os\n' + src
    return src


def _limit_process() -> None:
    # 不限制 CPU 时间——用户可以手动停止（前端"停止运行"按钮）
    # 保留内存、文件和进程数限制（防 OOM 和 fork 炸弹）。
    # Python 科学计算库会启动少量内部线程（例如 Matplotlib 字体缓存）；256 会让
    # 正常训练在导入阶段失败，1024 仍能限制异常 fork。
    resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (500 * 1024**2, 500 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (1024, 1024))


def _read_limited(path: Path) -> tuple[str, bool]:
    with path.open("rb") as stream:
        data = stream.read(MAX_OUTPUT_BYTES + 1)
    truncated = len(data) > MAX_OUTPUT_BYTES
    return data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), truncated


def run_workspace_snapshot(
    workspace: Path,
    entry_path: str,
    files: dict[str, Any] | None,
    code: str,
    environment_id: int | None,
    input_files: dict[str, dict[str, Any]] | None = None,
    stdout_dir: str | None = None,
) -> dict[str, Any]:
    """Run an in-memory edit snapshot without changing persisted workspace files."""
    run_started = time.monotonic()
    runtime = resolve_runtime(environment_id)
    entry = safe_relative_path(entry_path)
    if entry.suffix.lower() != ".py":
        raise WorkspaceRuntimeError("当前仅支持运行 Python (.py) 文件")
    if _workspace_size(workspace) > MAX_WORKSPACE_BYTES:
        raise WorkspaceRuntimeError("工作区超过 100 MB，无法创建运行快照")

    overlay = dict(files or {})
    overlay[str(entry)] = code
    total_overlay = 0
    normalized: dict[Path, str] = {}
    for raw_path, raw_content in overlay.items():
        relative = safe_relative_path(raw_path)
        content = str(raw_content if raw_content is not None else "")
        size = len(content.encode("utf-8"))
        if size > MAX_SOURCE_BYTES:
            raise WorkspaceRuntimeError(f"文件 {raw_path} 超过 5 MB")
        total_overlay += size
        normalized[relative] = content
    if total_overlay > MAX_WORKSPACE_BYTES:
        raise WorkspaceRuntimeError("本次运行代码总量超过 100 MB")

    input_size = sum(len(bytes(item.get("content") or b"")) for item in (input_files or {}).values())
    if input_size > MAX_WORKSPACE_BYTES:
        raise WorkspaceRuntimeError("本次运行输入文件总量超过 100 MB")

    with tempfile.TemporaryDirectory(prefix="foundation-workspace-run-") as tmp:
        snapshot = Path(tmp) / "workspace"
        def ignore_unsafe(directory: str, names: list[str]) -> set[str]:
            ignored = {".runs"} if Path(directory).resolve() == workspace.resolve() else set()
            ignored.update(name for name in names if (Path(directory) / name).is_symlink())
            return ignored

        shutil.copytree(
            workspace,
            snapshot,
            ignore=ignore_unsafe,
            symlinks=False,
        )
        for relative, content in normalized.items():
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        attached_inputs: dict[str, Path] = {}
        for role, payload in (input_files or {}).items():
            if role not in {"dataset", "hyperparameters", "field_roles"}:
                continue
            safe_name = Path(str(payload.get("filename") or f"{role}.txt")).name
            target = snapshot / ".foundation_inputs" / f"{role}_{safe_name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes(payload.get("content") or b""))
            attached_inputs[role] = target

        target_override = None
        feature_override: list[str] = []
        field_roles_path = attached_inputs.get("field_roles")
        if field_roles_path:
            try:
                requested_roles = json.loads(field_roles_path.read_text(encoding="utf-8"))
                if requested_roles.get("apply_target_override") and requested_roles.get("target_column"):
                    target_override = str(requested_roles["target_column"])
                if requested_roles.get("apply_feature_override") and isinstance(
                    requested_roles.get("feature_columns"), list
                ):
                    feature_override = [
                        str(column) for column in requested_roles["feature_columns"]
                        if isinstance(column, str) and column.strip()
                    ]
            except (OSError, ValueError, TypeError):
                pass

        entry_file = snapshot / entry
        if not entry_file.is_file():
            raise WorkspaceRuntimeError("入口文件不存在")
        # 仅在用户明确选择推荐/自定义目标时，临时替换脚本中的常见目标列常量。
        try:
            _orig_code = entry_file.read_text(encoding="utf-8")
            _prepared = _prepare_run_script(
                _orig_code,
                target_override=target_override,
                feature_override=feature_override,
            )
            if _prepared != _orig_code:
                entry_file = entry_file.parent / (entry_file.stem + "_prepared.py")
                entry_file.write_text(_prepared, encoding="utf-8")
        except Exception:
            pass
        out_dir = Path(stdout_dir) if stdout_dir else Path(tmp)
        stdout_path = out_dir / "stdout.log"
        stderr_path = out_dir / "stderr.log"
        child_env = {
            "PATH": str(Path(runtime["python_path"]).parent) + ":/usr/local/bin:/usr/bin:/bin",
            "HOME": str(snapshot),
            "PYTHONPATH": str(snapshot),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            # 终端日志由脚本原样输出，不会经过前端时间格式化；明确使用平台时区。
            "TZ": os.getenv("PLATFORM_TIMEZONE", "Asia/Shanghai"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "MPLCONFIGDIR": str(snapshot / ".matplotlib"),
            # 代码编辑器没有图形桌面，强制使用无界面后端，避免绘图初始化 GUI 线程。
            "MPLBACKEND": "Agg",
            # Numerical libraries otherwise try to create one worker per host CPU,
            # conflicting with the sandbox process limit and making pandas/numpy fail to import.
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "MALLOC_CONF": "background_thread:false,narenas:1",
        }
        result_path = snapshot / ".foundation_outputs" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        child_env["ODF_RESULT_PATH"] = str(result_path)
        # Each run owns a persistent artifact path. A shared /tmp filename can
        # otherwise make an unrelated stale model look like this run's output.
        model_output_path = out_dir / "model.pkl"
        model_output_path.unlink(missing_ok=True)
        child_env["ODF_MODEL_OUT_PATH"] = str(model_output_path)
        dataset_path = attached_inputs.get("dataset")
        hyperparameters_path = attached_inputs.get("hyperparameters")
        field_roles_path = attached_inputs.get("field_roles")
        if dataset_path:
            child_env["ODF_DATASET_PATH"] = str(dataset_path)
            # 同时导出 parquet 版本（给 read_parquet 的脚本用）
            try:
                import pandas as _pd
                parquet_path = dataset_path.parent / (dataset_path.stem + ".parquet")
                _df = _pd.read_csv(dataset_path)
                _df.to_parquet(parquet_path, index=False)
                child_env["ODF_DATASET_PARQUET"] = str(parquet_path)
            except Exception:
                pass  # parquet 导出失败不影响 CSV 可用
        if hyperparameters_path:
            child_env["ODF_HYPERPARAMS_PATH"] = str(hyperparameters_path)
        if field_roles_path:
            child_env["ODF_FIELD_ROLES_PATH"] = str(field_roles_path)
            try:
                field_roles = json.loads(field_roles_path.read_text(encoding="utf-8"))
                target_column = field_roles.get("target_column")
                if target_column:
                    child_env["ODF_TARGET_COLUMN"] = str(target_column)
                child_env["ODF_FEATURE_COLUMNS"] = json.dumps(
                    field_roles.get("feature_columns") or [], ensure_ascii=False
                )
                if field_roles.get("time_column"):
                    child_env["ODF_TIME_COLUMN"] = str(field_roles["time_column"])
            except (OSError, ValueError, TypeError):
                pass

        command = [runtime["python_path"], str(entry_file)]
        if dataset_path and re.search(r"['\"]--data['\"]", code):
            command.extend(["--data", str(dataset_path)])
        if hyperparameters_path:
            parameter_argument = re.search(
                r"['\"](--(?:params|hyperparams|config))['\"]", code
            )
            if parameter_argument:
                command.extend([parameter_argument.group(1), str(hyperparameters_path)])
        timed_out = False
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                command,
                cwd=str(snapshot),
                env=child_env,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
                preexec_fn=_limit_process,
            )
            # 不设超时——用户通过前端"停止运行"按钮手动终止
            # 轮询 .stop 标记文件 + 进程状态（每 1 秒检查一次）
            import time as _time
            _stop_file = out_dir / ".stop"
            while True:
                returncode = process.poll()
                if returncode is not None:
                    break
                if _stop_file.exists():
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    _time.sleep(1)
                    if process.poll() is None:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    returncode = process.wait(timeout=5)
                    output_note = "用户手动停止运行"
                    break
                _time.sleep(1)
            else:
                returncode = process.returncode

            output, stdout_truncated = _read_limited(stdout_path)
        error, stderr_truncated = _read_limited(stderr_path)
        if stdout_truncated:
            output += "\n[输出过长，已截断]"
        if stderr_truncated:
            error += "\n[错误输出过长，已截断]"
        if _stop_file.exists():
            error = (error + "\n用户手动停止运行").strip()

        status = "stopped" if _stop_file.exists() else ("success" if returncode == 0 else "error")
        duration_ms = (time.monotonic() - run_started) * 1000
        structured_result, result_warning = _read_structured_result(result_path)
        analysis = analyze_run_output(
            output,
            error,
            duration_ms=duration_ms,
            returncode=returncode,
            status=status,
            structured_result=structured_result,
        )
        if result_warning:
            analysis["warnings"].append(result_warning)
        return {
            "output": output,
            "error": error,
            "returncode": returncode,
            "status": status,
            "duration_ms": analysis["duration_ms"],
            "analysis": analysis,
            "environment": {
                "id": runtime.get("id"),
                "name": runtime.get("name"),
                "python_version": runtime.get("actual_python_version"),
                "mode": runtime.get("mode"),
                "base_environment_id": runtime.get("base_environment_id"),
                "dependency_set_id": runtime.get("dependency_set_id"),
                "runtime_digest": runtime.get("runtime_digest"),
                "compute_type": runtime.get("compute_type"),
                "cuda_version": runtime.get("cuda_version"),
            },
            "inputs": {
                role: {"filename": path.name, "size_bytes": path.stat().st_size}
                for role, path in attached_inputs.items()
            },
        }
