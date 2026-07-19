@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py serve.py --port 5173
) else (
  python serve.py --port 5173
)
pause
