@echo off
setlocal
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
if not exist ".venv\Scripts\python.exe" (
    echo Preparing the Python environment...
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -B -c "import numpy, matplotlib, Pyro4, flask, requests, threadpoolctl" >nul 2>&1
if errorlevel 1 (
    echo Installing project dependencies...
    ".venv\Scripts\python.exe" -B -m pip install --no-compile -r requirements.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -B run_benchmark.py --config configs/smoke.json --output results/quick-demo
if errorlevel 1 goto failed
echo Results saved in results\quick-demo.
pause
exit /b 0
:failed
echo The run failed. Check Python installation, internet connection and project logs.
pause
exit /b 1
