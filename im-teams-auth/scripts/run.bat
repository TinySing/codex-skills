@echo off
rem Windows 包装脚本：调用环境检测或认证脚本，并透传后续参数。
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PYTHON_CMD="

where py >nul 2>nul
if !errorlevel!==0 (
  set "PYTHON_CMD=py -3"
) else (
  where python >nul 2>nul
  if !errorlevel!==0 (
    set "PYTHON_CMD=python"
  )
)

if "!PYTHON_CMD!"=="" (
  echo Python not found. Install Python 3 or use py -3.
  exit /b 1
)

if "%~1"=="" (
  echo Usage: run.bat ^<env_check^|auth^> [args...]
  exit /b 1
)

set "SCRIPT_NAME=%~1"
shift

rem %* 不受 shift 影响，手动收集 shift 后的剩余参数（保留原始引号）
set "ARGS="
:collect
if "%~1"=="" goto run
set "ARGS=!ARGS! %1"
shift
goto collect

:run
if "%SCRIPT_NAME%"=="env_check" (
  %PYTHON_CMD% "%SCRIPT_DIR%env_check.py"!ARGS!
) else if "%SCRIPT_NAME%"=="auth" (
  %PYTHON_CMD% "%SCRIPT_DIR%auth.py"!ARGS!
) else (
  echo Unknown script: %SCRIPT_NAME%
  echo Available: env_check, auth
  exit /b 1
)

exit /b !errorlevel!
