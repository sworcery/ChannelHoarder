@echo off
REM ============================================================
REM  Install ChannelHoarder Cookie Exporter as a Scheduled Task
REM  Runs every 30 minutes to keep cookies fresh
REM ============================================================

setlocal

set SCRIPT_DIR=%~dp0
set PYTHON_EXE=python
set TASK_NAME=ChannelHoarder Cookie Exporter

echo.
echo ChannelHoarder Cookie Exporter - Task Scheduler Setup
echo ======================================================
echo.

REM Check for admin privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: This script requires Administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

REM Verify Python is available
%PYTHON_EXE% --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Install Python 3.10+ and ensure it's in PATH.
    pause
    exit /b 1
)

REM Verify config exists
if not exist "%SCRIPT_DIR%cookie_exporter.ini" (
    echo ERROR: cookie_exporter.ini not found in %SCRIPT_DIR%
    echo Edit cookie_exporter.ini with your settings before running this script.
    pause
    exit /b 1
)

REM Delete existing task if present
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo Removing existing scheduled task...
    schtasks /delete /tn "%TASK_NAME%" /f
)

REM Create the scheduled task (runs every 30 minutes)
REM The script opens Firefox to YouTube, waits for cookies to refresh,
REM closes Firefox, reads the cookie DB, and pushes to ChannelHoarder.
echo Creating scheduled task "%TASK_NAME%"...
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON_EXE%\" \"%SCRIPT_DIR%cookie_exporter.py\" --config \"%SCRIPT_DIR%cookie_exporter.ini\"" ^
    /sc minute ^
    /mo 30 ^
    /st 00:00 ^
    /ru "%USERNAME%" ^
    /rl HIGHEST ^
    /f

if %errorlevel% equ 0 (
    echo.
    echo Task created successfully!
    echo Schedule: Every 30 minutes
    echo.
    echo To test it now, run:
    echo   schtasks /run /tn "%TASK_NAME%"
    echo.
    echo To change the interval, edit via Task Scheduler or re-run this script.
) else (
    echo.
    echo ERROR: Failed to create scheduled task.
)

pause
