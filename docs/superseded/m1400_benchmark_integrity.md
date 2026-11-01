# m1400 — benchmark_integrity 服务 + 测试（归档）

**分支**：`agent/m1400`（提交 3594933）
**交付**：`backend/services/benchmark_integrity.py`（448 行变体）、`backend/services/hull_geometry.py`
（243 行）、`backend/services/resistance_core.py`（136 行）、`backend/tests/test_benchmark_integrity.py`
（378 行）、`scripts/audit_benchmark.py`、`scripts/gen_report_md.py`、报告与三张图。

**数值主张**：SUBOFF 仍为 12.5 N（未修 Rt 列），以闸门形式把置疑问号化。

**裁决**：**思路并入正典，代码不并入。**
- 采纳：`hull_geometry` 的"几何先验校验"思想 → 正典 R6 形体系数带（L/D、S/(πDL)、L/B、Cb）。
- 采纳：证据闸门与报告工件程序化生成（`scripts/gen_report_md.py` 零手抄）的做法 →
  正典 `--write` 自动产出 `docs/reports/m1561/{adjudication_table,canon_audit}.json`。
- 不采纳：`resistance_core` / `hull_geometry` 再次复制阻力物理；`benchmark_integrity` 448 行变体
  与基线模块并存会产生两套严重度口径。正典统一为基线模块 + m1561 的三处扩展
  （行内物性溯源、证据级恒等式容差 1%、不适用检查项不污染行级 verdict）。
- 不采纳：数值层仍保留 12.5 N，与恒等式冲突 7.4 倍，不可作为判据。
