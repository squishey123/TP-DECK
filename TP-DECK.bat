@echo off
REM Launch TP DECK without leaving a console window open.
cd /d "%~dp0"

REM Bundled embeddable Python (release zip)
if exist "%~dp0python\pythonw.exe" (
  start "" "%~dp0python\pythonw.exe" "%~dp0main.py"
  exit /b 0
)

REM Local development: system Python
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw "%~dp0main.py"
  exit /b 0
)

start "" python "%~dp0main.py"
exit /b 0
