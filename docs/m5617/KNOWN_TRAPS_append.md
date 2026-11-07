# KNOWN_TRAPS append（P96-N8，append-only：只增不改，单条 ≤120 字）

> 说明：平台权威账本在 `/root/tp-agent/AGENT_BOOTSTRAP.md`（工作树外，智能体只能读不能写）。
> 本文件是**本单的追加记录与证据锚点**，格式与权威账本一致，可直接 append 过去。
> 新增条目只追加在末尾，不删除、不改写历史条目。

## 本单新增（2026-11-07）

- 2026-11-07 合并门禁会静默滤除新文件：交付了 API 改动的分支若新增模块被滤除，主线 import 直接炸；验收必须含「模块存在」断言。
- 2026-11-07 测试放 `tests/` 而 pytest testpaths 只收 `backend/tests` → 用例从未被执行，「全绿」是空绿；新测试一律落 testpaths 内。
- 2026-11-07 纯函数单测测不出「门没接线」；报告链路验收必须经真 HTTP 端点（TestClient）+ 修复前代码 git 原文做红对照。
- 2026-11-07 并发送号要用真线程真库：全局 USER/进程级 FIGURE_DIR 这类共享状态只有并发才暴露，顺序测试永远绿。
- 2026-11-07 sqlite 不认 `CAST(:x AS jsonb)`（JSON 文本变 0）也不回吐 jsonb；测试夹具须做「仅剥 jsonb CAST + 读回归 JSON」的最小兼容层。
- 2026-11-07 裸 `/tmp/report.json` 是并发串号的客户端根因：路径必须含 mission 令牌；lint 对存量欠账登记基线，超基线即红。

## 修复机制（本单实际执行的动作，供后来者复用）

1. **归属门**：`backend/services/report_ownership.py` 提供纯函数 `ownership_verdict`；
   `POST /tensor/reports` 在 INSERT 之前调用，403 + `REPORT_MISSION_NOT_OWNED`
   + `X-Error-Code` 头（客户端 fail-fast）。
2. **配图门**：同模块 `validate_png_payload`（八字节魔数 + IEND 段），
   JPEG/WebP/GIF/BMP/TIFF → 422 `REPORT_FIGURE_NOT_PNG` 且错误体给期望魔数
   `89 50 4E 47 0D 0A 1A 0A`；截断 → `REPORT_FIGURE_TRUNCATED`；**先验后落**，
   拒绝时不留指向缺失图片的报告行。
3. **客户端工装**：`tools/report_payload.py::write_report_payload` 强制文件名含
   mission_id 令牌、`os.link` 独占写、返回自证 sha256/bytes/mission_id 并回读复算；
   `preflight` 提交前现场重算，漂移即 fail-fast。
4. **路径约定 lint**：`tools/audit/report_path_convention_lint.py` 扫
   `/tmp/**/report.json`（无 mission 令牌）；存量 84 处/39 文件登记在
   `config/report_path_baseline.json`，**超基线即红、欠账只减不增**。
5. **防复发**：`backend/tests/` 里七类判据（红→绿、8 线程 0/8、非 PNG 三态、
   正常路径、既有套件全绿、零域词、lint 负控）均可 `pytest` 一键复跑；
   `artifacts/m5617/collect_evidence.py` 把数字写进 `criteria.json`（零手抄）。