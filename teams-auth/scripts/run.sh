#!/usr/bin/env bash
# === 360Teams Auth - macOS/Linux Launcher ===
# Usage: run.sh <script> [args...]
#   e.g. run.sh env_check
#        run.sh auth
#        run.sh auth --no-cache

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CACHE_DIR="$ROOT_DIR/cache"
LOG_DIR="$ROOT_DIR/log"
CACHE_TXT="$CACHE_DIR/python_path.txt"

# ---------------------------------------------------------------------------
# Python discovery (cache -> sys.executable -> which -> common paths)
# ---------------------------------------------------------------------------

PYTHON_EXE=""

# 1a. Try plain-text cache (written by env_check.py)
if [ -f "$CACHE_TXT" ]; then
    CACHED=$(cat "$CACHE_TXT" 2>/dev/null || true)
    if [ -n "$CACHED" ] && [ -x "$CACHED" ]; then
        PYTHON_EXE="$CACHED"
        if ! "$PYTHON_EXE" --version >/dev/null 2>&1; then
            PYTHON_EXE=""
        fi
    fi
fi

# 1b. Try current Python
if [ -z "$PYTHON_EXE" ]; then
    CURRENT=$(python3 -c "import sys; print(sys.executable)" 2>/dev/null || true)
    if [ -n "$CURRENT" ] && [ -x "$CURRENT" ]; then
        # Skip VM shims
        case "$CURRENT" in
            *vm/tools/*|*.trae-cn/*|*ai-agent/vm/*) ;;
            *) PYTHON_EXE="$CURRENT" ;;
        esac
    fi
fi

# 1c. Try which python3
if [ -z "$PYTHON_EXE" ]; then
    CANDIDATE=$(which python3 2>/dev/null || true)
    if [ -n "$CANDIDATE" ] && [ -x "$CANDIDATE" ]; then
        case "$CANDIDATE" in
            *vm/tools/*|*.trae-cn/*|*ai-agent/vm/*) ;;
            *) PYTHON_EXE="$CANDIDATE" ;;
        esac
    fi
fi

# 1d. Try common paths
if [ -z "$PYTHON_EXE" ]; then
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        if [ -x "$candidate" ]; then
            PYTHON_EXE="$candidate"
            break
        fi
    done
fi

# ---------------------------------------------------------------------------
# No Python found - output guidance and exit
# ---------------------------------------------------------------------------
if [ -z "$PYTHON_EXE" ]; then
    PLATFORM=$(uname -s)
    if [ "$PLATFORM" = "Darwin" ]; then
        MSG="未找到可用的 Python 3.9+ 环境。通过 Homebrew 安装：brew install python@3.9，或下载官方安装包：https://www.python.org/downloads/"
    else
        MSG="未找到可用的 Python 3.9+ 环境。Ubuntu/Debian：sudo apt install python3.9；CentOS/RHEL：sudo yum install python3.9"
    fi
    echo "{\"status\": \"error\", \"message\": \"$MSG\", \"platform\": \"$PLATFORM\"}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Verify Python version >= 3.9
# ---------------------------------------------------------------------------
PY_MAJOR=$("$PYTHON_EXE" -c "import sys; print(sys.version_info[0])" 2>/dev/null || echo "0")
PY_MINOR=$("$PYTHON_EXE" -c "import sys; print(sys.version_info[1])" 2>/dev/null || echo "0")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    echo "{\"status\": \"error\", \"message\": \"Python $PY_MAJOR.$PY_MINOR 低于 3.9 要求，请安装 Python 3.9+\", \"python_path\": \"$PYTHON_EXE\"}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Ensure required directories exist
# ---------------------------------------------------------------------------
mkdir -p "$CACHE_DIR" "$LOG_DIR"

# ---------------------------------------------------------------------------
# Execute the requested script
# ---------------------------------------------------------------------------
export PYTHONPATH="$SCRIPT_DIR"

if [ $# -eq 0 ]; then
    echo "Usage: run.sh <script> [args...]"
    echo "  scripts: env_check, auth"
    exit 1
fi

SCRIPT_NAME="$1"
shift

case "$SCRIPT_NAME" in
    env_check) exec "$PYTHON_EXE" "$SCRIPT_DIR/env_check.py" "$@" ;;
    auth)      exec "$PYTHON_EXE" "$SCRIPT_DIR/auth.py" "$@" ;;
    *)
        echo "Unknown script: $SCRIPT_NAME"
        echo "Available: env_check, auth"
        exit 1
        ;;
esac