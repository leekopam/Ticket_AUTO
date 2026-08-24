# Ticket_AUTO

티켓 QR 스캔 및 영수증 발급 자동화 데스크탑 앱 (Python + Flet)

---

## 개발 환경 설정

### 처음 설치 (새 PC)

```bat
git clone <repo-url>
cd Ticket_AUTO
scripts\setup\setup_windows.bat
```

`scripts\setup\setup_windows.bat`이 자동으로:
1. `.venv` 가상환경 생성
2. `requirements.txt`의 정확한 버전으로 패키지 설치
3. Playwright 브라우저(Chromium) 설치

### 가상환경 활성화 (매 작업 세션마다)

```bat
.venv\Scripts\activate
```

### 앱 실행

```bat
python main.py
```

---

## 의존성 업데이트 규칙

AI가 새 패키지를 설치하거나 버전을 변경하면 **반드시 requirements.txt를 업데이트**해야 두 PC 간 환경이 일치한다.

```bat
# 패키지 추가/변경 후
pip freeze | Select-String "패키지명"   # 버전 확인
# requirements.txt 직접 수정 후 커밋
git add requirements.txt
git commit -m "의존성 업데이트: 패키지명 추가"
```

### 다른 PC에서 최신 의존성 반영

```bat
git pull
pip install -r requirements.txt
```

---

## 빌드 (exe 생성)

```bat
scripts\build\build_windows.bat
```

기본 빌드는 전체 pytest를 통과한 뒤 `dist/Ticket_AUTO_flat/`에 EXE를 생성합니다.

## 자동 검증

개발 중 빠른 회귀 검증:

```powershell
.\scripts\qa\verify_release.ps1 -Fast
```

배포 전 전체 검증(실제 Playwright, 빌드, 패키징 EXE 기동 검사):

```powershell
.\scripts\qa\verify_release.ps1 -Release
```

실행 결과는 `artifacts/test-results/<실행시각>/`에 저장되며 Git에는 포함되지 않습니다.
실패 시 해당 폴더의 `summary.md`와 단계별 로그를 확인하고, 원인 수정 및 회귀 테스트 추가 후 다시 실행합니다.

실제 QR, 카메라, 프린터, Witchform 로그인은 `docs/qa/QR_CHECKLIST.md`에 따라 배포 전에 수동 확인합니다.

### 스크립트 구조

- `scripts/setup/`: 개발 환경 설치
- `scripts/build/`: Windows 패키징
- `scripts/qa/`: 자동 검증과 EXE 스모크 테스트
- `scripts/diagnostics/`: 일회성 데이터 진단
- `build_support/specs/`: PyInstaller 패키징 설정

---

## Python 버전

Python **3.13** (`.python-version` 참고)
