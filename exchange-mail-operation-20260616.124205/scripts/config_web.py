#!/usr/bin/env python3
"""
Exchange 邮箱交互式配置服务器

通过浏览器页面让用户输入邮箱用户名和密码，完成 Exchange 邮箱配置。
启动后在浏览器中打开配置页面，用户输入信息后自动将凭证存入 OS Keyring。

Keyring 仅存储用户名和密码，服务器地址/域名/邮箱后缀/版本等由 keyring_store.py 常量提供。

使用方式:
    python config_web.py [--port 0] [--timeout 0]

AI 助手调用流程:
    1. 启动服务器: python scripts/config_web.py
    2. 脚本自动打开浏览器（多策略回退）
    3. 用户在页面中输入用户名和密码
    4. 配置成功后服务器自动关闭，AI 助手继续后续操作

端口选择:
    - 默认从 35000~40000 随机选择一个可用端口，防止端口冲突
    - 可通过 --port 手动指定端口

关闭方式:
    - 配置成功后自动关闭（始终生效，防止服务常驻导致内存泄漏）
    - --timeout <秒>: 超时自动关闭

安全说明:
    - 服务器仅监听 127.0.0.1，不暴露到局域网
    - 用户名和密码仅通过浏览器页面输入，不经过命令行或环境变量
    - 凭证存入 OS Keyring（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service）
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# 获取技能脚本目录
SKILL_DIR = Path(__file__).parent


PORT_RANGE_START = 35000
PORT_RANGE_END = 40000


def _find_available_port() -> int:
    """从 35000~40000 随机选择一个可用端口"""
    candidates = list(range(PORT_RANGE_START, PORT_RANGE_END + 1))
    random.shuffle(candidates)
    for port in candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
            return port
        except OSError:
            continue
    raise RuntimeError(f"端口范围 {PORT_RANGE_START}-{PORT_RANGE_END} 内无可用端口")


def _open_browser(url: str) -> None:
    """Open URL in the system default browser with platform-specific fallbacks.

    In VM/IDE sandbox environments (workbuddy, Trae, etc.), webbrowser.open()
    often fails because the VM environment cannot access OS browser services.
    We therefore try multiple strategies in order:

    1. webbrowser.open() — works in normal terminal environments
    2. macOS: `open` command or direct Safari/Chrome paths
    3. Linux: `xdg-open` / `google-chrome` / `firefox` / `chromium`
    4. Windows: `cmd /c start` — bypasses Python's browser discovery
    5. Print URL for manual copy — never raises, always safe fallback
    """
    sys_name = platform.system()

    # Strategy 1: webbrowser.open()
    try:
        opened = webbrowser.open(url)
        if opened:
            print(json.dumps({"success": True, "message": "浏览器已打开"}, ensure_ascii=False))
            return
    except Exception:
        pass

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
                print(json.dumps({"success": True, "message": f"浏览器已通过 {cmd[0]} 打开"}, ensure_ascii=False))
                return
            except (FileNotFoundError, PermissionError, OSError):
                continue

    elif sys_name == "Linux":
        for opener in ["xdg-open", "google-chrome", "firefox", "chromium"]:
            try:
                subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(json.dumps({"success": True, "message": f"浏览器已通过 {opener} 打开"}, ensure_ascii=False))
                return
            except (FileNotFoundError, PermissionError, OSError):
                continue

    else:  # Windows
        try:
            subprocess.Popen(["cmd", "/c", "start", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(json.dumps({"success": True, "message": "浏览器已通过 cmd start 打开"}, ensure_ascii=False))
            return
        except (FileNotFoundError, OSError):
            pass

    # All strategies failed — inform user to visit URL manually
    print(json.dumps({
        "success": False,
        "message": f"无法自动打开浏览器，请手动访问以下链接完成配置：{url}"
    }, ensure_ascii=False))


def _load_html_template():
    """加载 HTML 模板文件"""
    template_path = SKILL_DIR / "templates" / "config.html"
    return template_path.read_text(encoding="utf-8")


def _test_connection(config):
    """使用配置直接测试 Exchange 连接（不经过子进程，不暴露密码到命令行）"""
    try:
        # 动态导入，避免在配置页面启动时就需要安装 exchangelib
        sys.path.insert(0, str(SKILL_DIR))
        from common import get_account

        account = get_account(config, verify_target="inbox")
        return True, "连接成功"
    except Exception as e:
        return False, f"连接失败: {str(e)}"


class ConfigHandler(BaseHTTPRequestHandler):
    """配置页面 HTTP 请求处理器"""
    server_info = {
        "server": "mail.qifu.com",
        "domain": "jk",
        "email_suffix": "qifu.com",
    }

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        # 从模板文件加载 HTML 并替换占位符
        html = _load_html_template()
        html = html.replace("{{SERVER}}", self.server_info["server"])
        email_suffix = self.server_info["email_suffix"]
        domain = self.server_info["domain"]
        html = html.replace("{{EMAIL_SUFFIX}}", f"@{email_suffix}")
        # 只替换 server-info 区域内的域名，不影响其他 "company" 文本
        html = html.replace("域名：<span>{{DOMAIN}}</span>", f"域名：<span>{domain}</span>")
        self.wfile.write(html.encode('utf-8'))

    def do_POST(self):
        if self.path == '/configure':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            params = urllib.parse.parse_qs(body)
            username = params.get('username', [''])[0]
            password = params.get('password', [''])[0]

            if not username or not password:
                result = {"success": False, "message": "用户名和密码不能为空"}
            else:
                try:
                    # 确保 keyring 可用
                    sys.path.insert(0, str(SKILL_DIR))
                    from keyring_store import keyring_save_config, ensure_keyring

                    kr = ensure_keyring()
                    if not kr["available"]:
                        result = {"success": False, "message": kr["message"]}
                    else:
                        # 直接调用 keyring 存储，不经过子进程命令行
                        keyring_save_config(username=username, password=password)

                        # 从 keyring 读取配置，直接测试连接
                        from keyring_store import keyring_load_config
                        config = keyring_load_config()
                        if config:
                            ok, msg = _test_connection(config)
                            if ok:
                                result = {"success": True, "message": f"配置完成，{msg}"}
                                # 配置成功后自动关闭服务器，防止服务常驻导致内存泄漏
                                def delayed_shutdown():
                                    time.sleep(1)  # 给客户端时间接收响应
                                    self.server.shutdown()
                                threading.Thread(target=delayed_shutdown, daemon=True).start()
                            else:
                                result = {"success": False, "message": f"凭证已保存但{msg}，请检查用户名和密码"}
                        else:
                            result = {"success": False, "message": "凭证保存后读取失败，请重试"}

                except Exception as e:
                    result = {"success": False, "message": str(e)}

            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    # 确保 stdout/stderr 使用 UTF-8 编码（防止 Windows 下中文乱码）
    sys.path.insert(0, str(SKILL_DIR))
    from common import ensure_utf8_output
    ensure_utf8_output()

    parser = argparse.ArgumentParser(description="Exchange 邮箱交互式配置服务器")
    parser.add_argument("--port", type=int, default=0,
                        help="服务端口，默认从 35000~40000 随机选择")
    parser.add_argument("--timeout", type=int, default=0,
                        help="自动关闭超时（秒），0 表示不自动关闭")
    parser.add_argument("--clear-before", action="store_true",
                        help="启动前清除旧凭证（用于切换账号场景）")
    args = parser.parse_args()

    # 启动前清除旧凭证（切换账号场景）
    if args.clear_before:
        from keyring_store import keyring_clear_config
        keyring_clear_config()
        print(json.dumps({
            "success": True,
            "message": "已清除旧凭证，准备配置新账号"
        }, ensure_ascii=False))

    # 从 keyring_store 常量获取服务器信息，用于 HTML 模板渲染
    from keyring_store import SERVER, DOMAIN, EMAIL_SUFFIX
    ConfigHandler.server_info = {
        "server": SERVER,
        "domain": DOMAIN,
        "email_suffix": EMAIL_SUFFIX,
    }

    # 确定端口并创建服务器（带重试，防止 TOCTOU 竞态导致端口被抢占）
    if args.port > 0:
        # 用户指定端口，不重试
        server = HTTPServer(('127.0.0.1', args.port), ConfigHandler)
        port = args.port
    else:
        # 随机端口：_find_available_port 预检测 + HTTPServer 绑定重试
        port = _find_available_port()
        server = None
        for attempt in range(5):
            try:
                server = HTTPServer(('127.0.0.1', port), ConfigHandler)
                break
            except OSError:
                # 端口在检测后、绑定前被抢占（TOCTOU），重新检测
                port = _find_available_port()
        if server is None:
            print(json.dumps({"success": False, "message": "无法绑定可用端口，请稍后重试"}, ensure_ascii=False))
            sys.exit(1)
    url = f"http://127.0.0.1:{port}"
    print(json.dumps({
        "success": True,
        "message": "配置服务器已启动",
        "url": url,
        "port": port
    }, ensure_ascii=False))

    # 自动打开浏览器（多策略回退，兼容 VM/IDE 沙箱环境）
    _open_browser(url)

    # 超时自动关闭
    if args.timeout > 0:
        def timeout_shutdown():
            time.sleep(args.timeout)
            print(json.dumps({
                "success": False,
                "message": "配置超时，服务器已自动关闭",
                "config_completed": False,
            }, ensure_ascii=False))
            server.shutdown()
        threading.Thread(target=timeout_shutdown, daemon=True).start()

    server.serve_forever()

    # serve_forever 返回后（被 shutdown 触发）— 配置成功，输出最终状态信号
    print(json.dumps({
        "success": True,
        "message": "配置完成，服务器已自动关闭",
        "config_completed": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
