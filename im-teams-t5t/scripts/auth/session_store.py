#!/usr/bin/env python3
"""
IM Teams 认证待完成会话存储。

负责待完成认证会话的文件锁、读写、复用判定与时效计算；
不处理 receiver 网络、token 兑换或缓存，由 auth.py 编排调用。
"""

from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse

from config import CACHE_DIR, RECEIVER_HOST, RECEIVER_PORTS
from errors import AuthError

PENDING_SESSION_STATUS = "pending"
SESSION_LOCK_WAIT_SECONDS = 5
SESSION_LOCK_STALE_SECONDS = 30
SESSION_POLL_INTERVAL_SECONDS = 0.5


def pending_session_path(environment: str):
    return CACHE_DIR / f"pending_session_{environment}.json"


def pending_session_lock_path(environment: str):
    return CACHE_DIR / f"pending_session_{environment}.lock"


def parse_iso_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def remaining_seconds(expires_at: str | None) -> int:
    deadline = parse_iso_timestamp(expires_at)
    if deadline is None:
        return 0
    return max(0, int(deadline - time.time()))


def is_request_expired(expires_at: str | None) -> bool:
    deadline = parse_iso_timestamp(expires_at)
    return deadline is None or time.time() >= deadline


def format_request_expiry(deadline: float) -> str:
    return datetime.fromtimestamp(deadline).astimezone().isoformat(timespec="seconds")


@contextmanager
def pending_session_lock(environment: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = pending_session_lock_path(environment)
    deadline = time.time() + SESSION_LOCK_WAIT_SECONDS
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            # 残留锁兜底：进程在临界区内被杀会留下锁文件，超过 stale 阈值则抢占
            try:
                if time.time() - os.path.getmtime(lock_path) > SESSION_LOCK_STALE_SECONDS:
                    os.unlink(lock_path)
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                raise AuthError("等待认证会话锁超时")
            time.sleep(0.1)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass


def load_pending_session_snapshot(environment: str) -> dict | None:
    path = pending_session_path(environment)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_pending_session_locked(environment: str) -> dict | None:
    payload = load_pending_session_snapshot(environment)
    if payload and payload.get("environment") == environment:
        return payload
    return None


def save_pending_session_locked(environment: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = pending_session_path(environment)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def remove_pending_session_locked(environment: str) -> None:
    path = pending_session_path(environment)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def remove_pending_session(environment: str) -> None:
    with pending_session_lock(environment):
        remove_pending_session_locked(environment)


def is_valid_receiver_url(receiver_url: str) -> bool:
    try:
        parsed = urlparse(receiver_url)
        port = int(parsed.port or 0)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == RECEIVER_HOST
        and port in RECEIVER_PORTS
        and parsed.path == "/token"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_receiver_alive(receiver_url: str) -> bool:
    if not is_valid_receiver_url(receiver_url):
        return False
    parsed = urlparse(receiver_url)
    try:
        port = int(parsed.port or 0)
    except (TypeError, ValueError):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((RECEIVER_HOST, port))
            return True
        except OSError:
            return False


def is_reusable_pending_session(session: dict | None, environment: str) -> bool:
    if not isinstance(session, dict):
        return False
    if session.get("environment") != environment:
        return False
    if session.get("status") != PENDING_SESSION_STATUS:
        return False
    if is_request_expired(session.get("requestExpiresAt")):
        return False
    required_fields = ("sessionId", "state", "receiver", "landingUrl", "requestExpiresAt", "winId")
    if not all(isinstance(session.get(field), str) and session.get(field) for field in required_fields):
        return False
    return is_receiver_alive(session["receiver"])


def build_pending_session(environment: str, state: str, session_id: str, receiver_url: str,
                          landing_url: str, request_expires_at: str) -> dict:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "status": PENDING_SESSION_STATUS,
        "environment": environment,
        "sessionId": session_id,
        "winId": session_id,
        "state": state,
        "receiver": receiver_url,
        "landingUrl": landing_url,
        "requestExpiresAt": request_expires_at,
        "startedAt": now,
        "updatedAt": now,
    }


def store_session_result(environment: str, session_id: str, result: dict) -> None:
    with pending_session_lock(environment):
        session = load_pending_session_locked(environment)
        if not session or session.get("sessionId") != session_id:
            return
        session.update(result)
        session["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
        save_pending_session_locked(environment, session)
