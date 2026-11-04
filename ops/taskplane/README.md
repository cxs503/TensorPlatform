# 任务平面（Task Plane）——舰队守护的库内定义

PR-9（AOG P1）。P0 架构审计（报告 #3554）定谳：任务平面（舰队/守护/PR
自动化）全部宿主 cron/nohup 临时化——守护源码在库外的 `/root/tp-agent`，
cron 行/凭据/编制表全在库外，**宿主重装 = 舰队失忆**。本目录把任务平面
收进库：脚本 + 编制表 + manifest + 监护入口 + compose 定义。

## 布局

| 文件 | 角色 |
|---|---|
| `taskplane.manifest.yaml` | 守护清单单一事实源（增删守护改这里） |
| `supervise.py` | 统一监护：按 manifest 拉起 + 30s 巡检补拉（只补缺员不杀存活） |
| `*.py / *.sh / fleet.json` | 18 项守护脚本 + tp_worker 舰队编制表（生产实况快照收库） |
| `deploy/taskplane/`（库根） | Dockerfile + compose + env 模板 |

## 与生产实况的对账（2026-11-04 收库时点）

- 16 项 enabled：manifest 与生产 /proc 探针实况一一对应（相对路径启动的
  tp_pusher/selfstarter 亦覆盖——绝对路径 ps 会漏检）。
- **auto_approver**：生产仍在跑，但 owner 2026-10-31 自主可控宪章裁定
  「auto_approver 默认停」——库内定义按宪章门控（`AUTO_APPROVER_ON=1`
  才拉起）。**分歧待 owner 收敛**（停生产实例 or 宪章改判）。
- **eco_patrol**：生产已停（治理面被 merge_governor patrol 吸收 7 仓），
  库内保留脚本门控（`ECO_PATROL_ON=1`）。

## 净化披露（相对生产副本的差异）

生产 `/root/tp-agent` 副本含三处硬编码凭据，收库版已全部改为运行时注入
（env / 凭据文件），其余与生产逐字节一致：

| 文件 | 生产问题 | 库内行为 |
|---|---|---|
| `tp_pusher.py` | `PGPASSWORD` 字面量 | `TP_PG_PASSWORD` 环境变量 |
| `guide_tracker.py` / `credit_ledger.py` / `wave_cycle.py` | `TP_PG_PASSWORD` 默认值泄漏同一密码 | 空默认 |
| `eco_patrol.py` | 平台账密字面量 | 环境变量 / `eco_admin.env` 兜底 |

> 以上密码已因出现在生产明文副本中视为暴露，轮换建议已呈 owner。

## 切换序（cutover 属生产变更，须 owner 批后执行）

见 `deploy/taskplane/docker-compose.taskplane.yml` 头注：先停宿主侧同名
进程 → 起 compose → 观察一个巡更周期 → 撤宿主 crontab/nohup。

## CI 门禁

`backend/tests/test_taskplane_infra.py`：三方一致（manifest =
supervise.py = 固定清单）/ 脚本存在 / 语法 / 净化扫描（能红：曾抓到
三处字面量凭据）/ compose 结构 / env 覆盖。
