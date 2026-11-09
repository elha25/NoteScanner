@echo off
REM Bygger NoteScanner.exe (mappe i dist\NoteScanner) og, hvis Inno Setup er installert, installasjonsfila.
REM Den offisielle byggingen skjer automatisk på GitHub (se .github\workflows\release.yml).
cd /d "%~dp0"
python -m pip install -r requirements-dev.txt || goto :feil
python packaging\make_icon.py || goto :feil
python packaging\make_version_info.py || goto :feil
python -m PyInstaller --noconfirm packaging\NoteScanner.spec || goto :feil
for /f "delims=" %%v in ('python -c "import notescanner;print(notescanner.__version__)"') do set VER=%%v
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DMyAppVersion=%VER% packaging\installer.iss
echo.
echo Ferdig: dist\NoteScanner\NoteScanner.exe
pause
exit /b 0
:feil
echo Byggingen feilet - se meldingen over.
pause
exit /b 1
