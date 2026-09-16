@echo off
REM Launch TP DECK without leaving a console window open.
cd /d "%~dp0"

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw "%~dp0main.py"
  exit /b 0
)

REM Fallback: python.exe + FreeConsole inside the app
start "" python "%~dp0main.py"
exit /b 0
