@echo off
setlocal enabledelayedexpansion
title Build Voice Translator .exe
cd /d "%~dp0"

echo ============================================================
echo   Voice Translator - Windows .exe builder
echo ============================================================
echo.
echo This builds dist\VoiceTranslator\VoiceTranslator.exe
echo First build takes 5-15 minutes and needs internet.
echo.

REM ---------- find Python ----------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python was not found.
    echo.
    echo Install Python 3.11 or 3.12 ^(64-bit^) from:
    echo   https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" during installation,
    echo then run this file again.
    echo.
    pause
    exit /b 1
)

REM ---------- verify 64-bit ----------
REM Use an exit code, not "for /f": that construct wraps the command in single
REM quotes, so a Python expression containing 'P' breaks CMD's parsing.
REM sys.maxsize needs no inner quotes at all.
%PY% -c "import sys;sys.exit(0 if sys.maxsize>2**32 else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Your Python is 32-bit. The speech engine needs 64-bit.
    echo Install the "Windows installer ^(64-bit^)" from python.org.
    pause
    exit /b 1
)
echo [OK] Found 64-bit Python
%PY% --version

REM ---------- verify version ----------
%PY% -c "import sys;sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Python 3.10-3.13 is recommended.
    echo Other versions may try to compile audio drivers from source and fail.
    echo.
)

REM ---------- environment ----------
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Creating isolated build environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
set "VPY=.venv\Scripts\python.exe"

echo.
echo Installing build dependencies ^(this is the slow part^)...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    echo Check your internet connection and try again.
    pause
    exit /b 1
)
"%VPY%" -m pip install "pyinstaller>=6.0" pillow
if errorlevel 1 (
    echo [ERROR] Could not install PyInstaller.
    pause
    exit /b 1
)

REM ---------- icon ----------
if not exist "icon.ico" (
    echo.
    echo Generating application icon...
    "%VPY%" tools\make_icon.py
)

REM ---------- build ----------
echo.
echo ============================================================
echo   Building the executable... please wait.
echo ============================================================
echo.
"%VPY%" -m PyInstaller --noconfirm --clean VoiceTranslator.spec
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [ERROR] Build FAILED.
    echo  Scroll up for the first error message.
    echo  Details: build\VoiceTranslator\warn-VoiceTranslator.txt
    echo ============================================================
    pause
    exit /b 1
)

if not exist "dist\VoiceTranslator\VoiceTranslator.exe" (
    echo [ERROR] Build reported success but the .exe is missing.
    pause
    exit /b 1
)

REM ---------- ship docs + self-test runner alongside the exe ----------
copy /y README.md "dist\VoiceTranslator\README.md" >nul 2>nul
copy /y BUILD_EXE.md "dist\VoiceTranslator\BUILD_EXE.md" >nul 2>nul
copy /y selftest.bat "dist\VoiceTranslator\selftest.bat" >nul 2>nul

REM ---------- report ----------
echo.
echo ============================================================
echo   BUILD SUCCESSFUL
echo ============================================================
echo.
echo   Your app:  dist\VoiceTranslator\VoiceTranslator.exe
echo.
echo   Double-click it to run. Python is NOT needed on the
echo   target PC. To share the app, zip the WHOLE
echo   dist\VoiceTranslator folder, not just the .exe.
echo.
echo   The speech model downloads on first Start.
echo.
echo   To verify the built app, run selftest.bat in that folder.
echo ============================================================
echo.
choice /c YN /m "Open the output folder now? (Y/N)"
if errorlevel 2 goto :end
explorer "dist\VoiceTranslator"
:end
echo.
pause
endlocal
