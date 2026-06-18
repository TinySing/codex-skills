#!/usr/bin/env python3
"""T5T 新建和编辑流程共用的认证、请求与数据处理能力。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import platform
import ssl
import sys
import uuid

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from config import (
    ACTIVE_ENVIRONMENT,
    API_PREFIX,
    APP_KEY,
    AUTH_EXPIRED_EXIT_CODE,
    CLIENT_USER_AGENT_PREFIX,
    COMMIT_PATH,
    DETAIL_BY_ID_PATH,
    DEFAULT_REQUEST_TIMEOUT,
    ENV_DOMAINS,
    IM_TEAMS_AUTH_CREDENTIAL_STORE,
    IM_TEAMS_AUTH_SCRIPTS_DIR,
    LATEST_COPIES_PATH,
    PERIOD_LIST_PATH,
    PREVIEW_PATH,
    SELF_WEEKLY_DEFAULT_PAGE_NUM,
    SELF_WEEKLY_DEFAULT_PAGE_SIZE,
    SELF_WEEKLY_PATH,
)

_SSL_CONTEXT = ssl.create_default_context()


class T5TError(Exception):
    pass


class AuthExpiredError(T5TError):
    pass


class FormatError(T5TError):
    """用户内容/参数格式问题：可在本地修正后重新生成，不应重试或联网试错。"""
    pass


class NetworkError(T5TError):
    """本机到服务的网络不通/超时：对用户明说是网络问题，不重试。"""
    pass


def _load_gateway_errors():
    """加载认证子模块（scripts/auth/）的统一网关错误码分类（纯数据模块，无内部依赖）。"""
    spec = importlib.util.spec_from_file_location(
        "im_teams_auth_gateway_errors",
        IM_TEAMS_AUTH_SCRIPTS_DIR / "gateway_errors.py",
    )
    if spec is None or spec.loader is None:
        raise T5TError("无法加载统一网关错误码模块 gateway_errors.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway_errors = _load_gateway_errors()


def force_utf8_io() -> None:
    """强制 stdout/stderr 用 UTF-8。

    Windows 上 stdout 被重定向到管道时默认用 ANSI 代码页（如 cp936），
    打印含 emoji 等非 GBK 字符的中文 JSON 会抛 UnicodeEncodeError。
    入口脚本在任何输出前调用，保证跨平台一致。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


def json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def ensure_success(payload: Any, action: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise T5TError(f"{action}返回格式异常: {payload}")
    if payload.get("code") != 0:
        detail = str(payload.get("msg") or "").strip()
        if detail:
            raise T5TError(f"{action}失败：{detail}")
        raise T5TError(f"{action}失败，请稍后再试")
    return payload


def _load_credential_store():
    original_path = list(sys.path)
    original_config = sys.modules.pop("config", None)
    original_errors = sys.modules.pop("errors", None)
    try:
        sys.path.insert(0, str(IM_TEAMS_AUTH_SCRIPTS_DIR))
        spec = importlib.util.spec_from_file_location(
            "im_teams_auth_credential_store",
            IM_TEAMS_AUTH_CREDENTIAL_STORE,
        )
        if spec is None or spec.loader is None:
            raise AuthExpiredError(
                f"无法加载认证子模块凭证存储: {IM_TEAMS_AUTH_CREDENTIAL_STORE}"
            )
        credential_store = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(credential_store)
        return credential_store
    finally:
        sys.path[:] = original_path
        sys.modules.pop("config", None)
        sys.modules.pop("errors", None)
        if original_config is not None:
            sys.modules["config"] = original_config
        if original_errors is not None:
            sys.modules["errors"] = original_errors


def _read_cached_token(environment: str) -> str | None:
    credential_store = _load_credential_store()
    env_token = os.environ.get(credential_store.env_token_name_for(environment))
    if env_token:
        return env_token
    token = credential_store.keyring_load_token(environment)
    return token or None


def load_token(environment: str) -> str:
    # keyring/环境变量有有效 token 时直接复用。
    # 未认证时立即退出码 4，不在业务脚本内部拉起交互式认证：
    # 交互认证需要本机监听端口并等待用户操作，嵌在业务调用里会长时间阻塞，
    # 认证统一由代理按 SKILL.md 分支协议显式调用认证子模块（scripts/auth/）。
    token = _read_cached_token(environment)
    if token:
        return token
    raise AuthExpiredError("未认证或认证已过期")


def load_current_environment() -> str:
    override = os.environ.get("T5T_ENV")
    if override:
        normalized = override.strip().lower()
        if normalized in ENV_DOMAINS:
            return normalized
        raise T5TError(f"T5T_ENV 配置了不支持的环境: {override}")

    normalized = ACTIVE_ENVIRONMENT.strip().lower()
    if normalized not in ENV_DOMAINS:
        raise T5TError(
            f"scripts/config.py 的 ACTIVE_ENVIRONMENT 配置了不支持的环境: "
            f"{ACTIVE_ENVIRONMENT}"
        )
    return normalized


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env",
        choices=sorted(ENV_DOMAINS),
        default=load_current_environment(),
        help="临时覆盖当前环境；未传时使用 scripts/config.py 中的配置",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("T5T_BASE_URL"),
        help="显式覆盖接口域名",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT)


