@echo off
setlocal
cd /d "%~dp0..\.."

echo [1/5] 가상환경 생성...
python -m venv .venv

echo [2/5] pip 업그레이드...
.venv\Scripts\python.exe -m pip install --upgrade pip

echo [3/5] 의존성 설치...
.venv\Scripts\pip.exe install -r requirements.txt

echo [4/5] Playwright 브라우저 설치...
.venv\Scripts\python.exe -m playwright install chromium

echo [5/5] 완료!
echo.
echo 가상환경 활성화: .venv\Scripts\activate
echo 앱 실행:        python main.py
echo.
pause
