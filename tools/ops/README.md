# tools/ops — tensor_eco 运营层（仓库即运行时）

5090（LBM 用户）上以 cron 常驻的自动化线全部机制件。**本目录是唯一事实源**：
`/srv/tensor/shared/tensor_eco/agent_work/` 下同名文件为指向本目录的软链；状态、账本、
锁、日志、密钥留在 agent_work，永不入库。

## 拓扑（cron → 脚本 → 状态）

| cron | 脚本 | 状态/锁（agent_work） | 说明 |
|---|---|---|---|
| */10 | si_closure/si_harvest.py | si_closure/state.json, harvest.lock | SI 线终态 mission 收割：url-push→api_push 兜底、SUPPRESSED 抑制表、开 PR |
| */10 | benchmark_closure/benchmark_dispatch.py | benchmark_closure/{backlog,state}.json, dispatch.lock | B 闭环线：串行派发/中继门/网关重派≤3/合格自动 push+PR+报告；`touch OFF` 停机 |
| 5,25,45 | review_bridge/review_bridge.py | review_bridge/state.json, lock | 评审环：五库 PR 评论→回应 mission→代回贴（bridge-reply 标记防自激） |
| */2 | docker_watchdog.sh | docker_watchdog.log | rootless dockerd 看门狗：仅干净死亡才拉起，半死留 operator playbook |
| */2 | （tools/agent/auto_approver.py，另线） | approver_escalated.json | 审批器；停车表解冻=人工删键 |

辅助：`benchmark/gates_check.py`（四门机械复算）、`suite_check.py`（push 前套件门）、
`line_status.py`（线况）、`build_backlog.py`/`update_backlog.py`（账本维护）、
`benchmark/publish_report.py`（代发报告）、`api_push.sh`（Git Data API 推送，
github.com:443 断连时用；大 blob 走临时文件 --data-binary、cwd 须在仓内、
非快进 422 走 force PATCH ref）。

## 密钥契约（不入库）

`agent_work/{operator.env,review.env,github_read.env}`、`/tmp/gh_token`
（api_push.sh 可用 `GH_TOKEN_FILE` 覆写）。cron 行以 `set -a; source …` 注入。

## 范围

TensorLBM `benchmarks/pending/` 全案+WB2 晋级；marine 五案与结冰不在 B 线范围
（见 benchmark/README.md）。SI 目标模板与 goal 文本属派发数据，不入库。
