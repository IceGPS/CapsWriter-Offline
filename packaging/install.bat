@echo off
REM ============================================================
REM voice-ime Windows client one-click installer (v4: client only)
REM Reuses the existing CapsWriter server (Qwen3-ASR-1.7B) on this PC.
REM No model download, no llama-server install.
REM Pure ASCII - safe under any Windows code page.
REM ============================================================
setlocal

echo.
echo  ==============================================
echo   voice-ime Windows client installer
echo  ==============================================
echo.

REM ---------- 1. Locate Python ----------
REM Order: "python" on PATH -> "py" launcher -> common install locations.
REM We do NOT rely on PATH alone: a previous winget install may not
REM have refreshed PATH, which used to cause an endless re-run loop.
set PYCMD=

python --version >nul 2>&1
if not errorlevel 1 set PYCMD=python

if not defined PYCMD (
    py --version >nul 2>&1
    if not errorlevel 1 set PYCMD=py
)

if not defined PYCMD (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    )
)
if not defined PYCMD (
    if exist "C:\Program Files\Python312\python.exe" (
        set "PYCMD=C:\Program Files\Python312\python.exe"
    )
)

if not defined PYCMD (
    echo [1/3] Python not found, installing Python 3.12 via winget ...
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        echo.
        echo [ERROR] Could not find Python after install.
        echo        Install Python 3.12 manually from:
        echo        https://www.python.org/downloads/
        echo        (check "Add python.exe to PATH" during setup)
        echo        Then re-run this script.
        pause
        exit /b 1
    )
)

echo [1/3] Using Python: %PYCMD%
"%PYCMD%" --version

REM ---------- 2. Install client dependencies ----------
echo.
echo [2/3] Installing client dependencies (takes a few minutes)...
"%PYCMD%" -m pip install --upgrade pip -q
"%PYCMD%" -m pip install -q -r requirements-client.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check your network and retry.
    pause
    exit /b 1
)
echo        Dependencies installed.

REM ---------- 3. Generate launcher ----------
echo.
echo [3/3] Generating launcher ...
(
echo @echo off
echo cd /d %%~dp0
echo echo Starting voice-ime client...
echo echo Make sure the CapsWriter server is running.
echo "%PYCMD%" start_client.py
echo pause
) > start_client.bat

echo.
echo  ==============================================
echo   Installation complete!
echo.
echo   How to use:
echo     1. Make sure the CapsWriter SERVER is running
echo        (your existing one, no changes needed)
echo     2. Quit the original CapsWriter CLIENT from the tray
echo        (avoid two clients fighting over CapsLock)
echo     3. Double-click start_client.bat
echo        (tray icon "voice-ime" means ready)
echo     4. Open Notepad, hold CapsLock and speak, release to type
echo.
echo   This client connects to your existing server (port 6016),
echo   reusing its Qwen3-ASR-1.7B model. No re-download.
echo  ==============================================
echo.
pause
