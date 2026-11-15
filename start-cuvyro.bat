@echo off
REM Launch Cuvyro. Writes to cuvyro-run.log so problems stay visible.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py >> cuvyro-run.log 2>&1
