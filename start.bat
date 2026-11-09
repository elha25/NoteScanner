@echo off
cd /d "%~dp0"
rem Finn en ekte Python (ikke Microsoft Store-snarveien, som bare viser en melding)
set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if exist "%%i" set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do if exist "%%i" set "PYEXE=%%i"
if not defined PYEXE for /d %%D in ("%LocalAppData%\Programs\Python\Python3*" "%ProgramFiles%\Python3*" "%LocalAppData%\Python\pythoncore-3*") do if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
if not defined PYEXE (
  echo Fant ikke Python.
  echo Installer Python 3.13 fra python.org og huk av "Add python.exe to PATH".
  echo Sla ogsa av python.exe og python3.exe under
  echo Innstillinger - Apper - Avanserte appinnstillinger - Appkjoringsaliaser.
  pause
  exit /b 1
)
for %%F in ("%PYEXE%") do set "PYW=%%~dpFpythonw.exe"
if not exist "%PYW%" set "PYW=%PYEXE%"
"%PYEXE%" -c "import PySide6, cv2, numpy, PIL, img2pdf, win32com.client" >nul 2>&1
if errorlevel 1 (
  echo Installerer pakkene NoteScanner trenger - dette tar et par minutter forste gang ...
  "%PYEXE%" -m pip install -r requirements.txt
  if errorlevel 1 (echo. & echo Installasjonen feilet - se feilmeldingen over. & pause & exit /b 1)
)
start "" "%PYW%" NoteScanner.pyw %*
