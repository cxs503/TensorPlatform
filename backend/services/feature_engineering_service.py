"""
通用特征工程服务 —— 数据治理环节的派生特征计算。

设计目标:可配置、通用(不绑定具体业务),同时满足当前 Demo(FeO 的工艺参数优化比例/环冷斜率)
并可用于其他场景。

支持的转换类型(可扩展):
  - expression : 算术表达式,列名用 {列名} 包裹。例 "({煤粉}+{焦粉})/({铁料1}+{铁料2})"
                  仅允许 + - * / % ** 和数字、列引用;无函数调用/属性访问,杜绝任意代码执行。
  - rolling    : 按行序(或时间列)滚动聚合。agg ∈ diff|slope|mean|std|var|sum|min|max
                  slope = 滚动窗口内的线性回归斜率;diff = 一阶差分。

输入/输出均为 pandas.DataFrame,新增派生列。纯标准库 + numpy/pandas 实现。
"""
import ast
import json
import operator
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# -------------------- 安全的算术表达式求值 --------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _numeric_series(series: pd.Series, column_name: str) -> pd.Series:
    """Convert numeric strings while rejecting values that would be silently lost."""
    converted = pd.to_numeric(series, errors="coerce")
    invalid = series.notna() & converted.isna()
    if invalid.any():
        examples = ", ".join(repr(v) for v in series[invalid].astype(str).head(3))
        raise ValueError(
            f"列 {column_name!r} 含 {int(invalid.sum())} 个非数值内容"
            f"（例如 {examples}），不能参与数值特征计算"
        )
    return converted


def _build_series_map(expr: str, df: pd.DataFrame):
    """把 {列名} 占位符替换为合法标识符,返回(新表达式, {标识符: Series})。

    占位符内允许首尾空白(如 ``{ 列名 }``),空白会被自动去除再匹配列。
    替换用正则做,避免列名含特殊字符时 str.replace 漏匹配导致残留花括号。
    """
    if not expr or not expr.strip():
        raise ValueError("表达式不能为空")
    cols = re.findall(r"\{([^{}]*)\}", expr)
    seen: Dict[str, str] = {}
    smap: Dict[str, pd.Series] = {}
    for raw in cols:
        c = raw.strip()
        if not c:
            raise ValueError("表达式中有空的列占位符 '{}'")
        if c not in df.columns:
            raise ValueError(f"表达式引用了不存在的列: {c!r}")
        if c not in seen:
            key = f"__c{len(seen)}"
            seen[c] = key
            smap[key] = _numeric_series(df[c], c)
    new_expr = expr
    # 用正则逐个把 {原列名(可含空白)} 替换为标识符,re.escape 保证特殊字符安全
    for c, key in seen.items():
        new_expr = re.sub(r"\{\s*" + re.escape(c) + r"\s*\}", key, new_expr)
    return new_expr, smap


def _eval_node(node, smap: Dict[str, pd.Series]):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, smap)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](
            _eval_node(node.left, smap), _eval_node(node.right, smap)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand, smap))
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in smap:
        return smap[node.id]
    raise ValueError(f"不支持的表达式元素(仅允许算术运算与列引用): {ast.dump(node)}")


def eval_expression(expr: str, df: pd.DataFrame) -> pd.Series:
    """安全求值:列名用 {列名} 引用,只允许算术运算。返回与 df 等长的 Series。"""
    new_expr, smap = _build_series_map(expr, df)
    tree = ast.parse(new_expr, mode="eval")
    result = _eval_node(tree, smap)
    if isinstance(result, pd.Series):
        return result
    # 表达式退化成常数时,广播成等长 Series
    return pd.Series([result] * len(df), index=df.index, dtype=float)


# -------------------- 滚动/时序转换 --------------------

_ROLL_AGGS = {"mean", "std", "var", "sum", "min", "max"}


def _unique_name(base: str, used: set[str]) -> str:
    """Return a human-readable derived-column name that does not collide."""
    name = base
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    return name


