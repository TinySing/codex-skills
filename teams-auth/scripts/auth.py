#!/usr/bin/env python3
"""
360Teams 网关认证工具

通过浏览器 SSO 回调完成登录，获取网关 token 并缓存至 OS Keyring。
网关 token 可供其他 Teams 技能换取各服务级凭证。
凭证仅存储于 OS Keyring，不落盘明文。

示例:
    # 登录认证（自动打开浏览器）
    python auth.py
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import logging
import os
import random
import socket
import sys
import platform
import subprocess
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import webbrowser

from config import (
    TOKEN_MAX_AGE_SECONDS,
    SSO_BROWSER_LOGIN_URL, CALLBACK_PORTS, CALLBACK_TIMEOUT,
    TOKEN_LOGIN_API,
    KEYRING_SERVICE, KEYRING_GATEWAY_TOKEN, KEYRING_TOKEN_EXPIRY, KEYRING_DEVICE_CODE,
    keyring_save_gateway_token, keyring_load_gateway_token, keyring_clear_gateway_token,
    _keyring_available,
    TeamsError, AuthError, Config, build_config, setup_logging,
)
from http_client import http_request


# =============================================================================
# Gateway token cache helpers (Keyring only)
# =============================================================================

def load_cached_token() -> tuple[str | None, str | None]:
    """Load valid gateway token and device_code from cache.

    Resolution order:
        1. 环境变量 TEAMS_GATEWAY_TOKEN（Harness 场景）
        2. OS Keyring

    Returns:
        (token, device_code) — both may be None if cache is missing or expired.
    """
    logger = logging.getLogger(__name__)

    # 1. 环境变量
    env_token = os.environ.get("TEAMS_GATEWAY_TOKEN")
    if env_token:
        logger.debug("Using gateway token from environment variable")
        return env_token, None

    # 2. Keyring
    if _keyring_available():
        token, device_code = keyring_load_gateway_token()
        if token:
            logger.debug("Using gateway token from OS keyring")
            return token, device_code

    return None, None


def save_cached_token(token: str, device_code: str | None = None,
                      max_age_seconds: int = TOKEN_MAX_AGE_SECONDS) -> None:
    """Persist gateway token to OS Keyring.

    Raises:
        AuthError: if keyring is not available or save fails.
    """
    logger = logging.getLogger(__name__)
    keyring_save_gateway_token(token, device_code=device_code, max_age_seconds=max_age_seconds)
    logger.info("Gateway token saved to OS keyring")


def clear_token_cache() -> None:
    """Remove the gateway token from OS Keyring."""
    keyring_clear_gateway_token()


def is_authenticated() -> bool:
    """判断当前网关 token 缓存是否有效。"""
    token, _ = load_cached_token()
    return token is not None


# =============================================================================
# Browser SSO callback server
# =============================================================================

def _build_login_success_html(user_info: dict) -> bytes:
    """Build the login success page HTML with dynamic user info."""
    template_path = Path(__file__).parent / "templates" / "login_success.html"
    tpl = template_path.read_text("utf-8")

    # Build user info display rows
    rows = []
    fields = [
        ("accountName", "姓名"),
        ("email", "邮箱"),
        ("domainAccount", "账号"),
    ]
    for key, label in fields:
        value = user_info.get(key)
        if value:
            rows.append(
                f'<div class="info-row">'
                f'<span class="info-label">{label}</span>'
                f'<span class="info-value">{html_module.escape(str(value))}</span>'
                f'</div>'
            )

    if rows:
        user_info_html = '<div class="user-info">\n' + "\n".join(rows) + "\n</div>"
    else:
        user_info_html = ""

    return tpl.replace("{{USER_INFO}}", user_info_html).encode("utf-8")


def _build_login_error_html(error_msg: str) -> bytes:
    """Build the login error page HTML."""
    template_path = Path(__file__).parent / "templates" / "login_error.html"
    tpl = template_path.read_text("utf-8")
    return tpl.replace("{{ERROR_MSG}}", html_module.escape(error_msg)).encode("utf-8")


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handle the SSO redirect callback: extract ticket, exchange for token, cache, and respond."""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        ticket_list = params.get("ticket")

        if not ticket_list:
            self.server.auth_error = "SSO 回调未返回 ticket"
            self._send_error_page("回调参数缺少 ticket")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        ticket = ticket_list[0]
        self.server.ticket = ticket

        # Step 2: exchange ticket for gateway token
        try:
            token, user_info = self._exchange_ticket(ticket)
        except (AuthError, TeamsError) as exc:
            self.server.auth_error = str(exc)
            self._send_error_page(f"Token 交换失败: {exc}")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        # Step 3: cache the gateway token locally
        try:
            save_cached_token(token, device_code=self.server.device_code)
        except (AuthError, TeamsError) as exc:
            self.server.auth_error = str(exc)
            self._send_error_page(f"Token 缓存失败: {exc}")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        # Success: send success page with user info
        self._send_success_page(user_info)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _exchange_ticket(self, ticket: str) -> tuple[str, dict]:
        """Exchange SSO ticket for gateway token via API.

        Returns:
            (token, user_info_dict)

        Raises:
            AuthError: on any failure.
        """
        logger = logging.getLogger(__name__)
        token_login_url = self.server.token_login_url

        status, _, body = http_request(
            token_login_url,
            method="POST",
            body={"ticket": ticket},
        )
        if status >= 400:
            raise AuthError(f"Token exchange request failed with HTTP {status}")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            body_preview = body[:200].decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body[:200])
            raise AuthError(f"Token exchange returned non-JSON (HTTP {status}): {body_preview!r}") from exc

        if payload.get("code") != 0:
            raise AuthError(f"Token exchange failed: {payload.get('msg')}")

        try:
            token = payload["data"]["token"]
        except (KeyError, TypeError) as exc:
            raise AuthError(f"Token exchange response missing data.token: {payload}") from exc

        user_info = payload.get("data", {}).get("userInfoDTO", {})
        logger.info("Step 2 OK - gateway token acquired (user: %s)", user_info.get("accountName", "unknown"))
        return token, user_info

    def _send_success_page(self, user_info: dict) -> None:
        """Send the success HTML page with dynamic user info."""
        html_bytes = _build_login_success_html(user_info)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_bytes)

    def _send_error_page(self, error_msg: str) -> None:
        """Send an error HTML page to the browser."""
        html_bytes = _build_login_error_html(error_msg)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_bytes)

    def log_message(self, fmt, *args):
        """Suppress default stderr logging."""
        pass


