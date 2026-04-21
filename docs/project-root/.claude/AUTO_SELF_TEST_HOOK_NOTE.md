# Claude 자동 테스트 Hook 예시

기존 `.claude/settings.json`에 hook을 운영 중이라면, 작업 종료 또는 파일 수정 후 다음 명령을 연결할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/harness/auto_self_test.ps1
```

주의:
- 기존 settings를 덮어쓰지 말고 병합하세요.
- 테스트가 오래 걸리는 프로젝트라면 모든 파일 수정마다 실행하지 말고 Stop/PostToolUse 중 적절한 시점에 제한적으로 실행하세요.
- Unity PlayMode까지 자동 실행하면 시간이 오래 걸릴 수 있으므로, 기본은 문서/QR 체크만 실행하고 필요할 때 `-Unity` 옵션을 쓰는 방식을 권장합니다.
```
