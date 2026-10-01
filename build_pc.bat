@echo off
rem Ticket_AUTO PC build -- creates dist\Ticket_AUTO_flat\
rem Runs scripts\build\build_windows.ps1 (venv + deps + tests + PyInstaller)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build\build_windows.ps1" %*
if errorlevel 1 (
    echo [FAIL] PC build failed
    exit /b 1
)
echo [OK] PC build done: dist\Ticket_AUTO_flat\Ticket_AUTO_flat.exe
