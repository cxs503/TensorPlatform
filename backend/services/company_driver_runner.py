"""Portable runner copied beside a company DataDriver project at execution time."""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ENTRY = Path(os.environ['ODF_CONTROL_ENTRY']).resolve()
PROJECT = Path(os.environ.get('ODF_CONTROL_PROJECT_ROOT') or ENTRY.parent).resolve()
CONFIG = json.loads(Path(os.environ['ODF_CONTROL_CONFIG_PATH']).read_text(encoding='utf-8'))
RESULT_DIR = Path(os.environ['ODF_RESULT_DIR'])
RESULT_DIR.mkdir(parents=True, exist_ok=True)
MODE = os.environ.get('ODF_SIMULATION_MODE', 'dataset_replay')


def load_profile():
    basic_path = PROJECT / 'conf' / 'basic.json'
    if not basic_path.is_file():
        raise RuntimeError('实时/回放适配器需要项目提供 conf/basic.json')
    basic = json.loads(basic_path.read_text(encoding='utf-8'))
    entry_name = Path(os.environ['ODF_CONTROL_ENTRY']).name
    # File managers commonly append "(1)" to an uploaded Python file. Treat it
    # as equivalent to the name declared by the project's basic.json.
    canonical_entry_name = re.sub(r"\s*\(\d+\)(?=\.py$)", "", entry_name)
    driver_item = next((
        item for item in basic.get('driver', [])
        if Path(str(item.get('lib_name') or '')).name in {entry_name, canonical_entry_name}
    ), None)
    if not driver_item:
        raise RuntimeError(f'conf/basic.json 未声明入口驱动 {entry_name}')
    conf_dir = basic_path.parent
    base_cfg = json.loads((conf_dir / str(driver_item['basic_config'])).read_text(encoding='utf-8'))
    run_name = driver_item.get('run_config')
    run_cfg = json.loads((conf_dir / str(run_name)).read_text(encoding='utf-8')) if run_name else {}
    return base_cfg, run_cfg


def load_package_mqtt_config():
    """Read the uploaded project's MQTT settings without exposing credentials."""
    candidates = (PROJECT / 'conf' / 'mqtt.json', PROJECT / 'bin' / 'mqtt.json')
    for path in candidates:
        if not path.is_file():
            continue
        try:
            config = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'MQTT 配置文件无法读取：{path.name}（{exc}）') from exc
        if not isinstance(config, dict):
            raise RuntimeError(f'MQTT 配置文件格式不正确：{path.name}')
        return config, path
    raise RuntimeError('算法包未找到 mqtt.json；请使用数据集回放，或补充 conf/mqtt.json')


def import_driver():
    # Legacy projects often omit the empty logs directory from the archive while
    # their logging configuration writes to ../logs relative to bin.
    (PROJECT / 'logs').mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('_foundation_uploaded_driver', ENTRY)
    if not spec or not spec.loader:
        raise RuntimeError('无法加载控制驱动源码')
    module = importlib.util.module_from_spec(spec)
    for item in (ENTRY.parent, PROJECT / 'bin', PROJECT):
        item_text = str(item)
        if item_text not in sys.path:
            sys.path.insert(0, item_text)
    spec.loader.exec_module(module)
    if not hasattr(module, 'DataDriver'):
        raise RuntimeError('控制入口必须定义 DataDriver 类')
    return module, module.DataDriver


def normalise_payload(payload):
    if not isinstance(payload, dict):
        return {}
    for key in ('dcsdata', 'data', 'values', 'payload'):
        if isinstance(payload.get(key), dict):
            return payload[key]
    # Common gateway payloads supported by the private control-algorithm SDK.
    if isinstance(payload.get('Pt'), dict) and isinstance(payload['Pt'].get('Data'), dict):
        return payload['Pt']['Data']
    if isinstance(payload.get('d'), dict):
        for value in payload['d'].values():
            if isinstance(value, dict) and isinstance(value.get('Val'), dict):
                return value['Val']
    return payload


def _undeclared_source_tags(declared_tags):
    """Find literal self.m_tag['name'] references omitted from a legacy config.

    These are internal driver states, not dataset columns.  They are reported in
    the run summary so an incomplete algorithm contract remains visible.
    """
    try:
        tree = ast.parse(ENTRY.read_text(encoding='utf-8', errors='ignore'))
    except (OSError, SyntaxError):
        return set()
    tags = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        value = node.value
        if not (isinstance(value, ast.Attribute) and value.attr == 'm_tag'):
            continue
        base = value.value
        if not (isinstance(base, ast.Name) and base.id == 'self'):
            continue
        key = node.slice
        # Python 3.8 keeps subscription values inside ast.Index, while 3.9+
        # exposes the value directly.
        index_type = getattr(ast, 'Index', None)
        if index_type is not None and isinstance(key, index_type):
            key = key.value
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            tags.add(key.value)
    return tags - set(declared_tags)


