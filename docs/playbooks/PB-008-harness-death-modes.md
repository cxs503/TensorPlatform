# PB-008 Harness 两类败单死因（跨全部模型）

**两类死因（7 天 112 败单全属之）**：
1. **证据门 gitignore 全盲**：只信 `git status --porcelain` → runs/、artifacts/ 真产物被误杀
   （m425 实证）。且 worktree 检出使全文件 mtime=检出时刻——增量必须用**起始基线快照差集**，不能用新 mtime。
2. **裸死**：call_llm 零重试（一次网络抖动→break）+ messages 无界增长（上下文溢出）+ 预算/墙钟尽无强制收尾
   （近成功单被判 failed）。

**修复（harness=20261030.1）**：LLM 3 次退避重试（仅瞬态）/ 280k 字符压缩（保头尾 12 条）/
强制收尾轮（近成功转诚实 finish）/ 证据=git status ∪ (--ignored 终态 − 起始基线)。

**已知缺口**：agent_lost 等外部收割路径尚不写 result/fail_stage——败单归因仍不完整，修复中。
