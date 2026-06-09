#!/usr/bin/env python3
"""
360Teams 认证 - HTTP 客户端

本模块提供 urllib 封装的基础 HTTP 请求函数，
供 auth.py 引用。
"""

from __future__ import annotations

import json
import logging
import os
import ssl
from http.client import HTTPMessage
from urllib.request import Request, urlopen, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError as UrllibHTTPError, URLError

from config import DEFAULT_USER_AGENT, TeamsError


# =============================================================================
# HTTP helpers (urllib-based)
# =============================================================================

class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from following redirects so we can inspect headers."""

    def http_error_302(self, req, fp, code, msg, headers):
        return fp

    http_error_301 = http_error_303 = http_error_307 = http_error_302


def _create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context with proper CA certificates.

    On macOS (and some Linux distros), Python installed from python.org may not
    find system CA certificates, causing CERTIFICATE_VERIFY_FAILED. This function
    tries the default context first, and falls back to certifi's CA bundle if
    the default cert file does not exist.
    """
    ctx = ssl.create_default_context()

    # If the default CA file exists, trust it and return.
    if ctx.get_ca_certs() or (ssl.get_default_verify_paths().cafile and
                              os.path.isfile(ssl.get_default_verify_paths().cafile)):
        return ctx

    # Default CA file missing — try certifi, then SSL_CERT_FILE env var.
    cert_path = os.environ.get("SSL_CERT_FILE")
    if not cert_path:
        try:
            import certifi
            cert_path = certifi.where()
        except ImportError:
            pass

    if cert_path and os.path.isfile(cert_path):
        ctx.load_verify_locations(cert_path)

    return ctx


def _handle_ssl_error(exc: Exception) -> None:
    """Re-raise SSL certificate errors with actionable guidance."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        import platform
        plat = platform.system()
        if plat == "Darwin":
            hint = "macOS 证书缺失。运行 '/Applications/Python 3.*/Install Certificates.command' 或 pip install certifi"
        else:
            hint = "SSL 证书验证失败。尝试: pip install certifi 或设置 SSL_CERT_FILE 环境变量"
        raise TeamsError(f"SSL 证书验证失败: {exc}。{hint}") from exc


_HTTP_TIMEOUT = 120  # seconds


def http_request(
        url: str,
        method: str = "GET",
        body: dict | str | bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
        timeout: int = _HTTP_TIMEOUT,
) -> tuple[int, HTTPMessage, bytes]:
    """Execute an HTTP request via urllib and return (status, headers, body)."""
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)

    data = None
    if body is not None:
        if isinstance(body, dict):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body

    req = Request(url, data=data, headers=headers, method=method)
    ssl_context = _create_ssl_context()

    try:
        if follow_redirects:
            with urlopen(req, context=ssl_context, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read()
        else:
            opener = build_opener(HTTPSHandler(context=ssl_context), NoRedirectHandler())
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read()
    except UrllibHTTPError as exc:
        return exc.code, exc.headers, exc.read()
    except ssl.SSLCertVerificationError as exc:
        _handle_ssl_error(exc)
    except URLError as exc:
        raise TeamsError(f"网络请求失败: {exc.reason}") from exc
    except OSError as exc:
        raise TeamsError(f"网络连接错误: {exc}") from exc
