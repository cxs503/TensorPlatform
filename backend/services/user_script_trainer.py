"""UserScriptTrainer：真正执行用户上传的训练脚本（复用 workspace_runtime 的沙箱模式）。

与内置 SklearnTrainer/PyTorchTrainer 平行，subprocess 跑用户 .py：
  - rlimit 资源限制 + env 白名单净化 + 进程组超时 kill（照搬 workspace_runtime）
  - 平台把数据集导出成 parquet，用环境变量 ODF_* 喂给脚本
  - 脚本读 env 训练，把模型存到 ODF_MODEL_OUT_PATH、指标写到 ODF_RESULT_PATH
  - 平台读回模型 pkl + 指标，注册到 models/model_versions + 血缘

用户脚本需遵守的接口约定（同事改脚本时照此）：
  ODF_DATASET_PATH     训练数据 parquet 路径
  ODF_TARGET_COLUMN    目标列名
  ODF_FEATURE_COLUMNS  特征列 JSON（可选）
  ODF_HYPERPARAMS_PATH 本次训练的有效超参数 JSON
  ODF_MODEL_OUT_PATH   模型输出路径（脚本 joblib.dump 到这）
  ODF_RESULT_PATH      指标输出路径（脚本写 {"metrics":{"mae":..,"r2":..}}）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import signal
import subprocess
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from database.config import SessionLocal
from services.sandbox_model_artifact import inspect_sandbox_artifact

# 上传源码可能包含特征工程和超参数搜索，默认允许运行30分钟。
RUN_TIMEOUT_SECONDS = int(os.getenv("USER_SCRIPT_TRAINING_TIMEOUT_SECONDS", "1800"))


def _limit_process() -> None:
    """子进程资源限制（照搬 workspace_runtime._limit_process，放宽训练所需的内存/文件）。

    注意: RLIMIT_AS 限的是虚拟地址空间(VAS)。pandas+sklearn/lightgbm/xgboost 全家桶的 VAS
    很容易逼近 4GB, 届时新建线程连 8MB 栈都 mmap 不进来, 会误报
    "RuntimeError: can't start new thread"(实为地址空间耗尽, 非线程数限制)。
    故与 celery 容器内存上限(6G)对齐放宽到 6GB, NPROC 同步放宽。
    """
    resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT_SECONDS + 30, RUN_TIMEOUT_SECONDS + 60))
    resource.setrlimit(resource.RLIMIT_AS, (6 * 1024**3, 6 * 1024**3))           # 6GB
    resource.setrlimit(resource.RLIMIT_FSIZE, (500 * 1024**2, 500 * 1024**2))     # 500MB（模型可能较大）
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (1024, 1024))


def _prepare_script(src: str) -> str:
    # 预处理用户脚本：把常见路径/目标硬编码自动替换成读平台 env（配置优先于代码）。
    #
    # 用户原样上传脚本（含 Windows 路径、写死的 TARGET、np.random 假数据），平台跑之前自动改写：
    #   DATA_PATH / INPUT_PATH / DATA_FILE_PATH      → ODF_DATASET_PATH
    #   MODEL_PATH / MODEL_SAVE_PATH / OUTPUT_PATH   → ODF_MODEL_OUT_PATH
    #   MODEL_SAVE_DIR / OUTPUT_DIR / ARTIFACT_DIR   → ODF_MODEL_OUT_PATH 的父目录
    #   TARGET / target / TARGET_COLUMN / Y_COL ...  → ODF_TARGET_COLUMN（保留原值兜底）
    #   np.random.rand/randn 造数据                  → 读 ODF_DATASET_PATH
    # 这样用户不用手改脚本就能在平台跑。
    import re

    data_file_names = (
        'DATA_PATH', 'DATA_FILE', 'data_path', 'data_file',
        'INPUT_PATH', 'input_path', 'DATA_FILE_PATH', 'input_file', 'INPUT_FILE',
        'RAW_DATA_PATH', 'raw_data_path', 'SOURCE_PATH', 'source_path',
    )
    model_file_names = (
        'MODEL_PATH', 'MODEL_SAVE_PATH', 'model_path', 'model_save_path',
        'OUTPUT_PATH', 'output_path', 'SAVE_PATH', 'save_path',
        'MODEL_FILE', 'model_file', 'ARTIFACT_PATH', 'artifact_path',
        'PREDICTION_PATH', 'prediction_path', 'RESULT_PATH', 'result_path',
    )
    model_dir_names = (
        'MODEL_SAVE_DIR', 'model_save_dir', 'OUTPUT_DIR', 'output_dir',
        'SAVE_DIR', 'save_dir', 'ARTIFACT_DIR', 'artifact_dir',
        'RESULT_DIR', 'result_dir', 'EXPORT_DIR', 'export_dir',
    )

    def _rewrite_assignment(pattern_names: tuple[str, ...], replacement: str, text: str) -> str:
        names = '|'.join(re.escape(name) for name in pattern_names)
        pattern = rf"(?m)^(\s*)({names})\s*=\s*Path\(r?[\"'].*?[\"']\)"
        return re.sub(pattern, replacement, text)

    # 路径常量（数据）→ 按后缀区分 parquet/CSV
    src = _rewrite_assignment(
        data_file_names,
        r"\1\2 = Path(os.environ.get('ODF_DATASET_PARQUET', os.environ['ODF_DATASET_PATH']))",
        src,
    )
    # 其他（.csv 或无后缀）→ ODF_DATASET_PATH
    src = _rewrite_assignment(
        data_file_names,
        r"\1\2 = Path(os.environ['ODF_DATASET_PATH'])",
        src,
    )
    # 文件型产物 → ODF_MODEL_OUT_PATH
    src = _rewrite_assignment(
        model_file_names,
        r"\1\2 = Path(os.environ['ODF_MODEL_OUT_PATH'])",
        src,
    )
    # 目录型产物 → ODF_MODEL_OUT_PATH.parent
    src = _rewrite_assignment(
        model_dir_names,
        r"\1\2 = Path(os.environ['ODF_MODEL_OUT_PATH']).parent",
        src,
    )
    # 目标常量 → ODF_TARGET_COLUMN（保留原值兜底，所以用户不选目标时用脚本里的）
    src = re.sub(
        r"(?m)^(\s*)(TARGET|target|TARGET_COL|target_col|TARGET_COLUMN|target_column|Y_COL|y_col|LABEL|label)\s*=\s*['\"]([^'\"]*)['\"]",
        r"\1\2 = os.environ.get('ODF_TARGET_COLUMN') or '\3'",
        src,
    )
    # 删 Windows 风格的 parent.mkdir（平台目录已存在）
    src = re.sub(
        r'(?m)^\s*(MODEL_SAVE_PATH|MODEL_PATH|SAVE_PATH|OUTPUT_PATH|MODEL_FILE|ARTIFACT_PATH|RESULT_PATH|PREDICTION_PATH)\.parent\.mkdir[^\n]*\n',
        '',
        src,
    )

    # 假数据替换：np.random.rand/randn 造数据 → 读 ODF_DATASET_PATH
    src = re.sub(
        r'(?m)^(\s*)(sample_features|sample_data|df_demo|fake_data|demo_data)\s*=\s*np\.random\.(rand|randn)\(.*?\).*$',
        r"\1\2 = __import__('pandas').read_csv(os.environ['ODF_DATASET_PATH']) if os.environ.get('ODF_DATASET_PATH') else np.random.\3(200, 8)",
        src,
    )
    src = re.sub(
        r'(?m)^(\s*)(sample_target|fake_target)\s*=\s*.*$',
        r"\1\2 = __import__('pandas').read_csv(os.environ['ODF_DATASET_PATH']).iloc[:, -1].values.reshape(-1, 1) if os.environ.get('ODF_DATASET_PATH') else \2",
        src,
    )
    src = re.sub(
        r'(?m)^(\s*)df_demo\s*=\s*pd\.DataFrame\(np\.hstack\(\[.*?\]\)\).*$',
        r"\1df_demo = pd.read_csv(os.environ['ODF_DATASET_PATH']) if os.environ.get('ODF_DATASET_PATH') else df_demo",
        src,
    )

    # 始终注入 ODF_MODEL_OUT_PATH（默认路径）
    if 'ODF_MODEL_OUT_PATH' not in src:
        src = "import os\nos.environ.setdefault('ODF_MODEL_OUT_PATH', '/tmp/foundation_run_model.pkl')\n" + src

    # 确保有 import os（预处理后的代码用到 os.environ）
    if not re.search(r'^import os\b', src, re.MULTILINE):
        src = 'import os\n' + src
    return src

def _apply_source_hyperparameters(src: str, hyperparameters: dict) -> str:
    """Override literal top-level constants without evaluating user-provided code."""
    special_patterns = {
        "n_trials": r"(\bn_trials\s*=\s*)\d+",
        "early_stopping_rounds": r"(\bearly_stopping\(\s*)\d+",
        "cv_splits": r"(\bTimeSeriesSplit\(\s*n_splits\s*=\s*)\d+",
    }
    for key, pattern in special_patterns.items():
        value = (hyperparameters or {}).get(key)
        if isinstance(value, int) and value > 0:
            src = re.sub(pattern, lambda match: f"{match.group(1)}{value}", src)

    for key, value in (hyperparameters or {}).items():
        if not isinstance(key, str) or not key.isidentifier():
            continue
        if not isinstance(value, (bool, int, float, str, list, tuple, dict, type(None))):
            continue
        literal = repr(value)
        src = re.sub(
            rf"(?m)^(\s*{re.escape(key)}\s*=\s*).*$",
            lambda match: f"{match.group(1)}{literal}",
            src,
            count=1,
        )
        # Also cover the common argparse style: --learning-rate with
        # default=0.01.  This still edits only the temporary execution copy.
        flag = key.replace("_", "[-_]")
        src = re.sub(
            rf"(?m)(add_argument\(\s*['\"]--{flag}['\"][^\n]*?\bdefault\s*=\s*)([^,\)\n]+)",
            lambda match: f"{match.group(1)}{literal}",
            src,
            count=1,
        )
    return src


def _detect_source_progress(src: str) -> tuple[str, int | None]:
    """Infer the progress unit declared by an uploaded training script."""
    trial_patterns = (
        r"(?m)^\s*(?:N_TRIALS|NUM_TRIALS|TRIALS)\s*=\s*(\d+)",
        r"study\.optimize\s*\([^)]*?\bn_trials\s*=\s*(\d+)",
        r"\.optimize\s*\([^)]*?\bn_trials\s*=\s*(\d+)",
    )
    for pattern in trial_patterns:
        match = re.search(pattern, src, re.DOTALL)
        if match and int(match.group(1)) > 0:
            return "parameter_search", int(match.group(1))
    epoch_match = re.search(
        r"(?m)^\s*(?:EPOCHS|NUM_EPOCHS|N_EPOCHS)\s*=\s*(\d+)", src
    )
    if epoch_match and int(epoch_match.group(1)) > 0:
        return "epoch", int(epoch_match.group(1))
    return "script", None


def _prepare_training_dataframe(
    dataframe: pd.DataFrame,
    target_column: str,
    feature_columns: list[str] | None,
) -> tuple[pd.DataFrame, list[str]]:
    """Enforce the UI feature/target selection before user code sees the data."""
    target = str(target_column or "").strip()
    if not target or target not in dataframe.columns:
        raise RuntimeError(f"训练目标列不存在：{target or '未选择'}")
    if feature_columns:
        missing = [name for name in feature_columns if name not in dataframe.columns]
        if missing:
            raise RuntimeError(f"所选特征列不存在：{missing}")
        selected = [name for name in feature_columns if name != target]
    else:
        selected = [
            name for name in dataframe.select_dtypes(include="number").columns
            if name != target
        ]
    if not selected:
        raise RuntimeError("目标列之外没有可用的数值特征列")
    non_numeric = [name for name in selected if not pd.api.types.is_numeric_dtype(dataframe[name])]
    if non_numeric:
        raise RuntimeError(f"以下特征列不是数值类型：{non_numeric}")
    if not pd.api.types.is_numeric_dtype(dataframe[target]):
        raise RuntimeError(f"目标列 {target} 不是数值类型")
    return dataframe[selected + [target]].copy(), selected


def train(job) -> tuple[str, bytes, dict, dict]:
    """执行用户上传的训练脚本，返回模型、指标与可部署运行上下文。"""
    dataset_id = job.config.get("dataset_id")
    algorithm_id = int(job.config.get("algorithm_id") or 0)
    if not algorithm_id:
        raise RuntimeError("训练任务缺少 algorithm_id")
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT file_path, name FROM datasets WHERE id = :id"), {"id": dataset_id}
        ).fetchone()
        algorithm_row = db.execute(
            text("SELECT entry_point, metrics FROM algorithm_models WHERE id = :id"),
            {"id": algorithm_id},
        ).fetchone()
    finally:
        db.close()
    if not row or not row[0]:
        raise RuntimeError(f"数据集 {dataset_id} 没有可读的数据文件")
    src = Path(str(row[0]))
    if not src.is_file():
        raise RuntimeError(f"数据集文件不存在：{src}")
    df = pd.read_csv(src) if src.suffix.lower() == ".csv" else pd.read_parquet(src)
    if job.config.get("source_data_mode") == "full_dataset":
        data_contract = job.config.get("source_data_contract") or {}
        target_column = str(job.config.get("target_column") or "").strip()
        selected_features = list(job.config.get("feature_columns") or [])
        required_columns = [target_column, str(job.config.get("source_time_column") or "").strip()]
        required_columns.extend(selected_features)
        missing = [name for name in dict.fromkeys(required_columns) if name and name not in df.columns]
        if missing:
            raise RuntimeError(f"源码声明的数据列在所选数据集中不存在：{missing}")
        if not selected_features:
            time_column = str(data_contract.get("time_column") or "").strip()
            selected_features = [
                name for name in df.select_dtypes(include="number").columns
                if name not in {target_column, time_column}
            ]
        feature_columns_explicit = bool(job.config.get("feature_columns_explicit")) and bool(selected_features)
        if feature_columns_explicit:
            # 手动选择时，源码只会接收到这些特征及训练必须保留的目标/时间列。
            # 未选择时仍保持完整数据集，兼容源码自身的默认特征处理逻辑。
            keep_columns = [
                name for name in dict.fromkeys([
                    str(job.config.get("source_time_column") or "").strip(),
                    target_column,
                    *selected_features,
                ])
                if name
            ]
            df = df.loc[:, keep_columns].copy()

        # Uploaded source receives the complete, time-ordered dataframe, but it
        # still needs the same basic finite-value guarantee as the built-in
        # training path. Do not split or shuffle here: remove only rows that
        # cannot be used by the selected training columns. This prevents a
        # source script that combines tolerant estimators (LightGBM/XGBoost)
        # with strict estimators (for example RandomForest) from failing late
        # because a CSV cell was blank or infinite.
        training_columns = list(dict.fromkeys([*selected_features, target_column]))
        training_columns = [name for name in training_columns if name]
        if not training_columns:
            raise RuntimeError("执行上传源码时没有可用的训练特征或目标列")
        rows_before_cleaning = len(df)
        df.loc[:, training_columns] = df.loc[:, training_columns].replace(
            [float("inf"), float("-inf")], pd.NA
        )
        usable_rows = df.loc[:, training_columns].notna().all(axis=1)
        df = df.loc[usable_rows].reset_index(drop=True)
        removed_rows = rows_before_cleaning - len(df)
        if len(df) < 10:
            raise RuntimeError(
                "清理训练特征中的缺失值后可用数据不足10行："
                f"原始{rows_before_cleaning}行，清理{removed_rows}行"
            )
        job.config.update({
            "source_rows_total": rows_before_cleaning,
            "source_rows_used": len(df),
            "source_rows_removed": removed_rows,
            "source_missing_value_policy": "drop_rows_in_selected_training_columns",
        })
    else:
        df, selected_features = _prepare_training_dataframe(
            df,
            str(job.config.get("target_column") or ""),
            job.config.get("feature_columns"),
        )
    job.config["feature_columns"] = selected_features

    # 定位用户脚本。ZIP 项目包会在上传时安全解压，并保留其目录结构。
    script_dir = Path(f"/app/uploads/algorithms/{algorithm_id}").resolve()
    metadata = {}
    if algorithm_row:
        raw_metadata = algorithm_row[1]
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata)
            except Exception:
                raw_metadata = {}
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    source_root = Path(str(metadata.get("source_project_root") or script_dir)).resolve()
    if source_root != script_dir and script_dir not in source_root.parents:
        raise RuntimeError("算法项目源码目录无效")
    if not source_root.is_dir():
        raise RuntimeError(f"算法 {algorithm_id} 的源码目录不存在：{source_root}")

    candidates: list[Path] = []
    project_entry = str(metadata.get("source_entry_point") or "").strip()
    if project_entry:
        candidate = (source_root / project_entry).resolve()
        if source_root in candidate.parents and candidate.suffix.lower() == ".py":
            candidates.append(candidate)
    if metadata.get("artifact_path"):
        candidates.append(Path(str(metadata["artifact_path"])))
    if metadata.get("source_filename"):
        candidates.append(script_dir / Path(str(metadata["source_filename"])).name)
    if algorithm_row and algorithm_row[0] and not project_entry:
        candidates.append(source_root / Path(str(algorithm_row[0])).name)
    entry = next((path for path in candidates if path.is_file() and path.suffix.lower() == ".py"), None)
    if entry is None:
        raise RuntimeError(f"算法 {algorithm_id} 目录下没有可执行的 .py 训练入口：{source_root}")


    runtime = {
        "id": None,
        "name": "平台默认 Python 环境",
        "python_path": "/opt/venv/bin/python",
        "runtime_digest": None,
    }
    runtime_environment_id = job.config.get("runtime_environment_id") or metadata.get("dependency_id")
    if runtime_environment_id:
        from services.sandbox_environment_service import get_runtime_build
        runtime = get_runtime_build(int(runtime_environment_id))
    python_path = str(runtime.get("python_path") or "/opt/venv/bin/python")
    if not Path(python_path).is_file():
        raise RuntimeError(f"算法运行环境不可用，未找到 Python：{python_path}")
    original_source = entry.read_text(encoding="utf-8", errors="replace")

    with tempfile.TemporaryDirectory(prefix="foundation-user-train-") as tmp_str:
        tmp = Path(tmp_str)
        data_path = tmp / "dataset.csv"
        df.to_csv(data_path, index=False)
        parquet_path = tmp / "dataset.parquet"
        df.to_parquet(parquet_path, index=False)
        model_out = tmp / "model.pkl"
        result_path = tmp / ".foundation_outputs" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        hyperparameters_path = tmp / "hyperparameters.json"
        hyperparameters_path.write_text(
            json.dumps(job.config.get("effective_hyperparameters") or {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # env 白名单（净化掉 backend 的 DB/JWT 等敏感变量，只留运行必需 + ODF_* 约定）
        child_env = {
            "PATH": f"{Path(python_path).parent}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(tmp),
            "PYTHONPATH": os.pathsep.join(dict.fromkeys([str(entry.parent), str(source_root)])),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "MPLCONFIGDIR": str(tmp / ".matplotlib"),
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
            "MALLOC_CONF": "background_thread:false,narenas:1",
            "ODF_DATASET_PATH": str(data_path),
            "ODF_DATASET_PARQUET": str(parquet_path),
            "ODF_TARGET_COLUMN": str(job.config.get("target_column") or ""),
            "ODF_TIME_COLUMN": str(job.config.get("source_time_column") or ""),
            "ODF_MODEL_OUT_PATH": str(model_out),
            "ODF_RESULT_PATH": str(result_path),
            "ODF_FEATURE_COLUMNS": json.dumps(selected_features, ensure_ascii=False),
            "ODF_HYPERPARAMS_PATH": str(hyperparameters_path),
        }

        # 预处理：把脚本里的路径/目标硬编码自动替换成读平台 env（配置优先，用户不用改脚本）
        prepared_text = _prepare_script(original_source)
        prepared_text = _apply_source_hyperparameters(
            prepared_text,
            job.config.get("effective_hyperparameters") or {},
        )
        progress_kind, detected_total_steps = _detect_source_progress(prepared_text)
        job.config["source_progress_kind"] = progress_kind
        if detected_total_steps:
            job.config["source_total_steps"] = detected_total_steps
            job.total_epochs = detected_total_steps
        prepared_entry = tmp / "run_prepared.py"
        prepared_entry.write_text(prepared_text, encoding="utf-8")
        stdout_path = tmp / "stdout.log"
        stderr_path = tmp / "stderr.log"
        print(f"[UserScriptTrainer] 执行用户脚本：{entry.name}（预处理后 → {prepared_entry.name}, algorithm_id={algorithm_id}, 数据集={dataset_id}, 目标={job.config.get('target_column')}）")
        timed_out = False
        command = [python_path, str(prepared_entry)]
        for data_option in ("--data", "--dataset"):
            if re.search(rf"['\"]{re.escape(data_option)}['\"]", prepared_text):
                command.extend([data_option, str(data_path)])
                break
        for option in ("--params", "--hyperparams", "--config"):
            if re.search(rf"['\"]{re.escape(option)}['\"]", prepared_text):
                command.extend([option, str(hyperparameters_path)])
                break
        proc = subprocess.Popen(
            command,
            cwd=str(entry.parent),
            env=child_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=_limit_process,
        )
        import time as _time, re as _re
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        deadline = _time.monotonic() + RUN_TIMEOUT_SECONDS
        # Optuna 脚本按参数搜索次数更新进度；无法识别时只展示已观察步骤，
        # 不再虚构默认的 10 个 epoch。
        total_trials = int(detected_total_steps or 0)
        n_trials = 0
        job.progress = 5
        job._save_run()
        last_saved_progress = float(job.progress)
        last_saved_at = _time.monotonic()

        def persist_live_progress() -> None:
            nonlocal last_saved_progress, last_saved_at
            now = _time.monotonic()
            current = float(job.progress or 0)
            if current - last_saved_progress >= 1.0 or now - last_saved_at >= 1.0:
                job._save_run()
                last_saved_progress = current
                last_saved_at = now

        while True:
            if proc.poll() is not None:
                break
            if _time.monotonic() > deadline:
                timed_out = True
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                break
            line = proc.stdout.readline()
            if line:
                _l = line.decode("utf-8", errors="replace")
                stdout_parts.append(_l)
                _m = _re.search(r'[Tt]rial\s*(\d+)', _l)
                if _m:
                    n_trials = max(n_trials, int(_m.group(1)) + 1)
                    job.current_epoch = n_trials
                    job.total_epochs = max(total_trials, n_trials, 1)
                    job.progress = min(95, 5 + int(n_trials / max(job.total_epochs, 1) * 90))
                    persist_live_progress()
                _epoch = _re.search(r'[Ee]poch\s*\[?\s*(\d+)\s*/\s*(\d+)\s*\]?', _l)
                if _epoch:
                    job.current_epoch = int(_epoch.group(1))
                    total_epochs = max(int(_epoch.group(2)), 1)
                    job.total_epochs = total_epochs
                    job.progress = min(95, 5 + int(job.current_epoch / total_epochs * 90))
                    persist_live_progress()
            else:
                _time.sleep(0.2)
        try:
            _rest_out, _rest_err = proc.communicate(timeout=5)
            stdout_parts.append(_rest_out.decode("utf-8", errors="replace"))
            if _rest_err:
                stderr_parts.append(_rest_err.decode("utf-8", errors="replace"))
        except Exception:
            pass
        returncode = proc.returncode if proc.returncode is not None else -1
        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        if timed_out:
            raise RuntimeError(f"用户脚本执行超时（{RUN_TIMEOUT_SECONDS}s）\nstdout:\n{stdout[-2000:]}\nstderr:\n{stderr[-2000:]}")
        if returncode != 0:
            raise RuntimeError(
                f"用户脚本执行失败（returncode={returncode}）\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )

        # Directory-oriented scripts often save a descriptive filename such
        # as ``best_model.pkl`` instead of the platform contract's exact
        # ``model.pkl`` path.  The output directory is an isolated temporary
        # directory, so it is safe to adopt the newest generated pickle as the
        # deployable artifact when the canonical path was not written.
        if not model_out.is_file():
            generated_pickles = sorted(
                (
                    path for path in tmp.rglob("*.pkl")
                    if path.is_file() and path != model_out
                ),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
                reverse=True,
            )
            if generated_pickles:
                model_out.write_bytes(generated_pickles[0].read_bytes())
                job.config["source_artifact_filename"] = generated_pickles[0].name

        if not model_out.is_file():
            raise RuntimeError(
                f"用户脚本未把模型写到 ODF_MODEL_OUT_PATH。请确认脚本里 joblib.dump(..., os.environ['ODF_MODEL_OUT_PATH'])。\n"
                f"stdout:\n{stdout[-2000:]}\nstderr:\n{stderr[-2000:]}"
            )
        artifact_metadata = inspect_sandbox_artifact(model_out, prepared_text, python_path)
        model_blob = model_out.read_bytes()

        # 持久化到 MODEL_DIR，让 model_versions.file_path 指向它
        from services.real_training_service import MODEL_DIR
        Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
        persisted = Path(MODEL_DIR) / f"{job.job_id}_user_model.pkl"
        persisted.write_bytes(model_blob)

        metrics: dict = {}
        if result_path.is_file():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict):
                    metrics = {k: float(v) for k, v in payload["metrics"].items() if isinstance(v, (int, float))}
            except Exception:
                pass
        if not metrics:
            metrics = _metrics_from_stdout(stdout)

        print(f"[UserScriptTrainer] 完成：模型 {len(model_blob)} 字节，指标 {metrics}，脚本 {entry.name}")
        return str(persisted), model_blob, metrics, {
            "script_source": original_source[:50000],
            "python_path": python_path,
            "environment_id": runtime.get("id"),
            "environment_name": runtime.get("name"),
            "runtime_digest": runtime.get("runtime_digest"),
            "n_features_in": artifact_metadata.get("n_features_in"),
            "feature_columns": artifact_metadata.get("feature_columns") or selected_features,
            "predictor_type": artifact_metadata.get("model_type"),
        }


def _register_user_script_model(job, model_blob: bytes, metrics: dict, artifact_context: dict) -> None:
    """Register an uploaded-source result in the platform model store."""
    db = SessionLocal()
    try:
        artifact_sha256 = hashlib.sha256(bytes(model_blob or b"")).hexdigest() if model_blob else None
        artifact_source = "real_training"
        mid = db.execute(text(
            "INSERT INTO models (name, description, model_type, framework, status, organization_id, folder_id, created_at, updated_at) "
            "VALUES (:name, :desc, :mt, 'user_script', 'READY', :org, :folder_id, NOW(), NOW()) RETURNING id"
        ), {
            "name": job.config.get("name") or f"用户脚本模型-{job.job_id}",
            "desc": (
                f"用户上传脚本训练 (job={job.job_id}; artifact_source={artifact_source}; "
                f"artifact_sha256={artifact_sha256 or 'unavailable'})"
            ),
            # model_type 是面向用户的任务类型；user_script 仅表示执行方式，不能写入该字段。
            "mt": job.config.get("task_type") or job.config.get("model_type") or "regression",
            "org": job.config.get("organization_id"), "folder_id": job.config.get("model_folder_id"),
        }).scalar()
        # Same deterministic Registry convention as builtin training.  The
        # user-script path therefore needs no separate MLflow-to-model mapping.
        mlflow_name = f"foundation_model_{mid}"
        db.execute(text("UPDATE models SET mlflow_name = :name WHERE id = :mid"), {
            "name": mlflow_name, "mid": mid,
        })
        if model_blob:
            db.execute(text("UPDATE models SET model_blob = :blob WHERE id = :mid"), {"blob": model_blob, "mid": mid})
        parameters = {
            "job_id": job.job_id,
            "source": "algorithm_store",
            "source_execution_mode": "execute_user_script",
            "algorithm_id": job.config.get("algorithm_id"),
            "artifact_kind": "user_script_predictor",
            "artifact_format": "pkl",
            "artifact_source": artifact_source,
            "artifact_sha256": artifact_sha256,
            "script_source": artifact_context.get("script_source"),
            "python_path": artifact_context.get("python_path"),
            "environment_id": artifact_context.get("environment_id"),
            "environment_name": artifact_context.get("environment_name"),
            "runtime_digest": artifact_context.get("runtime_digest"),
            "n_features_in": artifact_context.get("n_features_in"),
            "feature_columns": artifact_context.get("feature_columns") or job.config.get("feature_columns"),
            "time_column": job.config.get("source_time_column"),
            "predictor_type": artifact_context.get("predictor_type"),
            "target_column": job.config.get("target_column"),
            "effective_hyperparameters": job.config.get("effective_hyperparameters") or {},
        }
        version_id = db.execute(text(
            "INSERT INTO model_versions (model_id, version_number, file_path, metrics, parameters, is_deployed, created_at) "
            "VALUES (:mid, 'v1', :fp, :met, :par, false, NOW()) RETURNING id"
        ), {
            "mid": mid, "fp": job.model_path,
            "met": json.dumps(metrics) if metrics else None,
            "par": json.dumps(parameters, ensure_ascii=False),
        }).scalar()
        db.commit()
        job.config["registered_model_id"] = mid
        job.config["registered_model_version_id"] = version_id
        job.config["mlflow_name"] = mlflow_name
        print(f"[UserScript] 注册模型 #{mid} (job={job.job_id})")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _track_user_script_mlflow(job) -> None:
    """Log the actual user-script artifact and register it in MLflow.

    Uploaded scripts may produce different joblib-compatible predictors, so this
    path registers the saved artifact instead of falsely assuming a framework.
    """
    try:
        from services.mlflow_service import MLFLOW_AVAILABLE
        from services.mlflow_tracker import get_mlflow_tracker
    except ImportError:
        return
    if not MLFLOW_AVAILABLE or job.config.get("mlflow_run_id"):
        return

    tracker = None
    run_id = None
    try:
        tracker = get_mlflow_tracker()
        run_id = tracker.start_run(
            run_name=f"{job.job_id}_user_script",
            experiment_name=job.config.get("project_name", "foundation_training"),
            tags={
                "job_id": job.job_id,
                "model_type": "user_script",
                "task_type": str(job.config.get("task_type", "unknown")),
                "algorithm_id": str(job.config.get("algorithm_id", "")),
                "source_execution_mode": "execute_user_script",
            },
        )
        if not run_id:
            raise RuntimeError("MLflow Run 创建失败")
        job.config["mlflow_run_id"] = run_id

        # Keep MLflow parameters compact; source code and validation reports stay
        # in the platform's immutable model-version snapshot instead.
        params = {key: value for key, value in {
            "training_job_id": job.job_id,
            "algorithm_id": job.config.get("algorithm_id"),
            "algorithm_name": job.config.get("algorithm_name"),
            "algorithm_version": job.config.get("algorithm_version"),
            "dataset_id": job.config.get("dataset_id"),
            "target_column": job.config.get("target_column"),
            "feature_columns": job.config.get("feature_columns"),
            "time_column": job.config.get("source_time_column"),
            "environment_id": job.config.get("runtime_environment_id"),
            "hyperparameter_source": job.config.get("hyperparameter_source"),
            "effective_hyperparameters": job.config.get("effective_hyperparameters"),
            "source_artifact_filename": job.config.get("source_artifact_filename"),
        }.items() if value is not None}
        tracker.log_params(params)
        tracker.log_metrics(job.best_metrics or {}, step=max(0, int(job.current_epoch or 0)))
        if not job.model_path or not Path(job.model_path).is_file():
            raise RuntimeError("用户脚本模型文件不存在，无法登记到 MLflow")
        if not tracker.log_artifact(job.model_path, "models"):
            raise RuntimeError("MLflow 模型文件上传失败")

        mlflow_name = str(job.config.get("mlflow_name") or "")
        registered = tracker.register_artifact_model(mlflow_name, run_id, "models")
        if not registered:
            raise RuntimeError("MLflow 模型注册失败")

        version_id = job.config.get("registered_model_version_id")
        db = SessionLocal()
        try:
            if version_id:
                db.execute(text(
                    "UPDATE model_versions SET mlflow_run_id = :run_id, mlflow_model_version = :version "
                    "WHERE id = :version_id"
                ), {"run_id": run_id, "version": str(registered.get("version")), "version_id": version_id})
            db.execute(text("UPDATE models SET mlflow_name = :name WHERE id = :model_id"), {
                "name": mlflow_name, "model_id": job.config.get("registered_model_id"),
            })
            db.commit()
        finally:
            db.close()
        print(f"[UserScript][MLflow] Run {run_id} registered as {mlflow_name} v{registered.get('version')}")
    except Exception as exc:
        # A tracking failure must not falsely mark a successful source execution
        # as failed, but it remains persisted and visible for diagnosis.
        job.config["mlflow_error"] = str(exc)[:300]
        print(f"[UserScript][MLflow] Tracking failed: {exc}")
    finally:
        if tracker is not None and run_id:
            tracker.end_run("FINISHED" if not job.config.get("mlflow_error") else "FAILED")


def _sync_lineage(job) -> None:
    """同步血缘：dataset → 训练任务 → 模型（复用 lineage_sync_service）。"""
    from services.lineage_sync_service import upsert_training_lineage
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT id FROM training_jobs WHERE training_job_id = :t"), {"t": job.job_id}).fetchone()
    finally:
        db.close()
    if row:
        db2 = SessionLocal()
        try:
            upsert_training_lineage(db2, int(row[0]))
        finally:
            db2.close()


def _update_algorithm_status(job, status: str) -> None:
    algorithm_id = job.config.get("algorithm_id")
    if not algorithm_id:
        return
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE algorithm_models SET status = :status, updated_at = NOW() WHERE id = :id"),
            {"status": status, "id": algorithm_id},
        )
        db.commit()
    finally:
        db.close()


def execute_user_script_train(job) -> None:
    """完整执行用户脚本训练：train + 注册模型 + 血缘。设 job 状态/产物（供 real_training 分支调用后 _save_run/_save_to_db）。"""
    try:
        model_path, model_blob, metrics, artifact_context = train(job)
        job.model_path = model_path
        job.best_metrics = metrics
        _register_user_script_model(job, model_blob, metrics, artifact_context)
        _track_user_script_mlflow(job)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:800]
        job.error_traceback = traceback.format_exc()
        job.finished_at = datetime.now().isoformat()
        try:
            _update_algorithm_status(job, "failed")
        except Exception as status_error:
            print(f"[UserScript] 算法状态回写失败: {status_error}")
        print(f"[UserScript] 训练失败: {exc}")
        return
    configured_steps = int(
        job.config.get("source_total_steps") or job.config.get("epochs") or 0
    )
    observed_steps = int(job.current_epoch or 0)
    completed_steps = max(configured_steps, observed_steps, 1)
    job.current_epoch = completed_steps
    job.total_epochs = max(configured_steps, observed_steps, 1)
    job.metrics_history = [{"epoch": completed_steps, "metrics": metrics}]
    job.status = "completed"
    job.progress = 100
    job.finished_at = datetime.now().isoformat()
    try:
        _update_algorithm_status(job, "trained")
    except Exception as status_error:
        print(f"[UserScript] 算法状态回写失败: {status_error}")
    try:
        _sync_lineage(job)
    except Exception as le:
        print(f"[UserScript] 血缘同步失败: {le}")


def _metrics_from_stdout(stdout: str) -> dict:
    """脚本若没写 result.json，就从 stdout 正则抓 r2/rmse/mae/accuracy/hit_rate。"""
    metrics: dict = {}
    for key in ("r2", "rmse", "mae", "accuracy", "hit_rate", "mae_test"):
        m = re.search(rf"{key}\s*[:=]\s*([0-9.]+)", stdout, re.IGNORECASE)
        if m:
            try:
                metrics[key] = float(m.group(1))
            except ValueError:
                pass
    # Hyperparameter-search scripts often print an intermediate "best MAE"
    # before reporting the selected model's final holdout result.  The final
    # line is authoritative for the task summary and must override the first
    # generic MAE match above.
    final_mae = re.search(
        r"最终(?:测试集)?\s*MAE\s*[:=]\s*([-+0-9.eE]+)",
        stdout,
        re.IGNORECASE,
    )
    if final_mae:
        try:
            metrics["mae"] = float(final_mae.group(1))
        except ValueError:
            pass
    final_hit_rate = re.search(
        r"最终[^\n]*命中率\s*[:=]\s*([-+0-9.eE]+)\s*%?",
        stdout,
        re.IGNORECASE,
    )
    if final_hit_rate:
        try:
            metrics["hit_rate"] = float(final_hit_rate.group(1))
        except ValueError:
            pass
    return metrics
