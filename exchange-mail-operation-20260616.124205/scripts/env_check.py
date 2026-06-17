#!/usr/bin/env python3
"""
env_check.py - Exchange 邮箱技能 Python 环境预检测模块

核心能力：
  - 检测可用的 Python 3.7+ 解释器路径（跨平台）
  - 绕过 VM Shim（vm/tools/ 下的 shim 会截断中文参数）
  - 缓存检测结果（7 天有效期，存储在技能目录下）
  - 检测依赖库可用性并自动安装（exchangelib、keyring 等）
  - Python 未安装时输出安装指引

作为模块使用:
    from env_check import find_python
    python_path = find_python()

作为 CLI 使用:
    python env_check.py
    # 输出 JSON: status, python_path, version, platform, cached, dependencies
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_PYTHON_VERSION: Tuple[int, int] = (3, 7)

# 技能目录
SKILL_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = SKILL_DIR / "cache"
CACHE_TXT: Path = CACHE_DIR / "python_path.txt"
CACHE_MAX_AGE_SECONDS: int = 7 * 24 * 3600

# Shim 路径标识 — 包含这些路径的被认为是 VM shim
SHIM_INDICATORS: Tuple[str, ...] = (
    "vm/tools/",
    "vm\\tools\\",
    ".trae-cn/modulardata/ai-agent/vm/",
)

# 需要检测的依赖库
REQUIRED_PACKAGES = [
    ("exchangelib", "exchangelib"),
    ("keyring", "keyring"),
]
# Python < 3.9 额外需要
if sys.version_info < (3, 9):
    REQUIRED_PACKAGES.append(("backports.zoneinfo", "backports.zoneinfo"))

# 可选依赖库（安装失败不退出，仅标记不可用）
OPTIONAL_PACKAGES = [
    ("pypdf", "pypdf"),
    ("lxml", "lxml"),
]


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _current_platform() -> str:
    """返回标准化平台字符串: Windows, macOS, 或 Linux"""
    system = platform.system()
    if system == "Darwin":
        return "macOS"
    if system == "Windows":
        return "Windows"
    return "Linux"


def _subprocess_args() -> dict:
    """返回通用 subprocess 关键参数（如 Windows 上的 CREATE_NO_WINDOW）"""
    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


# ---------------------------------------------------------------------------
# Shim detection
# ---------------------------------------------------------------------------

def is_shim(path: str) -> bool:
    """判断路径是否为 VM shim（会截断中文参数）"""
    normalised = str(path)
    for indicator in SHIM_INDICATORS:
        if indicator in normalised:
            return True
    return False


# ---------------------------------------------------------------------------
# Version validation
# ---------------------------------------------------------------------------

def _parse_version(version_output: str) -> Optional[Tuple[int, int]]:
    """将 Python 3.x.y 字符串解析为 (major, minor) 元组"""
    version_output = version_output.strip()
    if not version_output.startswith("Python"):
        return None
    try:
        parts = version_output.replace("Python", "").strip().split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return None


def validate_python(path: str) -> Optional[Tuple[str, str]]:
    """
    验证路径是否指向可用的 Python 3.7+ 解释器。

    成功返回 (version_string, real_executable_path)，
    失败返回 None。
    """
    try:
        # Step 1: 检查版本
        result = subprocess.run(
            [path, "--version"],
            **_subprocess_args(),
        )
        if result.returncode != 0:
            return None
        version_str = result.stdout.strip() or result.stderr.strip()
        version_tuple = _parse_version(version_str)
        if version_tuple is None or version_tuple < MIN_PYTHON_VERSION:
            return None

        # Step 2: 通过 sys.executable 解析真实路径
        result2 = subprocess.run(
            [path, "-c", "import sys; print(sys.executable)"],
            **_subprocess_args(),
        )
        if result2.returncode == 0 and result2.stdout.strip():
            real_path = result2.stdout.strip()
        else:
            real_path = path

        return (version_str, real_path)
    except (OSError, FileNotFoundError, PermissionError):
        return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _read_cache() -> Optional[str]:
    """读取缓存的 Python 路径（如果存在且未过期）"""
    try:
        if not CACHE_TXT.exists():
            return None
        if time.time() - CACHE_TXT.stat().st_mtime > CACHE_MAX_AGE_SECONDS:
            return None
        cached_path = CACHE_TXT.read_text(encoding="utf-8").strip()
        if not cached_path:
            return None
        return cached_path
    except OSError:
        return None


def _write_cache(python_path: str) -> None:
    """将检测到的 Python 路径持久化到缓存文件"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_TXT.write_text(python_path, encoding="utf-8")
    except OSError:
        pass  # 缓存写入失败不影响功能


# ---------------------------------------------------------------------------
# Discovery strategies
# ---------------------------------------------------------------------------

def _try_candidate(path: str) -> Optional[Tuple[str, str]]:
    """验证候选路径，返回 (version, real_path) 或 None"""
    if is_shim(path):
        return None
    return validate_python(path)


