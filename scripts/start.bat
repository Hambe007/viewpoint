@echo off
REM Starts the TradingView -> MT5 bridge on Windows.
REM First run creates a virtual environment and installs dependencies.
cd /d "%~dp0\.."

if not exist .venv (
    python -m venv .venv || goto :error
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt || goto :error
)
if not exist .env (
    copy .env.example .env
    echo Created .env - edit it, then run this script again.
    notepad .env
    exit /b 1
)

.venv\Scripts\python -m bridge
pause
exit /b 0

:error
echo Setup failed. Is Python 3.10+ installed and on PATH?
pause
exit /b 1
