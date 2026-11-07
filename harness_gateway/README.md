# harness_gateway —— harness 层门控仓（IN27-H2）

把 `/root/tp-agent` 的 harness 层（dispatcher / worker / daemons）变成**可 mission 化的门控仓**：
镜像入库 + 三道门 + 部署管线，使协调官诊断单**可被执行**而非只报告。

信任基座：从「整层归协调者」收缩到「只剩门本身」。

## 三道门

| 门 | 判据 | 实现 |
|---|---|---|
| 1. py_compile | 编译通过 + AST 无重复顶层 def/class + 无明文凭据 | `gates/compile_gate.py` |
| 2. 金丝雀单进程试跑 | `rc ∈ expect_exit` / 无 traceback / 超时内退出 / exec 单元必须打到 shim / 沙箱自检 fail-closed | `gates/canary_gate.py` |
| 3. git 回滚 | 任何门 FAIL → `git checkout` + 字节回写 → 逐文件 sha256 比对；先跑自身 drill | `gates/rollback_gate.py` |

## 唯一合法改法

```
patch(file, old, new) → preflight → checkpoint(+drill) → apply → gate1 → gate2 → commit → deploy(dry-run)
```

## 常用命令

```bash
python3 -m harness_gateway mirror --src /root/tp-agent --mirror harness_gateway/mirror   # 源→镜像（脱敏）
python3 -m harness_gateway verify                                                       # 漂移检测
python3 -m harness_gateway gate compile|canary                                          # 单跑一道门
python3 -m harness_gateway run-patch --patch harness_gateway/patches/0001-*.json --print-verdict
python3 -m harness_gateway diagnose --order harness_gateway/diagnostics/orders/IN27-H2-D1.json
python3 -m harness_gateway deploy --target /root/tp-agent                               # 部署计划（默认 dry-run）
python3 -m harness_gateway.experiments.timeout_bound                                    # psql 超时上限实验
python3 -m harness_gateway.experiments.summarize                                        # 证据汇总
python3 -m harness_gateway.experiments.make_figures                                     # 出图
python3 -m pytest tests/harness_gateway -q
```

## 纪律

- **镜像必须无明文凭据**（`redact.py`；`env` 兜底值也算），含占位符的单元 `blocked_redaction` 不可直投生产。
- **不降级地跑**：源码写死 `PATH`/绝对工具路径 → 该单元降级为符号探针并落 `degraded` 理由，绝不硬跑。
- **补丁必须提交**：未提交的补丁会被下一次 preflight 判 `MIRROR_TAMPER`（迫使改动留痕）。
- 完整报告：`docs/IN27_H2_harness_gated_repo.md`；机器证据：`artifacts/harness_gateway/MISSION_EVIDENCE.json`。