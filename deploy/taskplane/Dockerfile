# 任务平面镜像（PR-9 · AOG P1）
# 17 项守护全部 stdlib 实现（零 pip 依赖），只需系统工具：
#   git psql（worktree/账本线）· procps（探活）· openssh-client（集群桥）
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git postgresql-client procps openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 守护脚本与编制表经 bind mount 挂到 /root/tp-agent（见 compose），
# 镜像自身只装运行环境——代码以库内 ops/taskplane/ 为源。
WORKDIR /root/tp-agent

CMD ["python3", "/root/tp-agent/supervise.py"]
