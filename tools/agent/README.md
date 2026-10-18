# tensor-agent 桥接守护

自主开发智能体守护：从平台 `tensor_dev_missions` 队列领取任务，用 OpenHands SDK
（自部署 DeepSeek 中继）在每任务独立 worktree 中开发，测试提交后提请人工验收。
全程事件写入平台 append-only 会话审计链，公网控制台可实时查看。

## 组成

- `tensor_agent.py` — 守护本体（claim → worktree → SDK 会话 → 预算看护 → 终态回写）
- `run_agent.sh` — flock 单实例包装（`/srv/tensor/shared/tensor_eco/tensor-agent.lock`），
  crontab `@reboot sleep 90` + `*/5` 保活
- `agent.env` — 凭据与环境（600 权限，不入库；含平台账号、LLM 中继 key）

## 任务生命周期

`queued` → claim(SKIP LOCKED，惰性收割心跳 >90s 的任务为 failed/agent_lost) →
`running`（15s 心跳携带 tool_calls/elapsed/head 进度）→ `request_acceptance` 工具
触发 `awaiting_acceptance`（dev_acceptance 审批行）→ ADMIN 批 `succeeded` /
驳 `failed`。owner 可随时 stop（心跳返回 stop 指令，agent 收尾退出）。

LLM 以纯文本提前收尾（常见于端点超时后）时，守护以显式 nudge 消息重入会话，
最多 2 次；仍未提请验收才判 `finished_without_acceptance`。

## 隔离姿态（v1，宿主级）

- **环境白名单重建**：`os.environ.clear()` 后仅保留 PATH/HOME/LLM_* /TENSOR_*，
  `CUDA_VISIBLE_DEVICES=5`（约定唯一可用卡），`GIT_TERMINAL_PROMPT=0`、
  `GIT_CONFIG_GLOBAL=/dev/null`、`GIT_CONFIG_NOSYSTEM=1` —— 宿主密钥与
  docker/ssh 环境对 agent 缺席。
- **git 单向**：worktree `remote.*.pushurl=no-push` + 提交身份 `tensor-agent`；
  分支 `agent/m<id>` 留存，GitHub 推送与 PR 归 owner。
- **网络出口**：fetch_url 域白名单（GitHub/arXiv/PyPI 等）+ 私网 IP 拒绝；
  web_search 走 GitHub API 与 arXiv API。
- **预算**：工具调用与墙钟双上限，超限 `conversation.pause()` 后按
  `budget_exceeded` 终态回写。
- 容器级隔离（独立 namespace/文件系统）为后续加固项，当前为宿主进程级姿态。

## 已知限制

- **共享 venv 写入**：agent 可经 terminal 对 `/srv/tensor/shared/venvs/*` 执行
  `pip install`（冒烟任务曾向 eco venv 装 pytest 并 `pip install -e .`）。
  测试解释器按仓库固定，但 venv 本身未做只读绑定。
- **单任务串行**：每守护进程一次领一任务（模块级 BRIDGE 单例）；flock 保证
  单实例。多任务并发需改多进程架构。
- **daemon 重启恢复**：在飞任务靠心跳收割转 `failed/agent_lost` 后重新排队，
  不做会话级断点续跑（worktree 与已提交内容保留在 `agent/m<id>` 分支）。

## 运维

```bash
# 手动拉起（crontab 每 5 分钟自动保活）
bash tools/agent/run_agent.sh &

# 日志 / 工作区
tail -f /srv/tensor/shared/tensor_eco/agent_work/agent.log
ls  /srv/tensor/shared/tensor_eco/agent_work/m<id>/<Repo>/
```
