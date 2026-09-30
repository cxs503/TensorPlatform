"""Runtime for uploaded control/optimisation algorithm projects.

Control projects are deliberately not sent through the supervised-training path:
they consume a complete process dataset and produce a simulation/identification
report, rather than a ``model.pkl`` plus a prediction target.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from services.sandbox_environment_service import get_runtime_build
from services.user_script_trainer import _limit_process


CONTROL_RUNS_ROOT = Path("/app/uploads/control_runs")
CONTROL_RUNS_FILE = CONTROL_RUNS_ROOT / "runs.json"
MAX_LOG_BYTES = 1024 * 1024
MAX_OUTPUT_FILE_BYTES = 20 * 1024 * 1024
RUN_TIMEOUT_SECONDS = int(os.getenv("CONTROL_PROJECT_RUN_TIMEOUT_SECONDS", "1800"))
_LOCK = threading.RLock()


class ControlProjectRuntimeError(ValueError):
    """A safe, user-facing control project runtime error."""


PARAMETERIZED_DMC_RUNNER = r"""
# Platform-owned runner. It imports the uploaded DMC source project and only
# creates files inside the temporary execution copy.
import json
import math
import os
import sys
from pathlib import Path

project = Path(__file__).resolve().parent
config = json.loads(Path(os.environ["ODF_CONTROL_CONFIG_PATH"]).read_text(encoding="utf-8"))
result_dir = Path(os.environ["ODF_RESULT_DIR"])
result_dir.mkdir(parents=True, exist_ok=True)

# Both the standalone library and the framework template expose this package.
package_roots = []
for init_file in project.rglob("__init__.py"):
    if init_file.parent.name == "dmc_controller":
        package_roots.append(init_file.parent.parent)
for root in sorted(set(package_roots), key=lambda item: len(str(item))):
    sys.path.insert(0, str(root))
if not package_roots:
    raise RuntimeError("参数化仿真需要项目包含 dmc_controller Python 包")

uploaded_controller_config = config.get("controller_config")
config_source = "用户上传的控制配置"
if uploaded_controller_config is not None:
    if not isinstance(uploaded_controller_config, dict):
        raise ValueError("控制配置文件必须是 JSON 对象")
    raw_config = json.loads(json.dumps(uploaded_controller_config))
else:
    config_files = [
        *project.rglob("dmc_config.example.json"),
        *project.rglob("dmc_controller_config.json"),
    ]
    excluded_config_dirs = {"docs", "reports", "dist", "test", "tests", "fixture", "fixtures", "example", "examples", "__pycache__"}
    config_files = [item for item in config_files if not any(part.lower() in excluded_config_dirs for part in item.parts)]
    config_files.sort(key=lambda item: (0 if "conf" in item.parts else 1 if "config" in item.parts else 2, str(item)))
    if not config_files:
        raise RuntimeError("未在项目中找到 DMC 配置文件")
    raw_config = json.loads(config_files[0].read_text(encoding="utf-8"))
    config_source = str(config_files[0].relative_to(project))
dmc_config = json.loads(json.dumps(raw_config.get("dmc_config", raw_config)))
if not isinstance(dmc_config, dict):
    raise ValueError("控制配置文件中的 dmc_config 必须是对象")
simulation_options = dmc_config.pop("_foundation_simulation", {})
if simulation_options is None:
    simulation_options = {}
if not isinstance(simulation_options, dict):
    raise ValueError("_foundation_simulation 必须是 JSON 对象")

from dmc_controller import DMCController

parameters = {**(config.get("parameters") or {}), **simulation_options}
def number(key, default, minimum=None, maximum=None):
    try:
        value = float(parameters.get(key, default))
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value

steps = int(number("steps", 30, 2, 500))
dt = number("sample_time_s", 5.0, 0.1, 3600)
# The source package defaults (P/N=100, maxiter=2000) are appropriate for a
# long-lived plant service but unnecessarily slow for an interactive preview.
# These remain real DMC calculations and can be overridden through parameters.
timing = dmc_config.setdefault("timing", {})
timing["P"] = int(number("prediction_horizon", timing.get("P", 20), 5, 200))
timing["M"] = int(number("control_horizon", timing.get("M", 5), 1, 50))
# DMCController v1.1 validates this as a required timing field.  Some older
# project templates do not persist it in their example JSON, so preserve an
# explicit value when supplied and otherwise execute once per framework cycle.
timing["execution_interval_cycles"] = int(number(
    "execution_interval_cycles", timing.get("execution_interval_cycles", 1), 1, 10000,
))
solver = dmc_config.setdefault("solver", {})
solver["maxiter"] = int(number("solver_maxiter", solver.get("maxiter", 100), 10, 2000))
cvs = [item for item in dmc_config.get("cvs", []) if item.get("enabled", True)]
mvs = [item for item in dmc_config.get("mvs", []) if item.get("enabled", True)]
models = [item for item in dmc_config.get("process_models", []) if item.get("interaction_enabled", True)]
if not cvs or not mvs or not models:
    raise RuntimeError("DMC 配置缺少启用的 CV、MV 或过程模型")
