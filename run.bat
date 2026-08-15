@echo off
REM Entry point for Windows Task Scheduler. Extra args pass through to run.py,
REM e.g.  run.bat --dry-run   or   run.bat --no-mail
cd /d "%~dp0"
"C:\Python314\python.exe" "%~dp0scripts\run.py" %*
exit /b %ERRORLEVEL%
