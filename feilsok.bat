@echo off
REM Starter NoteScanner med synlig konsoll, slik at feilmeldinger vises
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
echo Bruker Python: %PYEXE%
"%PYEXE%" NoteScanner.pyw %*
echo.
echo (Appen er avsluttet. Feilmeldinger star over, og i feillogg.txt)
pause
