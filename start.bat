@echo off
setlocal

cd /d "%~dp0"

if not defined HOST set "HOST=127.0.0.1"
if not defined PORT set "PORT=8000"
set "APP_URL=http://%HOST%:%PORT%/"

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv was not found in PATH.
    echo Install uv first, then run this script again:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    pause
    exit /b 1
)

if not exist "pyproject.toml" (
    echo [ERROR] pyproject.toml was not found.
    echo Please keep this script in the project root directory.
    pause
    exit /b 1
)

if not exist "manage.py" (
    echo [ERROR] manage.py was not found.
    echo This script is intended for the Django project root directory.
    pause
    exit /b 1
)

echo [1/4] Syncing project dependencies with uv...
uv sync
if errorlevel 1 goto startup_failed

echo [2/4] Applying database migrations...
uv run python manage.py migrate
if errorlevel 1 goto startup_failed

echo [3/4] Opening browser at %APP_URL%...
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%APP_URL%'" >nul 2>nul

echo [4/4] Starting Django development server...
echo.
echo URL: %APP_URL%
echo Press Ctrl+C to stop the server.
echo.
uv run python manage.py runserver %HOST%:%PORT%
if errorlevel 1 goto startup_failed

echo.
echo Server stopped.
pause
exit /b 0

:startup_failed
echo.
echo [ERROR] Startup failed. Check the output above for details.
pause
exit /b 1
