#!/usr/bin/env python3
"""
Exchange 邮件技能 - 环境配置脚本

功能：
1. 检测 Python 环境是否满足要求（3.7+）
2. 自动安装依赖库（exchangelib、keyring 等）
3. 清除已存储的凭证

本脚本仅负责环境准备，不接受任何凭证参数。
用户名和密码请通过浏览器配置页面输入：
    python scripts/config_web.py --port 8765

用法：
    python scripts/setup.py           # 检查环境并安装依赖
    python scripts/setup.py --clear   # 清除已存储的凭证
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 将脚本目录加入 sys.path，以便导入 env_check 和 keyring_store
sys.path.insert(0, str(Path(__file__).parent))


def output_result(success, message, data=None):
    result = {"success": success, "message": message}
    if data:
        result.update(data)
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(
        description="Exchange 邮件技能 - 环境配置脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python scripts/setup.py           # 检查环境并安装依赖
  python scripts/setup.py --clear   # 清除已存储的凭证

配置邮箱凭证请使用浏览器配置页面:
  python scripts/config_web.py --port 8765
        """
    )
    parser.add_argument("--clear", action="store_true", help="清除已存储的凭证")
    args = parser.parse_args()

    if args.clear:
        from keyring_store import keyring_clear_config
        if keyring_clear_config():
            output_result(True, "已清除存储的凭证")
        else:
            output_result(False, "清除凭证失败，可能没有存储过凭证")
        return

    # 1. Python 环境预检测
    try:
        from env_check import find_python_with_info
        py_info = find_python_with_info()
        if py_info.get("status") != "ok":
            output_result(False, f"Python 环境不满足要求: {py_info.get('message', '未找到 Python 3.7+')}")
            sys.exit(1)
    except EnvironmentError as e:
        output_result(False, f"Python 环境检测失败: {str(e)}")
        sys.exit(1)

    # 2. 自动安装依赖
    from env_check import ensure_dependencies
    deps = ensure_dependencies()
    for pkg in deps["packages"]:
        if pkg["error"]:
            output_result(False, f"依赖安装失败: {pkg['error']}")
            sys.exit(1)

    # 3. 确保 keyring 可用
    from keyring_store import ensure_keyring
    kr = ensure_keyring()
    if not kr["available"]:
        output_result(False, kr["message"])
        sys.exit(1)

    # 输出结果
    py_version = py_info.get("version", "unknown")
    installed_pkgs = [p["name"] for p in deps["packages"] if p["installed"]]
    auto_installed = [p["name"] for p in deps["packages"] if p.get("auto_installed")]
    msg = f"环境配置完成 (Python {py_version})"
    if auto_installed:
        msg += f"，已自动安装: {', '.join(auto_installed)}"

    output_result(True, msg, {
        "python_path": py_info.get("python_path"),
        "python_version": py_version,
        "dependencies": installed_pkgs,
    })


if __name__ == "__main__":
    main()
