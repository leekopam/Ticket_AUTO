---
name: quality-review-loop
description: 시도 이력, 사용자 피드백, QR 체크리스트, 컨텍스트 압축, 자체 자동 테스트를 작업 완료 루프에 강제 적용한다.
---

# Quality Review Loop

## 목적

작업이 길어질 때 같은 실패를 반복하거나, 사용자의 피드백을 잊거나, 검증 없이 완료 선언하는 문제를 줄인다.

## 반드시 읽을 파일

1. `AGENTS.md`
2. `docs/ai-worklog/attempt-log.md`
3. `docs/ai-worklog/user-feedback.md`
4. `docs/ai-worklog/context-summary.md`
5. `docs/qa/QR_CHECKLIST.md`

## 실행 절차

1. 기존 시도 이력과 사용자 피드백을 확인한다.
2. 이번 작업의 목표와 금지 조건을 다시 정리한다.
3. 구현 또는 수정 후 자동 테스트를 실행한다.
4. QR 체크리스트를 기준으로 누락을 찾는다.
5. 의미 있는 성공/실패/부분 성공/롤백을 attempt-log에 기록한다.
6. 사용자의 새 피드백이 있으면 user-feedback에 반영한다.
7. 문서가 길거나 중복되면 context-summary로 압축한다.
8. 최종 보고에 자동 테스트 결과와 미확인 항목을 포함한다.

## 보고 형식

```md
### 시도/피드백 업데이트
- attempt-log 업데이트:
- user-feedback 업데이트:
- context-summary 업데이트:

### QR 체크리스트
- 통과:
- 실패/미확인:
- 자동 테스트 결과:
```
