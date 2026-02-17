@echo off
echo ===============================
echo        ClipTwist Launcher
echo ===============================

REM Vérifie si venv existe
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activation
call venv\Scripts\activate

REM Upgrade pip propre
python -m pip install --upgrade pip

REM Installation requirements
echo Installing dependencies...
pip install -r requirements.txt

REM Lancement app
echo Launching ClipTwist...
python main.py

pause
