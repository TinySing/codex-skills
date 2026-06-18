#!/usr/bin/env python3
"""
360Teams 网关错误码识别。

各业务 skill 共用，只识别两类需要特定处理的码：
- 鉴权失效：删本地 token + 重新走鉴权流程。
- 网络相关：提示用户网络问题。
其余非 0 业务码不逐一分类，统一按普通错误粗处理。纯数据模块、无内部依赖，便于跨 skill 加载。
"""

from __future__ import annotations

# 认证失效 / 需退出登录重认证：除 HTTP 401/403 外，登录过期、账号异常等业务码也归此类。
AUTH_CODES = frozenset({
    401, 403, 100401, 100403,
    10230, 10301,            # 登录过期 / 访问未授权
    10220, 10241,            # 身份校验失败 / 登录尝试超限
    10130,                   # 验证码错误（登录流程内）
    12001, 12003, 12004,     # 账号不存在 / 域账号停用 / 邮箱匹配多用户
})

# 网络相关：提示用户检查网络、稍后再试。
NETWORK_CODES = frozenset({
    408,                     # 链接到服务器发生异常
    502,                     # 链接到服务器发生错误，请检查网络
    504,                     # 链接超时，请检查网络
    9999,                    # 您的网络发生异常，请检查网络
})


def is_auth_code(code) -> bool:
    """该 code 是否表示鉴权失效（需删本地 token 重新认证）。无法识别的一律不是。"""
    try:
        return int(code) in AUTH_CODES
    except (TypeError, ValueError):
        return False


def is_network_code(code) -> bool:
    """该 code 是否表示网络相关问题（提示用户网络）。无法识别的一律不是。"""
    try:
        return int(code) in NETWORK_CODES
    except (TypeError, ValueError):
        return False
