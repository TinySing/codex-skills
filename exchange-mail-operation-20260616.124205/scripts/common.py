#!/usr/bin/env python3
"""
Exchange 邮件技能 - 共享工具模块

提供两个主脚本（exchange_mail.py、ews_calendar.py）共用的函数：
  - ensure_utf8_output()              确保 stdout/stderr 使用 UTF-8 编码
  - check_and_install_dependencies()  依赖检测与自动安装
  - check_model_safety()              模型安全校验
  - load_config()                     从 Keyring 加载配置
  - get_account()                     连接 Exchange 服务器
  - _is_auth_error()                  认证异常判断
  - _classify_connection_error()      连接异常分类
  - handle_auth_failure()             认证失败统一处理
  - configure_logging()               日志级别配置
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UTF-8 output encoding
# ---------------------------------------------------------------------------

def ensure_utf8_output() -> None:
    """确保 stdout/stderr 使用 UTF-8 编码，防止中文输出乱码。

    在 Windows 上，子进程的 stdout 默认编码可能是 GBK/CP936，
    导致中文 JSON 输出乱码。此函数在脚本入口处调用，
    将 stdout/stderr 重新配置为 UTF-8 编码。

    在 macOS/Linux 上通常已经是 UTF-8，此函数为无操作。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (LookupError, OSError):
                pass


# ---------------------------------------------------------------------------
# Version map (lazy-loaded)
# ---------------------------------------------------------------------------

def _get_version_map() -> dict:
    """延迟加载 Exchange 版本映射（避免 import 时要求 exchangelib 已安装）"""
    from exchangelib.version import (
        EXCHANGE_2007_SP1, EXCHANGE_2010, EXCHANGE_2010_SP1, EXCHANGE_2010_SP2,
        EXCHANGE_2010_SP3, EXCHANGE_2013, EXCHANGE_2013_SP1, EXCHANGE_2016,
        EXCHANGE_2019, EXCHANGE_O365,
    )
    return {
        "Exchange2007SP1": EXCHANGE_2007_SP1,
        "Exchange2010": EXCHANGE_2010,
        "Exchange2010SP1": EXCHANGE_2010_SP1,
        "Exchange2010SP2": EXCHANGE_2010_SP2,
        "Exchange2010SP3": EXCHANGE_2010_SP3,
        "Exchange2013": EXCHANGE_2013,
        "Exchange2013SP1": EXCHANGE_2013_SP1,
        "Exchange2016": EXCHANGE_2016,
        "Exchange2019": EXCHANGE_2019,
        "ExchangeO365": EXCHANGE_O365,
    }


# ---------------------------------------------------------------------------
# Dependency check and auto-install
# ---------------------------------------------------------------------------

def check_and_install_dependencies():
    """检测并自动安装依赖库（exchangelib、keyring 等）

    未安装时自动通过 pip 安装，安装失败则报错退出。
    """
    from env_check import REQUIRED_PACKAGES
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pip_name],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=120,
                )
                if result.returncode == 0:
                    try:
                        __import__(import_name)
                        continue
                    except ImportError:
                        pass
                print(json.dumps({
                    "success": False,
                    "message": f"依赖 {pip_name} 未安装且自动安装失败，请手动执行: pip install {pip_name}"
                }, ensure_ascii=False))
                sys.exit(1)
            except (OSError, subprocess.TimeoutExpired):
                print(json.dumps({
                    "success": False,
                    "message": f"依赖 {pip_name} 安装超时，请手动执行: pip install {pip_name}"
                }, ensure_ascii=False))
                sys.exit(1)


# ---------------------------------------------------------------------------
# Model safety check
# ---------------------------------------------------------------------------

def check_model_safety(action: str, read_actions: set) -> bool:
    """读取类操作前提醒用户确认模型安全性，写入类操作跳过。

    不自动检测环境变量中的模型 ID（不同平台环境变量不可靠），
    而是统一输出提醒信息，由用户自行确认当前模型是否合规。

    Args:
        action: 当前操作名称
        read_actions: 读取类操作集合（调用方传入各自的 READ_ACTIONS）

    Returns:
        True 表示非读取操作（无需校验），False 表示需要用户确认
    """
    if action not in read_actions:
        return True

    # 根据操作集合自动识别内容类型
    content_type = "邮件" if "recent" in read_actions else "日历"

    # 输出提醒，要求用户确认
    print(json.dumps({
        "status": "warning",
        "require_confirm": True,
        "message": (
            f"{content_type}中可能包含敏感业务信息或个人信息。\n"
            "请确认当前模型是否为公司安全要求的 M1 或 M2 模型\n"
            "（以 deepbank/ 或 360/360- 开头）。\n"
            "如是，请回复\"确认\"，我将继续本次分析。"
        ),
    }, ensure_ascii=False))
    return False


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config():
    """从 OS Keyring 加载配置（唯一凭证来源）

    Keyring 仅存储用户名和密码，其余字段（server/domain/email/version）
    由 keyring_store.py 中的固定常量提供。

    兼容旧 config.json：检测到明文密码时自动迁移到 Keyring，迁移后删除明文。
    Keyring 无配置且无旧文件时，提示用户通过浏览器配置。
    """
    from keyring_store import keyring_load_config, keyring_save_config

    # 1. 从 Keyring 加载
    config = keyring_load_config()
    if config:
        return config

    # 2. 兼容旧 config.json → 自动迁移到 Keyring（只存用户名和密码）
    config_file = Path.home() / ".exchange_skill" / "config.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            if file_config.get("password"):
                username = file_config.get("email", "").split("@")[0]
                keyring_save_config(username=username, password=file_config["password"])
                # 迁移成功，删除明文密码
                file_config.pop("password", None)
                with open(config_file, "w", encoding="utf-8") as fw:
                    json.dump(file_config, fw, indent=2, ensure_ascii=False)
                print("[提示] 已将凭证从配置文件迁移至 OS Keyring，明文密码已移除",
                      file=sys.stderr)
                config = keyring_load_config()
                if config:
                    return config
        except Exception as e:
            print(f"[警告] 旧配置迁移失败: {e}，请通过浏览器重新配置",
                  file=sys.stderr)

    # 3. Keyring 无配置且无旧文件 → 提示用户
    print(json.dumps({
        "success": False,
        "message": "尚未配置邮箱凭证，请先运行: python scripts/config_web.py",
        "hint": "通过浏览器配置页面输入用户名和密码"
    }, ensure_ascii=False))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def _is_auth_error(e) -> bool:
    """判断异常是否为认证失败（密码错误/过期/权限拒绝）"""
    try:
        from exchangelib.errors import UnauthorizedError
        if isinstance(e, UnauthorizedError):
            return True
    except ImportError:
        pass
    msg = str(e).lower()
    if "unauthorized" in msg or "access denied" in msg or "401" in msg:
        return True
    # exchangelib 在认证失败时可能抛出 ErrorAccessDenied 或 ErrorInvalidUserSid
    if "erroraccessdenied" in msg or "errorinvalidusersid" in msg:
        return True
    return False


