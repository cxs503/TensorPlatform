| 探针 | 契约项 | 观测 | 状态 | 严重度 |
|---|---|---|---|---|
| P01 | AC-4 | HTTP 200，落盘 209715200 bytes（209715200 bytes 已发送） | CONFIRMED_DEFECT | high |
| P02 | AC-3 | HTTP 200，文件已被 bob 覆盖（sha 2a2b86e74ffd → b37573c84e63） | CONFIRMED_DEFECT | critical |
| P03 | AC-3 | HTTP 200，匿名拿到 16 bytes | CONFIRMED_DEFECT | high |
| P04 | AC-3 | 401 / 401 | OK | - |
| P05 | AC-7 | HTTP 200，写出到 /tmp/p07_escape.csv（exists=True） | CONFIRMED_DEFECT | critical |
| P06 | AC-5 | HTTP 422，文件变为 b'\x00\x01\x02\xff'（V1 sha 2a2b86e74ffd → 3d1f57c98497），meta.rows=2 | CONFIRMED_DEFECT | critical |
| P07 | AC-6 | HTTP 200，8/1000 bytes 被登记为完整版本（rows=1, bytes=8） | CONFIRMED_DEFECT | high |
| P08 | AC-6 | 断开于 5242880 bytes（声明 41943040）；原文件不变=True；meta.rows=2；残留=['abort.csv']；服务端无续传状态字段/接口 | CONFIRMED_DEFECT | medium |
| P09 | AC-8 | HTTP 422，残留文件 exists=True，meta.path=None | CONFIRMED_DEFECT | medium |
| P10 | 非功能 | 两个请求返回 [200, 200]；读者观测到 3 个不同中间态 | CONFIRMED_DEFECT | high |

| 指标 | 数值 |
|---|---|
| 探针总数 | 10 |
| 已确认缺陷 | 9 |
| 按契约工作 | 1 |
| 未测/unknown | 0 |
| 被违反的契约项 | 6/7 |
| 严重度分布（缺陷） | critical=3、high=4、medium=2 |
| 作者测试套件 | 3 项，全部 happy path（负例断言 0） |
