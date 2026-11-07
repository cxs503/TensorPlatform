"""
回归测试：重试导致重复处理事故

测试目标：验证修复后的任务处理逻辑能够正确处理重试和并发场景，
确保同一任务不会被重复处理。

测试场景：
1. 正常处理：任务成功处理一次
2. 重试去重：同一任务被投递两次，只处理一次
3. 并发去重：多个 worker 同时收到同一任务，只处理一次
4. 失败重试：任务失败后重试，最终成功处理一次
"""

import pytest
import json
import time
import threading
from unittest.mock import Mock, MagicMock, patch
from collections import defaultdict


class MockDatabase:
    """模拟数据库"""
    def __init__(self):
        self.records = {}
        self.insert_count = defaultdict(int)
        
    def execute(self, query, params):
        """模拟 SQL 执行"""
        if "ON CONFLICT" in query:
            # 幂等性写入
            task_id = params[0]
            if task_id not in self.records:
                self.records[task_id] = params[1]
                self.insert_count[task_id] += 1
        else:
            # 普通写入（可能重复）
            task_id = params[0]
            self.records[task_id] = params[1]
            self.insert_count[task_id] += 1


class MockChannel:
    """模拟 RabbitMQ channel"""
    def __init__(self):
        self.ack_count = 0
        self.ack_tasks = []
        
    def basic_ack(self, delivery_tag):
        self.ack_count += 1
        self.ack_tasks.append(delivery_tag)


