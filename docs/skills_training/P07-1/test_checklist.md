# 测试清单（作者版，上传前提交）：数据集文件上传端点

> 由实现者（同一作者）自拟，代表「只有 happy-path 测试」的真实状态：
> 表中 status 列若为 `planned` 即未写测试；`pass` 表示本仓库 `candidate/test_upload_happy.py` 中确有对应断言。

| ID | 对应 AC | 场景 | 输入 | 期望 | status |
|---|---|---|---|---|---|
| T-01 | AC-1 | 合法 CSV 上传 | 3 行 CSV，`Authorization: Bearer <alice>` | 200；`rows=2`；`bytes`=请求长度 | pass |
| T-02 | AC-2 | 上传后下载回读 | 同上，`GET /download` | 200；body sha256 与上传一致 | pass |
| T-03 | AC-1 | 元数据回读 | `GET /meta` | 200；`owner=="alice"`、`rows` 与上传一致 | pass |
| T-04 | AC-3 | 非属主越权写 | bob 向 alice 的数据集上传 | 403 | planned |
| T-05 | AC-3 | 无 token / 错 token | 缺失或伪造 Bearer | 401 | planned |
| T-06 | AC-3 | 未认证读 | 不带 token 直接 `GET /download` | 401/403 | planned |
| T-07 | AC-4 | 尺寸上限 | 101 MiB CSV | 413，且不落盘 | planned |
| T-08 | AC-5 | 解析失败回滚 | 先传合法 CSV，再传不可解析内容 | 422；原文件字节不变 | planned |
| T-09 | AC-6 | 中断续传 | 首包 50 B + `Content-Range: bytes 0-49/1000` | 202 待续 / 409 冲突，不登记为完整版本 | planned |
| T-10 | AC-7 | 文件名穿越 | `filename="../../p07_escape.csv"` | 400；文件不得写出上传目录 | planned |
| T-11 | AC-8 | 类型伪装 | `.csv` 扩展名 + 二进制内容 | 422（且不产生残留） | planned |
| T-12 | 非功能 | 同数据集并发写 | 两请求同时上传 | 一 200 一 409 | planned |

## 作者对未写测试的说明

> 「T-04~T-12 属于异常路径，框架层已有通用防护，本次先覆盖主流程，异常路径留待集成阶段补。」

该说明是本次评审的**被检验对象**：需要逐条核实「框架层已有通用防护」是否成立。