def _looks_like_time_column(name: str, series: pd.Series) -> bool:
    """Conservative time-column detection used only for recommendations."""
    if re.search(r"(^|[_\\-])(time|date|timestamp)($|[_\\-])|时间|日期|时刻", str(name), re.I):
        return True
    sample = series.dropna().astype(str).head(80)
    if sample.empty:
        return False
    if pd.to_numeric(sample, errors="coerce").notna().mean() >= 0.9:
        return False
    return pd.to_datetime(sample, errors="coerce").notna().mean() >= 0.85


def _looks_numeric(series: pd.Series) -> bool:
    values = series.dropna()
    if values.empty:
        return False
    return pd.to_numeric(values.head(500), errors="coerce").notna().mean() >= 0.95

FEATURE_TRANSFORM_TYPES = {"expression", "rolling", "lag", "time_component", "normalize"}
MAX_FEATURE_TRANSFORMS = 50
MAX_FEATURE_RULE_FILE_BYTES = 256 * 1024
FEATURE_RULE_TEMPLATE = '''# 特征规则只用于声明特征步骤；平台不会执行任意 Python 代码。
# 列名请使用数据集中的真实字段名。
FEATURE_RULES = {
    "time_column": "timestamp",  # 没有时间列可删除这一行
    "transformations": [
        {
            "type": "rolling",
            "name": "温度_5行均值",
            "source_column": "温度",
            "window": 5,
            "agg": "mean",
        },
        {
            "type": "expression",
            "name": "自定义比值",
            "expression": "{列A} / {列B}",
            "fill_value": 0,
        },
    ],
}
'''


