@echo off
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

echo Installing build tools...
python -m pip install -r requirements.txt pyinstaller --quiet --disable-pip-version-check
if errorlevel 1 goto fail

echo Building FPSToolkit.exe (takes a minute)...
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
    --name FPSToolkit --collect-data customtkinter fps_toolkit.py
if errorlevel 1 goto fail

echo.
echo Done! Your exe is at: %~dp0dist\FPSToolkit.exe
explorer "%~dp0dist"
pause
exit /b 0

:fail
echo Build failed, see the messages above.
pause
exit /b 1
