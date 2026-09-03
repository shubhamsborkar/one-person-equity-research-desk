@echo off
rem Double-click me to start the desk (Windows). Leave this window open while you use it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   The desk is not installed yet. Open this folder in your coding agent and paste the
  echo   instruction from README.md first.
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" server.py
pause
