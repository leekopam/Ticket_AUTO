@AGENTS.md

# CLAUDE.md — Claude Code 프로젝트 지침

이 파일은 Claude Code가 이 저장소에서 작업할 때 `AGENTS.md`를 프로젝트 기준 문서로 사용하도록 연결한다.  
프로젝트의 실제 규칙, Skill Routing, 하네스, Godfield 훈련모드 전용 규칙, 긴 반복 프롬프트 자동 해석 규칙, 시도 이력/사용자 피드백/QR 체크리스트/자동 테스트 규칙은 `AGENTS.md`가 정본이다.

## Claude Code 전용 운영 원칙

1. 모든 작업은 `AGENTS.md`를 기준으로 수행한다.
2. 사용자가 “계속 진행해”, “이어서 진행해”, “서브에이전트 활용해서 계속해”, “Unity에서 구현하고 스크린샷 비교하면서 계속해”라고 말하면 `AGENTS.md`의 `CONTINUOUS_DEV_LOOP` 규칙을 실행한다.
3. 긴 작업은 Plan → Patch → Verify → Update → Report 순서로 진행한다.
4. 작업 종료 전 가능한 경우 `scripts/harness/auto_self_test.ps1`을 실행한다.
5. 의미 있는 성공/실패/부분 성공/롤백은 `docs/ai-worklog/attempt-log.md`에 기록한다.
6. 사용자의 반복 피드백, 선호, 금지 조건은 `docs/ai-worklog/user-feedback.md`에 반영한다.
7. 문서가 길어지거나 중복이 누적되면 `docs/ai-worklog/context-summary.md`로 압축한다.
8. 작업 완료 전 `docs/qa/QR_CHECKLIST.md`를 기준으로 자체 점검한다.
9. 서브에이전트는 분석, 리뷰, 시각 QA, 모드 격리 검사에 사용하고, 실제 구현은 메인 에이전트 하나가 담당한다.
10. 실제로 실행하지 않은 테스트나 Unity 검증을 실행했다고 말하지 않는다.

## 기본 보고 형식 추가

작업 종료 시 기존 진행 브리핑에 아래 항목을 포함한다.

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
