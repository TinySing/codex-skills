#!/usr/bin/env bash
# macOS/Linux 包装脚本：调用环境检测或认证脚本，并透传后续参数。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -eq 0 ]; then
  echo "Usage: run.sh <env_check|auth> [args...]"
  exit 1
fi

SCRIPT_NAME="$1"
shift

case "$SCRIPT_NAME" in
  env_check) exec python3 "$SCRIPT_DIR/env_check.py" "$@" ;;
  auth)      exec python3 "$SCRIPT_DIR/auth.py" "$@" ;;
  *)
    echo "Unknown script: $SCRIPT_NAME"
    echo "Available: env_check, auth"
    exit 1
    ;;
esac
