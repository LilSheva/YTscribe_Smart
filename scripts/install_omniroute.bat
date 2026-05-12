@echo off
chcp 65001 >nul
title YTS_bot — Установка OmniRoute

echo ============================================
echo   YTS_bot — Установка OmniRoute (AI Gateway)
echo ============================================
echo.

:: --- Проверка Node.js ---
echo [1/4] Проверка Node.js...
where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo    X Node.js НЕ НАЙДЕН!
    echo.
    echo    Установите Node.js 18+ с официального сайта:
    echo      https://nodejs.org/
    echo.
    echo    После установки перезапустите этот скрипт.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('node --version') do set NODE_VER=%%i
echo    [OK] Node.js найден: %NODE_VER%

:: --- Проверка npm ---
echo.
echo [2/4] Проверка npm...
where npm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    X npm не найден! Переустановите Node.js.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('npm --version') do set NPM_VER=%%i
echo    [OK] npm найден: v%NPM_VER%

:: --- Проверка npx ---
echo.
echo [3/4] Проверка npx...
where npx >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    [!] npx не найден, устанавливаю...
    npm install -g npx
)
echo    [OK] npx доступен

:: --- Установка OmniRoute ---
echo.
echo [4/4] Установка OmniRoute...
echo    Выполняю: npm install -g omniroute
echo.
npm install -g omniroute

if %ERRORLEVEL% neq 0 (
    echo.
    echo    X Ошибка при установке OmniRoute!
    echo    Попробуйте запустить этот скрипт от имени Администратора.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   OmniRoute успешно установлен!
echo ============================================
echo.
echo Теперь можете запустить: run_omniroute.bat
echo Или вручную: npx omniroute
echo.
echo После запуска:
echo   1. Откроется Dashboard в браузере
echo   2. Подключите провайдеры (Groq, OpenRouter и др.)
echo   3. Скопируйте API ключ в .env файл бота
echo.
pause