def initialise():
    base_cfg, run_cfg = load_profile()
    module, cls = import_driver()
    driver = cls()
    data_in = base_cfg.get('data_in') or {}
    data_out = base_cfg.get('data_out') or {}
    tags = dict(base_cfg.get('basic') or {})
    for alias in data_in.values():
        tags.setdefault(str(alias), 0.0)
    for alias in data_out.values():
        tags.setdefault(str(alias), 0.0)
    for alias in ('fan_out', 'last_fan_out', 'last_n2_out', 'last_n4_out', 'last_xdkwc_n2_out', 'last_ddkwc_n4_out', 'v_jj', 'v_offset', 'volume'):
        tags.setdefault(alias, 0.0)
    implicit_tags = sorted(_undeclared_source_tags(tags))
    for alias in implicit_tags:
        tags.setdefault(alias, 0.0)
    # The platform invokes the business driver directly rather than starting its
    # daemon. Populate the same data structures as DataDriverBase.set_basic_config,
    # but do not let the SDK create another MQTT client.
    driver.m_config = base_cfg
    driver.m_data_in = dict(data_in)
    driver.m_data_out = dict(data_out)
    driver.m_current_data = {str(alias): None for alias in data_in.values()}
    driver.m_tag = tags
    driver.m_run_config = run_cfg
    driver._foundation_implicit_tags = implicit_tags
    init_basic = getattr(driver, 'init_basic_config', None)
    if callable(init_basic):
        init_basic()
    update_run = getattr(driver, 'update_run_config', None)
    if callable(update_run):
        update_run()
    return module, driver, data_in, data_out, float((base_cfg.get('basic') or {}).get('t0') or 5.0)


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, float) and math.isnan(value)


def _parse_time(value):
    if _is_missing(value):
        return None
    text = str(value).strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for pattern in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def apply_values(driver, data_in, values):
    for external, alias in data_in.items():
        value = values.get(external, values.get(alias))
        if _is_missing(value):
            continue
        try:
            driver.m_tag[alias] = float(value)
            driver.m_current_data[alias] = float(value)
        except (TypeError, ValueError):
            driver.m_tag[alias] = value
            driver.m_current_data[alias] = value


def output_values(driver, data_out):
    return {alias: driver.m_tag.get(alias) for alias in data_out.values()}


def _realtime_duration():
    return max(5, min(int((CONFIG.get('realtime') or {}).get('duration_seconds') or 60), 1500))


def _write_realtime_summary(**values):
    values.setdefault('mode', 'realtime_stream')
    (RESULT_DIR / 'summary.json').write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')


def replay():
    module, driver, data_in, data_out, default_step = initialise()
    data_path = Path(os.environ['ODF_DATASET_PATH'])
    if not data_path.is_file():
        raise RuntimeError('未找到用于回放的数据集文件')
    with data_path.open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        rows_in = list(reader)
        columns = reader.fieldnames or []
    if not rows_in:
        raise RuntimeError('回放数据集为空')
    time_column = str(CONFIG.get('time_column') or '')
    if time_column and time_column not in columns:
        raise RuntimeError(f'时间列不存在：{time_column}')
    missing_inputs = [external for external, alias in data_in.items() if external not in columns and alias not in columns]
    if missing_inputs:
        raise RuntimeError('数据集缺少算法配置的输入列：' + '、'.join(missing_inputs[:12]))
    virtual_now = [time.time()]
    def now():
        return virtual_now[0]
    module.time.time = now
    rows = []
    previous_ts = None
    for index, row in enumerate(rows_in):
        step = default_step
        if time_column:
            ts = _parse_time(row.get(time_column))
            if ts is not None and previous_ts is not None:
                step = max(0.0, min(float((ts - previous_ts).total_seconds()), 3600.0))
            if ts is not None:
                previous_ts = ts
        virtual_now[0] += step
        apply_values(driver, data_in, row)
        driver.calc_core()
        rows.append({'row_index': int(index), 'time': str(row.get(time_column, '')) if time_column else index, **output_values(driver, data_out)})
    output_columns = ['row_index', 'time', *data_out.values()]
    with (RESULT_DIR / 'control_replay_outputs.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=output_columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    (RESULT_DIR / 'summary.json').write_text(json.dumps({
        'mode': 'dataset_replay', 'rows': len(rows), 'input_fields': len(data_in),
        'output_fields': len(data_out), 'default_step_seconds': default_step,
        'implicit_internal_tags': getattr(driver, '_foundation_implicit_tags', []),
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def realtime_zmq():
    try:
        import zmq
    except ImportError as exc:
        raise RuntimeError('实时 ZMQ 接入需要运行环境安装 pyzmq') from exc
    module, driver, data_in, data_out, _ = initialise()
    realtime_cfg = CONFIG.get('realtime') or {}
    endpoint = str(realtime_cfg.get('input_endpoint') or '').strip()
    topic = str(realtime_cfg.get('input_topic') or '')
    duration = _realtime_duration()
    if not endpoint.startswith('tcp://'):
        raise RuntimeError('实时输入地址必须以 tcp:// 开头')
    context = zmq.Context.instance()
    subscriber = context.socket(zmq.SUB)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)
    subscriber.connect(endpoint)
    publisher = None
    output_endpoint = str(realtime_cfg.get('output_endpoint') or '').strip()
    if realtime_cfg.get('publish_outputs'):
        if not output_endpoint.startswith('tcp://'):
            raise RuntimeError('启用输出发布时必须填写 tcp:// 输出地址')
        publisher = context.socket(zmq.PUB)
        publisher.connect(output_endpoint)
    poller = zmq.Poller()
    poller.register(subscriber, zmq.POLLIN)
    started = time.monotonic()
    count = 0
    with (RESULT_DIR / 'realtime_outputs.jsonl').open('w', encoding='utf-8') as stream:
        while time.monotonic() - started < duration:
            if subscriber not in dict(poller.poll(1000)):
                continue
            raw = subscriber.recv()
            try:
                payload = json.loads(raw.decode('utf-8'))
            except Exception:
                continue
            apply_values(driver, data_in, normalise_payload(payload))
            driver.calc_core()
            outputs = output_values(driver, data_out)
            stream.write(json.dumps({'received_at': time.time(), 'outputs': outputs}, ensure_ascii=False) + '\n')
            stream.flush()
            count += 1
            if publisher:
                publisher.send_json({'topic': topic or 'control', 'data': outputs})
    subscriber.close(0)
    if publisher:
        publisher.close(0)
    _write_realtime_summary(source='custom_zmq', received_messages=count, input_endpoint=endpoint, output_published=bool(publisher))


