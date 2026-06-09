#!/usr/bin/env python3
"""查询最新已填写 T5T 并提交编辑内容。"""

from __future__ import annotations

import argparse
import json

from typing import Any

import t5t_client as t5t


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询并编辑已填写 T5T")
    parser.add_argument("--query-latest", action="store_true", help="查询最新 T5T 详情")
    parser.add_argument(
        "--list-recent",
        action="store_true",
        help="只读浏览最近 T5T 列表（不进入编辑）",
    )
    parser.add_argument("--page-num", type=int, default=1, help="--list-recent 的页码")
    parser.add_argument(
        "--page-size",
        type=int,
        default=5,
        help="--list-recent 的每页条数",
    )
    parser.add_argument("--id", help="需要编辑的 T5T 详情 id")
    parser.add_argument("--items-json", help="修改后的完整 T5T JSON 数组")
    parser.add_argument("--content-file", help="包含修改后 T5T 内容的文件")
    parser.add_argument("--to-list-json", help="删除抄送人后的完整 toList JSON 数组")
    parser.add_argument(
        "--invite-same-group",
        choices=["true", "false"],
        help="同组可见开关；不传保留原值",
    )
    parser.add_argument("--dry-run", action="store_true", help="查询并输出待提交信息")
    parser.add_argument("--skip-confirmation", action="store_true", help="提交已确认修改")
    parser.add_argument("--confirmation-hash", help="编辑查询阶段返回的 confirmationHash")
    parser.add_argument(
        "--open-preview",
        action="store_true",
        help="提交成功后提供预览链接",
    )
    t5t.add_common_args(parser)
    return parser.parse_args(argv)


def print_latest_detail(
    environment: str,
    latest_response: dict[str, Any],
    detail_response: dict[str, Any],
    detail: dict[str, Any],
) -> None:
    to_list = detail.get("toList") or []
    if not isinstance(to_list, list):
        raise t5t.T5TError("详情中的权限格式异常")
    t5t.json_print(
        {
            "status": "latest_detail",
            "environment": environment,
            "period": {
                "reportId": detail.get("reportId"),
                "name": detail.get("reportName") or detail.get("title"),
            },
            "items": t5t.parse_raw_content(detail.get("rawContent")),
            "toList": to_list,
            "permissions": t5t.format_copies_info(to_list),
            "inviteSameGroupView": bool(detail.get("inviteSameGroupView", True)),
            "sameGroupVisible": t5t.format_same_group(
                detail.get("inviteSameGroupView", True)
            ),
            "canOperate": bool(detail.get("canOperate")),
            "detail": detail,
            "responses": {
                "selfWeekly": latest_response,
                "detail": detail_response,
            },
        }
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.dry_run and args.skip_confirmation:
            raise t5t.T5TError("--dry-run 和 --skip-confirmation 不能同时使用")
        if (
            not args.query_latest
            and not args.list_recent
            and not args.dry_run
            and not args.skip_confirmation
        ):
            raise t5t.T5TError(
                "必须使用 --query-latest、--list-recent、--dry-run 或 --skip-confirmation"
            )
        if not args.query_latest and not args.list_recent and not args.id:
            raise t5t.T5TError("编辑时必须提供 --id")
        if args.skip_confirmation and not args.confirmation_hash:
            raise t5t.T5TError("--skip-confirmation 提交时必须提供 --confirmation-hash")

        base_url, headers = t5t.create_request_context(args)
        if args.list_recent:
            list_response, items = t5t.query_self_weekly_list(
                base_url,
                headers,
                args.page_num,
                args.page_size,
                args.timeout,
            )
            t5t.json_print(
                {
                    "status": "recent_list",
                    "environment": args.env,
                    "pageNum": args.page_num,
                    "pageSize": args.page_size,
                    "count": len(items),
                    "records": [t5t.format_recent_item(item) for item in items],
                    "responses": {"selfWeekly": list_response},
                }
            )
            return 0
        if args.query_latest:
            latest_response, detail_response, detail = t5t.query_latest_detail(
                base_url,
                headers,
                args.timeout,
            )
            if detail_response is None or detail is None:
                t5t.json_print(
                    {
                        "status": "not_found",
                        "message": "未查询到已填写的 T5T",
                        "environment": args.env,
                    }
                )
                return 0
            print_latest_detail(args.env, latest_response, detail_response, detail)
            return 0

        detail_response, detail = t5t.query_detail(
            base_url,
            headers,
            args.id,
            args.timeout,
        )
        values = t5t.read_items(args, required=False)
        to_list = t5t.read_to_list(args)
        invite_same_group = t5t.read_invite_same_group(args)
        if values is None and to_list is None and invite_same_group is None:
            raise t5t.T5TError("编辑时必须修改 T5T 内容、抄送人或同组可见")

        payload = t5t.build_edit_payload(detail, values, to_list, invite_same_group)
        confirmation_hash = t5t.build_confirmation_hash(args.env, base_url, payload)
        output_items = values or t5t.parse_raw_content(payload["rawContent"])
        responses = {"detail": detail_response}
        if args.dry_run:
            t5t.json_print(
                {
                    "status": "dry_run",
                    "environment": args.env,
                    "baseUrl": base_url,
                    "confirmationHash": confirmation_hash,
                    "mode": "edit",
                    "responses": responses,
                    "payload": payload,
                }
            )
            return 0

        if args.confirmation_hash != confirmation_hash:
            raise t5t.T5TError("已确认信息发生变化，请重新查询并确认")
        commit_response = t5t.commit_payload(base_url, headers, payload, args.timeout)
        t5t.print_success(
            args.env,
            base_url,
            confirmation_hash,
            "edit",
            {
                "reportId": detail.get("reportId"),
                "name": detail.get("reportName") or detail.get("title"),
            },
            output_items,
            payload["toList"],
            {**responses, "commit": commit_response},
            payload["inviteSameGroupView"],
        )
        return 0
    except (t5t.T5TError, json.JSONDecodeError) as error:
        return t5t.print_error(error, args.env)


if __name__ == "__main__":
    raise SystemExit(main())
