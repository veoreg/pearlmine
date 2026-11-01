@echo off
cd /d "%~dp0"
if exist "PearlMine.exe" (
  start "" "%~dp0PearlMine.exe" --agent
  exit /b
)
where pythonw >nul 2>&1
if not errorlevel 1 (
  start "" pythonw -m boris_pearl.agent_ui
  exit /b
)
python -m boris_pearl.agent_ui
if errorlevel 1 pause
