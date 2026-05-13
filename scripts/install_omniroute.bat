@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title YTS_bot - Install OmniRoute

echo ============================================
echo   YTS_bot - OmniRoute Installation
echo ============================================
echo.

:: --- Check Node.js ---
echo [1/4] Checking Node.js...
where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo    [X] Node.js NOT FOUND!
    echo.
    echo    Install Node.js 18+ from:
    echo      https://nodejs.org/
    echo.
    echo    Then re-run this script.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('node --version') do set NODE_VER=%%i
echo    [OK] Node.js: %NODE_VER%

:: --- Check npm ---
echo.
echo [2/4] Checking npm...
where npm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    [X] npm not found! Reinstall Node.js.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('npm --version') do set NPM_VER=%%i
echo    [OK] npm: v%NPM_VER%

:: --- Check npx ---
echo.
echo [3/4] Checking npx...
where npx >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    [!] npx not found, installing...
    npm install -g npx
)
echo    [OK] npx available

:: --- Install OmniRoute ---
echo.
echo [4/4] Installing OmniRoute...
echo    Running: npm install -g omniroute
echo.
npm install -g omniroute

if %ERRORLEVEL% neq 0 (
    echo.
    echo    [X] Error installing OmniRoute!
    echo    Try running this script as Administrator.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   OmniRoute installed successfully!
echo ============================================
echo.
echo Next steps:
echo   1. Run: scripts\run_omniroute.bat
echo   2. Or manually: npx omniroute
echo   3. Connect providers in the dashboard
echo   4. Copy API key to .env
echo.
pause