def _discover_windows() -> Optional[Tuple[str, str]]:
    """Windows 平台 Python 发现"""
    # Strategy 1: py launcher - py -0p 列出已安装版本及路径
    try:
        result = subprocess.run(
            ["py", "-0p"],
            **_subprocess_args(),
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if parts:
                    candidate = parts[-1]
                    validated = _try_candidate(candidate)
                    if validated:
                        return validated
    except (OSError, FileNotFoundError):
        pass

    # Strategy 2: 常见安装路径
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        base = Path(local_app_data) / "Programs" / "Python"
        if base.exists():
            for child in sorted(base.iterdir(), reverse=True):
                if child.is_dir() and child.name.lower().startswith("python3"):
                    exe = child / "python.exe"
                    if exe.exists():
                        validated = _try_candidate(str(exe))
                        if validated:
                            return validated

    # Strategy 3: where python3 然后 where python（排除 shim）
    for cmd in ("python3", "python"):
        try:
            result = subprocess.run(
                ["where", cmd],
                **_subprocess_args(),
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    candidate = line.strip()
                    if candidate:
                        validated = _try_candidate(candidate)
                        if validated:
                            return validated
        except (OSError, FileNotFoundError):
            pass

    return None


def _discover_macos() -> Optional[Tuple[str, str]]:
    """macOS 平台 Python 发现"""
    # Strategy 1: which python3
    for cmd in ("python3",):
        try:
            result = subprocess.run(
                ["which", cmd],
                **_subprocess_args(),
            )
            if result.returncode == 0:
                candidate = result.stdout.strip()
                if candidate:
                    validated = _try_candidate(candidate)
                    if validated:
                        return validated
        except (OSError, FileNotFoundError):
            pass

    # Strategy 2: Homebrew 路径
    brew_prefixes = [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
    ]
    for candidate in brew_prefixes:
        validated = _try_candidate(candidate)
        if validated:
            return validated

    # Strategy 3: python3 -c "import sys; print(sys.executable)"
    try:
        result = subprocess.run(
            ["python3", "-c", "import sys; print(sys.executable)"],
            **_subprocess_args(),
        )
        if result.returncode == 0:
            candidate = result.stdout.strip()
            if candidate:
                validated = _try_candidate(candidate)
                if validated:
                    return validated
    except (OSError, FileNotFoundError):
        pass

    return None


def _discover_linux() -> Optional[Tuple[str, str]]:
    """Linux 平台 Python 发现"""
    # Strategy 1: which python3
    try:
        result = subprocess.run(
            ["which", "python3"],
            **_subprocess_args(),
        )
        if result.returncode == 0:
            candidate = result.stdout.strip()
            if candidate:
                validated = _try_candidate(candidate)
                if validated:
                    return validated
    except (OSError, FileNotFoundError):
        pass

    # Strategy 2: /usr/bin/python3
    validated = _try_candidate("/usr/bin/python3")
    if validated:
        return validated

    # Strategy 3: python3 -c "import sys; print(sys.executable)"
    try:
        result = subprocess.run(
            ["python3", "-c", "import sys; print(sys.executable)"],
            **_subprocess_args(),
        )
        if result.returncode == 0:
            candidate = result.stdout.strip()
            if candidate:
                validated = _try_candidate(candidate)
                if validated:
                    return validated
    except (OSError, FileNotFoundError):
        pass

    return None


# ---------------------------------------------------------------------------
# Installation guidance
# ---------------------------------------------------------------------------

_INSTALL_GUIDES = {
    "Windows": (
        "请安装 Python 3.7+：https://www.python.org/downloads/ "
        "安装时务必勾选 'Add Python to PATH' 和 'Install py launcher'。"
    ),
    "macOS": (
        "Homebrew: brew install python@3.9 "
        "或官方安装包: https://www.python.org/downloads/"
    ),
    "Linux": (
        "Ubuntu/Debian: sudo apt install python3.9 "
        "或 CentOS/RHEL: sudo yum install python3.9"
    ),
}


def _install_guide(plat: str) -> str:
    """返回平台特定的安装指引"""
    return _INSTALL_GUIDES.get(plat, _INSTALL_GUIDES["Linux"])


# ---------------------------------------------------------------------------
# Main discovery entry point
# ---------------------------------------------------------------------------

def find_python() -> str:
    """
    查找可用的 Python 3.7+ 解释器路径。

    成功返回路径字符串。
    失败抛出 EnvironmentError（附带安装指引）。
    """
    plat = _current_platform()

    # Priority 1: 缓存路径
    cached_path = _read_cache()
    if cached_path:
        validated = _try_candidate(cached_path)
        if validated:
            _write_cache(validated[1])
            return validated[1]

    # Priority 2: 当前运行环境（如果不是 shim）
    current_exe = sys.executable
    if current_exe and not is_shim(current_exe):
        validated = validate_python(current_exe)
        if validated:
            _write_cache(validated[1])
            return validated[1]

    # Priority 3: 平台特定发现
    discover_fn = {
        "Windows": _discover_windows,
        "macOS": _discover_macos,
        "Linux": _discover_linux,
    }.get(plat)

    if discover_fn:
        result = discover_fn()
        if result:
            version_str, real_path = result
            _write_cache(real_path)
            return real_path

    # Priority 4: 未找到 - 抛出异常附带指引
    raise EnvironmentError(_install_guide(plat))


def find_python_with_info() -> dict:
    """
    查找可用的 Python 3.7+ 解释器并返回详细信息字典。

    Returns:
        成功: {"status": "ok", "python_path": str, "version": str, "platform": str, "cached": bool}
        失败: {"status": "error", "message": str, "platform": str}
    """
    plat = _current_platform()

    # Priority 1: 缓存路径
    cached_path = _read_cache()
    if cached_path:
        validated = _try_candidate(cached_path)
        if validated:
            version_str, real_path = validated
            _write_cache(real_path)
            return {
                "status": "ok",
                "python_path": real_path,
                "version": version_str,
                "platform": plat,
                "cached": True,
            }

    # Priority 2: 当前运行环境
    current_exe = sys.executable
    if current_exe and not is_shim(current_exe):
        validated = validate_python(current_exe)
        if validated:
            version_str, real_path = validated
            _write_cache(real_path)
            return {
                "status": "ok",
                "python_path": real_path,
                "version": version_str,
                "platform": plat,
                "cached": False,
            }

    # Priority 3: 平台特定发现
    discover_fn = {
        "Windows": _discover_windows,
        "macOS": _discover_macos,
        "Linux": _discover_linux,
    }.get(plat)

    if discover_fn:
        result = discover_fn()
        if result:
            version_str, real_path = result
            _write_cache(real_path)
            return {
                "status": "ok",
                "python_path": real_path,
                "version": version_str,
                "platform": plat,
                "cached": False,
            }

    # Priority 4: 未找到
    return {
        "status": "error",
        "message": _install_guide(plat),
        "platform": plat,
    }


# ---------------------------------------------------------------------------
# Dependency check and auto-install
# ---------------------------------------------------------------------------

def ensure_dependencies() -> dict:
    """检测并自动安装所需依赖库

    Returns:
        {
            "all_ok": bool,
            "packages": [{"name": str, "installed": bool, "auto_installed": bool, "error": str|None}]
        }
    """
    packages_status = []
    all_ok = True

    for import_name, pip_name in REQUIRED_PACKAGES:
        pkg_info = {"name": pip_name, "installed": False, "auto_installed": False, "error": None}
        try:
            __import__(import_name)
            pkg_info["installed"] = True
        except ImportError:
            # 尝试自动安装
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pip_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    # 验证安装是否成功
                    try:
                        __import__(import_name)
                        pkg_info["installed"] = True
                        pkg_info["auto_installed"] = True
                    except ImportError:
                        pkg_info["error"] = f"{pip_name} 安装后仍无法导入"
                        all_ok = False
                else:
                    pkg_info["error"] = f"{pip_name} 安装失败: {result.stderr[:200]}"
                    all_ok = False
            except (OSError, subprocess.TimeoutExpired) as e:
                pkg_info["error"] = f"{pip_name} 安装超时或出错: {e}"
                all_ok = False

        packages_status.append(pkg_info)

    return {"all_ok": all_ok, "packages": packages_status}


def ensure_optional_dependencies() -> dict:
    """检测并尝试安装可选依赖库（pypdf、lxml 等）

    可选依赖安装失败不会导致退出，仅标记为不可用。

    Returns:
        {
            "packages": [{"name": str, "installed": bool, "auto_installed": bool, "error": str|None}]
        }
    """
    packages_status = []

    for import_name, pip_name in OPTIONAL_PACKAGES:
        pkg_info = {"name": pip_name, "installed": False, "auto_installed": False, "error": None}
        try:
            __import__(import_name)
            pkg_info["installed"] = True
        except ImportError:
            # 尝试自动安装（失败不退出）
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pip_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    try:
                        __import__(import_name)
                        pkg_info["installed"] = True
                        pkg_info["auto_installed"] = True
                    except ImportError:
                        pkg_info["error"] = f"{pip_name} 安装后仍无法导入"
                else:
                    pkg_info["error"] = f"{pip_name} 安装失败"
            except (OSError, subprocess.TimeoutExpired) as e:
                pkg_info["error"] = f"{pip_name} 安装超时或出错"

        packages_status.append(pkg_info)

    return {"packages": packages_status}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI 入口 - 输出检测结果 JSON"""
    info = find_python_with_info()

    # 检测并自动安装依赖
    deps = ensure_dependencies()
    info["dependencies"] = deps["packages"]
    info["all_dependencies_ok"] = deps["all_ok"]

    # 检测可选依赖
    opt_deps = ensure_optional_dependencies()
    info["optional_dependencies"] = opt_deps["packages"]

    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
