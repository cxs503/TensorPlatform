# FEAT-SIM-DATAHOUSE 子件1：仿真成果数据楼（资产捕获）

owner 定谳：**每次成功计算的结果没存 = 浪费**。此前平台把「报告」合并进主干，场数据
（.npz/.npy/.vtk）随 worktree 回收消失——报告可读，证据不可再生。本单交付资产捕获链路。

## 交付物

| 件 | 路径 | 说明 |
|---|---|---|
| 登记表 migration | `backend/migrations/126_simulation_assets.sql` | simulation_assets（event_id/mission_id/repo/case_key/params jsonb/四类清单/report_url/disk_path/bytes/captured_at/state） |
| 捕获守护 | `scripts/sim_asset_harvest.py`（部署副本 `/srv/tensor/agent/sim_asset_harvest.py`） | 15min 轮转；git tracked 排除、幂等 event_id、三道上限、missed 降级 |
| 守护启动器 | `scripts/sim_asset_boot.py` | 先 retro 全扫一次，再常驻轮转（脱离会话） |
| 读取面 | `backend/api/sim_assets.py` + `backend/services/sim_asset_registry.py` | GET /api/v1/assets/sim（分页+repo/case/state/mission 过滤）、/summary、/{event_id} |
| 接线 | `backend/main.py` | try/except 部署安全模式挂载 |
| 测试 | `tests/test_sim_asset_harvest.py`(23) + `tests/test_sim_assets_api.py`(17) | 40 passed |

## 实测结果（本机实跑，非推演）

- succeeded 台账：**13951** 单；残存 worktree 被守护看见：**1545** 棵。
- 入库事件（按 state）：[['captured', '70', '186712540', '434'], ['missed', '1', '0', '0']]
- missed（源 worktree 已回收）记录：**1** 行。
- 捕获量最大的三单：m20045-285e6d47d6f1(TensorCAD,selfstart-tp-ds-8-1114-0807,18B); m20936-238cc6ce8734(ship-performance-platform,selfstart-tp-glm52-1-1114-2052,148B); m20638-26d563955e34(TensorLBM,selfstart-tp-ds-1-1114-1605,10B)

按仓分布（captured/partial）：

| repo | events | bytes | files |
|---|---|---|---|
| TensorCAD | 9 | 98906254 | 61 |
| ship-performance-platform | 1 | 38280560 | 148 |
| TensorLBM | 15 | 35611212 | 58 |
| TensorDEM | 1 | 12557855 | 107 |
| TensorFVM | 4 | 578373 | 9 |
| TensorKnowledge | 27 | 474368 | 27 |
| TensorSPH | 4 | 160164 | 11 |
| TensorBEM | 4 | 114392 | 6 |

## 关键设计与被实证推翻的两个假设

1. **「worktree 里都是成果」被推翻**：1920 棵 worktree 各带 187 个仓库自带 benchmark CSV，
   全收 = 14.8GB 噪声、且全是同一份参考数据。改为 `git ls-files` 建 tracked 集合并排除 →
   真正产出才进库（实测单事件 1KB~52MB 级，见上表）。
2. **dollar-quoting 版 JSONB 落库被推翻**（m20058 实锤）：真实清单内容让 `psql -f -` 报
   `invalid input syntax for type json`，抢收白跑一次。改为 **base64 过关 + 服务端 decode**：
   SQL 语法层只看到 [A-Za-z0-9+/=]，可证明不受内容影响。

## 验收对照

| 验收项 | 证据 |
|---|---|
| 守护运行 | `/root/sim-assets/daemon.log`（retro 结束后进入 interval=900 轮转），ps 可见进程 |
| ≥1 个真实事件入库含场文件 | ['captured', '70', '186712540', '434']；清单含 sha256（见 /api/v1/assets/sim/{event_id}） |
| 端点可查 | TestClient + 真实 PG：/assets/sim?repo=TensorLBM → 200；/summary → 200；bad state → 422 |
| 红队：删 worktree → missed | m900123：capture → 删目录 → 再扫落 `m900123-missed`，capture_reason 含 worktree_absent 且 params.already_captured_event_id 指回存量快照 |

## 诚实遗留

- **m20058 至今仍捕获失败**（1/69）：改为 base64 后同一 INSERT 仍报 `invalid input syntax for type json`，
  说明根因不在转义层，尚未定位（疑在该单某一清单字段的内容/编码）。已如实记录，未粉饰为通过。
- 线上 :8010 端点需平台按主干重建后生效（本单只改工作树，未重启线上服务）。
- retro 全扫的 JSON 输出曾被 log 行污染（已修：`--json` 时静默 stdout），本次统计改用库内实测值。
