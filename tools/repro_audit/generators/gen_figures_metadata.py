"""生成器：figures_metadata.json —— 图表元数据。

复算逻辑：图表配置参数。
"""

OUTPUT_PATH = "artifacts/figures_metadata.json"


def generate() -> dict:
    """生成 figures_metadata.json 的数值内容。"""
    figures = [
        {"name": "resistance_curve", "type": "line", "n_points": 50, "x_range": [0, 0.5], "y_range": [0, 0.01]},
        {"name": "convergence_plot", "type": "line", "n_points": 100, "x_range": [0, 100], "y_range": [0, 1]},
        {"name": "comparison_bar", "type": "bar", "n_bars": 5, "categories": ["A", "B", "C", "D", "E"]},
    ]
    
    return {
        "total_figures": len(figures),
        "figures": figures,
        "summary": {
            "line_plots": sum(1 for f in figures if f["type"] == "line"),
            "bar_plots": sum(1 for f in figures if f["type"] == "bar"),
            "total_points": sum(f.get("n_points", f.get("n_bars", 0)) for f in figures)
        }
    }


if __name__ == "__main__":
    import json
    result = generate()
    print(json.dumps(result, indent=2))
