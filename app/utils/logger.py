"""
日志工具

基于 loguru 提供统一的日志初始化。
支持控制台输出（带颜色）和文件输出（纯文本 + 按天轮转）。
"""

import sys
from pathlib import Path

from loguru import logger


def setup_logger(
    level: str = "DEBUG",
    log_dir: str | Path = "logs",
) -> None:
    """
    初始化全局日志配置。

    控制台输出：带颜色，便于开发调试。
    文件输出：纯文本，按天轮转，保留 30 天，便于生产排查。

    Parameters
    ----------
    level : str
        日志级别，如 DEBUG / INFO / WARNING / ERROR
    log_dir : str | Path
        日志文件输出目录，默认 logs/
    """
    # 确保日志目录存在
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 移除 loguru 默认 handler
    logger.remove()

    # --- 控制台输出（带颜色） ---
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # --- 文件输出（纯文本 + 按天轮转） ---
    logger.add(
        log_path / "app_{time:YYYY-MM-DD}.log",
        level=level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message}"
        ),
        rotation="00:00",   # 每天午夜轮转
        retention="30 days",  # 保留最近 30 天
        encoding="utf-8",
        enqueue=True,        # 多进程安全
    )
