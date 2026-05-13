@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title YTS_bot - Run

echo ============================================
echo   YTS_bot - Pre-flight checks
echo ============================================
echo.

:: --- Check Node.js ---
echo [1/6] Node.js...
where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    [X] Node.js not found! Run install_omniroute.bat first.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('node --version') do echo    [OK] %%i

:: --- Check Python ---
echo.
echo [2/6] Python...
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    [X] Python not found! Install Python 3.11-3.13.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo    [OK] %%i

:: --- Check ffmpeg ---
echo.
echo [3/6] ffmpeg...
where ffmpeg >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    [X] ffmpeg not found in PATH!
    echo       Download: https://ffmpeg.org/download.html
    echo       Add bin folder to system PATH.
    pause
    exit /b 1
)
echo    [OK] ffmpeg found

:: --- Check .env ---
echo.
echo [4/6] .env file...
if not exist ".env" (
    echo    [X] .env not found!
    echo       Run: copy .env.example .env
    echo       Then fill in your API keys.
    pause
    exit /b 1
)
echo    [OK] .env found

:: --- Check venv ---
echo.
echo [5/6] Python venv...
if not exist "venv\Scripts\activate.bat" (
    echo    [!] venv not found, creating...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
    echo    [OK] venv activated
)

:: --- Start OmniRoute ---
echo.
echo [6/6] Starting OmniRoute (background)...
start "OmniRoute" /min cmd /c "npx omniroute"
timeout /t 3 >nul
echo    [OK] OmniRoute started (minimized window)
echo       Dashboard: http://localhost:3000

:: --- Start bot ---
echo.
echo ============================================
echo   Starting YTS_bot...
echo ============================================
echo.
echo   OmniRoute: http://localhost:3000
echo   Press Ctrl+C to stop the bot
echo.
echo ---

python main.py

:: --- After exit ---
echo.
echo   Bot stopped.
echo   Close the OmniRoute window manually if needed.
echo.
pause
