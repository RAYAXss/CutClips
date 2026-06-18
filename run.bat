@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: Go to the folder where run.bat lives (fixes "main.py not found" when launched from VS Code)
cd /d "%~dp0"

title ClipTwist Launcher

:: ANSI color codes (requires Windows 10+)
set "GREEN=[92m"
set "RED=[91m"
set "YELLOW=[93m"
set "CYAN=[96m"
set "WHITE=[97m"
set "DIM=[90m"
set "RST=[0m"

echo.
echo %CYAN%  ===========================================%RST%
echo %WHITE%        ClipTwist  ^|  Clip Generator%RST%
echo %CYAN%  ===========================================%RST%
echo %DIM%  Automated Clip Generation Tool%RST%
echo.

:: ─── STEP 1: Python ──────────────────────────────────────────────────────────
echo %WHITE%  [1/4] Checking Python installation...%RST%

where python >nul 2>&1
if errorlevel 1 (
    echo %RED%  [ERROR] Python not found in PATH.%RST%
    echo %DIM%          Install Python 3.8+ from https://python.org%RST%
    echo %DIM%          Check "Add Python to PATH" during installation.%RST%
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo %GREEN%  [OK]    Python !PYVER! detected%RST%
echo.

:: ─── STEP 2: Virtual Environment ─────────────────────────────────────────────
echo %WHITE%  [2/4] Setting up virtual environment...%RST%

if not exist venv (
    echo %DIM%          Creating new virtual environment...%RST%
    python -m venv venv
    if errorlevel 1 (
        echo %RED%  [ERROR] Failed to create virtual environment.%RST%
        pause
        exit /b 1
    )
    echo %GREEN%  [OK]    Virtual environment created%RST%
) else (
    echo %GREEN%  [OK]    Virtual environment already exists%RST%
)

call venv\Scripts\activate
if errorlevel 1 (
    echo %RED%  [ERROR] Failed to activate virtual environment.%RST%
    pause
    exit /b 1
)
echo.

:: ─── STEP 3: Dependencies ────────────────────────────────────────────────────
echo %WHITE%  [3/4] Installing / updating dependencies...%RST%

python -m pip install --upgrade pip --quiet

if not exist requirements.txt (
    echo %YELLOW%  [WARN]  requirements.txt not found. Skipping.%RST%
) else (
    pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo %RED%  [ERROR] Dependency installation failed.%RST%
        echo %DIM%          Try: pip install -r requirements.txt manually.%RST%
        pause
        exit /b 1
    )
    echo %GREEN%  [OK]    Dependencies up to date%RST%
)
echo.

:: ─── STEP 4: Check for existing instance on port 5000 ──────────────────────
echo %WHITE%  [4/5] Checking for existing ClipTwist instance...%RST%

set PORT_BUSY=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING" 2^>nul') do (
    set PORT_BUSY=1
)

if !PORT_BUSY!==1 (
    echo %YELLOW%  [WARN]  ClipTwist is already running on port 5000.%RST%
    echo %YELLOW%          Open http://127.0.0.1:5000 or close the existing instance first.%RST%
    echo.
    pause
    exit /b 0
)

echo %GREEN%  [OK]    Port 5000 is free%RST%
echo.

:: ─── STEP 5: Launch ──────────────────────────────────────────────────────────
echo %WHITE%  [5/5] Launching ClipTwist...%RST%
echo %CYAN%  ===========================================%RST%
echo.

if not exist main.py (
    echo %RED%  [ERROR] main.py not found in: %CD%%RST%
    echo %DIM%          Make sure run.bat is in the same folder as main.py.%RST%
    echo.
    pause
    exit /b 1
)

python main.py

echo.
if errorlevel 1 (
    echo %RED%  [ERROR] ClipTwist exited with an error.%RST%
) else (
    echo %GREEN%  [OK]    ClipTwist closed cleanly.%RST%
)
echo %CYAN%  ===========================================%RST%
echo.
pause
endlocal