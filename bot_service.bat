@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if /i "%~1"=="start" (
    powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\bot_service_menu.ps1" start
    exit /b %errorlevel%
)
if /i "%~1"=="stop" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bot_service_menu.ps1" stop
    exit /b %errorlevel%
)
if /i "%~1"=="restart" (
    powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\bot_service_menu.ps1" restart
    exit /b %errorlevel%
)
if /i "%~1"=="status" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bot_service_menu.ps1" status
    pause
    exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bot_service_menu.ps1" %*
if errorlevel 1 pause
