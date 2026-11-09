@echo off
title Voice Translator - Setup Check
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.10+ from python.org
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

python diagnose.py
