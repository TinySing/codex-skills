#!/usr/bin/env python3
"""
Exchange 邮件操作脚本

通过 Exchange Web Services (EWS) 协议连接自建 Exchange 服务器，
提供邮件读取、搜索、发送、附件处理等功能。

安全说明：
  - 用户名和密码仅通过浏览器配置页面输入（config_web.py），存入 OS Keyring
  - 命令行和环境变量不接受凭证参数
  - 读取类操作前会校验模型安全性（M1/M2 模型才允许）

使用方式:
    python exchange_mail.py --action <action> [options]

示例:
    python exchange_mail.py --action recent
    python exchange_mail.py --action detail --email-id <邮件ID>
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

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

# 读取类操作集合 — 这些操作会将邮件内容返回给模型，需要校验模型安全性
READ_ACTIONS = {
    "recent", "unread", "detail", "search", "folder",
    "list-folders", "search-gal", "attachments", "extract",
}


def get_folder(account, folder_name):
    """根据名称获取文件夹对象"""
    folder_map = {
        "inbox": account.inbox,
        "sent": account.sent,
        "drafts": account.drafts,
        "trash": account.trash,
        "junk": account.junk,
        "deleted": account.trash,
        "outbox": account.outbox,
    }

    folder_name_lower = folder_name.lower()
    if folder_name_lower in folder_map:
        return folder_map[folder_name_lower]

    for folder in account.root.walk():
        if folder.name.lower() == folder_name_lower:
            return folder

    raise ValueError(f"未找到文件夹: {folder_name}")


def find_email_by_id(account, email_id):
    """按 ID 查找邮件，优先使用全局搜索，回退到逐文件夹搜索"""
    # Strategy 1: 全局搜索（1 次 EWS 请求）
    try:
        item = account.root.get(id=email_id)
        if item:
            return item
    except Exception:
        pass

    # Strategy 2: 回退到逐文件夹搜索，限制文件夹数量防止过多请求
    FOLDER_LIMIT = 20
    folder_count = 0

    for folder in account.root.walk():
        if folder.folder_class != "IPF.Note":
            continue
        folder_count += 1
        if folder_count > FOLDER_LIMIT:
            logger.warning("已搜索 %d 个文件夹未找到邮件，停止搜索", FOLDER_LIMIT)
            break
        try:
            item = folder.get(id=email_id)
            if item:
                return item
        except Exception:
            continue

    return None


def email_to_dict(item, include_body=False):
    """将 EWS Message 对象转换为字典"""
    body_preview = ""
    if item.text_body:
        body_preview = item.text_body[:200]
    elif item.body:
        clean = re.sub(r"<[^>]+>", "", str(item.body))
        body_preview = clean[:200]

    sender = ""
    if item.sender:
        sender = str(item.sender.email_address) if item.sender.email_address else str(item.sender)

    to_list = []
    if item.to_recipients:
        to_list = [str(r.email_address) if r.email_address else str(r) for r in item.to_recipients]

    cc_list = []
    if item.cc_recipients:
        cc_list = [str(r.email_address) if r.email_address else str(r) for r in item.cc_recipients]

    result = {
        "id": str(item.id) if item.id else "",
        "subject": item.subject or "(无主题)",
        "sender": sender,
        "to": to_list,
        "cc": cc_list,
        "datetime": str(item.datetime_received),
        "body_preview": body_preview,
        "has_attachments": item.has_attachments,
        "is_read": item.is_read,
    }

    if include_body:
        if item.text_body:
            result["body"] = item.text_body
        elif item.body:
            result["body"] = str(item.body)
        else:
            result["body"] = ""
        bcc_list = []
        if item.bcc_recipients:
            bcc_list = [str(r.email_address) if r.email_address else str(r) for r in item.bcc_recipients]
        result["bcc"] = bcc_list
        result["importance"] = str(item.importance) if item.importance else "Normal"
        result["categories"] = list(item.categories) if item.categories else []

    return result


# 列表场景下 to/cc 最多显示的收件人数量（超出部分用 to_count/cc_count 概括）
_MAX_RECIPIENTS_IN_LIST = 3


def _truncate_recipients(recipients, max_count):
    """截断收件人列表：最多显示 max_count 个，超出部分用 count 字段概括

    Returns:
        (display_list, total_count)
    """
    addr_list = [str(r.email_address) if r.email_address else str(r) for r in recipients]
    total = len(addr_list)
    if total <= max_count:
        return addr_list, total
    return addr_list[:max_count], total


def email_to_summary(item):
    """将 EWS Message 对象转换为精简摘要字典（用于列表展示，减少输出体积）

    与 email_to_dict 的区别：
      - to/cc 最多显示 3 个 + to_count/cc_count 概括（群发邮件是输出膨胀主因）
      - body_preview 截断到 80 字符（列表场景不需要 200 字符）
      - 不输出 bcc/importance/categories（详情接口才有）
    """
    body_preview = ""
    if item.text_body:
        body_preview = item.text_body[:80]
    elif item.body:
        clean = re.sub(r"<[^>]+>", "", str(item.body))
        body_preview = clean[:80]

    sender = ""
    if item.sender:
        sender = str(item.sender.email_address) if item.sender.email_address else str(item.sender)

    to_display, to_total = _truncate_recipients(item.to_recipients or [], _MAX_RECIPIENTS_IN_LIST)
    cc_display, cc_total = _truncate_recipients(item.cc_recipients or [], _MAX_RECIPIENTS_IN_LIST)

    result = {
        "id": str(item.id) if item.id else "",
        "subject": item.subject or "(无主题)",
        "sender": sender,
        "to": to_display,
        "datetime": str(item.datetime_received),
        "body_preview": body_preview,
        "has_attachments": item.has_attachments,
        "is_read": item.is_read,
    }

    if to_total > _MAX_RECIPIENTS_IN_LIST:
        result["to_count"] = to_total
    if cc_total > 0:
        result["cc"] = cc_display
        if cc_total > _MAX_RECIPIENTS_IN_LIST:
            result["cc_count"] = cc_total

    return result


def action_recent(account, args):
    """获取最近邮件"""
    folder = get_folder(account, args.folder or "inbox")
    limit = args.limit or 20
    qs = folder.all().order_by("-datetime_received")[:limit]
    return [email_to_summary(item) for item in qs]


def action_unread(account, args):
    """获取未读邮件"""
    limit = args.limit or 50
    qs = account.inbox.filter(is_read=False).order_by("-datetime_received")[:limit]
    return [email_to_summary(item) for item in qs]


def action_detail(account, args):
    """获取邮件详情"""
    if not args.email_id:
        return {"success": False, "message": "缺少 --email-id 参数"}

    item = find_email_by_id(account, args.email_id)
    if not item:
        return {"success": False, "message": "邮件未找到"}

    return email_to_dict(item, include_body=True)


def _resolve_sender(account, sender_value):
    """解析 --sender 参数：如果是中文名（非邮箱格式），尝试通过 GAL 解析为邮箱地址

    Returns:
        list[str]: 用于 sender__contains 匹配的候选值列表
    """
    # 如果已经是邮箱格式（含 @），直接使用
    if "@" in sender_value:
        return [sender_value]

    # 尝试通过 GAL 解析中文名 → 邮箱地址
    try:
        results = account.protocol.resolve_names(
            [sender_value],
            return_full_contact_data=False,
            search_scope="ActiveDirectory",
        )
        emails = []
        for item in results:
            if isinstance(item, Exception):
                continue
            if hasattr(item, 'email_address') and item.email_address:
                emails.append(item.email_address)
        if emails:
            # 找到邮箱：同时用原名和邮箱搜索，覆盖显示名匹配和地址匹配
            return [sender_value] + emails
    except Exception:
        pass

    # GAL 解析失败，返回原值（回退到直接用 sender__contains 匹配）
    return [sender_value]


def action_search(account, args):
    """搜索邮件"""
    from exchangelib import Q

    folder = get_folder(account, args.folder or "inbox")
    limit = args.limit or 50
    qs = folder.all()

    if args.unread_only:
        qs = qs.filter(is_read=False)

    if args.sender:
        # --sender 智能解析：中文名 → GAL 查邮箱 → 同时匹配原名和邮箱
        sender_candidates = _resolve_sender(account, args.sender)
        if len(sender_candidates) == 1:
            qs = qs.filter(sender__contains=sender_candidates[0])
        else:
            # 多个候选值（原名 + 解析出的邮箱），用 OR 逻辑匹配
            sender_q = Q(sender__contains=sender_candidates[0])
            for candidate in sender_candidates[1:]:
                sender_q = sender_q | Q(sender__contains=candidate)
            qs = qs.filter(sender_q)
    if args.subject:
        qs = qs.filter(subject__contains=args.subject)
    if args.keyword:
        # --keyword 同时搜索主题和正文（OR 逻辑）
        qs = qs.filter(Q(subject__contains=args.keyword) | Q(body__contains=args.keyword))

    qs = qs.order_by("-datetime_received")[:limit]
    return [email_to_summary(item) for item in qs]


def action_folder(account, args):
    """获取指定文件夹邮件"""
    if not args.folder_name:
        return {"success": False, "message": "缺少 --folder-name 参数"}

    folder = get_folder(account, args.folder_name)
    limit = args.limit or 50

    if args.unread_only:
        qs = folder.filter(is_read=False).order_by("-datetime_received")[:limit]
    else:
        qs = folder.all().order_by("-datetime_received")[:limit]

    return [email_to_summary(item) for item in qs]


def action_list_folders(account, args):
    """列出所有邮件文件夹"""
    folders = []
    for folder in account.root.walk():
        if folder.folder_class == "IPF.Note":
            try:
                folders.append({
                    "name": folder.name,
                    "unread_count": folder.unread_count,
                    "total_count": folder.total_count,
                })
            except Exception:
                continue
    return folders


def action_search_gal(account, args):
    """搜索全局通讯录（GAL）"""
    if not args.keyword:
        return {"success": False, "message": "缺少 --keyword 参数（姓名或邮箱地址）"}

    try:
        results = account.protocol.resolve_names(
            [args.keyword],
            return_full_contact_data=False,
            search_scope="ActiveDirectory",
        )
    except Exception as e:
        return {"success": False, "message": f"搜索通讯录失败: {str(e)}"}

    contacts = []
    for item in results:
        # resolve_names 可能返回异常实例（如 ErrorNameResolutionNoResults）
        if isinstance(item, Exception):
            continue
        contact_info = {}
        if hasattr(item, 'name') and item.name:
            contact_info["name"] = item.name
        if hasattr(item, 'email_address') and item.email_address:
            contact_info["email"] = item.email_address
        if hasattr(item, 'mailbox_type') and item.mailbox_type:
            contact_info["type"] = str(item.mailbox_type)
        if contact_info:
            contacts.append(contact_info)

    result = {
        "success": True,
        "count": len(contacts),
        "contacts": contacts,
    }

    if len(contacts) == 0:
        result["message"] = "未找到匹配的联系人"
    elif len(contacts) == 1:
        result["message"] = f"找到 1 位联系人: {contacts[0].get('name', '')} ({contacts[0].get('email', '')})"
    else:
        result["message"] = f"找到 {len(contacts)} 位同名联系人，请确认要联系的具体人员"
        result["require_confirm"] = True

    return result


def _is_html(content: str) -> bool:
    """判断内容是否为 HTML（简单启发式：检测常见 HTML 标签）"""
    return bool(re.search(r"<(?:html|body|div|p|br|b|i|a|table|h[1-6]|ul|ol|li|strong|em|span)\b",
                          content, re.IGNORECASE))


def action_send(account, args):
    """发送邮件（需二次确认）"""
    from exchangelib import Message, HTMLBody

    if not args.to or not args.subject or not args.body:
        return {"success": False, "message": "缺少必要参数: --to, --subject, --body"}

    # 收件人地址列表（用于预览显示和返回值）
    to_addr_list = [t.strip() for t in args.to.split(",") if t.strip()]
    cc_addr_list = [c.strip() for c in args.cc.split(",")] if args.cc else []
    bcc_addr_list = [b.strip() for b in args.bcc.split(",")] if args.bcc else []

    # 判断正文是否为 HTML，自动选择 Body / HTMLBody
    body_content = HTMLBody(args.body) if _is_html(args.body) else args.body

    # 二次确认机制：未带 --confirm 时，仅展示邮件内容供用户确认，不实际发送
    if not args.confirm:
        preview = {
            "success": True,
            "status": "pending_confirm",
            "require_confirm": True,
            "message": "请确认以下邮件内容，确认后将发送",
            "email_preview": {
                "to": to_addr_list,
                "cc": cc_addr_list,
                "bcc": bcc_addr_list,
                "subject": args.subject,
                "body": args.body[:200] + ("..." if len(args.body) > 200 else ""),
            },
        }
        return preview

    m = Message(
        account=account,
        subject=args.subject,
        body=body_content,
    )
    # MailboxListField.clean() 会自动将字符串转为 Mailbox 对象
    m.to_recipients = to_addr_list
    if cc_addr_list:
        m.cc_recipients = cc_addr_list
    if bcc_addr_list:
        m.bcc_recipients = bcc_addr_list
    m.send()
    return {"success": True, "subject": args.subject, "to": to_addr_list}


def action_mark_read(account, args):
    """标记邮件为已读（需二次确认）"""
    if not args.email_id:
        return {"success": False, "message": "缺少 --email-id 参数"}

    item = find_email_by_id(account, args.email_id)
    if not item:
        return {"success": False, "message": "邮件未找到"}

    # 二次确认机制：未带 --confirm 时，仅展示邮件信息供用户确认
    if not args.confirm:
        preview = {
            "success": True,
            "status": "pending_confirm",
            "require_confirm": True,
            "message": "请确认是否将以下邮件标记为已读，确认后将无法撤销",
            "email_preview": {
                "subject": item.subject or "(无主题)",
                "sender": str(item.sender.email_address) if item.sender and item.sender.email_address else str(item.sender) if item.sender else "",
                "datetime": str(item.datetime_received),
                "is_read": item.is_read,
            },
        }
        return preview

    item.is_read = True
    item.save()
    return {"success": True, "email_id": args.email_id}


def action_attachments(account, args):
    """获取/下载附件"""
    from exchangelib import FileAttachment, ItemAttachment

    if not args.email_id:
        return {"success": False, "message": "缺少 --email-id 参数"}

    item = find_email_by_id(account, args.email_id)
    if not item:
        return {"success": False, "message": "邮件未找到"}

    attachments = []

    for attachment in item.attachments:
        if isinstance(attachment, FileAttachment):
            info = {
                "name": attachment.name,
                "size": attachment.size,
                "content_type": attachment.content_type,
            }

            if args.download_path:
                save_dir = Path(args.download_path)
                save_dir.mkdir(parents=True, exist_ok=True)
                save_path = save_dir / attachment.name
                with open(save_path, "wb") as f:
                    f.write(attachment.content)
                info["path"] = str(save_path)

            attachments.append(info)
        elif isinstance(attachment, ItemAttachment):
            attachments.append({
                "name": attachment.name,
                "size": 0,
                "content_type": "embedded_item",
            })

    return attachments


def action_extract(account, args):
    """提取附件文本内容"""
    from exchangelib import FileAttachment

    if not args.email_id:
        return {"success": False, "message": "缺少 --email-id 参数"}

    # 尝试确保可选依赖可用（pypdf、lxml）
    try:
        from env_check import ensure_optional_dependencies
        ensure_optional_dependencies()
    except Exception:
        pass

    item = find_email_by_id(account, args.email_id)
    if not item:
        return {"success": False, "message": "邮件未找到"}

    results = []

    for attachment in item.attachments:
        if args.attachment_name and attachment.name != args.attachment_name:
            continue

        if not isinstance(attachment, FileAttachment):
            continue

        result = {
            "name": attachment.name,
            "size": attachment.size,
        }

        content = attachment.content
        filename = attachment.name.lower()

        try:
            if filename.endswith(".txt") or filename.endswith(".csv") or filename.endswith(".log"):
                result["extracted_text"] = content.decode("utf-8", errors="replace")

            elif filename.endswith(".pdf"):
                import io
                from pypdf import PdfReader

                reader = PdfReader(io.BytesIO(content))
                text_parts = []
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                result["extracted_text"] = "\n".join(text_parts)

            elif filename.endswith(".docx"):
                import io
                import zipfile

                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    doc_xml = z.read("word/document.xml")
                    from lxml import etree
                    tree = etree.fromstring(doc_xml)
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    texts = tree.findall(".//w:t", ns)
                    result["extracted_text"] = "".join(t.text or "" for t in texts)

            elif filename.endswith(".xlsx"):
                import io
                import zipfile

                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    shared_strings = []
                    if "xl/sharedStrings.xml" in z.namelist():
                        ss_xml = z.read("xl/sharedStrings.xml")
                        from lxml import etree
                        tree = etree.fromstring(ss_xml)
                        ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                        for si in tree.findall(".//ns:si", ns):
                            texts = si.findall(".//ns:t", ns)
                            shared_strings.append("".join(t.text or "" for t in texts))

                    text_parts = []
                    sheet_files = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
                    for sf in sheet_files:
                        sheet_xml = z.read(sf)
                        from lxml import etree
                        tree = etree.fromstring(sheet_xml)
                        ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                        for row in tree.findall(".//ns:row", ns):
                            row_texts = []
                            for cell in row.findall(".//ns:c", ns):
                                v = cell.find("ns:v", ns)
                                t = cell.get("t", "")
                                if v is not None and v.text:
                                    if t == "s":
                                        idx = int(v.text)
                                        row_texts.append(shared_strings[idx] if idx < len(shared_strings) else "")
                                    else:
                                        row_texts.append(v.text)
                            if row_texts:
                                text_parts.append(" | ".join(row_texts))
                    result["extracted_text"] = "\n".join(text_parts)

            else:
                result["success"] = False
                result["message"] = f"不支持的文件类型: {filename.split('.')[-1]}"

        except ImportError as e:
            result["success"] = False
            result["message"] = f"缺少依赖: {str(e)}"
        except Exception as e:
            result["success"] = False
            result["message"] = str(e)

        results.append(result)

    return results


def main():
    parser = argparse.ArgumentParser(description="Exchange 邮件操作工具")
    parser.add_argument("--action", required=True,
                        choices=["recent", "unread", "detail", "search", "folder",
                                 "list-folders", "search-gal", "send", "mark-read",
                                 "attachments", "extract"],
                        help="操作类型")
    # 操作相关参数（仅非敏感参数）
    parser.add_argument("--folder", default="inbox", help="文件夹名称（默认 inbox）")
    parser.add_argument("--folder-name", help="文件夹名称（用于 folder 操作）")
    parser.add_argument("--limit", type=int, help="返回数量限制")
    parser.add_argument("--email-id", help="邮件 ID")
    parser.add_argument("--keyword", help="搜索关键词")
    parser.add_argument("--sender", help="发件人筛选")
    parser.add_argument("--subject", help="主题筛选")
    parser.add_argument("--unread-only", action="store_true", help="仅未读")
    parser.add_argument("--to", help="收件人（多个用逗号分隔）")
    parser.add_argument("--cc", help="抄送（多个用逗号分隔）")
    parser.add_argument("--bcc", help="密送（多个用逗号分隔）")
    parser.add_argument("--body", help="邮件正文")
    parser.add_argument("--download-path", help="附件下载目录")
    parser.add_argument("--attachment-name", help="指定附件名称")
    # 发送确认参数
    parser.add_argument("--confirm", action="store_true",
                        help="确认发送邮件（send 操作需二次确认，首次不带此参数仅预览）")
    # 日志参数
    parser.add_argument("--verbose", action="store_true", help="显示详细日志")
    parser.add_argument("--debug", action="store_true", help="显示调试日志（包含 exchangelib 内部日志）")
    parser.add_argument("--confirmed-risk", action="store_true",
                        help="用户已确认模型安全风险（用户确认后重新执行时使用）")

    args = parser.parse_args()

    # 确保 stdout/stderr 使用 UTF-8 编码（防止 Windows 下中文乱码）
    ensure_utf8_output()

    # 配置日志级别
    configure_logging(verbose=args.verbose, debug=args.debug)

    try:
        # 环境预检测 + 依赖自动安装
        check_and_install_dependencies()
        config = load_config()

        # 模型安全校验（读取类操作）— 放在环境/依赖/配置检查之后
        # --confirmed-risk 用于用户确认风险后重新执行
        if not args.confirmed_risk and not check_model_safety(args.action, READ_ACTIONS):
            sys.exit(2)  # 退出码 2 = 等待用户确认

        # 连接服务器
        try:
            account = get_account(config, verify_target="inbox")
        except (ConnectionError, Exception) as conn_err:
            # 认证失败 → 清除 Keyring 缓存，退出码 4 要求重新认证
            if _is_auth_error(conn_err):
                handle_auth_failure(conn_err)
            raise

        logger.debug("Executing action: %s", args.action)

        action_map = {
            "recent": action_recent,
            "unread": action_unread,
            "detail": action_detail,
            "search": action_search,
            "folder": action_folder,
            "list-folders": action_list_folders,
            "search-gal": action_search_gal,
            "send": action_send,
            "mark-read": action_mark_read,
            "attachments": action_attachments,
            "extract": action_extract,
        }

        result = action_map[args.action](account, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # 写入操作待确认（send / mark-read）→ 退出码 3
        if isinstance(result, dict) and result.get("status") == "pending_confirm":
            sys.exit(3)

    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"success": False, "message": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
