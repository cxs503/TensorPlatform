# tools/repro_exec_gate — 交付前可复现性执行门

补 `tools/evidence_grade.py` R02「只检 `repro_cmd` 字段存在性」的空隙：**真执行**契约里的
`repro_cmd`（cwd=worktree），硬超时杀进程组，并校验声明的工件事物摘要。

完整契约/枚举/证据/局限见 [`docs/repro_exec_gate.md`](../../docs/repro_exec_gate.md)。

```bash
# 冻结 CLI：0=PASS / 1=FAIL / 2=用法错误；stdout 单行 JSON
python3 -m tools.repro_exec_gate --contract C.json --worktree DIR \
        [--timeout SECONDS] [--json-out PATH]

python3 -m pytest -q tests/test_repro_exec_gate.py   # 验收套件
python3 -m tools.repro_exec_gate.matrix              # 夹具矩阵（JSON+PNG，零手抄）
python3 -m tools.repro_exec_gate.selftest --timeout 600   # 拿本仓自身 repro 命令真跑一次
```

模块：`core.py`（执行+判定，唯一判定路径）/ `report.py`（单行 JSON、封存工件）/
`cli.py`（冻结 CLI）/ `selftest.py`（自证）/ `matrix.py`（报告度量）。

平台铁律：**零领域语义**——域数据只经 `contract.json` 注入；`tests/test_repro_exec_gate.py`
用词表 grep 断言本包源码无域词条。