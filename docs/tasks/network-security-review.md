# 네트워크 관리 / 폰 연결 — 보안·신뢰성 분석

작성: 2026-XX | 대상: LAN API v1 (PC 서버 ↔ Android 클라이언트)

## 1. 위협 모델

| 항목 | 내용 |
|---|---|
| 보호 대상 | 주문 목록(이름·연락처 마스킹된 상태), 수령 처리 권한, 기기 레지스트리 |
| 신뢰 경계 | PC ↔ LAN ↔ 폰. LAN은 신뢰할 수 없다고 가정 (행사장 공유망/게스트 Wi-Fi) |
| 공격자 | 같은 LAN의 제3자, 탈취된 토큰, QR을 몰래 스캔한 기기, 버그 있는 클라이언트 |
| 범위 밖 | PC 자체 탈취(파일 접근), 폰 루팅, DDoS 수준의 네트워크 공격 |

## 2. 기존 통제 (검증됨)

- TLS + 폰 측 인증서 SHA-256 지문 핀 — 불일치 시 연결 거부 (`TicketAutoClient.pinned`)
- Bearer 토큰 256bit, PC는 SHA-256 해시만 저장, 폰은 Keystore 기반 secure storage
- 참가 코드: 6자리·일회용·10분 TTL·연속 10회 실패 시 60초 잠금
- 운영자 승인 게이트 (TOFU — Syncthing/KDE Connect와 동일 모델)
- `request_id` 멱등성, 타 기기 요청은 404로 은닉
- 본문 32KB 상한, pydantic 엄격 검증(`extra=forbid`, 길이 제한), QR 주소 화이트리스트
- 레지스트리 원자적 기록(tmp+replace), 손상 파일 `.corrupt-*` 보존, v1→v2 마이그레이션
- 차단 시 토큰만 무효화하고 기록·별칭 보존

## 3. 이번 라운드 수정 (갭 → 조치)

| # | 갭 | 조치 | 파일 |
|---|---|---|---|
| 1 | 폰이 보낸 `device_name`에 제어문자 필터 없음 → UI/로그 주입 가능 | `sanitize_reported_name`(출력 가능 문자만, 64자) 적용 | `pairing_service.py` |
| 2 | 토큰 해시 비교 `==` | `hmac.compare_digest` 상수 시간 비교 | `pairing_service.py` |
| 3 | `/v1/scan` 무제한 스레드 → 폭주 시 PC 마비 | `BoundedSemaphore(4)`, 초과 시 `SERVER_BUSY`(429) | `api_v1_server.py` |
| 4 | 승인 UI가 새 기기/재페어링 구분 불가 → `device_uid` 위장 승인 못 알아봄 | `PendingApproval.known_device` + "(등록된 기기 재페어링)" 표시 | `pairing_service.py`, 두 뷰 |
| 5 | 승인/거절/차단/잠금 이벤트 감사 로그 없음 | `logger.info` 추가 (토큰 원문 미기록) | `pairing_service.py` |

## 4. 잔여 위험 (수용 + 운영 지침)

| 위험 | 수준 | 이유/대응 |
|---|---|---|
| 토큰 무기한 유효 | 중 | 행사 당일 도구 특성상 세션 토큰 도입 비용 > 이득. 폰 분실·이탈 시 네트워크 관리 탭 "차단"으로 즉시 무효화 가능 |
| 참가 QR 어깨너머 촬영 → 코드 선점 | 낮음~중 | 승인 게이트가 토큰 발급을 막음. QR 화면은 필요할 때만 열고, 의심 시 "QR 재발급"으로 코드 무효화 |
| `device_uid` 자기선언 → 다른 폰 uid 위장 시 레코드 병합 | 낮음 | uid는 UUID v4라 추측 불가. 유출 + QR + 승인이 모두 필요. 재페어링 표시로 운영자가 인지 가능 |
| 0.0.0.0 바인드 → LAN 전체 노출 | 낮음 | 폰 연결에 필요. 인증서 핀+토큰+승인으로 인증 없는 접근은 거절됨. 공용망에서는 PC 방화벽으로 인바운드 제한 권장 |
| 서버 개인키 `.runtime/api_cert/server.key` 평문 | 낮음 | PC 파일 접근권 탈취 시 MITM 가능. 대응: `.runtime/api_cert/` 삭제 후 재기동하면 지문이 바뀌어 전 기기 재페어링 필요 |
| 인증서 370일 고정·로테이션 절차 없음 | 낮음 | 재생성 = 재페어링 강제라 안전한 실패 방향. 만료 임박 시 위와 동일 절차 |
| `티켓 확인 중지` 후에도 API 서버 유지 | 설계 | 기기 목록/별칭 조회와 폰 재접속을 위해 의도된 동작. 연결 다이얼로그 "서버 중지"로 명시 종료 가능, 탭에 주소 표시됨 |

## 5. 테스트 커버리지

| 경계 | 자동 검증 |
|---|---|
| 지문 불일치 거부, 전 엔드포인트 401 | `tests/e2e/test_phone_link_security.py` |
| 일회용 코드, 승인/토큰 발급, 폐기 후 401, 레지스트리 왕복 | 동일 |
| 거절 흐름, 10회 실패 잠금·잠금 중 정상 코드 거절, 제어문자 정제 | 동일 (신규) |
| 깨진 JSON 422, 대용량 본문 413 (실 TLS) | 동일 (신규) |
| 스캔 스레드 상한 429 | `test_api_v1_server.py::test_scan_saturated_slots_rejected` |
| 교차 기기 404, 멱등성, STALE_DATASET, PAUSED, 자동 일시정지 | `tests/test_api_v1_server.py` |
| 잠금/만료/마이그레이션/손상 파일/별칭/재페어링 병합 | `tests/test_pairing_service.py` |
| 재페어링 표시(known_device) | `tests/test_pairing_service.py`, 뷰 단위 테스트 |
| 탭 UI(승인·이름 변경·처리 건수) + 티켓 시작 시 서버 자동 기동 | `tests/e2e_ui/test_network_tab_ui.py` |

## 6. 미검증 항목 (수동 확인 필요)

- 실기기: PC↔Android LAN 페어링, 백그라운드 45초 끊김 판정, 재연결 복원
- PC 방화벽이 `18765` 인바운드를 LAN 범위로 제한했는지 행사 환경에서 확인
- 공용망(카페/행사장 게스트 Wi-Fi)에서의 실제 노출 범위
- 인증서 만료(370일) 전 교체 절차 리허설