class TestRetryDeduplication:
    """重试去重测试套件"""
    
    @pytest.fixture
    def mock_db(self):
        return MockDatabase()
    
    @pytest.fixture
    def mock_channel(self):
        return MockChannel()
    
    def test_normal_processing(self, mock_db, mock_channel):
        """测试 1：正常处理 - 任务成功处理一次"""
        from src.worker.task_processor import TaskProcessor
        
        processor = TaskProcessor("test_queue", mock_db)
        
        # 构造任务
        task = {"id": "TASK001", "params": {"L": 150, "B": 20, "T": 8}}
        body = json.dumps(task).encode()
        
        # 处理消息
        processor.process_message(
            ch=mock_channel,
            method=Mock(delivery_tag=1),
            properties=Mock(),
            body=body
        )
        
        # 验证
        assert mock_channel.ack_count == 1, "应该 ACK 一次"
        assert "TASK001" in mock_db.records, "结果应该保存"
        assert mock_db.insert_count["TASK001"] == 1, "应该只插入一次"
    
    def test_retry_deduplication(self, mock_db, mock_channel):
        """测试 2：重试去重 - 同一任务被投递两次，只处理一次"""
        from src.worker.task_processor import TaskProcessor
        
        processor = TaskProcessor("test_queue", mock_db)
        
        # 构造任务
        task = {"id": "TASK002", "params": {"L": 150, "B": 20, "T": 8}}
        body = json.dumps(task).encode()
        
        # 第一次处理
        processor.process_message(
            ch=mock_channel,
            method=Mock(delivery_tag=1),
            properties=Mock(),
            body=body
        )
        
        # 第二次处理（模拟重试）
        processor.process_message(
            ch=mock_channel,
            method=Mock(delivery_tag=2),
            properties=Mock(),
            body=body
        )
        
        # 验证
        assert mock_channel.ack_count == 2, "应该 ACK 两次（每次都 ACK）"
        assert "TASK002" in mock_db.records, "结果应该保存"
        assert mock_db.insert_count["TASK002"] == 1, "应该只插入一次（去重）"
    
    def test_concurrent_deduplication(self, mock_db):
        """测试 3：并发去重 - 多个 worker 同时收到同一任务"""
        from src.worker.task_processor import TaskProcessor
        
        # 创建两个 processor（模拟两个 worker）
        processor1 = TaskProcessor("test_queue", mock_db)
        processor2 = TaskProcessor("test_queue", mock_db)
        
        # 共享状态（模拟同一进程内的多个线程）
        processor2.processing_tasks = processor1.processing_tasks
        processor2.lock = processor1.lock
        
        channel1 = MockChannel()
        channel2 = MockChannel()
        
        # 构造任务
        task = {"id": "TASK003", "params": {"L": 150, "B": 20, "T": 8}}
        body = json.dumps(task).encode()
        
        # 并发处理
        def process_with_processor(processor, channel, delivery_tag):
            processor.process_message(
                ch=channel,
                method=Mock(delivery_tag=delivery_tag),
                properties=Mock(),
                body=body
            )
        
        thread1 = threading.Thread(
            target=process_with_processor,
            args=(processor1, channel1, 1)
        )
        thread2 = threading.Thread(
            target=process_with_processor,
            args=(processor2, channel2, 2)
        )
        
        thread1.start()
        time.sleep(0.1)  # 确保 thread1 先开始
        thread2.start()
        
        thread1.join()
        thread2.join()
        
        # 验证
        assert channel1.ack_count == 1, "worker1 应该 ACK"
        assert channel2.ack_count == 1, "worker2 应该 ACK"
        assert "TASK003" in mock_db.records, "结果应该保存"
        assert mock_db.insert_count["TASK003"] == 1, "应该只插入一次（并发去重）"
    
    def test_failure_retry(self, mock_db, mock_channel):
        """测试 4：失败重试 - 任务失败后重试，最终成功"""
        from src.worker.task_processor import TaskProcessor
        
        processor = TaskProcessor("test_queue", mock_db)
        
        # 模拟第一次失败
        with patch.object(processor, 'execute_task', side_effect=Exception("Network error")):
            task = {"id": "TASK004", "params": {"L": 150, "B": 20, "T": 8}}
            body = json.dumps(task).encode()
            
            with pytest.raises(Exception):
                processor.process_message(
                    ch=mock_channel,
                    method=Mock(delivery_tag=1),
                    properties=Mock(),
                    body=body
                )
        
        # 模拟第二次成功
        with patch.object(processor, 'execute_task', return_value={"Ct": 0.00342}):
            processor.process_message(
                ch=mock_channel,
                method=Mock(delivery_tag=2),
                properties=Mock(),
                body=body
            )
        
        # 验证
        assert mock_channel.ack_count == 2, "应该 ACK 两次"
        assert "TASK004" in mock_db.records, "结果应该保存"
        assert mock_db.insert_count["TASK004"] == 1, "应该只插入一次"
    
    def test_idempotent_database_write(self, mock_db):
        """测试 5：幂等性数据库写入"""
        from src.worker.task_processor import TaskProcessor
        
        processor = TaskProcessor("test_queue", mock_db)
        
        # 多次写入同一任务
        processor.save_result("TASK005", {"Ct": 0.00342})
        processor.save_result("TASK005", {"Ct": 0.00342})
        processor.save_result("TASK005", {"Ct": 0.00342})
        
        # 验证
        assert "TASK005" in mock_db.records, "结果应该保存"
        assert mock_db.insert_count["TASK005"] == 1, "应该只插入一次（幂等性）"
    
    def test_immediate_ack(self, mock_db):
        """测试 6：立即 ACK - 验证 ACK 在任务开始前发送"""
        from src.worker.task_processor import TaskProcessor
        
        processor = TaskProcessor("test_queue", mock_db)
        channel = MockChannel()
        
        # 模拟耗时任务
        def slow_execute(task):
            time.sleep(2)
            return {"Ct": 0.00342}
        
        processor.execute_task = slow_execute
        
        task = {"id": "TASK006", "params": {"L": 150, "B": 20, "T": 8}}
        body = json.dumps(task).encode()
        
        # 记录 ACK 时间
        ack_time = [None]
        original_ack = channel.basic_ack
        
        def track_ack(delivery_tag):
            ack_time[0] = time.time()
            original_ack(delivery_tag)
        
        channel.basic_ack = track_ack
        
        start_time = time.time()
        processor.process_message(
            ch=channel,
            method=Mock(delivery_tag=1),
            properties=Mock(),
            body=body
        )
        end_time = time.time()
        
        # 验证 ACK 在任务开始前发送（应该在 0.5 秒内）
        assert ack_time[0] is not None, "应该发送 ACK"
        assert ack_time[0] - start_time < 0.5, "ACK 应该在任务开始前发送"
        assert end_time - start_time >= 2, "任务应该执行完成"


class TestDatabaseConstraint:
    """数据库约束测试"""
    
    def test_unique_constraint_prevents_duplicate(self):
        """测试 7：唯一约束防止重复插入"""
        # 这个测试需要在真实数据库上运行
        # 这里只是示例
        pytest.skip("需要在真实数据库上运行")
        
        # 示例代码：
        # db.execute("INSERT INTO task_results (task_id, result) VALUES ('TASK007', '{}')")
        # with pytest.raises(IntegrityError):
        #     db.execute("INSERT INTO task_results (task_id, result) VALUES ('TASK007', '{}')")


class TestMonitoring:
    """监控测试"""
    
    def test_duplicate_detection_query(self, mock_db):
        """测试 8：重复检测查询"""
        # 模拟一些数据
        mock_db.records = {
            "TASK008": '{"Ct": 0.00342}',
            "TASK009": '{"Ct": 0.00343}',
        }
        mock_db.insert_count = {
            "TASK008": 1,
            "TASK009": 2,  # 重复
        }
        
        # 检测重复
        duplicates = {
            task_id: count
            for task_id, count in mock_db.insert_count.items()
            if count > 1
        }
        
        assert len(duplicates) == 1, "应该检测到 1 个重复"
        assert "TASK009" in duplicates, "TASK009 应该被检测到"
        assert duplicates["TASK009"] == 2, "重复次数应该是 2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
