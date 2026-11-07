"""harness_gateway —— /root/tp-agent harness 层（dispatcher / worker / daemons）的
门控镜像仓与部署管线（IN27-H2）。

设计目标（信任基座收缩）：
    历史上"整层 harness 归协调者（人工/LLM）所有" —— 谁改谁负责，改坏无人拦。
    本包把信任基座收缩到**三道门本身**：
        1) py_compile 编译门（含重复定义扫描 + 凭据泄露扫描）
        2) 金丝雀单进程试跑门（沙箱内单进程 dry-run，零生产副作用）
        3) git 回滚门（git checkout + 字节级 hash 校验，任何门失败即回滚）
    门之外的一切（补丁内容、诊断单内容、部署动作）都只是被检对象，
    必须产出可机器裁剪的证据（artifacts/ 下的 JSON），而不是自述。

模块：
    spec          harness 单元清单（角色/试跑模式/期望符号）
    redact        凭据扫描与脱敏（镜像入库前必须无明文口令）
    mirror        镜像生成与漂移检测（源 → 仓库镜像，sha256 双向可复算）
    gates.*       三道门
    pipeline      补丁 → 门链 → 部署/回滚 的执行器
    diagnostics   协调官诊断单 → 可执行动作（而非只报告）
    cli           python -m harness_gateway ... 命令行
"""

from harness_gateway.spec import DEFAULT_UNITS, HarnessUnit, unit_paths, units_by_name
from harness_gateway.mirror import MirrorReport, mirror_harness, verify_mirror
from harness_gateway.pipeline import HarnessDeployPipeline, PatchSpec, Replacement

__all__ = [
    "DEFAULT_UNITS",
    "HarnessUnit",
    "HarnessDeployPipeline",
    "MirrorReport",
    "PatchSpec",
    "Replacement",
    "mirror_harness",
    "unit_paths",
    "units_by_name",
    "verify_mirror",
]

__version__ = "1.0.0"