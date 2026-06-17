#!/usr/bin/env python3
"""
Exchange 日历操作脚本

通过 Exchange Web Services (EWS) 协议操作 Exchange 日历，
提供日程创建、读取、搜索、更新、删除等功能。

安全说明：
  - 用户名和密码仅通过浏览器配置页面输入（config_web.py），存入 OS Keyring
  - 命令行和环境变量不接受凭证参数
  - 读取类操作前会校验模型安全性（M1/M2 模型才允许）

使用方式:
    python ews_calendar.py <action> [options]

支持的操作:
    create-event   - 创建日程事件
    list-events    - 列出日程事件（按时间范围）
    search-events  - 搜索日程事件（按主题关键词）
    get-event      - 获取日程详情
    update-event   - 更新日程事件
    delete-event   - 删除日程事件
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# 将脚本目录加入 sys.path，以便导入 common 和 keyring_store
sys.path.insert(0, str(Path(__file__).parent))

from common import (
    check_and_install_dependencies,
    check_model_safety,
    ensure_utf8_output,
    load_config,
    get_account,
    _is_auth_error,
    handle_auth_failure,
    configure_logging,
)

logger = logging.getLogger(__name__)

# 读取类操作集合 — 这些操作会将日历内容返回给模型，需要校验模型安全性
READ_ACTIONS = {
    "list-events", "search-events", "get-event",
}


def event_to_dict(item):
    """将 CalendarItem 对象转换为字典"""
    result = {
        "id": str(item.id) if item.id else "",
        "subject": item.subject or "(无主题)",
        "start": str(item.start) if item.start else "",
        "end": str(item.end) if item.end else "",
        "location": str(item.location) if item.location else "",
        "body_preview": "",
        "organizer": "",
        "is_all_day": item.is_all_day if hasattr(item, 'is_all_day') else False,
        "categories": list(item.categories) if item.categories else [],
    }

    # 正文预览
    if item.text_body:
        result["body_preview"] = item.text_body[:300]
    elif item.body:
        import re
        clean = re.sub(r"<[^>]+>", "", str(item.body))
        result["body_preview"] = clean[:300]

    # 组织者
    if item.organizer:
        try:
            result["organizer"] = str(item.organizer.mailbox.email_address) if hasattr(item.organizer, 'mailbox') and item.organizer.mailbox else str(item.organizer)
        except Exception:
            result["organizer"] = str(item.organizer)

    # 参会者
    if hasattr(item, 'required_attendees') and item.required_attendees:
        attendees_list = []
        for a in item.required_attendees:
            try:
                attendees_list.append(str(a.mailbox.email_address) if hasattr(a, 'mailbox') and a.mailbox else str(a))
            except Exception:
                attendees_list.append(str(a))
        result["required_attendees"] = attendees_list
    if hasattr(item, 'optional_attendees') and item.optional_attendees:
        optional_list = []
        for a in item.optional_attendees:
            try:
                optional_list.append(str(a.mailbox.email_address) if hasattr(a, 'mailbox') and a.mailbox else str(a))
            except Exception:
                optional_list.append(str(a))
        result["optional_attendees"] = optional_list

    return result


def action_create_event(account, args):
    """创建日程事件（需二次确认）"""
    from exchangelib import CalendarItem

    tz = ZoneInfo(args.timezone)
    start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=tz)

    # 构建参会者列表（AttendeesField.clean 会自动将字符串转为 Attendee 对象）
    required_attendees = []
    optional_attendees = []
    if args.attendees:
        for addr in args.attendees.split(","):
            addr = addr.strip()
            if addr:
                required_attendees.append(addr)
    if args.optional_attendees:
        for addr in args.optional_attendees.split(","):
            addr = addr.strip()
            if addr:
                optional_attendees.append(addr)

    # 二次确认机制：未带 --confirm 时，仅展示日程信息供用户确认
    if not args.confirm:
        preview = {
            "success": True,
            "status": "pending_confirm",
            "require_confirm": True,
            "message": "请确认是否创建以下日程，确认后将发送邀请",
            "event_preview": {
                "subject": args.subject,
                "start": args.start,
                "end": args.end,
                "timezone": args.timezone,
                "location": args.location or "未指定",
                "body": (args.body or "无")[:200] + ("..." if len(args.body or "") > 200 else ""),
                "attendees": args.attendees or "无",
                "optional_attendees": args.optional_attendees or "无",
            },
        }
        return preview

    item_kwargs = {
        "account": account,
        "folder": account.calendar,
        "subject": args.subject,
        "start": start_dt,
        "end": end_dt,
        "body": args.body or "",
        "location": args.location or "",
    }
    if required_attendees:
        item_kwargs["required_attendees"] = required_attendees
    if optional_attendees:
        item_kwargs["optional_attendees"] = optional_attendees

    item = CalendarItem(**item_kwargs)
    item.save()

    return {
        "success": True,
        "message": "日程创建成功",
        "event": {
            "subject": args.subject,
            "start": args.start,
            "end": args.end,
            "timezone": args.timezone,
            "location": args.location or "未指定",
            "body": args.body or "无",
            "attendees": args.attendees or "无",
            "optional_attendees": args.optional_attendees or "无",
        }
    }


def action_list_events(account, args):
    """列出日程事件"""
    tz = ZoneInfo(args.timezone)

    if args.start:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    else:
        # 默认从今天开始
        start_dt = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    if args.end:
        end_dt = datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    else:
        # 默认到未来7天
        end_dt = start_dt + timedelta(days=args.days)

    limit = args.limit or 50

    items = account.calendar.view(start=start_dt, end=end_dt).order_by('start')[:limit]

    events = []
    for item in items:
        events.append(event_to_dict(item))

    return {
        "success": True,
        "message": f"找到 {len(events)} 个日程",
        "count": len(events),
        "range": {
            "start": str(start_dt),
            "end": str(end_dt),
        },
        "events": events,
    }


def action_search_events(account, args):
    """搜索日程事件"""
    tz = ZoneInfo(args.timezone)

    # 搜索范围
    if args.start:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    else:
        start_dt = datetime.now(tz) - timedelta(days=30)

    if args.end:
        end_dt = datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    else:
        end_dt = datetime.now(tz) + timedelta(days=90)

    keyword = args.keyword or ""
    limit = args.limit or 50

    qs = account.calendar.view(start=start_dt, end=end_dt).order_by('start')
    if keyword:
        # --keyword 同时搜索主题和正文（OR 逻辑）
        from exchangelib import Q
        qs = qs.filter(Q(subject__contains=keyword) | Q(body__contains=keyword))

    events = []
    for item in qs[:limit]:
        events.append(event_to_dict(item))

    return {
        "success": True,
        "message": f"找到 {len(events)} 个匹配的日程",
        "count": len(events),
        "keyword": keyword,
        "events": events,
    }


def action_get_event(account, args):
    """获取日程详情"""
    from exchangelib import CalendarItem

    event_id = args.event_id
    items = account.calendar.filter(id__in=[event_id])

    for item in items:
        result = {
            "success": True,
            "event": event_to_dict(item),
        }
        # 获取完整正文
        if item.body:
            result["event"]["body_full"] = str(item.body)[:5000]
        return result

    return {"success": False, "message": f"未找到日程: {event_id}"}


def action_update_event(account, args):
    """更新日程事件（需二次确认）"""
    event_id = args.event_id
    items = account.calendar.filter(id__in=[event_id])

    for item in items:
        updated_fields = []
        changes = {}  # 记录变更前后对比

        if args.subject:
            changes["主题"] = {"原值": item.subject or "(无主题)", "新值": args.subject}
            updated_fields.append("主题")
        if args.start:
            tz = ZoneInfo(args.timezone or "Asia/Shanghai")
            new_start = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            changes["开始时间"] = {"原值": str(item.start) if item.start else "", "新值": args.start}
            updated_fields.append("开始时间")
        if args.end:
            tz = ZoneInfo(args.timezone or "Asia/Shanghai")
            new_end = datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            changes["结束时间"] = {"原值": str(item.end) if item.end else "", "新值": args.end}
            updated_fields.append("结束时间")
        if args.body is not None:
            changes["正文"] = {"原值": (item.text_body or str(item.body) or "")[:100], "新值": args.body[:100]}
            updated_fields.append("正文")
        if args.location is not None:
            changes["地点"] = {"原值": str(item.location) if item.location else "", "新值": args.location}
            updated_fields.append("地点")

        if not updated_fields:
            return {"success": False, "message": "未指定任何更新字段"}

        # 二次确认机制：未带 --confirm 时，仅展示变更对比供用户确认
        if not args.confirm:
            preview = {
                "success": True,
                "status": "pending_confirm",
                "require_confirm": True,
                "message": f"请确认是否更新日程，将修改: {', '.join(updated_fields)}",
                "event_preview": {
                    "subject": item.subject or "(无主题)",
                    "start": str(item.start) if item.start else "",
                    "end": str(item.end) if item.end else "",
                },
                "changes": changes,
            }
            return preview

        # 用户确认后，实际执行更新
        if args.subject:
            item.subject = args.subject
        if args.start:
            tz = ZoneInfo(args.timezone or "Asia/Shanghai")
            item.start = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        if args.end:
            tz = ZoneInfo(args.timezone or "Asia/Shanghai")
            item.end = datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        if args.body is not None:
            item.body = args.body
        if args.location is not None:
            item.location = args.location

        item.save()

        return {
            "success": True,
            "message": f"日程已更新: {', '.join(updated_fields)}",
            "event": event_to_dict(item),
        }

    return {"success": False, "message": f"未找到日程: {event_id}"}


def action_delete_event(account, args):
    """删除日程事件（需二次确认）"""
    from exchangelib import CalendarItem

    event_id = args.event_id
    items = account.calendar.filter(id__in=[event_id])

    for item in items:
        # 二次确认机制：未带 --confirm 时，仅展示日程信息供用户确认，不实际删除
        if not args.confirm:
            return {
                "success": True,
                "status": "pending_confirm",
                "require_confirm": True,
                "message": "请确认是否删除以下日程，确认后将删除",
                "event_preview": {
                    "subject": item.subject or "(无主题)",
                    "start": str(item.start) if item.start else "",
                    "end": str(item.end) if item.end else "",
                    "location": str(item.location) if item.location else "",
                },
            }
        item.delete()
        return {"success": True, "message": "日程已删除", "subject": item.subject}

    return {"success": False, "message": f"未找到日程: {event_id}"}


def main():
    parser = argparse.ArgumentParser(description="Exchange 日历操作脚本")
    subparsers = parser.add_subparsers(dest="action", help="操作类型")

    # 创建日程
    create_parser = subparsers.add_parser("create-event", help="创建日程事件")
    create_parser.add_argument("--subject", required=True, help="事件主题")
    create_parser.add_argument("--start", required=True, help="开始时间，格式: YYYY-MM-DD HH:MM")
    create_parser.add_argument("--end", required=True, help="结束时间，格式: YYYY-MM-DD HH:MM")
    create_parser.add_argument("--body", default="", help="事件正文/描述")
    create_parser.add_argument("--location", default="", help="会议地点")
    create_parser.add_argument("--attendees", default="", help="必选参会人邮箱，多个用逗号分隔")
    create_parser.add_argument("--optional-attendees", default="", help="可选参会人邮箱，多个用逗号分隔")
    create_parser.add_argument("--timezone", default="Asia/Shanghai", help="时区，默认: Asia/Shanghai")
    create_parser.add_argument("--confirm", action="store_true",
                               help="确认创建日程（首次不带此参数仅预览，需二次确认）")

    # 列出日程
    list_parser = subparsers.add_parser("list-events", help="列出日程事件")
    list_parser.add_argument("--start", default="", help="开始时间，格式: YYYY-MM-DD HH:MM（默认今天0点）")
    list_parser.add_argument("--end", default="", help="结束时间，格式: YYYY-MM-DD HH:MM")
    list_parser.add_argument("--days", type=int, default=7, help="查询天数（默认7天，仅在未指定--end时生效）")
    list_parser.add_argument("--limit", type=int, default=50, help="最大返回数量")
    list_parser.add_argument("--timezone", default="Asia/Shanghai", help="时区，默认: Asia/Shanghai")

    # 搜索日程
    search_parser = subparsers.add_parser("search-events", help="搜索日程事件")
    search_parser.add_argument("--keyword", default="", help="搜索关键词（匹配主题）")
    search_parser.add_argument("--start", default="", help="搜索开始时间，格式: YYYY-MM-DD HH:MM")
    search_parser.add_argument("--end", default="", help="搜索结束时间，格式: YYYY-MM-DD HH:MM")
    search_parser.add_argument("--limit", type=int, default=50, help="最大返回数量")
    search_parser.add_argument("--timezone", default="Asia/Shanghai", help="时区，默认: Asia/Shanghai")

    # 获取日程详情
    get_parser = subparsers.add_parser("get-event", help="获取日程详情")
    get_parser.add_argument("--event-id", required=True, help="日程ID")

    # 更新日程
    update_parser = subparsers.add_parser("update-event", help="更新日程事件")
    update_parser.add_argument("--event-id", required=True, help="日程ID")
    update_parser.add_argument("--subject", default=None, help="新主题")
    update_parser.add_argument("--start", default=None, help="新开始时间，格式: YYYY-MM-DD HH:MM")
    update_parser.add_argument("--end", default=None, help="新结束时间，格式: YYYY-MM-DD HH:MM")
    update_parser.add_argument("--body", default=None, help="新正文/描述")
    update_parser.add_argument("--location", default=None, help="新地点")
    update_parser.add_argument("--timezone", default="Asia/Shanghai", help="时区，默认: Asia/Shanghai")
    update_parser.add_argument("--confirm", action="store_true",
                               help="确认更新日程（首次不带此参数仅预览，需二次确认）")

    # 删除日程
    delete_parser = subparsers.add_parser("delete-event", help="删除日程事件")
    delete_parser.add_argument("--event-id", required=True, help="日程ID")
    delete_parser.add_argument("--confirm", action="store_true",
                               help="确认删除日程（首次不带此参数仅预览，需二次确认）")

    # 日志参数（添加到主 parser，所有子命令共享）
    parser.add_argument("--verbose", action="store_true", help="显示详细日志")
    parser.add_argument("--debug", action="store_true", help="显示调试日志")
    parser.add_argument("--confirmed-risk", action="store_true",
                        help="用户已确认模型安全风险（用户确认后重新执行时使用）")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(1)

    # 确保 stdout/stderr 使用 UTF-8 编码（防止 Windows 下中文乱码）
    ensure_utf8_output()

    # 配置日志级别
    configure_logging(verbose=args.verbose, debug=args.debug)

    # 环境预检测 + 依赖自动安装
    check_and_install_dependencies()
    config = load_config()

    # 模型安全校验（读取类操作）— 放在环境/依赖/配置检查之后
    # --confirmed-risk 用于用户确认风险后重新执行
    if not args.confirmed_risk and not check_model_safety(args.action, READ_ACTIONS):
        sys.exit(2)  # 退出码 2 = 等待用户确认

    # 连接服务器
    try:
        account = get_account(config, verify_target="calendar")
    except (ConnectionError, Exception) as e:
        # 认证失败 → 清除 Keyring 缓存，退出码 4 要求重新认证
        if _is_auth_error(e):
            handle_auth_failure(e)
        print(json.dumps({"success": False, "message": f"连接服务器失败: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)

    logger.debug("Executing action: %s", args.action)

    # 执行操作
    action_map = {
        "create-event": action_create_event,
        "list-events": action_list_events,
        "search-events": action_search_events,
        "get-event": action_get_event,
        "update-event": action_update_event,
        "delete-event": action_delete_event,
    }

    try:
        result = action_map[args.action](account, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # 删除日程待确认 → 退出码 3
        if isinstance(result, dict) and result.get("status") == "pending_confirm":
            sys.exit(3)

    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"success": False, "message": f"操作失败: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()