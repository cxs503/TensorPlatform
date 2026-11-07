"""IN17-B1 平台 PR 边界门（SPEC §3 落机器）。

本包只放「边界门」类工具：把平台域中立项（domain-neutrality）从口头约定
变成机器可判的判决。工具本身零领域语义——**具体域词条不写进代码**，
一律从 ``data/boundary/pr_boundary_lexicon.json`` 注入（词表可换、可扩展，
工具代码不感知词）。

入口：``tools/boundary/pr_gate_scan.py``（CLI + 可 import 的判定 API）。
"""

__all__ = ["pr_gate_scan"]