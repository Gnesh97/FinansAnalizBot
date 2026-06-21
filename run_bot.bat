@echo off
pushd "%~dp0"
if exist .venv\Scripts\python.exe (
    echo [RUN] Virtual environment detected. Running bot.py...
    .venv\Scripts\python.exe bot.py
) else (
    echo [RUN] No virtual environment found. Running with system python...
    py -3 bot.py || python bot.py
)
pause
popd
