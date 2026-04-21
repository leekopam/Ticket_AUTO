# AI 작업 시도 이력

최신 항목이 위에 오도록 역순 정렬.

---

## 2026-04-21 — QR 자동화 안정성 개선 + 오프라인 스캔 기능 보완

### 완료 항목

#### BUG-01: 오프라인 스캔 모드에서 스캔 불가
- **증상**: 오프라인 모드 ON 후 런타임 시작 시 상태는 READY인데 QR 스캔이 안 됨
- **원인**: `run()` 내 오프라인 진입 시 `_emit_status()` 직접 호출 → `set_auth_ready(True)`, `set_scanning_enabled(True)` 누락
- **수정**: `_emit_status(...)` → `_enter_ready("[오프라인] 로그인 없이 스캔 테스트 모드")` (`main.py`)
- **결과**: 성공

#### BUG-02: 오프라인 스캔 시 "리다이렉트 응답이 아닙니다" 에러
- **증상**: 오프라인 스캔 모드 ON + 실제 witchform QR 스캔 → `REDIRECT_MISSING` 에러
- **원인**: `httpx.get(follow_redirects=False)` → 서버가 302 대신 200(로그인 페이지) 반환
- **수정**: `follow_redirects=True` + `str(resp.url)` 로 최종 URL 확인, `/w/login` 포함 시 fake 302 반환 (`main.py` `_resolve_qr_offline()`)
- **결과**: 성공

#### BUG-03: 영수증 출력 실패 시 엑셀 데이터 불일치
- **증상**: 수령완료 처리 후 프린터 오류 → 엑셀에 "거래종료" 상태 남음
- **원인**: `mark_order_status("거래종료")` 호출 후 출력 실패 시 롤백 없음
- **수정**: `previous_status = order.order_status` 저장 후 출력 실패 시 복원 (`main.py` `_process_resolved_qr()`)
- **결과**: 성공

#### BUG-04: 세션 타임아웃 후 RECOVERING 상태에서 멈춤
- **증상**: 로그인 대기 180초 초과 후 RECOVERING 상태, 스캔 재시도 불가
- **원인**: `_recover_auth_and_retry()` 내 타임아웃 시 `set_scanning_enabled(True)` 만 호출, READY 전환 없음
- **수정**: `_enter_ready("로그인 대기 시간 초과 - 준비 상태로 돌아갑니다.")` 로 변경
- **결과**: 성공

#### FEAT-01: 수령완료 클릭 자동 재시도
- **배경**: 페이지 로딩 지연으로 PRIMARY_CLICK_FAIL 간헐적 발생
- **구현**: 1.5초 대기 후 페이지 재열기 + 1회 재시도 (`main.py` `_process_resolved_qr()`)
- **결과**: 성공

#### FEAT-02: 개발자 도구 — 테스트용 QR 생성
- **배경**: 테스트 계정으로 실제 QR 발급 불가 → 주문번호로 QR 직접 생성 필요
- **구현**: 설정 → 개발자 도구 패널에 주문번호 입력 + QR 이미지 생성/표시 기능 추가 (`views/settings_flet_view.py`)
- **결과**: 성공

#### FEAT-03: 주문번호 복사 버튼
- **배경**: 검색 결과에서 주문번호를 직접 복사할 수 없어 불편
- **구현**: 검색 결과 목록 주문번호 왼쪽에 복사 아이콘 버튼 추가, `page.set_clipboard()` 연동 (`views/dashboard_flet_view.py`)
- **결과**: 성공

#### REFACTOR-01: "디버깅 모드" → "개발자 도구" 패널명 변경
- **구현**: `views/settings_flet_view.py` 패널 제목 변경, `tests/test_app_settings_modal_contract.py` 테스트 업데이트

### 변경 파일
- `main.py`
- `views/settings_flet_view.py`
- `views/dashboard_flet_view.py`
- `tests/test_app_print_flow.py` (재시도/롤백/타임아웃 테스트 추가)
- `tests/test_qr_order_number_recovery.py` (테스트 픽스처 수정)
- `tests/test_app_settings_modal_contract.py` (패널명 어서션 수정)

### 검증
```
452 passed, 13 skipped, 0 failed
```
