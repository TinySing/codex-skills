#!/usr/bin/env python3
"""
IM Teams 认证共享配置。

只维护静态配置。修改 ACTIVE_ENVIRONMENT 即可切换默认运行环境。
"""

from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = SKILL_ROOT / "cache"
LOG_DIR = SKILL_ROOT / "log"

LOG_FILE = LOG_DIR / "auth.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 3

KEYRING_SERVICE = "im-teams-auth"
KEYRING_GATEWAY_TOKEN = "gateway_token"
KEYRING_TOKEN_EXPIRY = "gateway_token_expiry"

ENV_TOKEN_NAME = "IM_TEAMS_GATEWAY_TOKEN"
ENV_LANDING_URL = "IM_TEAMS_AUTH_LANDING_URL"

# 默认运行环境。调试时只需在 production 和 test 之间修改此字段。
ACTIVE_ENVIRONMENT = "test"

# 本地 token 默认有效期：页面不回传 expiresAt 时用它。与 env_check 的 python 路径缓存（7 天）保持一致。
TOKEN_MAX_AGE_SECONDS = 7 * 24 * 3600
RECEIVER_HOST = "127.0.0.1"
RECEIVER_PORTS = list(range(35101, 35111))
# 认证等待超时（秒）：receiver 等用户完成授权回传 token 的最长时间，即 --wait 最长阻塞时长。
RECEIVER_TIMEOUT_SECONDS = 120

LANDING_URLS = {
    "production": "https://im.360teams.com/discover/imTeamsAuth",
    "test": "https://sit-im.360teams.com/discover/imTeamsAuth",
}

TOKEN_EXCHANGE_URLS = {
    "production": "https://im.360teams.com/api/qfin-api/token/user/getTokenByEncrypt",
    "test": "https://sit-im.360teams.com/api/qfin-api/token/user/getTokenByEncrypt",
}

SCHEME_PREFIXES = {
    "production": "sk360teams://",
    "test": "teamssit://",
}
