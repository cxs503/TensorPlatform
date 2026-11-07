# 失败判例（postmortem 自动注入）

> 仓内镜像：注入块由 `tools/ops/build_in59_a4_report_artifacts.py` 从库内已定稿规则渲染；
> 目标 bootstrap 为工作树外文件，写穿由 owner/协调者执行（命令见节内）。

## 失败判例（postmortem 自动注入，append-only）

> 机器生成：`python3 -m tools.failure_postmortems publish --target /root/tp-agent/AGENT_BOOTSTRAP.md`
> 定稿 postmortem 规则，单条 ≤120 字符，append-only（按 rule_id 幂等去重）。

```text
- PM-1046-R1 当单内工具调用已超 60 次仍无 finish 迹象时，先落最小可验收工件（一个测试+一份结果 JSON）并 finish，勿继续加码实现。
- PM-1066-R1 当首轮即 llm_error 且 tool_calls=0 时，先做一次最小模型/端点探活并换端点或降复杂度重派，勿原样重跑同一单。
- PM-1445-R1 当开工首步仓库路径/git 解析失败（仓库不存在、not a valid object name、443 超时）时，先报 setup 类 FAIL 并核对路径与基线 ref，勿进入实现环。
- PM-1938-R1 当交付门因工作树零改动拒绝时，先写 honest FAIL 并列出已读证据，勿用只读侦察冒充交付。
- PM-4106-R1 当 worktree 建立报错（锁冲突/ref 缺失）时，先串行化 worktree 创建并显式指定可解析基线，勿并行抢 .git 配置。
```
