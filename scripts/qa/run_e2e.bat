@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."

rem 인자 없이 실행: E2E 전체 스위트 / 인자 전달 시 그대로 사용 (-Fast, -Release 등)
if "%~1"=="" (
    echo [E2E] 전체 E2E 스위트 실행: tests\e2e + e2e_stub + e2e_ui + e2e_device + e2e_native
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\qa\verify_release.ps1" -E2E
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\qa\verify_release.ps1" %*
)
set "RC=%ERRORLEVEL%"

rem 탐색기 더블클릭으로 실행한 경우에만 창 유지
echo %cmdcmdline% | findstr /i /c:" /c " >nul && pause
exit /b %RC%
