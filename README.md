# Ticket_AUTO

티켓 QR 스캔 및 영수증 발급 자동화 데스크탑 앱 (Python + Flet)

---

## 개발 환경 설정

### 처음 설치 (새 PC)

```bat
git clone <repo-url>
cd Ticket_AUTO
setup.bat
```

`setup.bat`이 자동으로:
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
pyinstaller Ticket_AUTO.spec
```

빌드 결과물은 `dist/Ticket_AUTO/` 에 생성됩니다.

---

## Python 버전

Python **3.13** (`.python-version` 참고)
