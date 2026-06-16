#!/usr/bin/env python3
"""
T5T Skill 共享配置。

集中维护运行环境、接口地址和依赖 skill 路径等固定配置。
"""

from pathlib import Path


# 默认运行环境。调试时只需在 production 和 test 之间修改此字段。
ACTIVE_ENVIRONMENT = "test"

ENV_DOMAINS = {
    "production": "https://im.360teams.com",
    "test": "https://sit-im.360teams.com",
}

API_PREFIX = "/api/qfin-api"
PREVIEW_PATH = (
    "/applink/page/t5t?"
    "params=%7B%22notifyAction%22%3A%7B%22waitLoaded%22%3Atrue%2C"
    "%22method%22%3A%22browser.base.notifyChange%22%2C"
    "%22notifyKey%22%3A%22t5t.home%22%2C%22data%22%3A%7B"
    "%22type%22%3A%22locateEditor%22%7D%7D%7D"
)
PERIOD_LIST_PATH = "/dgt/tft/weekly/report/list/query"
SELF_WEEKLY_PATH = "/dgt/tft/im/self"
# 查最新单条固定用 1/1；浏览最近列表时才放大 pageSize。
SELF_WEEKLY_DEFAULT_PAGE_NUM = 1
SELF_WEEKLY_DEFAULT_PAGE_SIZE = 1
DETAIL_BY_ID_PATH = "/dgt/tft/weekly/report/queryById/{id}"
LATEST_COPIES_PATH = "/dgt/tft/weekly/report/latest/copies"
COMMIT_PATH = "/dgt/tft/weekly/report/commit"

AUTH_EXPIRED_EXIT_CODE = 4
APP_KEY = "t5t"
CLIENT_USER_AGENT_PREFIX = "T5T-Skill-Client/1.0"
# 业务接口单次 HTTP 请求超时（秒），非认证等待；认证等待（默认 120s）由 im-teams-auth 控制。
DEFAULT_REQUEST_TIMEOUT = 30

SKILLS_ROOT = Path(__file__).resolve().parents[2]
IM_TEAMS_AUTH_DIR = SKILLS_ROOT / "im-teams-auth"
IM_TEAMS_AUTH_SCRIPTS_DIR = IM_TEAMS_AUTH_DIR / "scripts"
IM_TEAMS_AUTH_SCRIPT = IM_TEAMS_AUTH_DIR / "scripts" / "auth.py"
IM_TEAMS_AUTH_CREDENTIAL_STORE = IM_TEAMS_AUTH_SCRIPTS_DIR / "credential_store.py"
