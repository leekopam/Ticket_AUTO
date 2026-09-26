@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."

rem 브라우저가 화면에 보이는 GUI 모드로 UI E2E를 실행한다.
rem slow_mo로 동작이 느려져 클릭/입력 과정을 눈으로 확인할 수 있다.
set "E2E_HEADED=1"
echo [E2E-GUI] 화면 표시 모드 실행: tests\e2e_ui (+e2e_stub은 인자로 추가 가능)
if "%~1"=="" (
    .venv\Scripts\python.exe -m pytest tests\e2e_ui -v
) else (
    .venv\Scripts\python.exe -m pytest %*
)
set "RC=%ERRORLEVEL%"

rem 탐색기 더블클릭으로 실행한 경우에만 창 유지
echo %cmdcmdline% | findstr /i /c:" /c " >nul && pause
exit /b %RC%
