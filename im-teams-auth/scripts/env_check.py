#!/usr/bin/env python3
"""
360Teams Skill 跨平台 Python 环境检测脚本。

主要能力：
  - 检测可用的 Python 3.9+ 解释器
  - 绕过可能截断中文参数的 VM Shim
  - 在 skill 目录缓存检测结果 7 天
  - 检查并按需自动安装 keyring
  - Python 不可用时输出安装指引

作为模块使用：
    from env_check import find_python
    python_path = find_python()

作为命令行使用：
    python env_check.py
    # 输出包含 status、python_path、version、platform、cached、keyring_available 的 JSON
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

MIN_PYTHON_VERSION: Tuple[int, int] = (3, 9)

# Cache file path (skill-local, non-credential)
from config import CACHE_DIR
from runtime import ensure_keyring
CACHE_TXT: Path = CACHE_DIR / "python_path.txt"
CACHE_MAX_AGE_SECONDS: int = 7 * 24 * 3600

# Shim path indicators - any path containing these is considered a VM shim
SHIM_INDICATORS: Tuple[str, ...] = (
    "vm/tools/",
    "vm\\tools\\",
    ".trae-cn/modulardata/ai-agent/vm/",
)


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _current_platform() -> str:
    """Return a normalised platform string: Windows, macOS, or Linux."""
    system = platform.system()
    if system == "Darwin":
        return "macOS"
    if system == "Windows":
        return "Windows"
    return "Linux"


def _subprocess_args() -> dict:
    """Return common subprocess keyword arguments (e.g. CREATE_NO_WINDOW on Windows)."""
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
    """Return True if path looks like a VM shim that truncates Chinese arguments."""
    normalised = str(path)
    for indicator in SHIM_INDICATORS:
        if indicator in normalised:
            return True
    return False


# ---------------------------------------------------------------------------
# Version validation
# ---------------------------------------------------------------------------

def _parse_version(version_output: str) -> Optional[Tuple[int, int]]:
    """Parse a Python 3.x.y string into a (major, minor) tuple."""
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
    Validate that path points to a usable Python 3.9+ interpreter.

    Returns a tuple of (version_string, real_executable_path) on success,
    or None if the path is invalid or the version is too old.
    """
    try:
        # Step 1: check version
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

        # Step 2: resolve real executable path via sys.executable
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
    """Read a cached Python path if it exists and is not expired."""
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
    """Persist the detected Python path to the cache file."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_TXT.write_text(python_path, encoding="utf-8")
    except OSError:
        pass  # Cache write failure is non-fatal


# ---------------------------------------------------------------------------
# Discovery strategies
# ---------------------------------------------------------------------------

def _try_candidate(path: str) -> Optional[Tuple[str, str]]:
    """Validate a candidate path and return (version, real_path) or None."""
    if is_shim(path):
        return None
    return validate_python(path)


def _discover_windows() -> Optional[Tuple[str, str]]:
    """Windows-specific Python discovery."""
    # Strategy 1: py launcher - py -0p lists installed versions with paths
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
                # Output format: " -V:3.13 *    C:\Users\...\python.exe"
                parts = line.split()
                if parts:
                    candidate = parts[-1]
                    validated = _try_candidate(candidate)
                    if validated:
                        return validated
    except (OSError, FileNotFoundError):
        pass

    # Strategy 2: Common installation paths
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

    # Strategy 3: where python3 then where python (exclude shims)
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
    """macOS-specific Python discovery."""
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

    # Strategy 2: Homebrew paths
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
    """Linux-specific Python discovery."""
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
        "请安装 Python 3.9+：https://www.python.org/downloads/ "
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
    """Return platform-specific installation guidance."""
    return _INSTALL_GUIDES.get(plat, _INSTALL_GUIDES["Linux"])


# ---------------------------------------------------------------------------
# Main discovery entry point
# ---------------------------------------------------------------------------

def find_python() -> str:
    """
    Find a usable Python 3.9+ interpreter path.

    Returns the path as a string on success.
    Raises EnvironmentError with installation guidance on failure.
    """
    plat = _current_platform()

    # Priority 1: cached path
    cached_path = _read_cache()
    if cached_path:
        validated = _try_candidate(cached_path)
        if validated:
            _write_cache(validated[1])  # refresh cache with real path
            return validated[1]

    # Priority 2: current running environment (if not a shim)
    current_exe = sys.executable
    if current_exe and not is_shim(current_exe):
        validated = validate_python(current_exe)
        if validated:
            _write_cache(validated[1])
            return validated[1]

    # Priority 3: platform-specific discovery
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

    # Priority 4: not found - raise with guidance
    raise EnvironmentError(_install_guide(plat))


def find_python_with_info() -> dict:
    """
    Find a usable Python 3.9+ interpreter and return detailed info dict.

    Returns:
        {
            "status": "ok",
            "python_path": str,
            "version": str,
            "platform": str,
            "cached": bool,
        }

    On failure:
        {
            "status": "error",
            "message": str,
            "platform": str,
        }
    """
    plat = _current_platform()

    # Priority 1: cached path
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

    # Priority 2: current running environment
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

    # Priority 3: platform-specific discovery
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

    # Priority 4: not found
    return {
        "status": "error",
        "message": _install_guide(plat),
        "platform": plat,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point - output detection result as JSON."""
    info = find_python_with_info()

    # Check and auto-install keyring
    kr = ensure_keyring()
    info["keyring_available"] = kr["available"]
    if kr.get("auto_installed"):
        info["keyring_auto_installed"] = True
    if not kr["available"]:
        info["keyring_message"] = kr["message"]

    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
