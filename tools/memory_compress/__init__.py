"""memory_compress —— 三层记忆压缩（全局 / 席位个体 / knowledge_base）。

分工（owner 2026-11-12 策展令）：
- 全局层 ``global_view``：bootstrap/PANORAMA 只做**索引密度**（标题+一句要点+指针），
  详情下沉到可检索文件；判据=结构保留率(标题 100%) + 数字保留率。
- 席位层 ``seat_memory``：留"能力证据 / 复发性失败模式 / 任务类型胜任度"，
  丢一次性过程细节（episodes 在 tensor_reports 可恢复），丢弃必附恢复指针。
- KB 层 ``kb_lifecycle``：条目带 use_count/last_used，引用即 +1，长期零引用降权归档，
  每次动作写遗忘日志（可审计、可回滚）。
- 节奏 ``cadence``：定期（interval_days）+ 超阈值（体积/腐化率）双触发。
"""

from .cadence import due_triggers, load_policy, update_state
from .global_view import compress_index_view, doc_metrics
from .kb_lifecycle import decay_tiers, touch
from .seat_memory import compress_seat

__all__ = ["compress_index_view", "doc_metrics", "compress_seat", "decay_tiers", "touch",
           "due_triggers", "load_policy", "update_state"]
