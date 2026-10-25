# m311 交接笔记（PL-SEM-3 质量门执行工具）

## 当前状态（2026-10-25）
- 分支 `agent/m311`，基线 main，未推送（owner 合并）。
- 交付：
  - `tools/ops/quality_gate.py`：质量门执行工具（五规则机判 R1-R5）
  - `tests/test_quality_gate.py`：34个单元测试（全过）
  - `docs/quality_gate_integration_plan.md`：集成方案（待 owner 批准接线）
  - `validation_report_m311.json`：机器可读工件

## 核心实现
- **R1 探针存在性**：检查 gates*.json/validation_report*.json 变更时是否有 @mutation 探针
- **R2 参考数据声明**：验证 reference_provenance 块（analytical/experiment+source/independent_impl）
- **R3 物理项路径**：grep 源码中的方程调用路径（非仅配置布尔）
- **R4 基线红记账**：.base_reds 快照缓存 + 新增红=FAIL
- **R5 判决史 append-only**：检查 FAIL 判决未被删除/修改

## 关键路径与坑
- **R3 实现**：用 `grep -r` 而非 `git grep`，因为测试文件可能未提交
- **R4 跳过选项**：`--skip-r4` 用于 CI 环境无 test suite 时
- **异常处理**：工具异常 → `overall=UNDETERMINED`（fail-open），标记人工位
- **PR 评论**：复用 review_bridge 的 GitHub 令牌（GH_PUSH_TOKEN 或 /tmp/gh_token）

## 未做（后续）
- **接线**：expand_follow/benchmark/si_harvest 三线接入质量门（方案已设计，待 owner 批准）
- **R3 物理开关映射**：当前仅覆盖 gravity/two_phase/contact/fracture/turbulence，需扩展
- **R4 性能优化**：套件执行可能慢，需并行化/缓存
- **监控告警**：接线后监控质量门判定分布

## 纪律遵守
- 不改现网 cron/收割行为（只交付工具+测试+方案）
- 规范文件 QUALITY_BAR.md 为权威，工具未发明规范外规则
- validation 工件命名 validation_report_m311.json
- 分支 agent/m311，merge 留 owner
