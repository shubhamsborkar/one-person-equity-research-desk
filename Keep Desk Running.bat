@echo off
rem Double-click me ONCE (Windows). From then on the desk starts when you log in and restarts
rem by itself if it ever stops. Double-click "Stop Desk.bat" to switch it off.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   The desk is not installed yet. Open this folder in your coding agent and paste the
  echo   instruction from README.md first.
  echo.
  pause
  exit /b 1
)
set "TASK=Research Desk"
set "SCRIPT=%~dp0desk-service.ps1"
schtasks /Delete /TN "%TASK%" /F >nul 2>&1
schtasks /Create /F /SC ONLOGON /TN "%TASK%" /RL LIMITED /TR "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%SCRIPT%\""
if errorlevel 1 (
  echo.
  echo   Could not register the task. Paste this window's text to your coding agent and ask it to fix it.
  echo.
  pause
  exit /b 1
)
schtasks /Run /TN "%TASK%" >nul
echo.
echo   The desk is starting and will start at every login: http://localhost:8765
echo   It restarts by itself if it stops. Log: logs\desk-service.log
echo.
pause
