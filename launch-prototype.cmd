@echo off
cd /d "%~dp0"
if exist "PearlMine.exe" (
  start "" "%~dp0PearlMine.exe"
  exit /b
)
where pythonw >nul 2>&1
if not errorlevel 1 (
  start "" pythonw -m boris_pearl gui
  exit /b
)
python -m boris_pearl gui
if errorlevel 1 pause
