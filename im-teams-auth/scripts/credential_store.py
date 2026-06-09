#!/usr/bin/env python3
"""
IM Teams 凭证存储。

负责环境名称规范化，以及 token 在环境变量和系统钥匙串中的命名、读写与清理。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config import (
    ACTIVE_ENVIRONMENT,
    ENV_TOKEN_NAME,
    KEYRING_GATEWAY_TOKEN,
    KEYRING_SERVICE,
    KEYRING_TOKEN_EXPIRY,
    LANDING_URLS,
    TOKEN_MAX_AGE_SECONDS,
)
from errors import AuthError


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


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def _require_keyring() -> None:
    if not _keyring_available():
        raise AuthError("OS Keyring 不可用，无法安全存储凭证。")


def _normalize_expiry(expires_at: str | None = None,
                      max_age_seconds: int = TOKEN_MAX_AGE_SECONDS) -> str:
    if expires_at:
        try:
            parsed = datetime.fromisoformat(expires_at)
            if parsed > datetime.now():
                return parsed.isoformat()
        except (TypeError, ValueError):
            pass
    return (datetime.now() + timedelta(seconds=max_age_seconds)).isoformat()


def keyring_save_token(token: str, environment: str, expires_at: str | None = None) -> str:
    _require_keyring()
    if not token:
        raise AuthError("Token 为空，无法写入 Keyring。")
    service = keyring_service_for(environment)
    expiry = _normalize_expiry(expires_at)
    try:
        import keyring
        keyring.set_password(service, KEYRING_GATEWAY_TOKEN, token)
        keyring.set_password(service, KEYRING_TOKEN_EXPIRY, expiry)
        return expiry
    except Exception as exc:
        raise AuthError(f"Keyring 写入失败: {exc}") from exc


def keyring_load_token(environment: str) -> tuple[str | None, str | None]:
    service = keyring_service_for(environment)
    try:
        import keyring
        expiry = keyring.get_password(service, KEYRING_TOKEN_EXPIRY)
        if not expiry:
            return None, None
        try:
            if datetime.now() >= datetime.fromisoformat(expiry):
                return None, expiry
        except (TypeError, ValueError):
            return None, None
        token = keyring.get_password(service, KEYRING_GATEWAY_TOKEN)
        return token, expiry
    except Exception:
        return None, None


def keyring_clear_token(environment: str) -> bool:
    """删除指定环境的钥匙串 token；返回是否真的删到了内容（本来为空返回 False）。"""
    service = keyring_service_for(environment)
    import keyring
    removed = False
    for key in (KEYRING_GATEWAY_TOKEN, KEYRING_TOKEN_EXPIRY):
        try:
            keyring.delete_password(service, key)
            removed = True
        except keyring.errors.PasswordDeleteError:
            pass
    return removed


def keyring_clear_all_tokens() -> dict:
    """清除所有环境的 token；值为各环境是否真的删到了内容。"""
    return {environment: keyring_clear_token(environment) for environment in LANDING_URLS}
