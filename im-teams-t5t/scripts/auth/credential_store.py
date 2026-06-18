#!/usr/bin/env python3
"""
IM Teams 凭证存储。

负责环境名称规范化，以及 token 的命名、读写与清理。
存储优先级：OS Keyring（更安全）→ 不可用/写失败时回退本地文件（沙箱里系统钥匙串常常用不了）。
本地文件存在技能自己的 cache 目录（可操作范围内），持久保存，不必每会话重认证。
"""

from __future__ import annotations

import json
import os

from config import (
    ACTIVE_ENVIRONMENT,
    CACHE_DIR,
    ENV_TOKEN_NAME,
    KEYRING_GATEWAY_TOKEN,
    KEYRING_SERVICE,
    LANDING_URLS,
)
from errors import AuthError
from runtime import _keyring_available


def normalize_environment(environment: str) -> str:
    normalized = (environment or ACTIVE_ENVIRONMENT).strip().lower()
    if normalized not in LANDING_URLS:
        raise AuthError(f"不支持的环境: {environment}")
    return normalized


def env_token_name_for(environment: str) -> str:
    normalized = normalize_environment(environment)
    return f"{ENV_TOKEN_NAME}_{normalized.upper()}"


def keyring_service_for(environment: str) -> str:
    normalized = normalize_environment(environment)
    return f"{KEYRING_SERVICE}:{normalized}"


# ---------------------------------------------------------------------------
# 本地文件兜底（钥匙串不可用时）
# ---------------------------------------------------------------------------

def _file_token_path(environment: str):
    return CACHE_DIR / f"gateway_token_{environment}.json"


def _save_token_file(token: str, environment: str) -> None:
    """把 token 写到技能 cache 目录的本地文件；建文件即限制为仅属主可读。"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _file_token_path(environment)
        payload = json.dumps({"token": token}, ensure_ascii=False)
        # 用 O_CREAT|0o600 原子建文件，避免「先按 umask 建好(常 0644) 再 chmod」之间的明文可读窗口。
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(payload)
        try:
            os.chmod(path, 0o600)  # 收紧旧版本可能残留的宽松权限；Windows 不支持时忽略
        except OSError:
            pass
    except OSError as exc:
        raise AuthError(f"无法保存凭证（系统钥匙串和本地文件都不可写）: {exc}") from exc


def _load_token_file(environment: str) -> str | None:
    try:
        path = _file_token_path(environment)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        token = data.get("token") if isinstance(data, dict) else None
        return token or None
    except (OSError, json.JSONDecodeError):
        return None


def _clear_token_file(environment: str) -> bool:
    try:
        _file_token_path(environment).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 对外：存 / 读 / 清（Keyring 优先，文件兜底）
# ---------------------------------------------------------------------------

def keyring_save_token(token: str, environment: str) -> None:
    """存 token；不记本地过期（失效以服务端为准）。Keyring 写不进就回退本地文件。"""
    if not token:
        raise AuthError("Token 为空，无法保存。")
    environment = normalize_environment(environment)
    if _keyring_available():
        try:
            import keyring
            keyring.set_password(keyring_service_for(environment), KEYRING_GATEWAY_TOKEN, token)
            _clear_token_file(environment)  # 钥匙串写成功，清掉可能残留的文件兜底，避免双份
            return
        except Exception:
            pass  # 钥匙串写失败（沙箱里 Keychain 锁住等）→ 落到文件兜底
    _save_token_file(token, environment)


def keyring_load_token(environment: str) -> str | None:
    """有 token 就返回；不判本地过期（失效由服务端认证码触发重认证）。

    顺序：Keyring → 本地文件兜底。
    """
    environment = normalize_environment(environment)
    if _keyring_available():
        try:
            import keyring
            token = keyring.get_password(keyring_service_for(environment), KEYRING_GATEWAY_TOKEN)
            if token:
                return token
        except Exception:
            pass
    return _load_token_file(environment)


def keyring_clear_token(environment: str) -> bool:
    """删除指定环境的 token（钥匙串 + 本地文件）；返回是否真的删到了内容。"""
    environment = normalize_environment(environment)
    removed = False
    if _keyring_available():
        try:
            import keyring
            keyring.delete_password(keyring_service_for(environment), KEYRING_GATEWAY_TOKEN)
            removed = True
        except Exception:
            pass
    if _clear_token_file(environment):
        removed = True
    return removed


def keyring_clear_all_tokens() -> dict:
    """清除所有环境的 token；值为各环境是否真的删到了内容。"""
    return {environment: keyring_clear_token(environment) for environment in LANDING_URLS}
