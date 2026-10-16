# 工业数据回放器

这是一个完全基于 Python 标准库的离线验证工具，用于把小型合成、脱敏或历史场景数据按时间顺序发送到测试消费者。它不访问平台数据库，不管理容器，也不要求安装 Kafka、MQTT 或其他依赖。

## 输入协议

输入文件可以是 JSONL/NDJSON 或 CSV。每行是一条工业遥测记录，默认必须包含 `timestamp`；可用 `--timestamp-field` 指定其他字段。时间戳支持 Unix 秒或带时区的 ISO 8601，输入必须按时间升序排列。

```json
{"timestamp":"2026-01-01T00:00:00Z","device_id":"sim-01","temperature":810.2,"pressure":-12.4}
{"timestamp":"2026-01-01T00:00:01Z","device_id":"sim-01","temperature":810.5,"pressure":-12.3}
```

CSV 字段保持字符串，回放器不会擅自改变标签精度或设备编号。场景包自己的 schema 负责类型解释。每条输出采用稳定信封：

```json
{
  "replay": {
    "run_id": "sensor-baseline-v1-run-001",
    "sequence": 1,
    "duplicate_number": 0,
    "scheduled_offset_ms": 0,
    "source_timestamp": 1767225600.0,
    "faults": []
  },
  "data": {"timestamp":"2026-01-01T00:00:00Z","device_id":"sim-01","temperature":810.2}
}
```

固定输入、固定 Run ID 和固定故障计划会得到相同的输出顺序、数据内容和故障标记。实际发送墙钟时间不进入信封。

## 使用

先进行零副作用校验：

```bash
python3 tools/industrial_replay/replay.py scenario.jsonl \
  --run-id sensor-baseline-v1-run-001 --dry-run
```

无等待输出到文件：

```bash
python3 tools/industrial_replay/replay.py scenario.csv \
  --timestamp-field event_time \
  --run-id load-forecast-v1-run-001 \
  --speed none --sink file:/tmp/load-replay.jsonl
```

按原始时间的 10 倍速发送到测试 HTTP 接口：

```bash
python3 tools/industrial_replay/replay.py scenario.jsonl \
  --run-id edge-http-v1-run-001 --speed 10 \
  --allow-network-sink \
  --sink http://127.0.0.1:19090/telemetry \
  --header 'Authorization:Bearer TEST_TOKEN'
```

速度支持 `1`、`10`、`100`、`0`/`none`。默认无等待，避免误用长时间数据阻塞 CLI。默认最多读取 100000 条，可用 `--max-events` 收紧；只有明确需要时才用 `--max-events 0` 关闭保护。

## 确定性故障注入

可使用 [fault-plan.example.json](fault-plan.example.json)，也可通过 CLI 覆盖：

```bash
python3 tools/industrial_replay/replay.py scenario.jsonl \
  --run-id fault-gate-v1-run-001 --speed 100 --sink stdout \
  --disconnect 41:45 \
  --duplicate-every 20 --duplicate-copies 1 \
  --reorder-window 3 \
  --latency-ms 500 --latency-every 10 \
  --freeze temperature:60:75
```

- `disconnect`：指定序号窗口内的数据被丢弃，用于验证断线和缺口处理。
- `duplicate`：每 N 条增加指定份数，副本带 `duplicate_number` 和故障标记。
- `reorder`：每个固定窗口逆序发送，同时保留递增的发送时间槽。
- `latency`：每 N 条额外增加固定发送延迟。
- `freeze`：在窗口内保持为开始前最后一个传感器值；若没有前值则校验失败。

序号从 1 开始。故障注入不修改原始文件。

## 输出端

- `stdout`：默认 JSONL 标准输出。
- `file:PATH`：覆盖写入 JSONL 文件，并自动创建父目录。
- `http://` / `https://`：逐条 POST，支持超时、有限重试和自定义请求头。
- `kafka://broker:9092/topic`：仅当当前解释器已经有 `kafka-python` 时启用。
- `mqtt://host:1883/topic`：仅当当前解释器已经有 `paho-mqtt` 时启用。

Kafka/MQTT 是可选适配器。库不存在时工具会给出明确错误，不会自动安装依赖。

文件输出仅允许 `/tmp`、`/srv/tensor/operator-b/foundation-validation-runs` 或
`ODF_VALIDATION_ALLOWED_ROOTS` 审核过的根目录。所有网络输出默认关闭；必须显式传入
`--allow-network-sink`，且默认只允许 localhost。额外测试主机需用 `--allow-host` 逐项加入，
生产地址不得进入白名单。

## 测试

```bash
python3 -m unittest discover -s tools/industrial_replay/tests -v
```

测试只使用临时文件、内存输出和本机随机端口 HTTP 服务，不访问生产数据或现有平台进程。
