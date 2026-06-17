#!/usr/bin/env python3
"""查询最新已填写 T5T 并提交编辑内容。"""

from __future__ import annotations

import argparse
import json

from typing import Any

import t5t_client as t5t


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="编辑已填写 T5T（只读查询用 query_t5t.py）")
    parser.add_argument("--id", help="需要编辑的 T5T 详情 id")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="编辑最新单条 T5T：自动定位 id，一条命令完成查最新→核对→提交，无需先单独查 id",
    )
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
    parser.add_argument(
        "--commit-confirmed",
        action="store_true",
        help="用户已确认后，在一次调用中核对并提交修改",
    )
    parser.add_argument("--confirmation-hash", help="编辑查询阶段返回的 confirmationHash")
    parser.add_argument(
        "--open-preview",
        action="store_true",
        help="提交成功后提供预览链接",
    )
    parser.add_argument(
        "--validate-items",
        action="store_true",
        help="仅本地校验 --items-json 格式，不联网、不认证、不提交",
    )
    t5t.add_common_args(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    t5t.force_utf8_io()
    args = parse_args(argv)
    try:
        # 纯本地格式校验：不联网、不认证。生成/修改内容后先自检，
        # 不合格就重新生成再校验，全程零接口调用；合格后才走真正提交。
        if args.validate_items:
            values = t5t.read_items(args)
            t5t.json_print({"status": "valid", "count": len(values)})
            return 0

        selected_modes = sum(
            bool(mode)
            for mode in (
                args.dry_run,
                args.skip_confirmation,
                args.commit_confirmed,
            )
        )
        if selected_modes != 1:
            raise t5t.T5TError(
                "必须且只能使用 --dry-run、--skip-confirmation 或 --commit-confirmed 之一"
                "（只读查询请用 query_t5t.py）"
            )
        if not args.id and not args.latest:
            raise t5t.T5TError("编辑时必须提供 --id 或 --latest")
        if args.skip_confirmation and not args.confirmation_hash:
            raise t5t.T5TError("--skip-confirmation 提交时必须提供 --confirmation-hash")

        # 编辑内容格式先于任何网络请求校验：格式错时快速失败，不浪费查询接口。
        editing = args.commit_confirmed or args.skip_confirmation or args.dry_run
        values = to_list = invite_same_group = None
        if editing:
            values = t5t.read_items(args, required=False)
            to_list = t5t.read_to_list(args)
            invite_same_group = t5t.read_invite_same_group(args)
            if values is None and to_list is None and invite_same_group is None:
                raise t5t.T5TError("没检测到要修改的内容")

        base_url, headers = t5t.create_request_context(args)
        if args.id:
            detail_response, detail = t5t.query_detail(
                base_url,
                headers,
                args.id,
                args.timeout,
            )
        else:
            # --latest：自动定位最新单条，一条命令完成查最新→核对→提交，省去先单独查 id。
            _latest_response, detail_response, detail = t5t.query_latest_detail(
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

        if args.skip_confirmation and args.confirmation_hash != confirmation_hash:
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
            concise=args.commit_confirmed,
        )
        return 0
    except (t5t.T5TError, json.JSONDecodeError) as error:
        return t5t.print_error(error, args.env)


if __name__ == "__main__":
    raise SystemExit(main())
