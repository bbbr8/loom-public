@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0Start-Loom.ps1"
if errorlevel 1 (
  echo Loom could not start. Review the message above.
  pause
  exit /b 1
)
