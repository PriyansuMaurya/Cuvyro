@echo off
REM Launch Notch. Writes to notch-run.log so problems stay visible.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py >> notch-run.log 2>&1
