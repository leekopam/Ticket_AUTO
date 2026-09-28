# Ticket_AUTO ↔ Android API 계약 v1

> 정본: 이 파일(Ticket_AUTO 저장소). Android 저장소에는 사본/스냅샷만 둔다.
> 상태: Android 자동 스캔 경로 구현, 실제 윗치폼 계정·QR 현장 검증 대기
> 근거: `Ticket_AUTO_Android/docs/work-plan.md` §4.1/§8, `docs/tasks/android-companion-plan.md` §4

---

## 1. 전송·인증

- 전송: `https://<PC LAN IP>:<port>/v1/...` — **TLS 필수, 평문 HTTP 없음**
- TLS: PC가 행사마다 자체 서명 인증서 생성 → 연결 QR에 **SHA-256 지문** 포함 → 앱은 그 지문의 인증서만 신뢰(TOFU). `badCertificateCallback => true` 식 전체 허용 금지
- 인증: `/pair` 외 모든 요청에 `Authorization: Bearer <device_token>` 필수
- 타임아웃: 폰 측 API 호출 3~5초. 서버 측 브라우저 작업은 작업 큐에서 비동기 진행

## 2. 페어링

### 연결 QR 페이로드 (PC 화면에 표시)

```json
{
  "v": 1,
  "addr": "https://192.168.0.10:8765",
  "cert_sha256": "AB:CD:...",
  "join_code": "482913",
  "server_id": "ticket-auto-pc",
  "dataset_generation": 3
}
```

- `join_code`: 1회용 + 유효기간(10분). QR 노출만으로는 등록 불가 — PC 화면에서 운영자 승인 필요
- `dataset_generation`: 현재 운영 XLSX 세대. 페어링 시점의 세대를 폰이 기억

### POST /v1/pair

```json
→ 요청:  { "join_code": "482913", "device_name": "staff-phone-1", "device_uid": "550e8400-..." }
← 승인 전: { "state": "pending_approval" }   (폰은 폴링)
← 승인 후: { "state": "approved", "device_token": "...", "dataset_generation": 3 }
← 거절/만료: { "state": "rejected" } / 오류 코드 EXPIRED_JOIN_CODE
```

- `device_name`: 실 기기 이름 — `Settings.Global.DEVICE_NAME`, 없으면 `Build.MODEL`. 서버는 제어문자를 제거하고 64자로 자른다 (UI 표시용이므로)
- `device_uid`: 앱이 최초 실행 시 생성한 UUID v4, 보안 저장소에 보관. 재페어링 시 같은 값을내면 PC는 같은 기기로 인식해 별칭·이력을 이어간다 (선택 필드 — 구버전 앱은 생략 가능)
- `device_token`은 마지막 인증 활동부터 24시간 유효(슬라이딩). 방치·만료 시 401이며 재페어링으로 복구. PC에서 기기별/전체 폐기 가능
- 토큰은 `flutter_secure_storage`(Keystore)에만 저장. 로그 출력 금지

### 연결 상태 (presence)

- 폰은 앱이 포그라운드일 때만 15초 간격으로 `GET /v1/status`를 호출한다 (별도 하트비트 엔드포인트 없음)
- PC는 인증된 요청마다 `last_seen`을 갱신하고, 45초 이상 활동이 없으면 네트워크 관리 탭에서 "연결 끊김"으로 표시한다
- 폰이 정상적으로 연결을 끊을 때 `POST /v1/disconnect`를 호출해 즉시 끊김으로 표시한다. 비정상 종료(앱 강제종료·네트워크 단절)는 45초 타임아웃이 감지한다
- 백그라운드/종료 시 폰은 하트비트를 멈추므로 끊김 표시는 정상 동작이다

## 3. 엔드포인트

| 메서드 | 경로 | 역할 |
|---|---|---|
| GET | `/v1/status` | `server_id`, `dataset_generation`, `data_version`, 윗치폼 로그인 여부, 처리 중 건수 |
| POST | `/v1/disconnect` | 명시적 연결 해제 통지 → PC presence를 즉시 끊김으로. 멱등·토큰 유효 유지 |
| GET | `/v1/orders/{order_id}` | 주문 상세 + 현재 상태 |
| GET | `/v1/orders?since={data_version}` | 변경분 조회(ETag 대응). 전체 재조회는 since 생략 |
| GET | `/v1/orders/search?q=` | 이름/주문번호 검색 — **200건 제한 없는 전체 조회 경로** (기존 `search_orders`와 분리) |
| POST | `/v1/scan` | `{ request_id, qr_url }` → PC 티켓 확인 런타임에서 QR 해석·윗치폼 수령·XLSX 기록을 비동기로 실행 |
| POST | `/v1/actions` | `{ request_id, order_id, action, dataset_generation }` → 작업 접수. 즉시 `accepted` 또는 기존 결과 반환 |
| GET | `/v1/actions/{request_id}` | 결과 재조회 — 응답 유실·재연결 복구용 |
| POST | `/v1/actions/{request_id}/result` | 폰이 자체 WebView로 실행한 처리 결과 보고 → XLSX 기록 |

