@echo off
rem plancheck launcher for Windows. First run creates .venv next to this file and installs requirements.
rem Double-click: shows a menu. Command line: plancheck.cmd run <folder-or-zip> --manifest <manifest.yaml> --sheets A1.1,A2.1
setlocal
set "HERE=%~dp0"
set "PY=%HERE%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [plancheck] First run: setting up in %HERE%.venv - this takes about a minute...
  py -3 -m venv "%HERE%.venv" 2>nul || python -m venv "%HERE%.venv" 2>nul || goto :nopython
  "%PY%" -m pip install --disable-pip-version-check -q -r "%HERE%requirements.txt" || (echo pip install failed - check your internet connection. & goto :end_pause)
)
set "PYTHONPATH=%HERE%src;%PYTHONPATH%"
if not "%~1"=="" (
  "%PY%" -m plancheck %*
  exit /b %ERRORLEVEL%
)

rem ---- no arguments: probably double-clicked ----
cd /d "%HERE%"
:menu
echo.
echo ==================== plancheck ====================
echo  1  Try the sample project (safe demo)
echo  2  Check my own project (asks for folder/ZIP and manifest)
echo  3  Show status of a previous check
echo  Q  Quit
echo ====================================================
set "CHOICE="
set /p "CHOICE=Choose 1, 2, 3 or Q: "
if /i "%CHOICE%"=="1" goto :sample
if /i "%CHOICE%"=="2" goto :own
if /i "%CHOICE%"=="3" goto :status
if /i "%CHOICE%"=="Q" exit /b 0
goto :menu

:sample
"%PY%" -m plancheck run "samples\synthetic_project.zip" --manifest "samples\manifest.yaml" --sheets A1.1,A2.1,A3.1 --continue-on-error --work "work\sample"
echo.
echo The sample has mistakes planted on purpose - the ERROR/WARN lines above are the tool catching them.
echo Opening the review packet folder...
start "" "%HERE%work\sample\packet"
goto :end_pause

:own
echo.
echo Tip: drag a folder or file onto this window to paste its path, then press Enter.
set "SRC="
set /p "SRC=Folder or ZIP of PDFs: "
set "MAN="
set /p "MAN=Manifest file (.yaml): "
set "SHEETS="
set /p "SHEETS=Sheets to review, e.g. A1.1,A2.1 (or all; blank = check sources only): "
set "SRC=%SRC:"=%"
set "MAN=%MAN:"=%"
if "%SHEETS%"=="" (
  "%PY%" -m plancheck run "%SRC%" --manifest "%MAN%" --work "work\project"
) else (
  "%PY%" -m plancheck run "%SRC%" --manifest "%MAN%" --sheets "%SHEETS%" --work "work\project"
  if exist "%HERE%work\project\packet" start "" "%HERE%work\project\packet"
)
echo.
echo Full report: %HERE%work\project\intake_report.json
goto :end_pause

:status
if exist "work\project\checkpoint.json" ("%PY%" -m plancheck status --work "work\project") else (echo No project checked yet.)
if exist "work\sample\checkpoint.json" (echo. & echo --- sample --- & "%PY%" -m plancheck status --work "work\sample")
goto :end_pause

:nopython
echo.
echo Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo then double-click plancheck.cmd again.

:end_pause
echo.
pause