def _find_available_port() -> int:
    """Find a random available port from CALLBACK_PORTS.

    Raises:
        AuthError: if no port is available after trying all candidates.
    """
    candidates = list(CALLBACK_PORTS)
    random.shuffle(candidates)
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise AuthError(f"无法在端口 {CALLBACK_PORTS} 中绑定可用端口")


def _open_browser(url: str) -> None:
    """Open URL in the system default browser with platform-specific fallbacks.

    On macOS (especially in Trae/IDE sandboxes), webbrowser.open() often fails
    because the VM environment cannot access macOS Launch Services. We therefore
    try multiple strategies in order:

    1. webbrowser.open() — works in normal terminal environments
    2. macOS: `open` command — bypasses Launch Services discovery
    3. macOS: direct Safari/Chrome/Firefox paths as last resort
    4. Linux: `xdg-open` command
    5. Print URL for manual copy — never raises AuthError here

    Raises:
        AuthError: only if no strategy could even be attempted (should not happen).
    """
    logger = logging.getLogger(__name__)
    sys_name = platform.system()

    # Strategy 1: webbrowser.open()
    try:
        opened = webbrowser.open(url)
        if opened:
            logger.info("Browser opened via webbrowser.open()")
            return
    except Exception as exc:
        logger.error("webbrowser.open() failed: %s", exc)

    # Strategy 2: platform-specific commands
    if sys_name == "Darwin":
        for cmd in [
            ["/usr/bin/open", url],
            ["open", url],
            ["/Applications/Safari.app/Contents/MacOS/Safari", url],
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", url],
        ]:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info("Browser opened via: %s", cmd[0])
                return
            except (FileNotFoundError, PermissionError, OSError) as exc:
                logger.error("Failed to open via %s: %s", cmd[0], exc)
                continue

    elif sys_name == "Linux":
        for opener in ["xdg-open", "google-chrome", "firefox", "chromium"]:
            try:
                subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info("Browser opened via: %s", opener)
                return
            except (FileNotFoundError, PermissionError, OSError) as exc:
                logger.error("Failed to open via: %s: %s ", opener, exc)
                continue

    else:
        try:
            subprocess.Popen(["cmd", "/c", "start", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("Browser opened via cmd /c start")
            return
        except (FileNotFoundError, OSError) as exc:
            logger.error("Failed to open via: windows: %s ", exc)

    # All strategies failed — inform user but don't raise AuthError
    logger.warning(
        "无法自动打开浏览器。请手动访问以下链接完成登录：\n%s", url
    )
    print(f"\n请在浏览器中打开以下链接完成登录：\n{url}\n")


def _wait_for_ticket(port: int, timeout: int = CALLBACK_TIMEOUT,
                     cached_device_code: str | None = None) -> None:
    """Start a local HTTP server, open the browser, and wait for the SSO callback.

    The callback handler will exchange the ticket for a gateway token and
    cache it before responding to the browser.

    Raises:
        AuthError: on timeout, if no ticket is received, or if token exchange/caching fails.
    """
    logger = logging.getLogger(__name__)

    # Resolve device_code and token_login_url before starting server
    token_login_url = TOKEN_LOGIN_API
    if "{deviceCode}" in token_login_url:
        device_code = cached_device_code or f"agent-{uuid.uuid4()}"
        token_login_url = token_login_url.replace("{deviceCode}", device_code)
        logger.info("Using deviceCode: %s (reused=%s)", device_code, cached_device_code is not None)
    else:
        device_code = None

    # Bind with retry to handle rare port-race between _find_available_port and HTTPServer
    server = None
    for attempt in range(3):
        try:
            server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
            break
        except OSError:
            logger.warning("Port %d bind failed (attempt %d/3), picking new port", port, attempt + 1)
            port = _find_available_port()
    if server is None:
        raise AuthError("无法绑定本地回调端口，请重试")
    server.ticket = None
    server.device_code = device_code
    server.token_login_url = token_login_url
    server.auth_error = None

    redirect_url = f"http://127.0.0.1:{port}"
    sso_url = f"{SSO_BROWSER_LOGIN_URL}?redirect={quote(redirect_url, safe='')}"

    _open_browser(sso_url)

    # Run the server in a background thread so we can enforce a timeout.
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    logger.info("Starting SSO callback server, port: %d", port)

    server_thread.join(timeout=timeout)
    if server_thread.is_alive():
        server.shutdown()
        raise AuthError(f"等待 SSO 回调超时（{timeout} 秒），请重试")

    # Check for errors from the callback handler (Step 2 or 3 failure)
    if server.auth_error:
        raise AuthError(server.auth_error)

    if not server.ticket:
        raise AuthError("SSO 回调未返回 ticket")

    logger.debug("Ticket acquired, token exchanged and cached via browser callback")


# =============================================================================
# Authentication (browser SSO -> gateway token)
# =============================================================================

def browser_login(cfg: Config) -> None:
    """Authenticate via browser SSO callback and cache the gateway token.

    Steps (1-2-3 now happen inside the callback handler):
        1. Open browser for SSO login -> receive ticket via callback
        2. (In callback) POST token/ticket/login -> gateway token
        3. (In callback) Cache gateway token to OS Keyring (7-day validity)

    Raises:
        AuthError: if any step fails, keyring is unavailable, or expected fields are missing.
    """
    logger = logging.getLogger(__name__)

    if not cfg.no_cache:
        cached_token, _ = load_cached_token()
        if cached_token:
            logger.info("Using cached gateway token (expires at %s)",
                        _load_token_expiry())
            cfg._used_cache = True
            return

    logger.info("Authenticating via browser SSO callback...")

    # Load cached device_code for potential {deviceCode} substitution
    _, cached_device_code = load_cached_token()

    # Steps 1-2-3: ticket acquisition, token exchange, and caching
    # all happen inside the callback handler now
    port = _find_available_port()
    _wait_for_ticket(port, cached_device_code=cached_device_code)
    logger.info("Login complete - gateway token acquired and cached")


def _load_token_expiry() -> str:
    """Return the human-readable expiry of the current token cache, or 'unknown'."""
    if _keyring_available():
        try:
            import keyring
            expiry_str = keyring.get_password(KEYRING_SERVICE, KEYRING_TOKEN_EXPIRY)
            if expiry_str:
                return expiry_str
        except Exception:
            pass
    return "unknown"


# =============================================================================
# CLI entry point
# =============================================================================

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="360Teams 网关认证工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 登录认证（自动打开浏览器）
  %(prog)s
  # 检查认证状态
  %(prog)s --check
        """,
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="忽略本地认证缓存，强制重新认证"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="仅检查认证状态，不触发登录（已认证返回0，未认证返回4）"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="启用调试日志")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口 — 执行浏览器 SSO 登录认证，缓存网关 token。

    Returns:
        0 成功，1 失败，4 认证过期/未认证（--check 模式或凭证无效时）。
    """
    args = _parse_args(argv)
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        cfg = build_config(args)
    except TeamsError as exc:
        logger.error("配置错误: %s", exc)
        return 1

    # --check: 仅检查认证状态，不触发登录
    if args.check:
        token, _ = load_cached_token()
        if token:
            expiry = _load_token_expiry()
            logger.info("认证有效 (expires at %s)", expiry)
            print(json.dumps({"status": "ok", "authenticated": True, "expires_at": expiry},
                             ensure_ascii=False))
            return 0
        else:
            logger.info("AUTH_EXPIRED: 无有效凭证")
            print(json.dumps({"status": "expired", "authenticated": False,
                              "message": "认证已过期或未认证，需重新登录"},
                             ensure_ascii=False))
            return 4

    try:
        browser_login(cfg)
        if cfg._used_cache:
            msg = "Already authenticated (using cached token)."
        else:
            msg = "Login successful, gateway token cached."
        logger.info(msg)
        print(json.dumps({"status": "ok", "message": msg}, ensure_ascii=False))
        return 0

    except AuthError as exc:
        logger.error("认证失败: %s", exc)
        # 仅在强制重新认证失败时清除旧 token；
        # 普通登录失败时保留旧 token（可能仍有效，弱网场景下避免丢失凭证）
        if cfg.no_cache:
            clear_token_cache()
        return 1
    except TeamsError as exc:
        logger.error("错误: %s", exc)
        return 1
    except Exception as exc:
        logger.error("未预期错误: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
