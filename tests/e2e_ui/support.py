"""L2/L3 UI E2E용 런타임 스텁·제어 서버 재수출 + Playwright 탐색 헬퍼.

하니스 런타임(스텁 런타임, 스텁 프린터, 제어 서버)은 패키징 exe와 공유를 위해
프로젝트 루트의 `e2e_harness` 모듈로 이동했다. 이 파일은 기존 테스트의
`e2e_ui.support` import 경로를 유지하기 위한 재수출과 Playwright 전용
헬퍼만 담는다.
"""
from __future__ import annotations

from e2e_harness import (  # noqa: F401 - 재수출
    FakeDashboardRuntimeApp,
    fetch_printer_jobs,
    fetch_runtime_calls,
    find_free_port,
    run_control_server,
    send_control_command,
    wait_for_port,
)


# --- Playwright 탐색 헬퍼 (브라우저 측, exe에 포함되지 않음) ---


def semantics_leaf(page, text: str):
    """주어진 텍스트를 포함하는 최하위 flt-semantics 노드를 선택한다.

    Flet은 Text.tooltip을 노드 텍스트에 병합하고 부모 노드가 자식 텍스트를
    합치므로, 상위 병합 노드와의 strict-mode 충돌을 피하기 위해 리프만 고른다.
    """
    return page.locator(
        f'flt-semantics:has-text("{text}"):not(:has(flt-semantics))'
    )


def wait_semantics_text(page, label: str, expected: str, timeout_ms: int = 10000) -> None:
    """라벨을 포함한 리프 semantics 노드 중 하나가 기대 문자열을 담을 때까지 폴링한다.

    같은 라벨을 포함하는 노드가 여럿일 수 있어 모든 후보를 검사한다.
    """
    import time

    deadline = time.monotonic() + timeout_ms / 1000.0
    last: list[str] = []
    while time.monotonic() < deadline:
        try:
            nodes = semantics_leaf(page, label).all()
            last = [node.inner_text(timeout=500) or "" for node in nodes]
        except Exception:
            last = []
        if any(expected in text for text in last):
            return
        time.sleep(0.2)
    raise AssertionError(
        f"[{label}] 텍스트 대기 시간 초과: expected={expected!r} last={last!r}"
    )


def wait_for_button(page, name: str, timeout_ms: int = 15000):
    """지정 이름의 버튼이 semantics 트리에 나타날 때까지 기다린다.

    Flet 0.25 클라이언트는 IconButton의 tooltip을 부모 semantics 노드의
    aria-label로 올리고 role=button 노드를 그 자식으로 분리한다.
    role/name 직접 매칭이 안 되는 아이콘 버튼은 라벨 노드의 자손 버튼을 찾는다.
    """
    import time

    direct = page.get_by_role("button", name=name, exact=True)
    wrapped = page.locator(
        f'flt-semantics[aria-label="{name}"] >> flt-semantics[role="button"]'
    )
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if direct.count() and direct.first.is_visible():
            return direct
        if wrapped.count() and wrapped.first.is_visible():
            return wrapped.first
        time.sleep(0.3)
    direct.wait_for(state="visible", timeout=3000)
    return direct
