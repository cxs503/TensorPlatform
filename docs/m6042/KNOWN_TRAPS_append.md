# m6042 · KNOWN_TRAPS 追加（供 AGENT_BOOTSTRAP 合并，append-only）

以下三条为 IN50-W3-N07 实测踩到的坑，可直接追加到 `AGENT_BOOTSTRAP.md` 的 KNOWN_TRAPS：

- 2026-11-07 合并门禁「N 个文件被滤除」会让**新增模块丢失而引用方已入库**：主线 `api/tensor_reports.py`
  因此悬空 import、报告端点整段不可用。修完必须补全仓 `services.*` 导入解析判据（m6042 G0）。
- 2026-11-07 仓库 pytest 配置自带 `-q`，脚本里再叠 `-q` 会吞掉汇总行（"N passed" 消失），
  按 stdout 正则取数会静默得到 0；取数一律读 `--junitxml` 的机器可读字段。
- 2026-11-07 证据脚本复用 sqlite 文件库时，上一次失败残留的 `.db` 会让 `CREATE TABLE` 撞
  "already exists"；每次取证前清 `_tmp`，别复用库文件。
- 2026-11-07 同一类资产的存量缺口：`artifacts/m4977_metrics.json`、`artifacts/m5014_*` 在历史提交里存在、当前主线不在（被滤除），导致 `tests/test_report_boundary.py` 5 failed / 2 errors。这是**既有欠账**，与本单无关；清理方式是补资产或把测试改成显式 `WITHHELD` skip，不要靠改断言让绿灯。
