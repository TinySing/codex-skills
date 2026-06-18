#!/usr/bin/env python3
"""
IM Teams 认证运行环境准备。

负责日志初始化，以及 keyring 依赖的检测和自动安装。
"""

import logging
import subprocess
import sys
from functools import lru_cache
from logging.handlers import RotatingFileHandler

from config import LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES


def force_utf8_io() -> None:
    """强制 stdout/stderr 用 UTF-8。

    Windows 上 stdout 被重定向到管道时默认用 ANSI 代码页（如 cp936），
    打印含 emoji 等非 GBK 字符的中文 JSON 会抛 UnicodeEncodeError。
    调用方在任何输出前调用，保证跨平台一致。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


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


def _keyring_importable() -> bool:
    """keyring 模块能不能 import（不管后端能不能用）。"""
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _keyring_available() -> bool:
    """keyring 已装且有可用后端。

    沙箱里常是「装了但后端是 fail.Keyring」——能 import 却任何读写都抛异常，
    等同不可用，此时应走本地文件兜底。

    每个进程只探测一次（结果缓存）：探到不可用就一路走本地文件兜底，
    避免 save/load/clear 反复触发 get_keyring()（某些后端走 DBus 探测偏慢）。
    """
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return False
    try:
        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def ensure_keyring() -> dict:
    if _keyring_available():
        return {"available": True}

    # 能 import 但后端不可用（沙箱常见）：pip 装不出后端，别浪费时间，直接走文件兜底。
    if _keyring_importable():
        return {
            "available": False,
            "message": "keyring 已安装但无可用系统后端（沙箱常见），凭证回退本地文件存储。",
        }

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "keyring"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            _keyring_available.cache_clear()  # 装完得重新探，否则读到装前缓存的 False
            if _keyring_available():
                return {"available": True, "auto_installed": True}
    except (OSError, subprocess.TimeoutExpired):
        pass

    return {
        "available": False,
        "message": "keyring 不可用，请安装 keyring 或系统凭证后端。",
    }
