#!/usr/bin/env python3
"""查询可填写周期并新建 T5T。"""

from __future__ import annotations

import argparse
import json

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import t5t_client as t5t


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询可填写周期并新建 T5T")
    parser.add_argument("--items-json", help='T5T JSON 数组，例如 ["重点一"]')
    parser.add_argument("--content-file", help="包含 T5T JSON 数组或逐行文本的文件")
    parser.add_argument("--check", action="store_true", help="只查询是否存在可填写周期")
    parser.add_argument("--dry-run", action="store_true", help="查询并输出待提交信息")
    parser.add_argument("--skip-confirmation", action="store_true", help="提交已确认内容")
    parser.add_argument(
        "--commit-confirmed",
        action="store_true",
        help="用户已确认后，在一次调用中核对并提交",
    )
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
    parser.add_argument(
        "--validate-items",
        action="store_true",
        help="仅本地校验 --items-json 格式，不联网、不认证、不提交",
    )
    t5t.add_common_args(parser)
    return parser.parse_args(argv)


def print_already_submitted(
    environment: str,
    latest_response: dict[str, Any] | None = None,
    detail_response: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    result: dict[str, Any] = {
        "status": "already_submitted",
        "message": "最新周期已经填写，目前不可新建",
        "environment": environment,
    }
    if latest_response is not None and detail_response is not None and detail is not None:
        to_list = detail.get("toList") or []
        if not isinstance(to_list, list):
            raise t5t.T5TError("详情中的权限格式异常")
        result.update(
            {
                "period": {
                    "reportId": detail.get("reportId"),
                    "name": detail.get("reportName") or detail.get("title"),
                },
                "id": detail.get("id"),
                "items": t5t.parse_raw_content(detail.get("rawContent")),
                "toList": to_list,
                "permissions": t5t.format_copies_info(to_list),
                "inviteSameGroupView": bool(detail.get("inviteSameGroupView", True)),
                "sameGroupVisible": t5t.format_same_group(
                    detail.get("inviteSameGroupView", True)
                ),
                "canOperate": bool(detail.get("canOperate")),
            }
        )
    t5t.json_print(result)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        # 纯本地格式校验：不联网、不认证。模型生成内容后先用它自检，
        # 不合格就重新生成再校验，全程零接口调用；合格后才走真正提交。
        if args.validate_items:
            values = t5t.read_items(args)
            t5t.json_print({"status": "valid", "count": len(values)})
            return 0

        selected_modes = sum(
            bool(mode)
            for mode in (
                args.check,
                args.dry_run,
                args.skip_confirmation,
                args.commit_confirmed,
            )
        )
        if selected_modes != 1:
            raise t5t.T5TError(
                "必须且只能使用 --check、--dry-run、--skip-confirmation "
                "或 --commit-confirmed 之一"
            )
        if args.skip_confirmation and (not args.report_id or not args.confirmation_hash):
            raise t5t.T5TError(
                "--skip-confirmation 提交时必须提供 --report-id 和 --confirmation-hash"
            )

        # 提交内容格式先于任何网络请求校验：--items-json 格式错时快速失败，
        # 不浪费查周期/查抄送人接口，模型按正确格式重新生成后再提交即可。
        prevalidated = args.commit_confirmed or args.skip_confirmation
        values = t5t.read_items(args) if prevalidated else None
        invite_same_group = t5t.read_invite_same_group(args) if prevalidated else None

        base_url, headers = t5t.create_request_context(args)
        copies_result: tuple[dict[str, Any], list[Any]] | None = None
        copies_error: t5t.T5TError | None = None
        if args.check:
            period_response, periods = t5t.query_periods(base_url, headers, args.timeout)
        else:
            # 周期列表与历史抄送人互不依赖，并行查询省一次网络往返；
            # 周期为空（冲突分支）时抄送人结果直接丢弃，无副作用（只读接口）
            with ThreadPoolExecutor(max_workers=2) as pool:
                periods_future = pool.submit(
                    t5t.query_periods, base_url, headers, args.timeout
                )
                copies_future = pool.submit(
                    t5t.query_latest_copies, base_url, headers, args.timeout
                )
                period_response, periods = periods_future.result()
                try:
                    copies_result = copies_future.result()
                except t5t.T5TError as exc:
                    copies_error = exc
        if not periods:
            if args.commit_confirmed:
                latest_response, detail_response, detail = t5t.query_latest_detail(
                    base_url,
                    headers,
                    args.timeout,
                )
                print_already_submitted(
                    args.env,
                    latest_response,
                    detail_response,
                    detail,
                )
            else:
                print_already_submitted(args.env)
            return 0

        # 多个可填周期且未指定：交还代理让用户选，附 reportId 供二次提交（--report-id）。
        # 只有一个周期则静默选用，不打扰用户。--check 仅做可填性预检，沿用第一个即可。
        if not args.check and not args.report_id and len(periods) > 1:
            t5t.json_print(
                {
                    "status": "period_choice",
                    "environment": args.env,
                    "periods": [
                        {"reportId": p.get("reportId"), "name": p.get("name")}
                        for p in periods
                    ],
                }
            )
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
                }
            )
            return 0

        if not prevalidated:
            values = t5t.read_items(args)
            invite_same_group = t5t.read_invite_same_group(args)
        if copies_error is not None:
            raise copies_error
        assert copies_result is not None
        copies_response, copies = copies_result
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

        if args.skip_confirmation and args.confirmation_hash != confirmation_hash:
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
            concise=args.commit_confirmed,
        )
        return 0
    except (t5t.T5TError, json.JSONDecodeError) as error:
        return t5t.print_error(error, args.env)


if __name__ == "__main__":
    raise SystemExit(main())
