@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo               Cafe POS Production Server
echo ========================================================

cd /d "%~dp0"

REM 1. Activate virtual environment
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
) else (
    echo [ERROR] Virtual environment not found (.venv or venv).
    echo Please create one using: python -m venv .venv
    pause
    exit /b 1
)

echo.
echo [1/3] Applying database migrations...
python manage.py migrate
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Database migration failed.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/3] Collecting static assets...
python manage.py collectstatic --noinput
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Static collection failed.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [3/3] Starting Waitress WSGI production server on 0.0.0.0:8000...
echo --------------------------------------------------------
echo Local address:   http://127.0.0.1:8000
echo Network address: http://0.0.0.0:8000
echo Press Ctrl+C to terminate the server.
echo --------------------------------------------------------
echo.

waitress-serve --listen=0.0.0.0:8000 cafe_pos.wsgi:application

endlocal

