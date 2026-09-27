@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."

rem 스텁 런타임으로 대시보드를 띄우고 브라우저를 자동으로 연다.
rem 실제 카메라/프린터/witchform 없이 UI를 수동으로 조작할 수 있다.
set "PYTHONIOENCODING=utf-8"
.venv\Scripts\python.exe tests\e2e_ui\web_entry.py --demo %*
set "RC=%ERRORLEVEL%"

rem 탐색기 더블클릭으로 실행한 경우에만 창 유지
echo %cmdcmdline% | findstr /i /c:" /c " >nul && pause
exit /b %RC%
