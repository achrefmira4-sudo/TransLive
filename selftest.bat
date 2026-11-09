@echo off
setlocal enabledelayedexpansion
title Voice Translator - Self Test
cd /d "%~dp0"

REM Works in three places:
REM   * next to a built VoiceTranslator.exe (the normal case)
REM   * the source folder, with a dist\VoiceTranslator build present
REM   * the source folder, using .venv or system Python

set "EXE="
if exist "VoiceTranslator.exe" set "EXE=VoiceTranslator.exe"
if not defined EXE if exist "dist\VoiceTranslator\VoiceTranslator.exe" set "EXE=dist\VoiceTranslator\VoiceTranslator.exe"

set "LOG=%APPDATA%\VoiceTranslator\selftest.log"

if defined EXE (
    echo Testing the packaged app: %EXE%
    echo.

    REM Delete any previous log so we never show stale results.
    if exist "%LOG%" del /q "%LOG%" >nul 2>nul

    REM "start /wait" is required: CMD does NOT wait for a windowed GUI exe,
    REM so calling it directly would read the log before the app writes it.
    start "" /wait "%EXE%" --selftest
    set "RC=!errorlevel!"

    echo The app is windowed, so results are read from its log:
    echo   %LOG%
    echo.
    if exist "%LOG%" (
        type "%LOG%"
    ) else (
        echo [WARN] No log was produced - the exe failed to start.
        echo Try running it from a Command Prompt to see any error output.
    )
    echo.
    if not "!RC!"=="0" echo Self-test exit code: !RC!  ^(0 = all checks passed^)
    echo.
    pause
    endlocal
    exit /b 0
)

REM ---- No exe found: run from source ----
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --selftest
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Neither a built VoiceTranslator.exe nor Python was found.
        echo Run build_exe.bat first, or install Python 3.10+ ^(64-bit^).
        pause
        endlocal
        exit /b 1
    )
    python main.py --selftest
)

echo.
pause
endlocal
