#!/usr/bin/env python3
"""只读查询 T5T：查看最新已填写详情、浏览最近列表（不修改、不提交）。"""

from __future__ import annotations

import argparse

import t5t_client as t5t


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读查询 T5T（不修改、不提交）")
    parser.add_argument("--latest", action="store_true", help="查询最新已填写 T5T 详情")
    parser.add_argument("--list-recent", action="store_true", help="浏览最近 T5T 列表")
    parser.add_argument("--page-num", type=int, default=1, help="--list-recent 的页码")
    parser.add_argument("--page-size", type=int, default=5, help="--list-recent 的每页条数")
    t5t.add_common_args(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    t5t.force_utf8_io()
    args = parse_args(argv)
    try:
        if bool(args.latest) == bool(args.list_recent):
            raise t5t.T5TError("必须且只能使用 --latest 或 --list-recent 之一")

        base_url, headers = t5t.create_request_context(args)
        if args.list_recent:
            _list_response, items = t5t.query_self_weekly_list(
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
                }
            )
            return 0

        # --latest：查最新单条详情
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
        t5t.print_latest_detail(args.env, detail)
        return 0
    except Exception as error:
        # 收口一切：T5TError/JSON 错走专属分支，畸形接口数据等意外（如 AttributeError）
        # 落 print_error 通用 error 分支，保证 stdout 必有结果 JSON，不漏 traceback。
        return t5t.print_error(error, args.env)


if __name__ == "__main__":
    raise SystemExit(main())
