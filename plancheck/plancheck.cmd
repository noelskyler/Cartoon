@echo off
rem plancheck launcher for Windows. First run creates .venv next to this file and installs requirements.
rem Usage: plancheck.cmd run <folder-or-zip> --manifest <manifest.yaml> --sheets A1.1,A2.1
setlocal
set "HERE=%~dp0"
set "PY=%HERE%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [plancheck] creating virtual environment in %HERE%.venv ...
  py -3 -m venv "%HERE%.venv" 2>nul || python -m venv "%HERE%.venv" || (echo Python 3.10+ is required. & exit /b 1)
  "%PY%" -m pip install --disable-pip-version-check -q -r "%HERE%requirements.txt" || (echo pip install failed. & exit /b 1)
)
set "PYTHONPATH=%HERE%src;%PYTHONPATH%"
"%PY%" -m plancheck %*
exit /b %ERRORLEVEL%
