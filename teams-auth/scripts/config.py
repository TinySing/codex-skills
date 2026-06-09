#!/usr/bin/env python3
"""
360Teams 认证 - 公共配置

本模块定义常量、异常类及 Config 数据类，
供 auth.py 引用。

凭证策略：
  网关 token → 仅存储至 OS Keyring（安全要求，不落盘明文）
  若 Keyring 不可用 → 登录时报错，要求用户安装 keyring 后端
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

# =============================================================================
# API endpoints (prod)
# =============================================================================

# SSO_BROWSER_LOGIN_URL = "https://ssac.sk.360shuke.com/login"
# CALLBACK_PORTS = [35001, 35002, 35003, 35004, 35005, 35006, 35007, 35008, 35009, 35010]
# CALLBACK_TIMEOUT = 120
# TOKEN_LOGIN_API = "https://sk.360teams.com/api/token/ticket/login"


# =============================================================================
# API endpoints (test)
# =============================================================================
SSO_BROWSER_LOGIN_URL = "https://sso-test.sk.360shuke.com"
CALLBACK_PORTS = [35001, 35002, 35003, 35004, 35005, 35006, 35007, 35008, 35009, 35010]
CALLBACK_TIMEOUT = 120
TOKEN_LOGIN_API = "https://sit-360teams.sk.360shuke.com/"

# Gateway token max-age (7 days in seconds)
TOKEN_MAX_AGE_SECONDS = 7 * 24 * 3600

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# =============================================================================
# Directory helpers
# =============================================================================

def _skill_root_dir() -> Path:
    """技能根目录（scripts 的上级目录）。"""
    return Path(__file__).resolve().parent.parent


# =============================================================================
# Exceptions (defined early so keyring helpers can reference AuthError)
# =============================================================================

class TeamsError(Exception):
    """Base exception for 360Teams operations."""


class AuthError(TeamsError):
    """Raised when SSO login fails or credentials are invalid."""


# =============================================================================
# Keyring credential store
# =============================================================================

KEYRING_SERVICE = "360teams"
KEYRING_GATEWAY_TOKEN = "gateway_token"
KEYRING_DEVICE_CODE = "gateway_device_code"
KEYRING_TOKEN_EXPIRY = "gateway_token_expiry"


def _keyring_available() -> bool:
    """Check if keyring is importable and functional."""
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def _require_keyring() -> None:
    """Raise AuthError if keyring is not available."""
    if not _keyring_available():
        raise AuthError(
            "OS Keyring 不可用，无法安全存储凭证。请安装 keyring 后端："
            "pip install keyring (Windows/macOS 通常自动可用；"
            "Linux 需额外安装如 pip install keyrings.alt)"
        )


def ensure_keyring() -> dict:
    """Ensure keyring is available, auto-installing via pip if missing.

    Returns:
        {"available": True} on success,
        {"available": True, "auto_installed": True} if auto-installed,
        {"available": False, "message": str} on failure.
    """
    if _keyring_available():
        return {"available": True}

    # Try auto-install via pip
    pip_cmd = [sys.executable, "-m", "pip", "install", "keyring"]
    try:
        result = subprocess.run(
            pip_cmd,
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
        "message": (
            "keyring 未安装且自动安装失败。请手动执行："
            "pip install keyring（Linux 需额外安装如 pip install keyrings.alt）"
        ),
    }


def keyring_save_gateway_token(token: str, device_code: str | None = None,
                               max_age_seconds: int = TOKEN_MAX_AGE_SECONDS) -> None:
    """Save gateway token to OS keyring.

    Raises:
        AuthError: if keyring is not available or save fails.
    """
    _require_keyring()
    try:
        import keyring
        from datetime import datetime, timedelta
        keyring.set_password(KEYRING_SERVICE, KEYRING_GATEWAY_TOKEN, token)
        expires_at = (datetime.now() + timedelta(seconds=max_age_seconds)).isoformat()
        keyring.set_password(KEYRING_SERVICE, KEYRING_TOKEN_EXPIRY, expires_at)
        if device_code:
            keyring.set_password(KEYRING_SERVICE, KEYRING_DEVICE_CODE, device_code)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_DEVICE_CODE)
            except keyring.errors.PasswordDeleteError:
                pass
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError(f"Keyring 写入失败: {exc}") from exc


def keyring_load_gateway_token() -> tuple[str | None, str | None]:
    """Load gateway token and device_code from OS keyring.

    Returns (token, device_code), both may be None.
    """
    try:
        import keyring
        from datetime import datetime
        expiry_str = keyring.get_password(KEYRING_SERVICE, KEYRING_TOKEN_EXPIRY)
        if not expiry_str:
            return None, None
        try:
            if datetime.now() >= datetime.fromisoformat(expiry_str):
                return None, None
        except (ValueError, TypeError):
            return None, None
        token = keyring.get_password(KEYRING_SERVICE, KEYRING_GATEWAY_TOKEN)
        device_code = keyring.get_password(KEYRING_SERVICE, KEYRING_DEVICE_CODE)
        return token, device_code
    except Exception:
        return None, None


def keyring_clear_gateway_token() -> bool:
    """Clear gateway token from OS keyring. Returns True on success."""
    try:
        import keyring
        for key in (KEYRING_GATEWAY_TOKEN, KEYRING_TOKEN_EXPIRY, KEYRING_DEVICE_CODE):
            try:
                keyring.delete_password(KEYRING_SERVICE, key)
            except keyring.errors.PasswordDeleteError:
                pass
        return True
    except Exception:
        return False


# =============================================================================
# Skill-private paths (non-credential)
# =============================================================================

SKILL_ROOT = _skill_root_dir()
CACHE_DIR = SKILL_ROOT / "cache"
LOG_DIR = SKILL_ROOT / "log"

# Ensure runtime directories exist
for _d in (CACHE_DIR, LOG_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        pass

LOG_FILE = LOG_DIR / "auth.log"
LOG_MAX_BYTES = 100 * 1024 * 1024  # 100 MB
LOG_BACKUP_COUNT = 7


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class Config:
    """Runtime configuration container for auth."""

    no_cache: bool = False
    _used_cache: bool = field(default=False, init=False, repr=False)


def build_config(args: argparse.Namespace) -> Config:
    """Build a Config from CLI arguments."""
    return Config(no_cache=args.no_cache)


# =============================================================================
# Logging
# =============================================================================

def setup_logging(verbose: bool = False, console: bool = False) -> None:
    """Configure root logger with rotating file handler.

    Args:
        verbose: If True, set log level to DEBUG; otherwise INFO.
        console: If True, also add a StreamHandler to stderr for
                 interactive use.  Default False to avoid noisy output
                 when scripts are invoked by AI agents.
    """
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        return

    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s %(funcName)s:%(lineno)d: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_fmt = logging.Formatter(
            "[%(levelname)s] %(name)s: %(message)s",
        )
        console_handler.setFormatter(console_fmt)
        root.addHandler(console_handler)