def resolve_base_url(args: argparse.Namespace) -> str:
    return args.base_url or f"{ENV_DOMAINS[args.env]}{API_PREFIX}"


def build_headers(token: str) -> dict[str, str]:
    system_name = platform.system() or "UnknownOS"
    python_version = platform.python_version()
    return {
        "User-Agent": f"{CLIENT_USER_AGENT_PREFIX} ({system_name}; Python {python_version})",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Appkey": APP_KEY,
        "Authorization": token,
    }


def create_request_context(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    token = load_token(args.env)
    return resolve_base_url(args), build_headers(token)


def request_json(
    base_url: str,
    path: str,
    method: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    url = urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(
            request,
            context=_SSL_CONTEXT,
            timeout=timeout,
        ) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        # 识别鉴权失效（删 token 重认证）与网络相关码；其余 HTTP 错误粗处理为服务端不可用。
        if gateway_errors.is_auth_code(exc.code):
            raise AuthExpiredError("认证已失效或无权限，请重新认证后重试") from exc
        if gateway_errors.is_network_code(exc.code):
            raise NetworkError("网络连接失败，请稍后再试") from exc
        raise T5TError("服务暂时不可用，请稍后再试") from exc
    except (URLError, TimeoutError) as exc:
        raise NetworkError("网络连接失败，请稍后再试") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise T5TError("接口返回异常，请稍后再试") from exc
    # 成功（code 0）直接返回；鉴权失效码触发删 token + 重认证；网络相关码提示网络；
    # 其余非 0 业务码不细分，交 ensure_success 统一报错（带步骤名）。
    code = payload.get("code")
    if code in (0, None):
        return payload
    if gateway_errors.is_auth_code(code):
        # 服务端已判定 token 失效（即使本地未过期），触发删 token + 重认证。
        raise AuthExpiredError("认证已失效，请重新认证")
    if gateway_errors.is_network_code(code):
        raise NetworkError("网络连接失败，请稍后再试")
    return payload


def query_periods(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
) -> tuple[dict[str, Any], list[Any]]:
    response = ensure_success(
        request_json(base_url, PERIOD_LIST_PATH, "GET", headers, timeout=timeout),
        "获取周期列表",
    )
    periods = response.get("data")
    if not isinstance(periods, list):
        raise T5TError(f"周期列表返回格式异常: {response}")
    return response, periods


def query_latest_copies(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
) -> tuple[dict[str, Any], list[Any]]:
    response = ensure_success(
        request_json(base_url, LATEST_COPIES_PATH, "GET", headers, timeout=timeout),
        "获取历史抄送人",
    )
    copies = response.get("data")
    if not isinstance(copies, list):
        raise T5TError(f"历史抄送人返回格式异常: {response}")
    return response, copies


def query_detail(
    base_url: str,
    headers: dict[str, str],
    detail_id: str,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = DETAIL_BY_ID_PATH.format(id=detail_id)
    response = ensure_success(
        request_json(base_url, path, "GET", headers, timeout=timeout),
        "查询 T5T 详情",
    )
    detail = response.get("data")
    if not isinstance(detail, dict):
        raise T5TError(f"T5T 详情返回格式异常: {response}")
    return response, detail


def build_self_weekly_path(
    page_num: int = SELF_WEEKLY_DEFAULT_PAGE_NUM,
    page_size: int = SELF_WEEKLY_DEFAULT_PAGE_SIZE,
) -> str:
    if page_num < 1 or page_size < 1:
        raise T5TError("pageNum 和 pageSize 必须 >= 1")
    return f"{SELF_WEEKLY_PATH}?pageNum={page_num}&pageSize={page_size}"


def query_self_weekly_list(
    base_url: str,
    headers: dict[str, str],
    page_num: int,
    page_size: int,
    timeout: int,
) -> tuple[dict[str, Any], list[Any]]:
    path = build_self_weekly_path(page_num, page_size)
    response = ensure_success(
        request_json(base_url, path, "GET", headers, timeout=timeout),
        "查询最近 T5T 列表",
    )
    items = response.get("data")
    if not isinstance(items, list):
        raise T5TError(f"最近 T5T 列表返回格式异常: {response}")
    return response, items


def query_latest_detail(
    base_url: str,
    headers: dict[str, str],
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    latest_response = ensure_success(
        request_json(base_url, build_self_weekly_path(), "GET", headers, timeout=timeout),
        "查询最新 T5T",
    )
    latest_list = latest_response.get("data")
    if not isinstance(latest_list, list):
        raise T5TError(f"最新 T5T 列表返回格式异常: {latest_response}")
    if not latest_list:
        return latest_response, None, None
    latest_id = latest_list[0].get("weekId") or latest_list[0].get("id")
    if latest_id in (None, ""):
        raise T5TError(f"最新 T5T 数据缺少 id: {latest_list[0]}")
    detail_response, detail = query_detail(
        base_url,
        headers,
        str(latest_id),
        timeout,
    )
    return latest_response, detail_response, detail


def commit_payload(
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    return ensure_success(
        request_json(
            base_url,
            COMMIT_PATH,
            "POST",
            headers,
            body=payload,
            timeout=timeout,
        ),
        "提交 T5T",
    )


def read_items(args: argparse.Namespace, required: bool = True) -> list[str] | None:
    if not args.items_json and not args.content_file and not required:
        return None
    if args.items_json:
        raw = json.loads(args.items_json)
    else:
        text = (
            open(args.content_file, "r", encoding="utf-8").read()
            if args.content_file
            else sys.stdin.read()
        ).strip()
        if not text:
            raise FormatError("T5T 内容为空")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = [line.strip() for line in text.splitlines() if line.strip()]
    return extract_values(raw)


def extract_values(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise FormatError("T5T 内容必须是 JSON 数组或逐行文本")
    values = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = str(item.get("value") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            values.append(value)
    if not values:
        raise FormatError("请至少提供一条 T5T")
    if len(values) > 5:
        raise FormatError("T5T 最多只能提供 5 条")
    for index, value in enumerate(values, start=1):
        if len(value) > 80:
            raise FormatError(f"第 {index} 条超过 80 个字符")
    return values


def parse_raw_content(raw_content: Any) -> list[str]:
    if isinstance(raw_content, str):
        try:
            raw_content = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise T5TError("详情中的 T5T 内容格式异常") from exc
    return extract_values(raw_content)


def _extract_values_lenient(raw: Any) -> list[str]:
    """只读展示用：提取已写入的条目文本，不校验条数与长度。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    values = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = str(item.get("value") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            values.append(value)
    return values


def format_recent_item(item: Any) -> dict[str, Any]:
    """把 /im/self 列表项收敛成展示用的周期名 + 内容。"""
    if not isinstance(item, dict):
        return {"raw": item}
    record: dict[str, Any] = {
        "id": item.get("weekId") or item.get("id"),
        "period": item.get("reportName") or item.get("title") or item.get("name"),
        "items": _extract_values_lenient(item.get("rawContent")),
    }
    if "canOperate" in item:
        record["canOperate"] = bool(item.get("canOperate"))
    return record


def print_latest_detail(environment: str, detail: dict[str, Any]) -> None:
    """把最新已填写 T5T 详情收敛成展示用字段（查询与编辑前置共用）。"""
    to_list = detail.get("toList") or []
    if not isinstance(to_list, list):
        raise T5TError("详情中的权限格式异常")
    # 只输出编辑和展示需要的字段；完整接口响应不回显，减少代理要读的内容
    json_print(
        {
            "status": "latest_detail",
            "environment": environment,
            "id": detail.get("id"),
            "period": {
                "reportId": detail.get("reportId"),
                "name": detail.get("reportName") or detail.get("title"),
            },
            "items": parse_raw_content(detail.get("rawContent")),
            "toList": to_list,
            "permissions": format_copies_info(to_list),
            "inviteSameGroupView": bool(detail.get("inviteSameGroupView", True)),
            "sameGroupVisible": format_same_group(detail.get("inviteSameGroupView", True)),
            "canOperate": bool(detail.get("canOperate")),
        }
    )


def read_to_list(args: argparse.Namespace) -> list[Any] | None:
    if not args.to_list_json:
        return None
    try:
        to_list = json.loads(args.to_list_json)
    except json.JSONDecodeError as exc:
        raise FormatError(f"权限 JSON 解析失败: {exc}") from exc
    if not isinstance(to_list, list):
        raise FormatError("权限必须是 JSON 数组")
    return to_list


def read_invite_same_group(args: argparse.Namespace) -> bool | None:
    value = getattr(args, "invite_same_group", None)
    if value is None:
        return None
    return str(value).strip().lower() == "true"


def format_same_group(value: Any) -> str:
    return "同组可见" if value else "同组不可见"


def validate_reduced_to_list(
    current_to_list: list[Any],
    to_list: list[Any],
) -> None:
    if len(to_list) >= len(current_to_list):
        raise FormatError("编辑抄送人时只能删除现有人员")

    current_counts: dict[str, int] = {}
    for person in current_to_list:
        key = json.dumps(person, ensure_ascii=False, sort_keys=True)
        current_counts[key] = current_counts.get(key, 0) + 1

    for person in to_list:
        key = json.dumps(person, ensure_ascii=False, sort_keys=True)
        if not current_counts.get(key):
            raise FormatError("抄送人只能从当前权限中删除，不能新增或修改")
        current_counts[key] -= 1


def format_copies_info(copies: list[Any]) -> str:
    if not copies:
        return "无抄送人"
    copy_names = []
    for copy in copies:
        name = copy.get("name", "")
        org_path = copy.get("orgPath", "")
        if not name:
            continue
        path_parts = org_path.split("-")
        short_path = "-".join(path_parts[-2:]) if len(path_parts) >= 2 else org_path
        copy_names.append(f"{name}（{short_path}）" if short_path else name)
    return "、".join(copy_names)


def select_period(periods: list[Any], report_id: str | None = None) -> dict[str, Any]:
    if not report_id:
        return periods[0]
    for period in periods:
        if str(period.get("reportId")) == report_id:
            return period
    raise T5TError(f"已确认周期不可用或已填写: {report_id}")


def _make_summary_items(
    values: list[str],
    current_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    today = dt.datetime.now().strftime("%Y%m%d")
    items = []
    for index in range(5):
        current = current_items[index] if current_items and index < len(current_items) else {}
        if not isinstance(current, dict):
            current = {}
        items.append(
            {
                **current,
                "id": current.get("id") or f"{today}_{uuid.uuid4()}",
                "value": values[index] if index < len(values) else "",
                "state": current.get("state") or "normal",
                "sort": index,
            }
        )
    return items


def build_create_payload(
    period: dict[str, Any],
    copies: list[Any],
    values: list[str],
    invite_same_group: bool | None = None,
) -> dict[str, Any]:
    report_id = period.get("reportId")
    report_name = str(period.get("name") or "").strip()
    if report_id in (None, "") or not report_name:
        raise T5TError(f"周期数据缺少必要字段: {period}")
    return {
        "id": "",
        "reportId": report_id,
        "reportName": report_name,
        "title": report_name,
        "toList": copies,
        "rawContent": json.dumps(_make_summary_items(values), ensure_ascii=False),
        "notifyFlag": False,
        "inviteSameGroupView": True if invite_same_group is None else invite_same_group,
        "updateStamp": "",
        "canOperate": True,
    }


def build_edit_payload(
    detail: dict[str, Any],
    values: list[str] | None,
    to_list: list[Any] | None,
    invite_same_group: bool | None = None,
) -> dict[str, Any]:
    if not detail.get("canOperate"):
        raise T5TError("该 T5T 当前不可修改")
    detail_id = detail.get("id")
    report_id = detail.get("reportId")
    report_name = str(detail.get("reportName") or detail.get("title") or "").strip()
    if detail_id in (None, "") or report_id in (None, "") or not report_name:
        raise T5TError("详情数据缺少必要字段")
    current_to_list = detail.get("toList") or []
    if not isinstance(current_to_list, list):
        raise T5TError("详情中的权限格式异常")
    if to_list is not None:
        validate_reduced_to_list(current_to_list, to_list)

    raw_content = detail.get("rawContent")
    if values is not None:
        current_items = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        if not isinstance(current_items, list):
            raise T5TError("详情中的 T5T 内容格式异常")
        raw_content = json.dumps(
            _make_summary_items(values, current_items),
            ensure_ascii=False,
        )
    return {
        "id": detail_id,
        "reportId": report_id,
        "reportName": report_name,
        "title": detail.get("title") or report_name,
        "toList": to_list if to_list is not None else current_to_list,
        "rawContent": raw_content,
        "notifyFlag": detail.get("notifyFlag", False),
        "inviteSameGroupView": detail.get("inviteSameGroupView", True)
        if invite_same_group is None
        else invite_same_group,
        "updateStamp": detail.get("updateStamp") or "",
        "canOperate": True,
    }


def build_confirmation_hash(
    environment: str,
    base_url: str,
    payload: dict[str, Any],
) -> str:
    normalized_payload = {
        **payload,
        "rawContent": parse_raw_content(payload.get("rawContent")),
    }
    serialized = json.dumps(
        {
            "environment": environment,
            "baseUrl": base_url,
            "payload": normalized_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_preview_url(environment: str) -> str:
    return f"{ENV_DOMAINS[environment]}{PREVIEW_PATH}"


def print_success(
    environment: str,
    base_url: str,
    confirmation_hash: str,
    mode: str,
    period: dict[str, Any],
    items: list[str],
    copies: list[Any],
    responses: dict[str, Any],
    same_group_visible: bool = True,
    concise: bool = False,
) -> None:
    preview_url = build_preview_url(environment)
    result: dict[str, Any] = {
        "status": "ok",
        "message": "T5T 提交成功",
        "previewUrl": preview_url,
        "previewLink": f"[点击预览 T5T]({preview_url})",
        "previewOpened": False,
        "period": period,
        "mode": mode,
        "items": items,
        "toList": copies,
        "permissions": format_copies_info(copies),
        "inviteSameGroupView": same_group_visible,
        "sameGroupVisible": format_same_group(same_group_visible),
    }
    if concise:
        json_print(result)
        return

    print("\n" + "=" * 60)
    print("T5T 提交成功")
    print("=" * 60)
    print(f"周期：{period.get('name', '未知周期')}")
    print("\nT5T 内容：")
    for index, value in enumerate(items, 1):
        print(f"  {index}. {value}")
    print(f"\n权限：{format_copies_info(copies)}")
    print(f"\n同组可见：{format_same_group(same_group_visible)}")
    print(f"\n[点击预览 T5T]({preview_url})")
    print("=" * 60)
    json_print(
        {
            **result,
            "environment": environment,
            "baseUrl": base_url,
            "confirmationHash": confirmation_hash,
            "responses": responses,
        }
    )


def print_error(error: Exception, environment: str) -> int:
    if isinstance(error, AuthExpiredError):
        json_print(
            {
                "status": "expired",
                "message": str(error),
                "hint": (
                    "两段式认证：先运行 scripts/auth/auth.py --start --no-cache"
                    "（秒级返回；必须带 --no-cache，本地缓存的 token 可能已被服务端判定失效），"
                    "把返回的 appLinkUrl（在 Teams 中打开认证，https，任何客户端都可点）以可点击链接"
                    "立即发给用户（输出包含 landingUrl 时再附浏览器一条；窗口没弹出或被关掉时点链接可重新打开）；"
                    "不要发 schemeUrl（teamssit://，多数客户端点不开）。再运行 auth.py --wait 等待结果。"
                    "认证成功后重试原命令一次；认证失败或重试仍失败时，停止调用接口，"
                    "把已生成的 T5T 内容直接交给用户并说明需手动提交。本次任务最多一轮认证。"
                ),
                "environment": environment,
            }
        )
        return AUTH_EXPIRED_EXIT_CODE
    if isinstance(error, (FormatError, json.JSONDecodeError)):
        message = str(error)
        if isinstance(error, json.JSONDecodeError):
            message = f"T5T JSON 解析失败: {error}"
        json_print(
            {
                "status": "error",
                "message": message,
                "hint": (
                    "内容或参数格式不符，请在本地修正后重新生成内容再提交"
                    "（内容可先用 --validate-items 本地自检），最多 3 次；"
                    "不要联网试错，也不要重试提交接口。"
                ),
                "environment": environment,
            }
        )
        return 1
    if isinstance(error, NetworkError):
        json_print(
            {
                "status": "error",
                "message": str(error),
                "hint": (
                    "这是网络连接问题。脚本已正常运行并返回此结果，"
                    "解释器没问题——不要换 python/py 重试、不要探测 Python 环境、不要列目录、不要连环调用其他接口排查；"
                    "明确告诉用户是网络连接失败、请检查网络后稍后再试，"
                    "并把已生成的 T5T 内容交给用户。"
                ),
                "environment": environment,
            }
        )
        return 1
    # 服务端/系统错误：一次失败即停，不重试。
    json_print(
        {
            "status": "error",
            "message": str(error),
            "hint": (
                "这是服务端/系统问题。脚本已正常运行并返回此结果，"
                "解释器没问题——不要换 python/py 重试、不要探测 Python 环境、不要列目录、不要连环调用其他接口排查；"
                "告诉用户系统暂时不可用、稍后再试，不要暴露 code 或接口细节，"
                "并把已生成的 T5T 内容交给用户。"
            ),
            "environment": environment,
        }
    )
    return 1