cv_id, mv_id = str(cvs[0]["id"]), str(mvs[0]["id"])
# Apply the remaining recognised DMC JSON settings to this temporary config.
# Missing values retain the uploaded project's own defaults.
cv = cvs[0]
mv = mvs[0]
cv.setdefault("ll", {})["value"] = number("cv_lower_bound", (cv.get("ll") or {}).get("value", 0.0), -100000, 100000)
cv.setdefault("hl", {})["value"] = number("cv_upper_bound", (cv.get("hl") or {}).get("value", 0.0), -100000, 100000)
cv["feedback_beta"] = number("feedback_beta", cv.get("feedback_beta", 0.8), 0, 1)
mv["lower_bound"] = number("mv_lower_bound", mv.get("lower_bound", 0.0), -100000, 100000)
mv["upper_bound"] = number("mv_upper_bound", mv.get("upper_bound", 0.0), -100000, 100000)
mv["dmv_lower_bound"] = number("dmv_lower_bound", mv.get("dmv_lower_bound", -1.0), -100000, 100000)
mv["dmv_upper_bound"] = number("dmv_upper_bound", mv.get("dmv_upper_bound", 1.0), -100000, 100000)
mv["move_suppression"] = number("move_suppression", mv.get("move_suppression", 1.0), 0, 100000)
model = next((item for item in models if str(item.get("cv_id")) == cv_id and str(item.get("mv_id")) == mv_id), models[0])
plant = model.setdefault("parameters", {})
gain = number("plant_gain", plant.get("K", 1.0), -100000, 100000)
plant["K"] = gain
tau = number("plant_time_constant_s", plant.get("T", 60.0), 0.1, 100000)
delay_s = number("plant_dead_time_s", plant.get("D", 0.0), 0, 100000)
plant["T"], plant["D"] = tau, delay_s
# DMC's own validator requires N * sample_time >= dead_time + 4 * time_constant.
# Keep an explicit user setting when valid, otherwise raise it to the minimum valid horizon.
minimum_model_horizon = max(5, int(math.ceil((delay_s + 4 * tau) / dt)))
timing["N"] = max(int(number("model_horizon", minimum_model_horizon, 5, 2000)), minimum_model_horizon)
setpoint = number("setpoint", config.get("setpoint", 3.5) if config.get("setpoint") is not None else 3.5, -100000, 100000)
initial_cv = number("initial_cv", 2.0, -100000, 100000)
initial_mv = number("initial_mv", 1.0, -100000, 100000)
disturbance = number("disturbance", 0.0, -100000, 100000)
disturbance_start = int(number("disturbance_start_step", steps + 1, 0, steps + 1))

controller = DMCController(dmc_config, framework_period_s=dt)
y, u = initial_cv, initial_mv
delay_steps = max(0, int(round(delay_s / dt)))
input_history = [u] * (delay_steps + 1)
rows = []
for index in range(steps):
    timestamp = index * dt
    current_disturbance = disturbance if index >= disturbance_start else 0.0
    result = controller.calculate(
        cv_values={cv_id: y}, mv_values={mv_id: u}, sp_values={cv_id: setpoint}, timestamp=timestamp,
    )
    candidate = (result.mv_out or {}).get(mv_id, u)
    try:
        u_next = float(candidate)
    except (TypeError, ValueError):
        u_next = u
    if result.commit_required:
        controller.commit_applied_mv(result.calculation_id, result.mv_out, timestamp=timestamp)
    input_history.append(u_next)
    delayed_u = input_history.pop(0)
    y += (dt / tau) * ((gain * delayed_u + current_disturbance) - y)
    rows.append({
        "step": index + 1, "time_s": timestamp, "cv": y, "setpoint": setpoint,
        "mv": u_next, "disturbance": current_disturbance,
        "status": str(result.status_code), "solver_success": bool(result.solver_success),
    })
    u = u_next

csv_path = result_dir / "dmc_parameterized_trajectory.csv"
with csv_path.open("w", encoding="utf-8") as stream:
    stream.write("step,time_s,cv,setpoint,mv,disturbance,status,solver_success\\n")
    for row in rows:
        stream.write("{step},{time_s:.6f},{cv:.8f},{setpoint:.8f},{mv:.8f},{disturbance:.8f},{status},{solver_success}\\n".format(**row))
