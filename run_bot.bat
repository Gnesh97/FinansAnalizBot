@echo off
pushd "%~dp0"
py -3 bot.py || python bot.py
pause
popd
