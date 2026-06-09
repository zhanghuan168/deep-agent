@echo off
REM Windows one-click launcher for Deep Agent ZH
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [INFO] Creating virtual environment .venv ...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [INFO] Checking dependencies ...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt

if "%DAGENT_PORT%"=="" set DAGENT_PORT=8766
echo [INFO] Starting service at http://127.0.0.1:%DAGENT_PORT% ...
start "" http://127.0.0.1:%DAGENT_PORT%/
python main.py

endlocal