errors = [row["setpoint"] - row["cv"] for row in rows]
summary = {
    "simulation_mode": "parameterized", "engine": "uploaded_dmc_controller",
    "config_file": config_source, "steps": steps,
    "sample_time_s": dt, "cv_id": cv_id, "mv_id": mv_id,
    "plant": {"K": gain, "T": tau, "D": delay_s}, "setpoint": setpoint,
    "final_cv": rows[-1]["cv"], "final_mv": rows[-1]["mv"],
    "mae_to_setpoint": sum(abs(value) for value in errors) / len(errors),
    "rmse_to_setpoint": math.sqrt(sum(value * value for value in errors) / len(errors)),
    "solver_success_cycles": sum(1 for row in rows if row["solver_success"]),
}
(result_dir / "dmc_parameterized_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
try:
    import matplotlib.pyplot as plt
    figure, (axis_cv, axis_mv) = plt.subplots(2, 1, sharex=True, figsize=(10, 6), constrained_layout=True)
    times = [row["time_s"] for row in rows]
    axis_cv.plot(times, [row["cv"] for row in rows], label=cv_id, linewidth=2)
    axis_cv.plot(times, [row["setpoint"] for row in rows], label="Setpoint", linestyle="--")
    axis_cv.set_ylabel("CV")
    axis_cv.grid(alpha=0.3); axis_cv.legend()
    axis_mv.plot(times, [row["mv"] for row in rows], label=mv_id, color="#3b82f6", linewidth=2)
    axis_mv.set_xlabel("Time (s)"); axis_mv.set_ylabel("MV")
    axis_mv.grid(alpha=0.3); axis_mv.legend()
    figure.savefig(result_dir / "dmc_parameterized_trend.png", dpi=150)
    plt.close(figure)
except Exception as exc:
    print("趋势图未生成：{}".format(exc))
print(json.dumps(summary, ensure_ascii=False))
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_runs() -> list[dict[str, Any]]:
    if not CONTROL_RUNS_FILE.is_file():
        return []
    try:
        value = json.loads(CONTROL_RUNS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _write_runs(runs: list[dict[str, Any]]) -> None:
    CONTROL_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    target = CONTROL_RUNS_FILE.with_suffix(".tmp")
    target.write_text(json.dumps(runs[-1000:], ensure_ascii=False, indent=2), encoding="utf-8")
    target.replace(CONTROL_RUNS_FILE)


def list_runs(algorithm_id: int | None, organization_id: int | None, *, is_admin: bool) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _read_runs()
    if algorithm_id is not None:
        rows = [item for item in rows if int(item.get("algorithm_id") or 0) == int(algorithm_id)]
    if not is_admin:
        rows = [item for item in rows if item.get("organization_id") == organization_id]
    return list(reversed(rows))


def get_run(run_id: str, organization_id: int | None, *, is_admin: bool) -> dict[str, Any]:
    with _LOCK:
        row = next((item for item in _read_runs() if item.get("id") == run_id), None)
    if not row or (not is_admin and row.get("organization_id") != organization_id):
        raise ControlProjectRuntimeError("控制运行记录不存在")
    return row


def delete_run(run_id: str, organization_id: int | None, *, is_admin: bool) -> None:
    """Remove one control simulation record and its persisted outputs."""
    if Path(run_id).name != run_id:
        raise ControlProjectRuntimeError("控制运行记录不存在")
    with _LOCK:
        rows = _read_runs()
        record = next((item for item in rows if item.get("id") == run_id), None)
        if not record or (not is_admin and record.get("organization_id") != organization_id):
            raise ControlProjectRuntimeError("控制运行记录不存在")
        _write_runs([item for item in rows if item.get("id") != run_id])
    target = (CONTROL_RUNS_ROOT / run_id).resolve()
    if CONTROL_RUNS_ROOT.resolve() in target.parents and target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def _load_dataset(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise ControlProjectRuntimeError("所选数据集文件不存在")
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt", ".tsv"}:
        return pd.read_csv(path, sep="\t" if suffix == ".tsv" else ",")
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".json", ".jsonl"}:
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise ControlProjectRuntimeError(f"暂不支持控制运行的数据格式：{suffix or '未知'}")


def _safe_entry(source_root: Path, entry_point: str) -> Path:
    entry = (source_root / str(entry_point or "")).resolve()
    if source_root not in entry.parents or not entry.is_file() or entry.suffix.lower() != ".py":
        raise ControlProjectRuntimeError("控制算法训练入口无效或不存在")
    return entry


def _read_limited(path: Path) -> str:
    """Return a compact tail for list APIs while preserving full log files on disk."""
    if not path.is_file():
        return ""
    size = path.stat().st_size
    offset = max(0, size - MAX_LOG_BYTES)
    with path.open("rb") as stream:
        if offset:
            stream.seek(offset)
        content = stream.read(MAX_LOG_BYTES)
    prefix = f"[日志前 {offset} 字节已省略；可查看完整日志]\n" if offset else ""
    return prefix + content.decode("utf-8", errors="replace")


def _collect_outputs(project_root: Path, result_root: Path, started_at: float) -> list[dict[str, Any]]:
    """Keep only outputs created or overwritten by this run, never bundled examples."""
    files: list[dict[str, Any]] = []
    excluded = {".git", "__pycache__", ".venv", "venv"}
    for candidate in project_root.rglob("*"):
        if not candidate.is_file() or any(part in excluded for part in candidate.parts):
            continue
        # Company control projects commonly write their business trace through a
        # rotating ``.log`` handler instead of stdout.  Keep that trace with the
        # run so a successful replay is inspectable as well as a failed one.
        if candidate.suffix.lower() not in {".json", ".png", ".jpg", ".jpeg", ".svg", ".csv", ".txt", ".log"}:
            continue
        try:
            size = candidate.stat().st_size
        except OSError:
            continue
        if size > MAX_OUTPUT_FILE_BYTES:
            continue
        # copytree preserves bundled sample timestamps; a produced file must be new for this run.
        if candidate.stat().st_mtime < started_at - 1.0:
            continue
        relative = candidate.relative_to(project_root)
        target = result_root / "outputs" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)
        files.append({"path": str(Path("outputs") / relative), "name": candidate.name, "size_bytes": size, "kind": candidate.suffix.lower().lstrip(".")})
    return files[:100]


def _write_runtime_log(result_root: Path, stdout_path: Path, stderr_path: Path) -> Path:
    """Persist one user-facing log stream for every control run.

    A number of company drivers configure their normal diagnostic logger to
    stderr.  stderr therefore does not necessarily mean a failed run.  This
    merged file preserves the stream origin while providing one predictable
    artifact for the UI and for download.
    """
    target = result_root / "runtime.log"
    with target.open("wb") as destination:
        for label, source in ((b"=== stdout ===\n", stdout_path), (b"\n=== stderr / driver diagnostics ===\n", stderr_path)):
            destination.write(label)
            if source.is_file():
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, destination)
            destination.write(b"\n")
    return target

