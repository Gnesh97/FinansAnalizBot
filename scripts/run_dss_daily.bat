@echo off
REM Nightly DSS chain for Windows Task Scheduler (unattended -- no pause).
REM
REM Chain: Tiingo --refresh-daily -> strict validation -> signal generation
REM        -> DSS report -> Telegram send. Reports only; never places orders.
REM
REM Hardened execution: checks .env for PYTHON_PATH, bare python, and common
REM Windows Python locations. Logs errors to reports\dss\scheduler.log if missing.

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
set "PROJECT_ROOT=%CD%"
popd

if not exist "%PROJECT_ROOT%\reports\dss" mkdir "%PROJECT_ROOT%\reports\dss"
set "LOG_FILE=%PROJECT_ROOT%\reports\dss\scheduler.log"

set "PYTHON_EXE="

REM 1. Read PYTHON_PATH or PYTHON_EXECUTABLE from .env if present
if exist "%PROJECT_ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
        if /i "%%A"=="PYTHON_PATH" set "PYTHON_EXE=%%B"
        if /i "%%A"=="PYTHON_EXECUTABLE" set "PYTHON_EXE=%%B"
    )
)

if defined PYTHON_EXE (
    set "PYTHON_EXE=!PYTHON_EXE:"=!"
)

REM 2. Check if configured PYTHON_EXE exists and works
if defined PYTHON_EXE (
    if exist "!PYTHON_EXE!" (
        "!PYTHON_EXE!" --version >nul 2>&1
        if !errorlevel! equ 0 (
            set "RUN_PYTHON=!PYTHON_EXE!"
        )
    )
)

REM 3. Check bare python command on PATH
if not defined RUN_PYTHON (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        python --version >nul 2>&1
        if !errorlevel! equ 0 (
            set "RUN_PYTHON=python"
        )
    )
)

REM 4. Fall back to standard Windows Python paths
if not defined RUN_PYTHON (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
    ) do (
        if not defined RUN_PYTHON (
            if exist "%%~P" (
                "%%~P" --version >nul 2>&1
                if !errorlevel! equ 0 (
                    set "RUN_PYTHON=%%~P"
                )
            )
        )
    )
)

REM 5. Fail-loud locally if no Python interpreter found
if not defined RUN_PYTHON (
    echo [%date% %time%] [dss-daily ERROR 9009] Python interpreter not found or unable to execute. >> "%LOG_FILE%"
    echo [%date% %time%] [dss-daily ERROR 9009] Please set PYTHON_PATH=C:\path\to\python.exe in .env >> "%LOG_FILE%"
    echo [dss-daily ERROR 9009] Python interpreter not found. Set PYTHON_PATH in .env
    exit /b 9009
)

echo [%date% %time%] [dss-daily] using python: "!RUN_PYTHON!" >> "%LOG_FILE%"
echo [%date% %time%] [dss-daily] starting scheduler chain %* >> "%LOG_FILE%"

"!RUN_PYTHON!" "%PROJECT_ROOT%\testdata\dss_scheduler.py" %* >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo [%date% %time%] [dss-daily] completed with exit code %EXIT_CODE% >> "%LOG_FILE%"
exit /b %EXIT_CODE%
