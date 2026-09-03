@echo off
rem ONLY for the shipped ICICI Direct (Breeze) adapter, whose regulator requires a fresh login every
rem trading day. Most other brokers never need this file. Double-click on a morning you want the home
rem account live: it opens the login page, you paste the number after apisession=, press Enter.
cd /d "%~dp0"
".venv\Scripts\python.exe" paste_token.py
schtasks /Query /TN "Research Desk" >nul 2>&1
if not errorlevel 1 (
  echo Restarting the desk with the new token...
  powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server.py*' -and $_.CommandLine -like '*%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
  echo   The service restarts it within a few seconds: http://localhost:8765
) else (
  echo   Token saved. Start the desk with "Start Desk.bat", or run "Keep Desk Running.bat" once to make it always on.
)
echo.
pause