def realtime_mqtt():
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError('实时 MQTT 接入需要运行环境安装 paho-mqtt') from exc
    mqtt_cfg, mqtt_path = load_package_mqtt_config()
    host = str(mqtt_cfg.get('host') or '').strip()
    try:
        port = int(mqtt_cfg.get('port') or 1883)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('mqtt.json 中 port 必须是有效端口号') from exc
    topics = mqtt_cfg.get('subTopic') or mqtt_cfg.get('sub_topics') or []
    if isinstance(topics, str):
        topics = [topics]
    topics = [str(item).strip() for item in topics if str(item).strip()]
    if not host or not topics:
        raise RuntimeError('mqtt.json 必须配置 host 与至少一个 subTopic 订阅主题')

    _, driver, data_in, data_out, _ = initialise()
    duration = _realtime_duration()
    received_messages = 0
    accepted_messages = 0
    connected = False
    connection_error = []
    message_queue = []

    def on_connect(client, userdata, flags, rc, properties=None):
        nonlocal connected
        if rc != 0:
            connection_error.append(f'MQTT 连接被服务器拒绝（返回码 {rc}）')
            return
        connected = True
        for topic in topics:
            client.subscribe(topic, qos=0)

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode('utf-8'))
            values = normalise_payload(payload)
            if values:
                message_queue.append(values)
        except Exception:
            # A malformed one-off packet must not terminate a bounded run.
            return

    prefix = ''.join(ch for ch in str(mqtt_cfg.get('clientIdPrefix') or 'foundation') if ch.isalnum() or ch in '-_')[:12] or 'foundation'
    client = mqtt.Client(client_id=f'{prefix}-platform-{int(time.time() * 1000) % 100000000}', protocol=mqtt.MQTTv311)
    username = mqtt_cfg.get('username')
    password = mqtt_cfg.get('pwd', mqtt_cfg.get('password'))
    if username:
        client.username_pw_set(str(username), None if password is None else str(password))
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(host, port, keepalive=30)
        client.loop_start()
        wait_started = time.monotonic()
        while not connected and not connection_error and time.monotonic() - wait_started < 10:
            time.sleep(0.1)
        if connection_error:
            raise RuntimeError(connection_error[0])
        if not connected:
            raise RuntimeError('MQTT 连接超时：10 秒内未建立订阅连接')
        started = time.monotonic()
        with (RESULT_DIR / 'realtime_outputs.jsonl').open('w', encoding='utf-8') as stream:
            while time.monotonic() - started < duration:
                if not message_queue:
                    time.sleep(0.05)
                    continue
                values = message_queue.pop(0)
                received_messages += 1
                matched = sum(1 for external, alias in data_in.items() if external in values or alias in values)
                if not matched:
                    continue
                apply_values(driver, data_in, values)
                driver.calc_core()
                outputs = output_values(driver, data_out)
                stream.write(json.dumps({'received_at': time.time(), 'outputs': outputs}, ensure_ascii=False) + '\n')
                stream.flush()
                accepted_messages += 1
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
    _write_realtime_summary(
        source='package_mqtt', mqtt_config=str(mqtt_path.relative_to(PROJECT)),
        subscribed_topics=topics, received_messages=received_messages,
        accepted_messages=accepted_messages, output_published=False,
    )


def realtime():
    transport = str((CONFIG.get('realtime') or {}).get('transport') or 'mqtt').strip().lower()
    if transport in {'mqtt', 'package_mqtt'}:
        return realtime_mqtt()
    if transport == 'zmq':
        return realtime_zmq()
    raise RuntimeError(f'不支持的实时接入协议：{transport}')


if MODE == 'realtime_stream':
    realtime()
elif MODE == 'dataset_replay':
    replay()
else:
    raise RuntimeError(f'公司驱动适配器不支持运行方式：{MODE}')
