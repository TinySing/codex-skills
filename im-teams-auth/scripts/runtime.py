#!/usr/bin/env python3
"""
IM Teams 认证运行环境准备。

负责日志初始化，以及 keyring 依赖的检测和自动安装。
"""

import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler

from config import LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if root.handlers:
        return
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s %(funcName)s:%(lineno)d: %(message)s"
    ))
    root.addHandler(handler)


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_keyring() -> dict:
    if _keyring_available():
        return {"available": True}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "keyring"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and _keyring_available():
            return {"available": True, "auto_installed": True}
    except (OSError, subprocess.TimeoutExpired):
        pass

    return {
        "available": False,
        "message": "keyring 不可用，请安装 keyring 或系统凭证后端。",
    }
