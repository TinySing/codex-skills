@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

REM === 360Teams Auth - Windows Launcher ===
REM Usage: run.bat <script> [args...]
REM   e.g. run.bat env_check
REM        run.bat auth
REM        run.bat auth --no-cache

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "CACHE_DIR=%ROOT_DIR%\cache"
set "LOG_DIR=%ROOT_DIR%\log"
set "CACHE_TXT=%CACHE_DIR%\python_path.txt"

REM ---------------------------------------------------------------------------
REM Python discovery (cache -> py launcher -> where -> common paths)
REM ---------------------------------------------------------------------------

set "PYTHON_EXE="

REM 1a. Try plain-text cache (written by env_check.py, no PowerShell needed)
if exist "%CACHE_TXT%" (
    set /p "PYTHON_EXE=" <"%CACHE_TXT%"
    if defined PYTHON_EXE (
        "!PYTHON_EXE!" --version >nul 2>&1
        if !errorlevel! equ 0 goto :found
    )
    set "PYTHON_EXE="
)

REM 1b. Try py launcher (py -0p lists installed Python versions with paths)
for /f "usebackq tokens=* delims=" %%a in (`py -0p 2^>nul`) do (
    set "PY_LINE=%%a"
    for /f "tokens=1,* delims= " %%x in ("!PY_LINE!") do (
        set "PYTHON_EXE=%%y"
    )
    if defined PYTHON_EXE (
        "!PYTHON_EXE!" --version >nul 2>&1
        if !errorlevel! equ 0 goto :found
    )
    set "PYTHON_EXE="
)

REM 1c. Try where python
for /f "usebackq tokens=* delims=" %%a in (`where python 2^>nul`) do (
    set "PYTHON_EXE=%%~a"
    REM Skip VM shims
    echo "!PYTHON_EXE!" | findstr /i "vm\\tools vm/tools .trae-cn ai-agent\\vm" >nul 2>&1
    if !errorlevel! neq 0 (
        "!PYTHON_EXE!" --version >nul 2>&1
        if !errorlevel! equ 0 goto :found
    )
    set "PYTHON_EXE="
)

REM 1d. Try common installation paths
for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
) do (
    if exist %%d (
        set "PYTHON_EXE=%%~d"
        "!PYTHON_EXE!" --version >nul 2>&1
        if !errorlevel! equ 0 goto :found
        set "PYTHON_EXE="
    )
)

REM ---------------------------------------------------------------------------
REM No Python found - output guidance and exit
REM ---------------------------------------------------------------------------
echo {"status": "error", "message": "未找到可用的 Python 3.9+ 环境。请安装 Python 3.9+：https://www.python.org/downloads/ 安装时务必勾选 Add Python to PATH 和 Install py launcher。", "platform": "Windows"}
exit /b 1

:found
REM ---------------------------------------------------------------------------
REM Verify Python version >= 3.9
REM ---------------------------------------------------------------------------
set "PY_MAJOR=0"
set "PY_MINOR=0"
"!PYTHON_EXE!" -c "import sys;print(sys.version_info[0],sys.version_info[1])" >"%TEMP%\pyver.txt" 2>nul
for /f "usebackq tokens=1,2" %%a in ("%TEMP%\pyver.txt") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)
del "%TEMP%\pyver.txt" 2>nul
if !PY_MAJOR! lss 3 (
    echo {"status": "error", "message": "Python !PY_MAJOR!.!PY_MINOR! 低于 3.9 要求，请安装 Python 3.9+", "python_path": "!PYTHON_EXE!"}
    exit /b 1
)
if !PY_MAJOR! equ 3 if !PY_MINOR! lss 9 (
    echo {"status": "error", "message": "Python !PY_MAJOR!.!PY_MINOR! 低于 3.9 要求，请安装 Python 3.9+", "python_path": "!PYTHON_EXE!"}
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM Ensure required directories exist
REM ---------------------------------------------------------------------------
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ---------------------------------------------------------------------------
REM Execute the requested script
REM ---------------------------------------------------------------------------
set "PYTHONPATH=%SCRIPT_DIR%"

if "%~1"=="" (
    echo Usage: run.bat ^<script^> [args...]
    echo   scripts: env_check, auth
    exit /b 1
)

set "SCRIPT_NAME=%~1"
shift

REM Collect remaining arguments (no GUID re-joining needed for auth)
set "SCRIPT_ARGS="
:collect_args
if "%~1"=="" goto :done_args
set "SCRIPT_ARGS=%SCRIPT_ARGS% %1"
shift
goto :collect_args
:done_args

if "%SCRIPT_NAME%"=="env_check" (
    "%PYTHON_EXE%" "%SCRIPT_DIR%env_check.py" %SCRIPT_ARGS%
) else if "%SCRIPT_NAME%"=="auth" (
    "%PYTHON_EXE%" "%SCRIPT_DIR%auth.py" %SCRIPT_ARGS%
) else (
    echo Unknown script: %SCRIPT_NAME%
    echo Available: env_check, auth
    exit /b 1
)

exit /b %errorlevel%