def _classify_connection_error(e):
    """将连接异常分类为友好提示"""
    msg = str(e).lower()

    if _is_auth_error(e):
        return "认证失败：用户名或密码错误或已过期，请通过浏览器重新配置"
    try:
        from exchangelib.errors import TransportError
        if isinstance(e, TransportError):
            if "ssl" in msg or "certificate" in msg or "cert" in msg:
                return "SSL证书验证失败，请检查服务器证书或网络代理设置"
            if "timeout" in msg or "timed out" in msg:
                return "连接超时，请检查网络是否可以访问 Exchange 服务器"
            if "refused" in msg or "connection" in msg:
                return "无法连接服务器，请检查服务器地址是否正确以及网络是否通畅"
            return f"网络通信错误: {str(e)}"
    except ImportError:
        pass

    if "ssl" in msg or "certificate" in msg or "cert" in msg:
        return "SSL证书验证失败，请检查服务器证书或网络代理设置"
    if "timeout" in msg or "timed out" in msg:
        return "连接超时，请检查网络是否可以访问 Exchange 服务器"
    if "refused" in msg or "name or service not known" in msg:
        return "无法连接服务器，请检查服务器地址是否正确以及网络是否通畅"

    return f"连接失败: {str(e)}"


# ---------------------------------------------------------------------------
# Account connection
# ---------------------------------------------------------------------------

def get_account(config, verify_target="inbox"):
    """连接到 Exchange 服务器并返回 Account 对象

    Args:
        config: 配置字典（含 server, domain, username, password, email, version）
        verify_target: 连接验证目标，"inbox" 或 "calendar"

    Returns:
        exchangelib Account 对象

    Raises:
        ConnectionError: 连接或验证失败
    """
    from exchangelib import Account, Configuration, Credentials, DELEGATE
    from exchangelib.version import Version

    version_map = _get_version_map()
    username = f"{config['domain']}\\{config['username']}"
    credentials = Credentials(username=username, password=config["password"])

    version_name = config.get("version", "Exchange2010SP2")
    build = version_map.get(version_name, version_map["Exchange2010SP2"])
    version = Version(build=build)

    # 当 server 地址明确时关闭 autodiscover，避免 30+ 秒超时
    server = config.get("server")
    autodiscover = not bool(server)

    config_kwargs = {
        "credentials": credentials,
        "version": version,
    }
    if server:
        config_kwargs["server"] = server

    configuration = Configuration(**config_kwargs)

    logger.debug("Connecting to %s, autodiscover=%s", server or "auto", autodiscover)

    account = Account(
        primary_smtp_address=config["email"],
        config=configuration,
        access_type=DELEGATE,
        autodiscover=autodiscover,
    )

    # 验证连接可用性
    try:
        logger.debug("Verifying connection via %s.total_count", verify_target)
        if verify_target == "calendar":
            _ = account.calendar.total_count
        else:
            _ = account.inbox.total_count
    except Exception as e:
        error_msg = _classify_connection_error(e)
        raise ConnectionError(error_msg)

    return account


# ---------------------------------------------------------------------------
# Auth failure handling
# ---------------------------------------------------------------------------

def handle_auth_failure(error=None):
    """认证失败统一处理：清除 Keyring 缓存，输出 need_reauth，退出码 4

    Args:
        error: 原始异常（可选，用于日志）
    """
    from keyring_store import keyring_clear_config
    keyring_clear_config()
    if error:
        logger.debug("Auth failure: %s", error)
    print(json.dumps({
        "success": False,
        "message": "认证失败：用户名或密码错误或已过期",
        "hint": "请通过浏览器重新配置: python scripts/config_web.py",
        "need_reauth": True,
    }, ensure_ascii=False))
    sys.exit(4)  # 退出码 4 = 凭证失效，需重新认证


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

def configure_logging(verbose=False, debug=False):
    """配置日志级别

    Args:
        verbose: 设置 INFO 级别
        debug: 设置 DEBUG 级别（包含 exchangelib 的调试日志）
    """
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s [%(name)s] %(message)s")
        logging.getLogger("exchangelib").setLevel(logging.DEBUG)
    elif verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
