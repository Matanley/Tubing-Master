@echo off
REM Build Tubing Master with PyInstaller (Windows → dist\Tubing Master\ + Tubing Master.exe)
setlocal
cd /d "%~dp0\.."

if not defined PYTHON set PYTHON=python
where %PYTHON% >nul 2>&1 || (echo Python not on PATH: %PYTHON% & exit /b 1)
%PYTHON% -m pip install -q -r requirements.txt -r requirements-build.txt
if errorlevel 1 exit /b 1
%PYTHON% packaging\generate_icons.py
if errorlevel 1 exit /b 1

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

%PYTHON% -m PyInstaller packaging\tubing_master.spec --noconfirm --clean
if errorlevel 1 exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -File packaging\create_windows_installer.ps1
if errorlevel 1 exit /b 1

echo.
echo Build finished:
echo   dist\Tubing Master\Tubing Master.exe
echo   dist\Tubing-Master-*-Windows-x64.zip
echo   dist\Tubing-Master-*-Windows-x64-Setup.exe  (if Inno Setup is installed)
endlocal
