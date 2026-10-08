# LAN 페어링 배포·장애 체크리스트

폰이 PC에 페어링되지 않을 때 위부터 순서대로 확인한다.
관련 계획서: `docs/tasks/pairing-lan-address-plan.md`, 계약: `docs/contracts/api-v1.md`

## 1. 네트워크 기본 조건

- [ ] 폰과 PC가 **같은 Wi-Fi/같은 서브넷**에 있는가?
  - 폰 Wi-Fi 상세에서 IP 확인 (예: PC `192.168.31.233`이면 폰도 `192.168.31.x`)
  - 폰이 모바일 데이터만 쓰거나 게스트 Wi-Fi에 있으면 연결 불가
- [ ] 공유기의 **AP/클라이언트 격리**가 꺼져 있는가?
  - 게스트망·일부 공유기는 단말 간 통신을 차단 — 격리 해제 또는 다른 SSID 사용
- [ ] VPN이 켜져 있지 않은가? (PC·폰 모두) — VPN이 LAN 트래픽을 우회시킴

## 2. PC 측

- [ ] QR의 `addr`에 **실제 LAN IP**가 실렸는가?
  - 기대: 물리 어댑터 IP (예 `192.168.31.233`)
  - 잘못된 사례: `172.x.x.x`(WSL), `192.168.56.x`(VirtualBox), `169.254.x.x`(APIPA)
  - v1.1부터 `order_serving_ips`가 자동 선택 + `alt_addrs` 후보 제공.
    그래도 실패하면 PowerShell로 확인:
    `Get-NetIPAddress -AddressFamily IPv4 | ? {$_.IPAddress -notlike '169.254*'}`
- [ ] **Windows 방화벽** — `ticket_auto_flat` 인바운드 허용 규칙이 현재 네트워크 프로필(Public/Private)에 적용되는가?
  - 현재 프로필 확인: `Get-NetConnectionProfile`
  - 프로필이 Private인데 규칙이 Public 전용이면 차단됨 → 규칙을 양쪽 프로필로 등록
- [ ] 포트가 다른 프로그램과 충돌하지 않는가? (서버 기동 실패 시 앱에 명시적 오류 표시됨)
- [ ] WSL·Docker·Hyper-V·VMware·VirtualBox 어댑터가 있어도 광고 주소는 자동 보정됨.
  가상 어댑터 비활성화는 임시 회피책일 뿐 근본 해결 아님

## 3. 폰 측

- [ ] PC 핫스팟 사용 시: 폰이 **PC 핫스팟에 직접 연결**됐는가? (핫스팟 켜면 `192.168.137.x`가 자동 1순위)
- [ ] 페어링 실패 문구로 원인 구분:
  | 문구 | 의미 | 조치 |
  |---|---|---|
  | 서버 응답 시간이 초과 | 주소에 도달 못함 | §1·§2 네트워크 확인 |
  | 서버에 연결할 수 없습니다 (시도한 주소: ...) | 모든 후보 주소 도달 실패 | 시도 주소 목록으로 어떤 IP를 광고했는지 확인 |
  | 서버 인증서가 일치하지 않습니다 | 다른 서버에 접속 or 인증서 재생성 | 같은 PC인지 확인 후 재페어링 |
  | 페어링이 거절/만료 | join_code 만료(10분) 또는 운영자 거절 | QR 새로고침 후 재시도 |
- [ ] 앱 재설치·데이터 삭제 후에는 반드시 재페어링 (토큰이 지워짐)

## 4. 검증 도구

- `python scripts/qa/packaged_e2e.py` — 패키징 exe에서 광고 주소 검증 + 실 TLS 페어링·스캔 자동 검증
- `python scripts/qa/emulator_link_e2e.py --serials <시리얼>` — 실기기/에뮬레이터 전체 플로우
- `.runtime/app.log` — 서버 기동 로그에 실제 광고 주소 기록
