# Ticket_AUTO

## 인증/쿠키 운영 정책

1. 인증 소스는 Playwright 브라우저 컨텍스트 쿠키 단일 소스다.
2. QR 처리 요청은 `BrowserService.resolve_qr_redirect()`에서 `context.request`로만 수행한다.
3. 기본 브라우저에서 쿠키를 수동 복사해 붙여넣는 절차는 사용하지 않는다.
4. 별도 세션 파일(`session.txt`) 없이 동작한다.

## 로그인 정책

1. 기본값은 `require_login_each_run=True`다.
2. 앱 시작 시 Witchform 인증 상태를 초기화하고 로그인 페이지로 이동한다.
3. 로그인 완료 후 Playwright 쿠키를 자동으로 사용해 QR 기능을 처리한다.
