"""生成绿色航行 EEDI/CII 结果图。"""

import json
import os
import sys
import base64
import io

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ship_perf.green_navigation import (
    calc_cii_boundaries, REQUIRED_EEDI_COEFFS,
)

ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "..", "artifacts", "green_eedi")

def load_result():
    with open(os.path.join(ARTIFACTS_DIR, "result.json")) as f:
        return json.load(f)

def generate_eedi_comparison(result):
    """EEDI attained vs required 对比柱状图。"""
    ships = result["ships"]
    names = [s["ship_name"] for s in ships]
    attained = [s["attained_eedi"] for s in ships]
    required = [s["required_eedi"] for s in ships]

    x = np.arange(len(names))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 6))
    bars1 = ax.bar(x - width/2, attained, width, label='attained EEDI', color='#2196F3')
    bars2 = ax.bar(x + width/2, required, width, label='required EEDI (Phase 2)', color='#FF9800')

    ax.set_ylabel('EEDI [g-CO₂/(t·nm)]', fontsize=12)
    ax.set_title('EEDI: attained vs required (Phase 2)', fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=11)
    ax.legend(fontsize=11)
    ax.grid(axis='y', alpha=0.3)

    # 添加数值标签
    for bar in bars1:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f'{bar.get_height():.2f}', ha='center', va='bottom', fontsize=9)
    for bar in bars2:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f'{bar.get_height():.2f}', ha='center', va='bottom', fontsize=9)

    plt.tight_layout()
    path = os.path.join(ARTIFACTS_DIR, "eedi_comparison.png")
    fig.savefig(path, dpi=150)
    plt.close()
    print(f"eedi_comparison.png saved to {path}")
    return path

def generate_cii_boundaries(result):
    """CII 边界与评级示意图。"""
    ship_types = ["bulk_carrier", "oil_tanker", "container_ship"]
    dwt_values = [82000, 307844, 14000]
    labels = ["Bulk 82K", "VLCC 308K", "Container 14K"]

    fig, ax = plt.subplots(figsize=(12, 7))

    colors = {'A': '#4CAF50', 'B': '#8BC34A', 'C': '#FFC107', 'D': '#FF9800', 'E': '#F44336'}

    for idx, (st, dwt, label) in enumerate(zip(ship_types, dwt_values, labels)):
        boundaries = calc_cii_boundaries(st, dwt, 2023)
        ratings = ['A', 'B', 'C', 'D', 'E']
        vals = [boundaries[r] for r in ratings]

        x = np.arange(len(ratings)) + idx * 0.15
        bars = ax.bar(x, vals, width=0.12, label=label, color=[colors[r] for r in ratings], alpha=0.8)

    ax.set_ylabel('CII boundary [g-CO₂/(t·nm)]', fontsize=12)
    ax.set_xlabel('Rating', fontsize=12)
    ax.set_title('CII A–E Rating Boundaries by Ship Type', fontsize=14)
    ax.set_xticks(np.arange(5) + 0.15)
    ax.set_xticklabels(['A', 'B', 'C', 'D', 'E'], fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    path = os.path.join(ARTIFACTS_DIR, "cii_boundaries.png")
    fig.savefig(path, dpi=150)
    plt.close()
    print(f"cii_boundaries.png saved to {path}")
    return path

def encode_png_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

if __name__ == "__main__":
    result = load_result()
    generate_eedi_comparison(result)
    generate_cii_boundaries(result)
    print("All figures generated successfully.")
