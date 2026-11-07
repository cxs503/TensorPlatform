"""台账证伪仪（falsification instrument）。

模块划分：
  spec.py     —— 单一真值能力台账（21 项）+ 每项的可执行调用规格
  knives.py   —— 三把刀（扰动不变性 / 解析解对拍 / 单调性），纯函数
  runner.py   —— 探针执行（HTTP 与进程内两种传输层）
  grade.py    —— 判决规则与混淆矩阵
  selftest.py —— 变异体自检（检出率/误报率）
  figures.py  —— 混淆矩阵与刀具剖面图
"""

__all__ = ["spec", "knives", "runner", "grade", "selftest", "figures"]