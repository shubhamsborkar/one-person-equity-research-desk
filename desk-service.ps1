# Runs the desk and restarts it if it ever exits. Started at logon by the task that
# "Keep Desk Running.bat" registers. Nothing to run by hand.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
New-Item -ItemType Directory -Force -Path (Join-Path $here "logs") | Out-Null
$log = Join-Path $here "logs\desk-service.log"
$python = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
while ($true) {
  "$(Get-Date -Format s) starting desk" | Out-File -Append $log
  & $python server.py *>> $log
  "$(Get-Date -Format s) desk exited, restarting in 5 seconds" | Out-File -Append $log
  Start-Sleep -Seconds 5
}
