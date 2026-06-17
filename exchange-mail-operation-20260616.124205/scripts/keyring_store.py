#!/usr/bin/env python3
"""
Exchange 邮箱技能 - Keyring 凭证存储模块

使用 OS Keyring（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service）
安全存储 Exchange 邮箱的用户名和密码，避免明文存储密码。

服务器地址、域名、邮箱后缀、Exchange 版本等均为固定常量，不存入 Keyring。

参考 teams-auth 的 keyring 实现模式。
"""

from __future__ import annotations

import subprocess
import sys

KEYRING_SERVICE = "exchange-mail-skill"
KEYRING_USERNAME = "username"     # @ 前面的部分
KEYRING_PASSWORD = "password"

# 固定常量 — 所有用户共享，不存入 Keyring
SERVER = "mail.qifu.com"
DOMAIN = "jk"
EMAIL_SUFFIX = "qifu.com"
VERSION = "Exchange2010SP2"


def _keyring_available() -> bool:
    """检查 keyring 是否可导入（零副作用，不触发 I/O）"""
    try:
        import keyring
        return True
    except ImportError:
        return False


def _require_keyring():
    """要求 keyring 可用，否则抛出 RuntimeError"""
    if not _keyring_available():
        raise RuntimeError(
            "OS Keyring 不可用，请先运行: pip install keyring\n"
            "Linux 用户可能还需要: pip install keyrings.alt"
        )


def ensure_keyring() -> dict:
    """检查 keyring 可用性，不可用时自动安装

    Returns:
        {"available": True} 或
        {"available": True, "auto_installed": True} 或
        {"available": False, "message": str}
    """
    if _keyring_available():
        return {"available": True}

    pip_cmd = [sys.executable, "-m", "pip", "install", "keyring"]
    try:
        result = subprocess.run(
            pip_cmd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=60
        )
        if result.returncode == 0 and _keyring_available():
            return {"available": True, "auto_installed": True}
    except (OSError, subprocess.TimeoutExpired):
        pass

    return {
        "available": False,
        "message": "keyring 安装失败，请手动执行: pip install keyring"
    }


def keyring_save_config(username, password):
    """将用户名和密码存入 OS Keyring

    Args:
        username: 邮箱用户名（@ 前面的部分）
        password: 邮箱密码
    """
    _require_keyring()
    import keyring
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, username)
        keyring.set_password(KEYRING_SERVICE, KEYRING_PASSWORD, password)
    except Exception as exc:
        raise RuntimeError(f"Keyring 写入失败: {exc}") from exc


def keyring_load_config() -> dict | None:
    """从 OS Keyring 读取凭证，结合固定常量生成完整配置字典

    Returns:
        配置字典，或 None（凭证缺失或发生异常时）
    """
    try:
        import keyring
        username = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        password = keyring.get_password(KEYRING_SERVICE, KEYRING_PASSWORD)
        if not username or not password:
            return None

        email = f"{username}@{EMAIL_SUFFIX}"

        return {
            "server": SERVER,
            "domain": DOMAIN,
            "email": email,
            "username": username,
            "password": password,
            "version": VERSION,
        }
    except Exception:
        return None


def keyring_clear_config() -> bool:
    """清除 Keyring 中所有凭证

    Returns:
        True 表示成功，False 表示失败（不抛异常）
    """
    try:
        import keyring
        for key in (KEYRING_USERNAME, KEYRING_PASSWORD):
            try:
                keyring.delete_password(KEYRING_SERVICE, key)
            except keyring.errors.PasswordDeleteError:
                pass
        return True
    except Exception:
        return False