"""Knowledge Factory 试点调度器（TensorPlatform 侧）。

零领域语义：本包只实现「工包 → 派单 → 双门校验 → 独立审核 → draft 入库 → 看板」这层共性机制。
工序/泳道/契约全部来自 config.json + contracts.template.json（可被 KBM-RCI-F1-SPEC 产物覆盖），
域词条、域判据、域算例不入代码。
"""

__all__ = ["store", "contract", "gates", "discover", "dashboard", "scheduler"]