def normalize_feature_definition(raw: Any) -> Dict[str, Any]:
    """Validate a reusable declarative feature rule without executing code."""
    if isinstance(raw, list):
        raw = {"transformations": raw}
    if not isinstance(raw, dict):
        raise ValueError("特征规则必须是对象，或由特征步骤组成的数组")
    transformations = raw.get("transformations")
    if not isinstance(transformations, list) or not transformations:
        raise ValueError("特征规则至少需要包含一个特征步骤")
    if len(transformations) > MAX_FEATURE_TRANSFORMS:
        raise ValueError(f"单个特征规则最多包含 {MAX_FEATURE_TRANSFORMS} 个步骤")
    result: List[Dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(transformations, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个特征步骤必须是对象")
        transform = dict(item)
        transform_type = str(transform.get("type") or "").strip().lower()
        if transform_type not in FEATURE_TRANSFORM_TYPES:
            raise ValueError(f"第 {index} 个步骤类型无效：{transform_type or '未填写'}")
        name = str(transform.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {index} 个步骤的新列名不能为空")
        if len(name) > 200 or name in names:
            raise ValueError(f"第 {index} 个步骤的新列名重复或过长")
        names.add(name)
        transform["type"] = transform_type
        transform["name"] = name
        result.append(transform)
    time_column = raw.get("time_column")
    if time_column is not None and not str(time_column).strip():
        time_column = None
    return {"version": 1, "time_column": str(time_column).strip() if time_column else None, "transformations": result}


def parse_feature_rule_file(filename: str, content: bytes) -> Tuple[Dict[str, Any], str]:
    """Parse .json/.py declarations, never execute an uploaded rule file."""
    if len(content) > MAX_FEATURE_RULE_FILE_BYTES:
        raise ValueError("规则文件不能超过 256KB")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".py", ".json"}:
        raise ValueError("仅支持 .py 或 .json 规则文件")
    try:
        source = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("规则文件必须使用 UTF-8 编码") from exc
    if suffix == ".json":
        try:
            return normalize_feature_definition(json.loads(source)), source
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败（第 {exc.lineno} 行）") from exc
    try:
        tree = ast.parse(source, filename=filename or "feature_rule.py", mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"Python 语法错误（第 {exc.lineno} 行）") from exc
    raw = None
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"FEATURE_RULES", "RULES"} and raw is None:
            try:
                raw = ast.literal_eval(node.value)
            except (ValueError, TypeError) as exc:
                raise ValueError("FEATURE_RULES 只能包含字典、数组、字符串、数字、布尔值和空值") from exc
            continue
        raise ValueError("为保证安全，规则文件只允许定义一次 FEATURE_RULES；不允许 import、函数或循环")
    if raw is None:
        raise ValueError("未找到 FEATURE_RULES 或 RULES 定义")
    return normalize_feature_definition(raw), source



def build_recommendations(df: pd.DataFrame) -> Dict[str, Any]:
    """Return conservative, executable feature suggestions for one dataset."""
    used = set(str(c) for c in df.columns)
    time_columns = [str(c) for c in df.columns if _looks_like_time_column(str(c), df[c])]
    numeric_columns = [str(c) for c in df.columns if str(c) not in time_columns and _looks_numeric(df[c])]
    recommendations: List[Dict[str, Any]] = []

    def add(key: str, title: str, description: str, transform: Dict[str, Any], selected: bool = True):
        transform = dict(transform)
        transform["name"] = _unique_name(str(transform["name"]), used)
        used.add(transform["name"])
        recommendations.append({
            "key": key,
            "title": title,
            "description": description,
            "default_selected": selected,
            "transform": transform,
        })

    if time_columns:
        time_col = time_columns[0]
        add("time-hour", "小时", f"从时间列「{time_col}」拆分小时，适合存在日内周期的数据。", {"type": "time_component", "name": f"{time_col}_小时", "source_column": time_col, "component": "hour"})
        add("time-dayofweek", "星期", f"从时间列「{time_col}」拆分星期几，适合存在周周期的数据。", {"type": "time_component", "name": f"{time_col}_星期", "source_column": time_col, "component": "dayofweek"})

    if numeric_columns:
        source = numeric_columns[0]
        if time_columns:
            add("lag-1", "滞后值", f"生成「{source}」上一时刻的值，用于时序预测。", {"type": "lag", "name": f"{source}_滞后1", "source_column": source, "periods": 1})
            add("rolling-mean", "滑动均值", f"计算「{source}」的 5 行滑动均值，帮助降低短期波动。", {"type": "rolling", "name": f"{source}_5行均值", "source_column": source, "window": 5, "agg": "mean"})
            add("difference", "一阶差分", f"计算「{source}」相邻时刻的变化量，突出变化趋势。", {"type": "rolling", "name": f"{source}_一阶差分", "source_column": source, "window": 1, "agg": "diff"})
        add("minmax", "归一化", f"把「{source}」缩放到 0~1，适合量纲差异较大的模型输入。", {"type": "normalize", "name": f"{source}_归一化", "source_column": source, "method": "minmax"}, selected=False)

    # 组合特征只在已有 FeO 领域模板实际匹配时才推荐，避免随意相乘两列。
    feo = build_feo_template(df)
    for index, transform in enumerate(feo["transformations"]):
        if transform.get("type") == "expression":
            add(f"feo-expression-{index}", "业务组合特征", "根据煤粉、焦粉和铁料列生成可解释的工艺参数优化比例。", transform)

    return {
        "profile": {"column_count": int(len(df.columns)), "numeric_columns": numeric_columns, "time_columns": time_columns},
        "recommendations": recommendations,
        "notes": ["未识别到时间列，因此未推荐时序特征；如有时间列，可在高级配置中手动选择。"] if not time_columns else [],
    }


def time_component_transform(series: pd.Series, component: str) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.isna().any():
        raise ValueError("时间拆分需要可解析且无空值的时间列")
    values = {
        "hour": parsed.dt.hour,
        "dayofweek": parsed.dt.dayofweek,
        "day": parsed.dt.day,
        "month": parsed.dt.month,
    }
    if component not in values:
        raise ValueError("时间拆分类型无效(可选 hour/dayofweek/day/month)")
    return values[component].astype(float)


def normalize_transform(series: pd.Series, method: str) -> pd.Series:
    values = _numeric_series(series, str(series.name))
    method = (method or "minmax").lower()
    if method == "minmax":
        low, high = values.min(), values.max()
        if pd.isna(low) or pd.isna(high) or high == low:
            raise ValueError("归一化列需要至少两个不同的有效数值")
        return (values - low) / (high - low)
    if method == "zscore":
        std = values.std(ddof=0)
        if pd.isna(std) or std == 0:
            raise ValueError("标准化列需要至少两个不同的有效数值")
        return (values - values.mean()) / std
    raise ValueError("归一化方式无效(可选 minmax/zscore)")


def rolling_transform(
    series: pd.Series, window: int, agg: str, fill_value: Optional[float] = None
) -> pd.Series:
    """滚动聚合。agg: diff(一阶差分) / slope(线性回归斜率) / mean/std/var/sum/min/max。"""
    s = pd.to_numeric(series, errors="coerce")
    w = max(1, int(window))
    agg = (agg or "mean").lower()

    if agg == "diff":
        out = s.diff()
    elif agg == "slope":
        x = np.arange(w, dtype=float)
        x_mean = x.mean()
        denom = float(((x - x_mean) ** 2).sum())

        def _slope(y):
            yv = np.asarray(y, dtype=float)
            if yv.size < 2 or np.isnan(yv).any() or denom == 0:
                return np.nan
            ym = yv.mean()
            return float(((x[: yv.size] - x_mean) * (yv - ym)).sum() / denom)

        out = s.rolling(w, min_periods=2).apply(_slope, raw=True)
    elif agg in _ROLL_AGGS:
        out = getattr(s.rolling(w, min_periods=1), agg)()
    else:
        raise ValueError(f"不支持的聚合类型: {agg!r}(可选 diff/slope/mean/std/var/sum/min/max)")

    if fill_value is not None:
        out = out.replace([np.inf, -np.inf], np.nan).fillna(float(fill_value))
    return out


# -------------------- 转换执行 --------------------

def run_transformations(
    df: pd.DataFrame,
    transformations: List[Dict[str, Any]],
    time_column: Optional[str] = None,
) -> pd.DataFrame:
    """对 df 顺序应用一组转换,返回含派生列的新 DataFrame(不修改输入)。"""
    if not transformations:
        raise ValueError("至少需要配置一个特征转换")

    if time_column:
        if time_column not in df.columns:
            raise ValueError(f"时间列不存在: {time_column!r}")
        parsed_time = pd.to_datetime(df[time_column], errors="coerce")
        invalid_time = df[time_column].notna() & parsed_time.isna()
        if invalid_time.any():
            raise ValueError(
                f"时间列 {time_column!r} 含 {int(invalid_time.sum())} 个无法解析的时间值"
            )
        if parsed_time.isna().any():
            raise ValueError(
                f"时间列 {time_column!r} 含 {int(parsed_time.isna().sum())} 个空值，无法可靠排序"
            )
        # 时序特征必须按可解析的时间稳定排序。
        sort_idx = parsed_time.sort_values(kind="mergesort").index
        df = df.loc[sort_idx].reset_index(drop=True)

    out = df.copy()
    reserved = set(df.columns)
    for i, t in enumerate(transformations):
        ttype = (t.get("type") or "").lower()
        raw_name = t.get("name")
        name = str(raw_name).strip() if raw_name is not None else f"feat_{i + 1}"
        if not name:
            raise ValueError(f"第 {i + 1} 个转换的新列名不能为空")
        if name in reserved:
            raise ValueError(f"派生列名与已有列冲突: {name!r}")
        fv = t.get("fill_value")

        if ttype == "expression":
            expression = t.get("expression")
            out[name] = eval_expression(expression, out)
            noise = t.get("noise")
            if noise:  # 可选高斯噪声(合成标签/数据增强);按列名确定性种子,保证可复现
                noise = float(noise)
                if noise < 0:
                    raise ValueError("噪声 σ 不能小于 0")
                seed = sum(ord(c) for c in name) % (2**32)
                out[name] = out[name] + np.random.default_rng(seed).normal(
                    0, noise, size=len(out)
                )
        elif ttype == "lag":
            src = t.get("source_column")
            if not src or src not in out.columns:
                raise ValueError(f"lag 的 source_column 不存在: {src!r}")
            periods = int(t.get("periods", 1))
            if periods < 1:
                raise ValueError("lag 的 periods 必须是大于 0 的整数")
            out[name] = _numeric_series(out[src], str(src)).shift(periods)
        elif ttype == "time_component":
            src = t.get("source_column")
            if not src or src not in out.columns:
                raise ValueError(f"time_component 的 source_column 不存在: {src!r}")
            out[name] = time_component_transform(out[src], str(t.get("component", "hour")))
        elif ttype == "normalize":
            src = t.get("source_column")
            if not src or src not in out.columns:
                raise ValueError(f"normalize 的 source_column 不存在: {src!r}")
            out[name] = normalize_transform(out[src], str(t.get("method", "minmax")))
        elif ttype == "rolling":
            src = t.get("source_column")
            if not src:
                raise ValueError("rolling 的 source_column 不能为空")
            if src not in out.columns:
                raise ValueError(f"rolling 的 source_column 不存在: {src!r}")
            out[name] = rolling_transform(
                _numeric_series(out[src], str(src)),
                int(t.get("window", 10)),
                t.get("agg", "mean"),
                None,
            )
        else:
            raise ValueError(f"不支持的转换类型: {ttype!r}(可选 expression / rolling)")

        # JSON/CSV 不能可靠表达无穷大。只有用户明确配置填充值时才替换，
        # 否则给出可操作的错误；普通 NaN（如差分首行）可保留并由 API 安全展示。
        derived = pd.to_numeric(out[name], errors="coerce")
        infinite = pd.Series(np.isinf(derived.to_numpy(dtype=float)), index=derived.index)
        if fv is not None:
            derived = derived.mask(infinite).fillna(float(fv))
        elif infinite.any():
            raise ValueError(
                f"派生列 {name!r} 产生了 {int(infinite.sum())} 个无穷大值，"
                "请检查除数是否为 0，或设置空值填充"
            )
        if not derived.notna().any():
            raise ValueError(f"派生列 {name!r} 没有产生任何有效数值，请检查源列和计算配置")
        out[name] = derived

        reserved.add(name)
    return out


# -------------------- FeO 场景模板(按列名关键字自动匹配) --------------------

def _find_col(columns: List[str], *keywords) -> Optional[str]:
    for c in columns:
        cl = c.lower()
        if all(k.lower() in c or k in cl for k in keywords):
            return c
    # 任一关键字命中
    for c in columns:
        if any(k.lower() in c for k in keywords):
            return c
    return None


def build_feo_template(df: pd.DataFrame) -> Dict[str, Any]:
    """根据 FeO 数据集的列名自动生成 工艺参数优化比例 + 环冷斜率 转换配置。"""
    cols = list(df.columns)
    coal = _find_col(cols, "煤粉")
    coke = _find_col(cols, "焦粉")
    iron = [c for c in cols if "铁料" in c]
    ret = [c for c in cols if ("冷返矿" in c or "炉返矿" in c)]
    hood = _find_col(cols, "罩内温度") or _find_col(cols, "罩内")

    transformations: List[Dict[str, Any]] = []
    notes: List[str] = []

    if coal and coke and iron:
        num = "{" + coal + "}+{" + coke + "}"
        den = "+".join("{" + c + "}" for c in (iron + ret))
        transformations.append(
            {
                "type": "expression",
                "name": "工艺参数优化比例",
                "expression": f"({num})/({den})",
                "fill_value": 0,
            }
        )
    else:
        notes.append("未匹配到 煤粉/焦粉/铁料 列,工艺参数优化比例未生成")

    if hood:
        transformations.append(
            {
                "type": "rolling",
                "name": "环冷斜率",
                "source_column": hood,
                "window": 10,
                "agg": "slope",
                "fill_value": 0,
            }
        )
    else:
        notes.append("未匹配到 罩内温度 列,环冷斜率未生成")

    return {"transformations": transformations, "notes": notes, "matched": {
        "coal": coal, "coke": coke, "iron_count": len(iron), "return_count": len(ret), "hood": hood
    }}
