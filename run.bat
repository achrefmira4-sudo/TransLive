@echo off
title Voice Translator
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on this PC.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

if not exist ".venv\" (
    echo Creating a private Python environment, please wait...
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create the environment.
        pause
        exit /b 1
    )
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    echo Installing dependencies, this can take a few minutes...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check your internet connection.
        pause
        exit /b 1
    )
) else (
    call ".venv\Scripts\activate.bat"
)

python main.py
if errorlevel 1 pause
