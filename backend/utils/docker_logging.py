# -*-: utf-8 -*-
"""
Docker 日志轮转配置
"""
import logging
import os
from logging.handlers import RotatingFileHandler


def setup_docker_logging():
    """为 Docker 环境配置日志轮转"""

    # Docker 日志目录
    log_dir = os.getenv("LOG_DIR", "/app/logs")
    os.makedirs(log_dir, exist_ok=True)

    # 日志文件路径
    log_file = os.path.join(log_dir, "backend.log")

    # 创建 rotation handler
    handler = RotatingFileHandler(
        log_file,
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=5,  # 保留 5 个备份
        encoding="utf-8",
    )

    # 日志格式
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    # 获取根 logger 并添加 handler
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # 对于 Docker，推荐将日志输出到 stdout
    # Docker 会自动处理日志轮转
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    return root_logger


if __name__ == "__main__":
    # 测试配置
    logger = setup_docker_logging()
    logger.info("Docker 日志配置已初始化")
    logger.info("日志将输出到 /app/logs/backend.log (轮转: 100MB, 5 个备份)")
