@echo off
chcp 65001 >nul
title YTS_bot — Запуск OmniRoute + Бот

echo ============================================
echo   YTS_bot — Проверка и запуск
echo ============================================
echo.

:: --- Проверка Node.js ---
echo [1/6] Node.js...
where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    X Node.js не найден! Запустите install_omniroute.bat
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('node --version') do echo    [OK] %%i

:: --- Проверка Python ---
echo.
echo [2/6] Python...
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    X Python не найден! Установите Python 3.11+
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo    [OK] %%i

:: --- Проверка ffmpeg ---
echo.
echo [3/6] ffmpeg...
where ffmpeg >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo    X ffmpeg не найден в PATH!
    echo       Скачайте: https://ffmpeg.org/download.html
    echo       Добавьте в PATH системы.
    pause
    exit /b 1
)
echo    [OK] ffmpeg найден

:: --- Проверка .env ---
echo.
echo [4/6] Файл .env...
if not exist ".env" (
    echo    X .env не найден!
    echo       Скопируйте: copy .env.example .env
    echo       Заполните API-ключи.
    pause
    exit /b 1
)
echo    [OK] .env найден

:: --- Проверка venv и зависимостей ---
echo.
echo [5/6] Python venv...
if not exist "venv\Scripts\activate.bat" (
    echo    [!] venv не найден, создаю...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
    echo    [OK] venv активирован
)

:: --- Запуск OmniRoute в фоне ---
echo.
echo [6/6] Запуск OmniRoute (фоновый процесс)...
start "OmniRoute" /min cmd /c "npx omniroute"
timeout /t 3 >nul
echo    [OK] OmniRoute запущен (окно свёрнуто)
echo       Dashboard: http://localhost:3000

:: --- Запуск бота ---
echo.
echo ============================================
echo   Запуск YTS_bot...
echo ============================================
echo.
echo   OmniRoute: http://localhost:3000 (фоновое окно)
echo   Для остановки нажмите Ctrl+C
echo.
echo ---

python main.py

:: --- После завершения ---
echo.
echo   Бот остановлен.
echo    OmniRoute всё ещё работает в фоне.
echo    Закройте окно "OmniRoute" вручную, если нужно.
echo.
pause
