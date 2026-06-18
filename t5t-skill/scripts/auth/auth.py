#!/usr/bin/env python3
"""
IM Teams 公网认证脚本。

启动一次性本地回环接收服务，打开 max-oplatform 认证落地页，接收页面回传的
短期认证凭证，兑换为 token 并写入系统钥匙串后退出。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from config import (
    ACTIVE_ENVIRONMENT,
    ENV_LANDING_URL,
    LANDING_URLS,
    RECEIVER_HOST,
    RECEIVER_PORTS,
    RECEIVER_TIMEOUT_SECONDS,
    SCHEME_PREFIXES,
    TOKEN_EXCHANGE_URLS,
)
from credential_store import (
    env_token_name_for,
    keyring_clear_all_tokens,
    keyring_clear_token,
    keyring_load_token,
    keyring_save_token,
    normalize_environment,
)
from errors import AuthError
from runtime import ensure_keyring, force_utf8_io, setup_logging
from session_store import (
    SESSION_POLL_INTERVAL_SECONDS,
    build_pending_session as _build_pending_session,
    format_request_expiry as _format_request_expiry,
    is_reusable_pending_session as _is_reusable_pending_session,
    load_pending_session_locked as _load_pending_session_locked,
    load_pending_session_snapshot as _load_pending_session_snapshot,
    parse_iso_timestamp as _parse_iso_timestamp,
    pending_session_lock as _pending_session_lock,
    remaining_seconds as _remaining_seconds,
    remove_pending_session as _remove_pending_session,
    remove_pending_session_locked as _remove_pending_session_locked,
    save_pending_session_locked as _save_pending_session_locked,
    store_session_result as _store_session_result,
)

AUTH_EXPIRED_EXIT_CODE = 4
AUTH_PENDING_EXIT_CODE = 3
MAX_REQUEST_BODY_BYTES = 16 * 1024


def _json_print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _wait_for_existing_session(environment: str, session: dict,
                               poll_timeout: float | None = None) -> dict:
    deadline = _parse_iso_timestamp(session.get("requestExpiresAt"))
    if deadline is None:
        return {"status": "expired", "authenticated": False, "message": "认证会话已失效，请重新发起认证"}

    session_id = session["sessionId"]
    # poll_timeout 不传：阻塞到会话剩余全部时长（旧行为，供后台 receiver 进程自用）。
    # 传了：本次只探这么久，会话还没到期就返回 pending，调用方可再调一次 --wait 继续等。
    poll_deadline = deadline if poll_timeout is None else min(deadline, time.time() + poll_timeout)
    while time.time() < poll_deadline:
        token, expiry, _source = _load_cached_token(environment)
        if token:
            return {
                "status": "ok",
                "authenticated": True,
                "expires_at": expiry,
                "reusedSession": True,
            }

        current = _load_pending_session_snapshot(environment)
        if isinstance(current, dict) and current.get("sessionId") == session_id:
            status = current.get("status")
            if status == "error":
                return {"status": "error", "message": current.get("message") or "认证失败"}
            if status == "expired":
                return {
                    "status": "expired",
                    "authenticated": False,
                    "message": current.get("message") or "认证会话已过期，请重新发起认证",
                }
        elif isinstance(current, dict) and current.get("sessionId") != session_id:
            return {
                "status": "expired",
                "authenticated": False,
                "message": "已有新的认证会话，请使用最新的认证窗口或链接",
            }

        time.sleep(SESSION_POLL_INTERVAL_SECONDS)

    if time.time() < deadline:
        return {
            "status": "pending",
            "authenticated": False,
            "message": "认证尚未完成，会话仍有效，可再次调用 --wait 继续等待",
            "remainingSeconds": max(0, int(deadline - time.time())),
        }
    return {"status": "expired", "authenticated": False, "message": "等待现有认证会话完成超时"}


def _load_cached_token(environment: str) -> tuple[str | None, str | None, str]:
    env_token = os.environ.get(env_token_name_for(environment))
    if env_token:
        return env_token, None, "env"
    token, expiry = keyring_load_token(environment)
    if token:
        return token, expiry, "keyring"
    return None, expiry, "none"


def _find_port(preferred_port: int | None = None) -> int:
    if preferred_port is not None and preferred_port not in RECEIVER_PORTS:
        raise AuthError("本地接收端口必须在 35101-35110 范围内")
    candidates = [preferred_port] if preferred_port else list(RECEIVER_PORTS)
    for port in candidates:
        if not port:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((RECEIVER_HOST, port))
                return port
            except OSError:
                continue
    raise AuthError(
        "无法绑定本地接收端口 35101-35110（很可能被沙箱或防火墙限制了本机端口监听）。"
        "认证需要监听 127.0.0.1 接收回传，请在本地终端或非沙箱环境重试认证。"
    )


def _same_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_landing_url(base_url: str, state: str, receiver_url: str,
                       request_expires_at: str, session_id: str) -> str:
    separator = "&" if "?" in base_url else "?"

    win_config = {
        "width": 375,
        "height": 616,
        "resizable": False,
        "maximizable": False,
    }

    params = {
        "navigation_to": "win",
        "state": state,
        "receiver": receiver_url,
        "session_id": session_id,
        "win_id": session_id,
        "request_expires_at": request_expires_at,
        "win_config": json.dumps(win_config, ensure_ascii=False, separators=(",", ":")),
    }

    return f"{base_url}{separator}{urlencode(params)}"

def _build_scheme_url(environment: str, landing_url: str) -> str:
    scheme_prefix = SCHEME_PREFIXES[environment]
    return f"{scheme_prefix}applink/link?url={quote(landing_url, safe='')}"


def _build_applink_url(landing_url: str) -> str:
    """可点的 Teams 认证链接：用 https 的 applink 协议（任何客户端都可点），
    域名从落地页派生（生产 im / 测试 sit-im），url 参数为认证落地页。"""
    return f"{_same_origin(landing_url)}/applink/link?url={quote(landing_url, safe='')}"


def _open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _get_browser_fallback_message(environment: str, landing_url: str) -> str | None:
    """测试环境的浏览器兜底入口文案，与 stderr/主入口口径一致、与会话状态无关。

    生产落地页依赖 Teams 容器，不提供浏览器入口（返回 None）。
    """
    if environment != "test":
        return None
    return f"（测试环境）也可在浏览器中完成认证：{landing_url}"


def _print_auth_fallback(environment: str, applink_url: str, landing_url: str) -> None:
    """拉起 scheme 的同时，给用户兜底认证入口（stderr）。

    措辞与会话状态无关：scheme 没拉起、或认证失败时，点链接即可重新认证。
    appLinkUrl（https，任何客户端可点）对所有环境可用，是统一入口；
    测试环境再额外给一条浏览器直开入口（landingUrl，生产落地页依赖 Teams 容器不提供）。
    """
    print(
        f"认证如果失败，请点此重新认证（在 Teams 中打开）：{applink_url}",
        file=sys.stderr,
        flush=True,
    )
    if environment == "test":
        print(
            f"（测试环境）也可在浏览器中完成认证：{landing_url}",
            file=sys.stderr,
            flush=True,
        )


def _build_browser_fallback_payload(environment: str, landing_url: str,
                                    request_expires_at: str) -> dict[str, str]:
    message = _get_browser_fallback_message(environment, landing_url)
    if not message:
        return {}
    return {
        "landingUrl": landing_url,
        "requestExpiresAt": request_expires_at,
        "browserFallbackMessage": message,
    }


def _exchange_short_key(encrypt: str, environment: str) -> str:
    url = f"{TOKEN_EXCHANGE_URLS[environment]}?{urlencode({'encrypt': encrypt})}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AuthError("短期认证凭证兑换失败") from exc

    data = payload.get("data")
    token = data.get("token") if isinstance(data, dict) else None
    if payload.get("code") != 0 or not isinstance(token, str) or len(token) < 10:
        raise AuthError(payload.get("msg") or "短期认证凭证无效或已过期")
    return token


class _TokenReceiver(BaseHTTPRequestHandler):
    def _is_allowed_origin(self) -> bool:
        return self.headers.get("Origin") == self.server.allowed_origin

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.server.allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if not self._is_allowed_origin():
            self._send_json(403, {"code": 403, "message": "Origin 校验失败"})
            return
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/token":
            self._send_json(404, {"code": 404, "message": "Not found"})
            return
        if not self._is_allowed_origin():
            self._send_json(403, {"code": 403, "message": "Origin 校验失败"})
            return
        if time.time() >= self.server.request_deadline:
            self._send_json(410, {"code": 410, "message": "认证会话已过期，请重新发起认证"})
            return
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._send_json(415, {"code": 415, "message": "Content-Type 必须为 application/json"})
            return

        try:
            raw_len = int(self.headers.get("Content-Length") or 0)
            if raw_len <= 0 or raw_len > MAX_REQUEST_BODY_BYTES:
                raise ValueError("请求体大小异常")
            raw_body = self.rfile.read(raw_len)
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"code": 400, "message": "请求体不是合法 JSON"})
            return

        state = payload.get("state")
        encrypt = payload.get("encrypt")
        if state != self.server.expected_state:
            self._send_json(403, {"code": 403, "message": "state 校验失败"})
            return
        if not isinstance(encrypt, str) or len(encrypt) < 10:
            self._send_json(400, {"code": 400, "message": "短期认证凭证为空或格式异常"})
            return

        try:
            token = _exchange_short_key(encrypt, self.server.environment)
            keyring_save_token(token, self.server.environment)
        except AuthError as exc:
            self.server.result = {"status": "error", "message": str(exc)}
            self._send_json(500, {"code": 500, "message": str(exc)})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self.server.result = {
            "status": "ok",
            "authenticated": True,
        }
        self._send_json(200, {"code": 0, "message": "short key exchanged"})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, fmt, *args) -> None:
        return


def _create_token_receiver(port: int, state: str, allowed_origin: str,
                           environment: str, request_deadline: float) -> HTTPServer:
    server = HTTPServer((RECEIVER_HOST, port), _TokenReceiver)
    server.expected_state = state
    server.allowed_origin = allowed_origin
    server.environment = environment
    server.request_deadline = request_deadline
    server.result = None
    return server


def _wait_for_token(server: HTTPServer, timeout: int) -> dict:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    start = time.time()
    while thread.is_alive() and time.time() - start < timeout:
        thread.join(timeout=0.2)
    if thread.is_alive():
        server.shutdown()
        return {"status": "expired", "authenticated": False, "message": "等待落地页回传短期认证凭证超时"}
    return server.result or {"status": "error", "message": "本地接收服务未收到有效结果"}


def _get_or_create_pending_session(environment: str, landing_base_url: str, timeout: int,
                                   preferred_port: int | None) -> tuple[dict, HTTPServer | None, bool]:
    with _pending_session_lock(environment):
        existing = _load_pending_session_locked(environment)
        if _is_reusable_pending_session(existing, environment):
            return existing, None, False
        if existing:
            _remove_pending_session_locked(environment)

        port = _find_port(preferred_port)
        state = secrets.token_urlsafe(24)
        session_id = secrets.token_urlsafe(16)
        request_deadline = time.time() + max(timeout, 0)
        request_expires_at = _format_request_expiry(request_deadline)
        receiver_url = f"http://{RECEIVER_HOST}:{port}/token"
        landing_url = _build_landing_url(
            landing_base_url,
            state,
            receiver_url,
            request_expires_at,
            session_id,
        )
        server = _create_token_receiver(
            port,
            state,
            _same_origin(landing_base_url),
            environment,
            request_deadline,
        )
        session = _build_pending_session(
            environment,
            state,
            session_id,
            receiver_url,
            landing_url,
            request_expires_at,
        )
        _save_pending_session_locked(environment, session)
        return session, server, True


def _print_pending_links(environment: str, session: dict) -> None:
    landing_url = session["landingUrl"]
    payload = {
        "status": "pending",
        "authenticated": False,
        "message": "认证已拉起，等待用户完成授权",
        "appLinkUrl": _build_applink_url(landing_url),
        "schemeUrl": _build_scheme_url(environment, landing_url),
        "requestExpiresAt": session["requestExpiresAt"],
        "remainingSeconds": max(1, _remaining_seconds(session["requestExpiresAt"])),
        "sessionId": session["sessionId"],
        "environment": environment,
        "hint": (
            "下一步必须是把认证链接以可点击形式发给用户作为一条消息，禁止在发这条消息之前调用 --wait 或任何其它命令。"
            "用 appLinkUrl 作「在 Teams 中打开认证」"
            "（https 链接，任何客户端都可点；schemeUrl 是 teamssit://协议链接，多数客户端点不开，仅作内部自动拉起，不要发给用户）。"
            "输出包含 landingUrl 时再附一条（在浏览器中打开认证）。"
            "并说明：认证窗口没有弹出、或不小心被关掉时，点链接即可重新打开，"
            "完成授权后会自动继续。把链接发给用户之后，再运行 auth.py --wait 等待认证结果。"
        ),
    }
    # 浏览器落地页兜底仅测试环境可用；生产落地页依赖 Teams 容器，不给浏览器链接
    if environment == "test":
        payload["landingUrl"] = landing_url
    _json_print(payload)


def _handle_start(args: argparse.Namespace) -> int:
    """后台拉起认证（receiver 由子进程持有），立即返回认证链接，不阻塞等待用户。"""
    if args.no_cache:
        keyring_clear_token(args.env)
    else:
        token, expiry, source = _load_cached_token(args.env)
        if token:
            _json_print({
                "status": "ok",
                "authenticated": True,
                "expires_at": expiry,
                "source": source,
                "environment": args.env,
            })
            return 0

    session = _load_pending_session_snapshot(args.env)
    if not _is_reusable_pending_session(session, args.env):
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--env", args.env,
            "--timeout", str(args.timeout),
        ]
        if args.landing_url:
            command += ["--landing-url", args.landing_url]
        if args.open_url_directly:
            command.append("--open-url-directly")
        if args.port:
            command += ["--port", str(args.port)]
        # 让后台 receiver 脱离父进程：POSIX 用 setsid；Windows 上
        # start_new_session 会被忽略，需用 creationflags 真正脱离控制台与进程组，
        # 否则父进程退出（或被按进程树回收）时后台认证服务会被一起杀掉。
        if os.name == "nt":
            # DETACHED_PROCESS 已表示无控制台窗口，不能再叠 CREATE_NO_WINDOW
            # （两者在 CreateProcess 里互斥，同时传会 ERROR_INVALID_PARAMETER）。
            detach_kwargs = {
                "creationflags": (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            }
        else:
            detach_kwargs = {"start_new_session": True}
        proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detach_kwargs,
        )
        deadline = time.time() + 10
        session = None
        while time.time() < deadline:
            snapshot = _load_pending_session_snapshot(args.env)
            if _is_reusable_pending_session(snapshot, args.env):
                session = snapshot
                break
            # 后台进程在建立会话前就退出 = 起不来（多为沙箱禁端口监听/禁后台进程）。
            # 立即失败、给精确提示，不必干等满 10 秒。成功时它会常驻持有 receiver，poll() 为 None。
            if proc.poll() is not None:
                break
            time.sleep(0.3)
        if session is None:
            _json_print({
                "status": "error",
                "message": "认证服务启动失败：当前环境（很可能是受限沙箱）不允许本机监听端口或起后台进程，认证无法在此完成。",
                "hint": "立刻告知用户：当前环境无法完成自动认证（需监听 127.0.0.1:35101-35110 并拉起 Teams），请在本地终端或非沙箱环境重试；同时把已生成的内容交给用户。不要重试。",
                "environment": args.env,
            })
            return 1
    _print_pending_links(args.env, session)
    return 0


def _handle_wait(args: argparse.Namespace) -> int:
    """等待已拉起的认证完成；token 落地或会话结束即返回。"""
    token, expiry, _source = _load_cached_token(args.env)
    if token:
        _json_print({
            "status": "ok",
            "authenticated": True,
            "expires_at": expiry,
            "message": "认证成功，凭证已缓存。",
            "environment": args.env,
        })
        return 0
    session = _load_pending_session_snapshot(args.env)
    if not _is_reusable_pending_session(session, args.env):
        _json_print({
            "status": "expired",
            "authenticated": False,
            "message": "没有进行中的认证会话，请用 --start 重新发起认证",
            "environment": args.env,
        })
        return AUTH_EXPIRED_EXIT_CODE
    result = _wait_for_existing_session(args.env, session, poll_timeout=args.poll_timeout)
    if result.get("status") == "ok":
        _json_print({
            **result,
            "message": "认证成功，凭证已缓存。",
            "environment": args.env,
        })
        return 0
    _json_print({**result, "environment": args.env})
    if result.get("status") == "pending":
        return AUTH_PENDING_EXIT_CODE
    return AUTH_EXPIRED_EXIT_CODE if result.get("status") == "expired" else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IM Teams 公网认证工具")
    parser.add_argument("--check", action="store_true", help="仅检查 Keyring/环境变量中的 token")
    parser.add_argument(
        "--start",
        action="store_true",
        help="后台拉起认证并立即返回认证链接（之后用 --wait 等待结果）",
    )
    parser.add_argument("--wait", action="store_true", help="等待已拉起的认证完成")
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=None,
        help="仅配合 --wait：本次轮询最长秒数，超时且会话未过期返回 status=pending（可再调一次 --wait 继续等）；不传则阻塞到会话剩余全部时长",
    )
    parser.add_argument("--no-cache", action="store_true", help="忽略并清理本地缓存，强制重新认证")
    parser.add_argument("--clear", action="store_true", help="清除当前环境的 token")
    parser.add_argument("--clear-all", action="store_true", help="清除所有环境的 token")
    parser.add_argument(
        "--env",
        choices=sorted(LANDING_URLS),
        default=ACTIVE_ENVIRONMENT,
        help="临时覆盖当前环境；未传时使用 scripts/config.py 中的 ACTIVE_ENVIRONMENT",
    )
    parser.add_argument("--landing-url", default=os.environ.get(ENV_LANDING_URL), help="覆盖认证落地页 URL")
    parser.add_argument("--open-url-directly", action="store_true", help="调试用：不走 Teams scheme，直接打开落地页")
    parser.add_argument("--port", type=int, help="指定本地接收端口，默认从 35101-35110 选择")
    parser.add_argument("--timeout", type=int, default=RECEIVER_TIMEOUT_SECONDS)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    args.env = normalize_environment(args.env)
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    keyring_status = ensure_keyring()
    if not keyring_status["available"]:
        # 系统钥匙串不可用（沙箱里常见）：不阻断，凭证回退到本地文件存储（credential_store 自动处理）。
        logger.warning("OS Keyring 不可用，凭证将回退本地文件存储: %s", keyring_status.get("message"))

    if args.check:
        token, expiry, source = _load_cached_token(args.env)
        if token:
            _json_print({
                "status": "ok",
                "authenticated": True,
                "expires_at": expiry,
                "source": source,
                "environment": args.env,
            })
            return 0
        _json_print({
            "status": "expired",
            "authenticated": False,
            "message": "认证已过期或未认证",
            "environment": args.env,
        })
        return AUTH_EXPIRED_EXIT_CODE

    if args.clear:
        removed = keyring_clear_token(args.env)
        _remove_pending_session(args.env)
        payload = {
            "status": "ok",
            "message": (
                f"已清除 {args.env} 环境的 token"
                if removed
                else f"{args.env} 环境没有需要清除的 token"
            ),
            "environment": args.env,
        }
        if os.environ.get(env_token_name_for(args.env)):
            payload["warning"] = (
                "检测到环境变量 token，清除钥匙串不会移除它；"
                "如需彻底清除请手动 unset 对应环境变量。"
            )
        _json_print(payload)
        return 0

    if args.clear_all:
        results = keyring_clear_all_tokens()
        for environment in LANDING_URLS:
            _remove_pending_session(environment)
        payload = {
            "status": "ok",
            "message": "已清除所有环境的 token",
            "results": results,
        }
        env_tokens = [
            env for env in results if os.environ.get(env_token_name_for(env))
        ]
        if env_tokens:
            payload["warning"] = (
                f"检测到环境变量 token（{', '.join(env_tokens)}），"
                "清除钥匙串不会移除它们；如需彻底清除请手动 unset 对应环境变量。"
            )
        _json_print(payload)
        return 0

    if args.start or args.wait:
        try:
            return _handle_start(args) if args.start else _handle_wait(args)
        except AuthError as exc:
            _json_print({"status": "error", "message": str(exc), "environment": args.env})
            return 1

    if args.no_cache:
        keyring_clear_token(args.env)
    else:
        token, expiry, source = _load_cached_token(args.env)
        if token:
            _json_print({
                "status": "ok",
                "message": "已认证（使用缓存凭证）。",
                "authenticated": True,
                "expires_at": expiry,
                "source": source,
                "environment": args.env,
            })
            return 0

    try:
        landing_base_url = args.landing_url or LANDING_URLS[args.env]
        session, server, created_session = _get_or_create_pending_session(
            args.env,
            landing_base_url,
            args.timeout,
            args.port,
        )
        landing_url = session["landingUrl"]
        request_expires_at = session["requestExpiresAt"]
        receiver_url = session["receiver"]
        session_id = session["sessionId"]
        login_url = landing_url if args.open_url_directly else _build_scheme_url(args.env, landing_url)
        # 可点的 Teams 认证链接（https，任何客户端可点）。scheme 自动拉起在
        # Windows 等环境可能失败，始终输出 appLinkUrl 作为兜底，让用户能手动点开。
        applink_url = _build_applink_url(landing_url)
        opened = False
        if created_session:
            _print_auth_fallback(args.env, applink_url, landing_url)
            opened = _open_browser(login_url)
            logger.info("Landing page opened=%s receiver=%s session=%s", opened, receiver_url, session_id)
            result = _wait_for_token(server, args.timeout)
        else:
            _print_auth_fallback(args.env, applink_url, landing_url)
            logger.info("Reusing pending auth session receiver=%s session=%s", receiver_url, session_id)
            result = _wait_for_existing_session(args.env, session)

        _store_session_result(args.env, session_id, result)

        if result.get("status") == "ok":
            # 成功后清理待完成会话文件；token 已在 keyring，复用进程从 keyring 取
            _remove_pending_session(args.env)
            _json_print({
                **result,
                "message": "认证成功，凭证已缓存。",
                "environment": args.env,
                "receiver": receiver_url,
                "sessionId": session_id,
                "winId": session["winId"],
                "appLinkUrl": applink_url,
                "landingUrl": landing_url,
                "requestExpiresAt": request_expires_at,
                "landingUrlOpened": opened,
                "reusedSession": not created_session,
            })
            return 0
        _json_print(
            {
                **result,
                "environment": args.env,
                "receiver": receiver_url,
                "sessionId": session_id,
                "winId": session["winId"],
                "appLinkUrl": applink_url,
                "requestExpiresAt": request_expires_at,
                **_build_browser_fallback_payload(
                    args.env,
                    landing_url,
                    request_expires_at,
                ),
                "reusedSession": not created_session,
            }
        )
        return AUTH_EXPIRED_EXIT_CODE if result.get("status") == "expired" else 1
    except AuthError as exc:
        _json_print({"status": "error", "message": str(exc), "environment": args.env})
        return 1
    except Exception as exc:
        logger.exception("Unexpected auth failure")
        _json_print({"status": "error", "message": f"认证失败: {exc}", "environment": args.env})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
