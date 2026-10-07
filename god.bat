@echo off
title Godsdownload Telegram Bot
color 0b

echo ==============================================================================
echo                      ⚡ GODSDOWNLOAD TELEGRAM BOT ⚡
echo ==============================================================================
echo.

cd /d "%~dp0"

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your system PATH!
    echo Please install Python 3.10+ from https://www.python.org/
    echo.
    pause
    exit /b 1
)

:: Check if .env exists
if not exist ".env" (
    echo [INFO] .env file not found. Creating from .env.example...
    copy ".env.example" ".env" >nul
    echo [IMPORTANT] Please open .env and set your BOT_TOKEN from @BotFather!
    echo.
    pause
    exit /b 1
)

:: Set up virtual environment if missing
if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Creating virtual environment (.venv)...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    echo [1/2] Activating virtual environment (.venv)...
    call .venv\Scripts\activate.bat
)

echo.
echo [2/2] Starting Godsdownload Bot...
echo ==============================================================================
echo.

python main.py

echo.
echo ==============================================================================
echo Bot process ended.
pause
