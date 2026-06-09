#!/usr/bin/env python3
"""查询可填写周期并新建 T5T。"""

from __future__ import annotations

import argparse
import json

from typing import Any

import t5t_client as t5t


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询可填写周期并新建 T5T")
    parser.add_argument("--items-json", help='T5T JSON 数组，例如 ["重点一"]')
    parser.add_argument("--content-file", help="包含 T5T JSON 数组或逐行文本的文件")
    parser.add_argument("--check", action="store_true", help="只查询是否存在可填写周期")
    parser.add_argument("--dry-run", action="store_true", help="查询并输出待提交信息")
    parser.add_argument("--skip-confirmation", action="store_true", help="提交已确认内容")
    parser.add_argument("--report-id", help="提交时指定已确认的周期 reportId")
    parser.add_argument("--confirmation-hash", help="查询阶段返回的 confirmationHash")
    parser.add_argument(
        "--invite-same-group",
        choices=["true", "false"],
        help="同组可见开关；不传默认 true",
    )
    parser.add_argument(
        "--open-preview",
        action="store_true",
        help="提交成功后提供预览链接",
    )
    t5t.add_common_args(parser)
    return parser.parse_args(argv)


def print_already_submitted(environment: str) -> None:
    t5t.json_print(
        {
            "status": "already_submitted",
            "message": "最新周期已经填写，目前不可新建",
            "environment": environment,
        }
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.dry_run and args.skip_confirmation:
            raise t5t.T5TError("--dry-run 和 --skip-confirmation 不能同时使用")
        if not args.check and not args.dry_run and not args.skip_confirmation:
            raise t5t.T5TError("必须使用 --check、--dry-run 或 --skip-confirmation")
        if args.skip_confirmation and (not args.report_id or not args.confirmation_hash):
            raise t5t.T5TError(
                "--skip-confirmation 提交时必须提供 --report-id 和 --confirmation-hash"
            )

        base_url, headers = t5t.create_request_context(args)
        period_response, periods = t5t.query_periods(base_url, headers, args.timeout)
        if not periods:
            print_already_submitted(args.env)
            return 0

        selected_period = t5t.select_period(periods, args.report_id)
        if args.check:
            t5t.json_print(
                {
                    "status": "available",
                    "environment": args.env,
                    "period": {
                        "reportId": selected_period.get("reportId"),
                        "name": selected_period.get("name"),
                    },
                    "responses": {"periodList": period_response},
                }
            )
            return 0

        values = t5t.read_items(args)
        invite_same_group = t5t.read_invite_same_group(args)
        copies_response, copies = t5t.query_latest_copies(
            base_url,
            headers,
            args.timeout,
        )
        payload = t5t.build_create_payload(
            selected_period, copies, values, invite_same_group
        )
        confirmation_hash = t5t.build_confirmation_hash(args.env, base_url, payload)
        responses: dict[str, Any] = {
            "periodList": period_response,
            "latestCopies": copies_response,
        }
        if args.dry_run:
            t5t.json_print(
                {
                    "status": "dry_run",
                    "environment": args.env,
                    "baseUrl": base_url,
                    "confirmationHash": confirmation_hash,
                    "mode": "create",
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
            "create",
            {
                "reportId": selected_period.get("reportId"),
                "name": selected_period.get("name"),
            },
            values,
            copies,
            {**responses, "commit": commit_response},
            payload["inviteSameGroupView"],
        )
        return 0
    except (t5t.T5TError, json.JSONDecodeError) as error:
        return t5t.print_error(error, args.env)


if __name__ == "__main__":
    raise SystemExit(main())
