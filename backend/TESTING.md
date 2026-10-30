# 测试命令
# 运行所有测试
pytest

# 运行指定文件
pytest tests/test_database.py

# 生成覆盖率报告
pytest --cov=. --cov-report=html

# 仅显示失败
pytest -x

# 并行运行
pytest -n 4

# CI 后端门禁（在 backend/ 目录执行）
PYTHONPATH=. pytest -q tests/ --timeout=600

# 只验证本次修复涉及的 API 契约及证据脚本
PYTHONPATH=. pytest -q tests/test_abtest_unit.py tests/test_algorithm_store_unit.py tests/test_api_key_unit.py tests/test_audit_unit.py tests/test_code_generator_unit.py tests/test_model_factory_experiment_evidence_gate.py tests/test_smoke.py

pytest 的导入路径相对本目录配置，同时包含后端与仓库根目录，
因此从 `backend/` 运行时也能导入根目录的 `scripts` 证据工具。
不要在测试收集阶段向 `sys.modules` 注入全局 PyTorch 替身；
CI 已安装真实 PyTorch，离线证据测试必须执行真实适配器。

后端套件在 PR、main 推送、定时和手动运行中均为阻塞检查；
失败必须显式处理，不能用整个作业的 `continue-on-error` 隐藏。
前端类型检查暂时仍为 advisory，其存量问题需单独治理。

API Key 测试针对当前 SQL 存储而非旧文件管理器：平台级密钥管理
仅限管理员，明文只在创建/轮换时返回，读取仅含前缀和元数据。
用量接口只报告最近使用时间，`history_available: false` 明确表示
当前表不保存逐次调用历史。
