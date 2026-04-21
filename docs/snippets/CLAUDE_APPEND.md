## 작업 이력, 피드백, QR 체크리스트, 자체 자동 테스트

이 프로젝트에 `docs/ai-worklog/attempt-log.md`, `docs/ai-worklog/user-feedback.md`, `docs/ai-worklog/context-summary.md`, `docs/qa/QR_CHECKLIST.md`, `scripts/harness/auto_self_test.ps1`가 있으면 작업 완료 전 이를 사용한다.

- 의미 있는 성공/실패/부분 성공/롤백은 attempt-log에 기록한다.
- 사용자 피드백은 user-feedback에 반영한다.
- 문서가 길거나 중복되면 context-summary로 압축한다.
- QR_CHECKLIST로 자체 점검한다.
- 가능한 경우 auto_self_test.ps1을 실행하고, 미실행 시 이유를 보고한다.