def _json_summaries(result_root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for item in files:
        if item["kind"] != "json":
            continue
        path = result_root / item["path"]
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                summaries[item["path"]] = parsed
        except Exception:
            continue
    return summaries


def get_control_config_template(metadata: dict[str, Any]) -> dict[str, Any]:
    """Load a project-owned DMC JSON template without importing project code."""
    source_root = Path(str(metadata.get("source_project_root") or "")).resolve()
    if not source_root.is_dir():
        raise ControlProjectRuntimeError("算法项目的控制配置模板不存在")
    files = [*source_root.rglob("dmc_config.example.json"), *source_root.rglob("dmc_controller_config.json")]
    excluded = {"docs", "reports", "dist", "test", "tests", "fixture", "fixtures", "example", "examples", "__pycache__"}
    files = [item for item in files if not any(part.lower() in excluded for part in item.parts)]
    files.sort(key=lambda item: (0 if "conf" in item.parts else 1 if "config" in item.parts else 2, str(item)))
    if not files:
        raise ControlProjectRuntimeError("该项目未提供可下载的 DMC 配置模板")
    try:
        raw = json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlProjectRuntimeError(f"控制配置模板无效：{exc}") from exc
    config = raw.get("dmc_config", raw)
    if not isinstance(config, dict):
        raise ControlProjectRuntimeError("控制配置模板必须是 JSON 对象")
    result = json.loads(json.dumps(config))
    timing = result.setdefault("timing", {})
    if not isinstance(timing, dict):
        raise ControlProjectRuntimeError("控制配置中的 timing 必须是对象")
    timing.setdefault("execution_interval_cycles", 1)
    cvs = result.get("cvs") if isinstance(result.get("cvs"), list) else []
    mvs = result.get("mvs") if isinstance(result.get("mvs"), list) else []
    cv = next((item for item in cvs if isinstance(item, dict) and item.get("enabled", True)), {})
    mv = next((item for item in mvs if isinstance(item, dict) and item.get("enabled", True)), {})
    lower, upper = (cv.get("ll") or {}).get("value"), (cv.get("hl") or {}).get("value")
    setpoint = (float(lower) + float(upper)) / 2 if isinstance(lower, (int, float)) and isinstance(upper, (int, float)) else 3.5
    mv_low, mv_high = mv.get("lower_bound"), mv.get("upper_bound")
    initial_mv = (float(mv_low) + float(mv_high)) / 2 if isinstance(mv_low, (int, float)) and isinstance(mv_high, (int, float)) else 1.0
    result.setdefault("_foundation_simulation", {"steps": 30, "sample_time_s": 5, "setpoint": setpoint, "initial_cv": setpoint, "initial_mv": initial_mv, "disturbance": 0})
    return {"config": result, "source_file": str(files[0].relative_to(source_root))}


FAN_EFFICIENCY_DATASET_REPLAY_RUNNER = r"""
# Platform-owned offline replay adapter for the Cangzhou fan-efficiency project.
# It maps a tabular process dataset into the source project's documented logical
# inputs, then invokes its frozen DataDriver calculation for every timestamp.
import csv
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

workspace = Path(__file__).resolve().parent
config_candidates = [item for item in workspace.rglob("fan_efficiency_config.json") if item.parent.name == "conf"]
if not config_candidates:
    raise RuntimeError("未找到示例现场风机正式配置文件 conf/fan_efficiency_config.json")
config_path = sorted(config_candidates, key=lambda item: (len(item.parts), str(item)))[0]
project = config_path.parent.parent
if str(project) not in sys.path:
    sys.path.insert(0, str(project))
result_dir = Path(os.environ["ODF_RESULT_DIR"])
result_dir.mkdir(parents=True, exist_ok=True)
dataset_path = Path(os.environ["ODF_DATASET_PATH"])
time_column = str(os.environ.get("ODF_TIME_COLUMN") or "").strip()

from bin.drivers.fan_business_engine import DataDriver

config = json.loads(config_path.read_text(encoding="utf-8"))
definitions = config.get("data_acquisition_variables") or {}
inputs = config.get("inputs") or {}
aliases = {
    "fan_running_hz": ["引风机运行频率", "风机运行频率"],
    "motor_running_current_a": ["引风机运行电流", "电机运行电流"],
    "dust_collector_inlet_temperature_c": ["除尘器入口温度", "除尘器进口温度"],
    "dust_collector_outlet_temperature_c": ["出口烟气温度", "除尘器出口温度"],
    "dust_collector_inlet_pressure_pa": ["除尘器入口压力", "除尘器进口压力"],
    "dust_collector_outlet_pressure_pa": ["除尘器出口压力", "除尘器出口/风机进口静压"],
    "dust_collector_inlet_velocity_m_s": ["南区散点除尘器入口风速", "除尘器入口风速", "除尘器进口风速"],
    "motor_energy_total_kwh": ["电机累计有功电量", "累计有功电量", "累计电量"],
    "motor_input_power_kw": ["电网侧直接有功功率", "电机输入功率", "有功功率"],
    "fan_outlet_static_pressure_pa": ["风机出口静压", "风机出口压力"],
    "motor_running_voltage_kv": ["电机运行电压", "运行电压"],
    "diagnostic_measured_flow_m3_h": ["CEMS实测流量", "实测流量", "烟气流量"],
}
with dataset_path.open("r", encoding="utf-8-sig", newline="") as stream:
    reader = csv.DictReader(stream)
    columns = list(reader.fieldnames or [])
    rows = list(reader)
if not rows:
    raise RuntimeError("过程数据集为空")
if not time_column:
    for candidate in ("时间", "timestamp", "time", "采集时间", "记录时间"):
        if candidate in columns:
            time_column = candidate
            break
if not time_column or time_column not in columns:
    raise RuntimeError("示例现场风机回放必须选择有效时间列（例如“时间”）")

column_lookup = {str(column).strip().lower(): str(column) for column in columns}
mapping = {}
for logical_name, definition in definitions.items():
    candidates = [logical_name, str((inputs.get(logical_name) or {}).get("tag") or ""), str(definition.get("name_cn") or "")]
    candidates.extend(aliases.get(logical_name, []))
    found = next((column_lookup.get(str(item).strip().lower()) for item in candidates if str(item).strip().lower() in column_lookup), None)
    if found:
        mapping[logical_name] = found
required = [name for name, definition in definitions.items() if definition.get("required")]
missing_required = [name for name in required if name not in mapping]
power_sources = ("motor_energy_total_kwh", "motor_input_power_kw", "motor_running_current_a")
if not any(name in mapping for name in power_sources):
    missing_required.append("motor_energy_total_kwh / motor_input_power_kw / motor_running_current_a（三选一）")
if missing_required:
    labels = [str((definitions.get(name) or {}).get("name_cn") or name) for name in missing_required]
    raise RuntimeError("数据集缺少示例现场风机计算必需列：" + "、".join(labels))

driver = DataDriver()
driver.m_config = config
validation = driver.init_basic_config()
if not validation.get("config_valid"):
    raise RuntimeError("风机算法配置校验失败：" + str(validation.get("errors") or []))

def as_number(value):
    if value is None or not str(value).strip():
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None

def parse_time(value, fallback):
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return fallback
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

first_time = next((parse_time(row.get(time_column), None) for row in rows if parse_time(row.get(time_column), None) is not None), None)
if first_time is None:
    raise RuntimeError("时间列无法解析为日期时间：" + time_column)
records, status_counts, source_counts = [], Counter(), Counter()
for index, row in enumerate(rows):
    timestamp = parse_time(row.get(time_column), first_time + timedelta(seconds=index * 5))
    cycle_time = float(timestamp.timestamp())
    snapshot, metadata = {}, {}
    for logical_name, column in mapping.items():
        value = as_number(row.get(column))
        snapshot[logical_name] = value
        metadata[logical_name] = {
            "source_timestamp_s": cycle_time if value is not None else None,
            "receive_timestamp_s": cycle_time,
            "last_valid_timestamp_s": cycle_time if value is not None else None,
            # The source engine follows the OPC quality convention: 192 is good.
            "quality": 192 if value is not None else 0,
            "stale": value is None,
        }
    snapshot["_cycle_timestamp_s"] = cycle_time
    driver.m_tag = snapshot
    driver.m_input_metadata = metadata
    result = driver.calc_core()
    valid = int(bool(result.get("calculation_valid")))
    status_counts[str(result.get("status_code"))] += 1
    source_counts[str(result.get("motor_power_source_code") or "unavailable")] += 1
    records.append({
        time_column: timestamp.isoformat(),
        "calculation_valid": valid,
        "status_code": result.get("status_code"),
        "status_message": result.get("status_message"),
        "fan_efficiency_pct": result.get("fan_efficiency_pct"),
        "motor_input_power_kw": result.get("motor_input_power_kw"),
        "fan_inlet_flow_m3_h": result.get("fan_inlet_flow_m3_h"),
        "fan_outlet_flow_m3_h": result.get("fan_outlet_flow_m3_h"),
        "dust_collector_pressure_drop_pa": result.get("dust_collector_pressure_drop_pa"),
        "total_pressure_rise_pa": result.get("total_pressure_rise_pa"),
        "grid_specific_energy_kwh_per_10000m3": result.get("grid_specific_energy_kwh_per_10000m3"),
        "motor_power_source_code": result.get("motor_power_source_code"),
    })

output_csv = result_dir / "fan_efficiency_replay_results.csv"
fieldnames = list(records[0].keys())
with output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)
efficiencies = [float(item["fan_efficiency_pct"]) for item in records if isinstance(item["fan_efficiency_pct"], (int, float)) and math.isfinite(float(item["fan_efficiency_pct"]))]
valid_rows = sum(item["calculation_valid"] for item in records)
summary = {
    "replay_type": "cangzhou_fan_efficiency_dataset_replay",
    "algorithm": config.get("basic", {}).get("project_name"),
    "input_rows": len(rows),
    "time_column": time_column,
    "input_mapping": mapping,
    "calculation_valid_rows": valid_rows,
    "calculation_invalid_rows": len(records) - valid_rows,
    "calculation_valid_ratio": round(valid_rows / max(len(records), 1), 6),
    "fan_efficiency_pct": {"count": len(efficiencies), "mean": round(sum(efficiencies) / len(efficiencies), 6) if efficiencies else None, "min": round(min(efficiencies), 6) if efficiencies else None, "max": round(max(efficiencies), 6) if efficiencies else None},
    "status_code_counts": dict(status_counts),
    "motor_power_source_counts": dict(source_counts),
    "result_file": output_csv.name,
}
summary_path = result_dir / "fan_efficiency_replay_summary.json"
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
"""


def _is_cangzhou_fan_efficiency_project(source_root: Path) -> bool:
    """Identify the documented Cangzhou fan-efficiency project, including ZIP roots."""
    for config_path in source_root.rglob("fan_efficiency_config.json"):
        if config_path.parent.name != "conf":
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(config.get("data_acquisition_variables"), dict)
            and isinstance(config.get("data_out"), dict)
            and "FAN_EFFICIENCY_PCT" in config.get("data_out", {})
        ):
            return True
    return False