- 현재 Android 앱은 `/v1/scan`을 사용한다. PC의 티켓 확인과 윗치폼 로그인이 먼저 준비되어야 한다.
- `/v1/scan` 결과는 `GET /v1/actions/{request_id}`로 조회한다. 종결 응답의 `order_id`와 `result.message`를 화면에 표시한다.
- `action` v1 값: `"receipt"` (향후 폰 자체 윗치폼 처리 경로용)
- 응답 공통: `{ "state": ..., "data_version": ..., "error": { "code": ..., "message": ... }? }`

## 4. 공통 필드

| 필드 | 규칙 |
|---|---|
| `request_id` | 폰이 생성한 고유값. 결과 조회·재전송 시 동일 값 유지 |
| `dataset_generation` | `/v1/actions` 요청에 포함. 서버 세대와 불일치 → `STALE_DATASET` 거절 |
| `order_id` | 윗치폼 주문번호(`AAAA..._BBBB...`). `/v1/scan`은 PC가 QR 리다이렉트에서 확정 |

## 5. 상태 모델

### 작업(action) 상태

| 상태 | 의미 | 같은 request_id 재요청 |
|---|---|---|
| `accepted` | 접수됨, 큐 대기 | 현재 상태 반환 |
| `in_progress` | 실행 중 | 진행 중 반환 |
| `succeeded` | 윗치폼 수령 확인 + XLSX 저장 완료 | 저장된 결과 반환 |
| `already_processed` | 이미 수령된 주문 | 저장된 결과 반환 |
| `failed` | 수령 미처리가 확정된 실패 | 새 request_id로 재시도 가능 |
| `needs_reconciliation` | 처리 후 저장 실패/응답 유실/중단 — 실제 상태 불명 | **자동 재클릭 금지**, 윗치폼 재조회로 대조 후 확정 |
| `rejected` | QR 무효/주문 없음/취소 주문/세대 불일치 | 원인 해결 후 재요청 |

### 주문 상태

`미수령` / `처리중(기기ID, 시작시각)` / `수령됨` / `확인필요`

## 6. 오류 코드

| 코드 | 의미 |
|---|---|
| `STALE_DATASET` | dataset_generation 불일치 — 재동기화 필요 |
| `AUTH_REQUIRED` | 윗치폼 로그인 필요 |
| `INVALID_QR` | 윗치폼 QR URL 형식이 아님 |
| `RUNTIME_UNAVAILABLE` | PC 티켓 확인 런타임이 연결되지 않음 |
| `ORDER_NOT_FOUND` | 운영 XLSX에 주문 없음 |
| `ORDER_CANCELLED` | 취소된 주문 (`주문취소`, `자동주문취소`) |
| `DUPLICATE_IN_PROGRESS` | 다른 기기/요청이 같은 주문 처리 중 |
| `INVALID_REQUEST` | 필드 누락/형식 오류 |
| `EXPIRED_JOIN_CODE` | 페어링 코드 만료/사용됨 |
| `PAIRING_LOCKED` | 참가 코드 연속 실패로 60초 잠금 — 잠시 후 재시도 |
| `SERVER_BUSY` | 동시 스캔 상한 초과 (HTTP 429) — 잠시 후 재시도 |
| `UNAUTHORIZED` | 토큰 없음/폐기됨 |

## 7. 불변 규칙 (계약 수준)

1. 클릭 성공 ≠ 윗치폼 수령 확인 ≠ XLSX 저장 성공 — 3단계 분리.
2. `needs_reconciliation`은 자동 재시도/재클릭 대상이 아니다. 대조는 읽기 전용 재조회.
3. 폰은 응답 유실만으로 재요청을 보내지 않는다 — `GET /actions/{request_id}`로 먼저 복구.
4. PC의 브라우저 쿠키를 폰으로 전송하지 않는다. `/v1/scan`은 PC의 로그인 세션을 사용한다.
5. QR 문자열로 임의 URL 이동 금지 — `qrcode_link.php` 접두사 + 주문 상세 경로 검증 후에만 처리.
6. 응답의 연락처/이름은 마스킹 규칙 적용(기존 `_mask_*` 기준).
7. XLSX는 PC만 쓴다. 폰의 파일 업로드/병합 경로는 없다.

## 8. 버저닝

- URL의 `/v1/`이 계약 버전. 필드 추가는 하위호환, 의미 변경은 `/v2/`.
- 계약 문서 변경 시 양쪽 저장소에 같은 버전으로 동기 갱신.
