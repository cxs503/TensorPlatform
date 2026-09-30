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