def _generic_parameterized_entry(project: Path, default_entry: Path, contract: Any) -> Path | None:
    """Resolve a manifest-declared parameterized entry inside a copied project."""
    if not isinstance(contract, dict):
        return None
    modes = contract.get("modes")
    if not isinstance(modes, dict) or "parameterized" not in modes:
        return None
    mode_config = modes.get("parameterized")
    mode_config = mode_config if isinstance(mode_config, dict) else {}
    relative = Path(str(mode_config.get("entry_point") or default_entry.relative_to(project)))
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".py":
        raise ControlProjectRuntimeError("算法运行描述中的参数化入口无效")
    target = (project / relative).resolve()
    if project not in target.parents or not target.is_file():
        raise ControlProjectRuntimeError(f"算法运行描述指定的参数化入口不存在：{relative}")
    return target


def run_control_project(*, algorithm_id: int, algorithm_name: str, metadata: dict[str, Any], dataset, config: dict[str, Any], organization_id: int | None, source_files: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy and run a source project, preserving its reports without fabricating a model.

    ``source_files`` is an optional editor overlay: it makes code-editor simulation
    runs execute the currently edited snapshot without persisting it first.
    """
    source_root = Path(str(metadata.get("source_project_root") or "")).resolve()
    entry_point = str(metadata.get("source_entry_point") or "").strip()
    entry_relative = Path(entry_point)
    if not source_root.is_dir() or entry_relative.is_absolute() or ".." in entry_relative.parts or entry_relative.suffix.lower() != ".py":
        raise ControlProjectRuntimeError("控制算法训练入口无效或不存在")
    if not (source_root / entry_relative).is_file() and str(entry_relative) not in (source_files or {}):
        raise ControlProjectRuntimeError("控制算法训练入口无效或不存在")
    simulation_mode = str(config.get("simulation_mode") or "dataset_replay")
    control_contract = metadata.get("control_contract") if isinstance(metadata.get("control_contract"), dict) else None
    if simulation_mode not in {"dataset_replay", "parameterized", "realtime_stream"}:
        raise ControlProjectRuntimeError("不支持的控制运行方式")
    frame = None
    time_column = str(config.get("time_column") or "").strip()
    if simulation_mode == "dataset_replay":
        if dataset is None or not dataset.file_path:
            raise ControlProjectRuntimeError("数据集回放方式必须选择可读取的数据集")
        frame = _load_dataset(Path(dataset.file_path))
        if frame.empty:
            raise ControlProjectRuntimeError("所选数据集为空")
        if time_column and time_column not in frame.columns:
            raise ControlProjectRuntimeError(f"时间列不存在：{time_column}")
        for group in ("state_columns", "control_columns", "disturbance_columns"):
            missing = [str(item) for item in config.get(group, []) if str(item) not in frame.columns]
            if missing:
                raise ControlProjectRuntimeError(f"{group} 包含不存在的数据列：{', '.join(missing[:5])}")
        if time_column:
            frame = frame[[time_column] + [column for column in frame.columns if column != time_column]]

    runtime_id = metadata.get("dependency_id")
    try:
        # Keep source-project behaviour aligned with normal uploaded-source training:
        # an omitted environment means the platform's complete Python runtime,
        # not the deliberately minimal "standard library" sandbox image.
        runtime = get_runtime_build(int(runtime_id)) if runtime_id else {
            "id": None,
            "name": "平台默认 Python 环境",
            "python_path": "/opt/venv/bin/python",
            "actual_python_version": None,
            "mode": "platform_default",
        }
    except Exception as exc:
        raise ControlProjectRuntimeError(f"控制算法运行环境不可用：{exc}") from exc
    python_path = str(runtime.get("python_path") or "")
    if not Path(python_path).is_file():
        raise ControlProjectRuntimeError("控制算法运行环境缺少 Python 解释器")

    run_id = uuid.uuid4().hex[:12]
    result_root = CONTROL_RUNS_ROOT / run_id
    result_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    started_wall = time.time()
    status = "failed"
    returncode = -1
    output = ""
    error = ""
    runtime_log = ""
    files: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="foundation-control-run-") as tmp_dir:
            tmp = Path(tmp_dir)
            project = tmp / "project"
            shutil.copytree(source_root, project, symlinks=False, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "venv", ".runs"))
            for raw_path, raw_content in (source_files or {}).items():
                relative = Path(str(raw_path))
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise ControlProjectRuntimeError("编辑器快照包含不安全文件路径")
                content = str(raw_content if raw_content is not None else "")
                if len(content.encode("utf-8")) > 5 * 1024 * 1024:
                    raise ControlProjectRuntimeError(f"编辑器文件过大：{relative}")
                target = (project / relative).resolve()
                if project not in target.parents:
                    raise ControlProjectRuntimeError("编辑器快照包含不安全文件路径")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            entry = (project / entry_relative).resolve()
            if project not in entry.parents or not entry.is_file():
                raise ControlProjectRuntimeError("控制算法训练入口无效或不存在")
            data_path = tmp / "dataset.csv"
            if frame is not None:
                frame.to_csv(data_path, index=False)
            config_path = tmp / "control_config.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            child_env = {
                "PATH": f"{Path(python_path).parent}:/usr/local/bin:/usr/bin:/bin",
                "HOME": str(tmp), "PYTHONPATH": str(project), "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1",
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "MPLCONFIGDIR": str(tmp / ".matplotlib"),
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
                "ODF_DATASET_PATH": str(data_path), "ODF_CONTROL_CONFIG_PATH": str(config_path),
                "ODF_CONTROL_CONFIG": json.dumps(config, ensure_ascii=False), "ODF_RESULT_DIR": str(project / ".foundation_results"),
                "ODF_SIMULATION_MODE": simulation_mode,
                "ODF_CONTROL_ENTRY": str(entry),
                "ODF_REALTIME_CONFIG": json.dumps(config.get("realtime") or {}, ensure_ascii=False),
                "ODF_TIME_COLUMN": time_column,
                "ODF_STATE_COLUMNS": json.dumps(config.get("state_columns") or [], ensure_ascii=False),
                "ODF_CONTROL_COLUMNS": json.dumps(config.get("control_columns") or [], ensure_ascii=False),
                "ODF_DISTURBANCE_COLUMNS": json.dumps(config.get("disturbance_columns") or [], ensure_ascii=False),
            }
            # Company DataDriver projects are normally hosted by a proprietary real-time
            # service. The platform-owned adapter supplies either a historical CSV stream
            # or a bounded MQTT/ZMQ live stream without changing the uploaded business code.
            execution_entry = entry
            # The entry may be nested below bin/drivers while conf/basic.json belongs to
            # the project root. Find that root instead of assuming entry.parent.
            company_driver_root = None
            candidate = entry.parent
            while True:
                if (candidate / "conf" / "basic.json").is_file():
                    company_driver_root = candidate
                    break
                if candidate == project:
                    break
                candidate = candidate.parent
            is_company_driver = bool(company_driver_root) and "class DataDriver" in entry.read_text(encoding="utf-8", errors="ignore")
            if simulation_mode in {"dataset_replay", "realtime_stream"} and is_company_driver:
                runner_template = Path(__file__).with_name("company_driver_runner.py")
                if not runner_template.is_file():
                    raise ControlProjectRuntimeError("平台缺少公司控制驱动适配器")
                # Traditional projects use bin as their working directory and resolve
                # ../conf from there. Keep that convention only inside the temporary copy.
                runner_dir = company_driver_root / "bin" if (company_driver_root / "bin").is_dir() else company_driver_root
                # Several SDK projects configure a rotating file handler at ../logs
                # relative to bin. Create it only in the temporary execution copy.
                (runner_dir.parent / "logs").mkdir(parents=True, exist_ok=True)
                if runner_dir == company_driver_root:
                    legacy_conf_dir = company_driver_root.parent / "conf"
                    if not legacy_conf_dir.exists():
                        shutil.copytree(company_driver_root / "conf", legacy_conf_dir)
                    legacy_logs_dir = company_driver_root.parent / "logs"
                    if not legacy_logs_dir.exists():
                        source_logs_dir = company_driver_root / "logs"
                        if source_logs_dir.is_dir():
                            shutil.copytree(source_logs_dir, legacy_logs_dir)
                        else:
                            legacy_logs_dir.mkdir(parents=True, exist_ok=True)
                child_env["ODF_CONTROL_PROJECT_ROOT"] = str(company_driver_root)
                child_env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([str(runner_dir), str(company_driver_root), str(project)]))
                runner_path = runner_dir / ".foundation_company_driver_runner.py"
                shutil.copy2(runner_template, runner_path)
                execution_entry = runner_path
                command = [python_path, str(runner_path)]
            elif simulation_mode == "realtime_stream":
                raise ControlProjectRuntimeError("该项目未识别为公司 DataDriver 控制项目，暂不支持实时接入运行")
            elif simulation_mode == "parameterized":
                generic_entry = _generic_parameterized_entry(project, entry, control_contract)
                if generic_entry:
                    execution_entry = generic_entry
                    command = [python_path, str(execution_entry)]
                elif any(item.parent.name == "dmc_controller" for item in project.rglob("__init__.py")):
                    # Compatibility path for existing DMC packages that predate
                    # the declarative contract. It still invokes their real controller.
                    runner_path = project / ".foundation_parameterized_dmc.py"
                    runner_path.write_text(PARAMETERIZED_DMC_RUNNER, encoding="utf-8")
                    execution_entry = runner_path
                    command = [python_path, str(runner_path)]
                else:
                    raise ControlProjectRuntimeError(
                        "该算法未声明参数化仿真入口；请在项目中添加 foundation_algorithm.json，或选择数据集回放"
                    )
            else:
                # The Cangzhou fan project is an online ZMQ/MQTT service at its
                # declared entry point.  For an offline dataset replay, invoke a
                # platform-owned adapter that feeds each row through the project's
                # frozen business engine instead of starting its infinite service.
                if _is_cangzhou_fan_efficiency_project(project):
                    runner_path = project / ".foundation_fan_efficiency_replay.py"
                    runner_path.write_text(FAN_EFFICIENCY_DATASET_REPLAY_RUNNER, encoding="utf-8")
                    execution_entry = runner_path
                    command = [python_path, str(runner_path)]
                else:
                    # The conventional first positional data argument covers many existing scripts, including FOPDT/MPC projects.
                    command = [python_path, str(entry), str(data_path)]
            stdout_path, stderr_path = tmp / "stdout.log", tmp / "stderr.log"
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                process = subprocess.Popen(command, cwd=str(execution_entry.parent), env=child_env, stdout=stdout_file, stderr=stderr_file, start_new_session=True, preexec_fn=_limit_process)
                timed_out = False
                try:
                    returncode = process.wait(timeout=RUN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    process.wait(timeout=10)
                    returncode = -9
                    timed_out = True
            # Keep complete raw logs for diagnosis; only the API summary below is shortened.
            shutil.copy2(stdout_path, result_root / "stdout.log")
            shutil.copy2(stderr_path, result_root / "stderr.log")
            runtime_log_path = _write_runtime_log(result_root, stdout_path, stderr_path)
            output, error = _read_limited(stdout_path), _read_limited(stderr_path)
            runtime_log = _read_limited(runtime_log_path)
            if returncode == 0:
                files = _collect_outputs(project, result_root, started_wall)
                status = "success"
            else:
                reason = f"控制算法运行超过 {RUN_TIMEOUT_SECONDS // 60} 分钟，已停止" if timed_out else f"进程退出码：{returncode}"
                error = (error + f"\n{reason}").strip()
                with (result_root / "stderr.log").open("a", encoding="utf-8") as stream:
                    stream.write(f"\n{reason}\n")
    except Exception as exc:
        error = (error + f"\n{exc}").strip()
        (result_root / "stderr.log").write_text(error, encoding="utf-8")
        runtime_log_path = _write_runtime_log(result_root, result_root / "stdout.log", result_root / "stderr.log")
        runtime_log = _read_limited(runtime_log_path)

    record = {
        "id": run_id, "algorithm_id": int(algorithm_id), "algorithm_name": algorithm_name,
        "organization_id": organization_id,
        "dataset_id": int(dataset.id) if dataset is not None else None,
        "dataset_name": dataset.name if dataset is not None else ("实时接入数据流" if simulation_mode == "realtime_stream" else "平台生成的虚拟过程"),
        "simulation_mode": simulation_mode,
        "name": str(config.get("name") or f"{algorithm_name}-控制运行"), "status": status,
        "returncode": returncode, "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "created_at": _now(), "config": config,
        "environment": {key: runtime.get(key) for key in ("id", "name", "python_version", "actual_python_version", "mode")},
        "outputs": files, "json_results": _json_summaries(result_root, files),
        "stdout_tail": output[-12000:], "stderr_tail": error[-12000:],
        "runtime_log_tail": runtime_log[-12000:],
    }
    with _LOCK:
        rows = _read_runs(); rows.append(record); _write_runs(rows)
    return record
