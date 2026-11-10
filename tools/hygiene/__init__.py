"""交付面与重启动卫生机制（TensorPlatform 共性机制，零领域语义）。

两个互相独立的机制，共用一套「证据可复算」纪律：

* :mod:`tools.hygiene.coupling_restart`
  跨求解器 checkpoint / restart 一致性。三个子问题被拆成三个可机器裁的命题：
  位级可复现（sha256 相等）、能量账本守恒（残差 / 漂移阈值）、时间同步点裁决
  （非同步点 checkpoint 必须被拒绝或证明发散）。状态存取复用
  :mod:`tools.longrun.checkpoint`（原子写 + 每数组位级 sha256 + 链式 manifest），
  本模块只负责「耦合态 = 多求解器 + 时钟相位 + 能量账本」这一层的语义。

* :mod:`tools.hygiene.delivery_surface`
  未合并分支的内容等价分类（A 已被主干覆盖 / B 被超集覆盖 / C 真未交付 / D 孤儿）
  与「只减不增」门（每仓基线化，新波次未合并分支数不得回升）。

铁律（owner 2026-11-06 重建纪律）：本包**不含任何领域词条/判据/算例**。
求解器只按「格点数 + 时间步 + 刚度」参数化，属数值方法层；分支分类只看 git 对象，
与仓内容无关。
"""

from . import coupling_restart as coupling_restart  # noqa: F401
from . import delivery_surface as delivery_surface  # noqa: F401

__all__ = ["coupling_restart", "delivery_surface"]