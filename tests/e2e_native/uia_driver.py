"""pywinauto UIA + PostMessage로 Flet 네이티브 창을 구동하는 헬퍼.

실측으로 확인된 Flutter Windows 특성:
- UIA descendants()로 시맨틱 트리(이름·control_type·좌표·포커스)를 읽을 수 있다.
- UIA 패턴(Value/Invoke)은 미구현이다.
- 물리 클릭 대신 FLUTTERVIEW에 WM_LBUTTONDOWN/UP를 PostMessage로 보내면
  커서·키보드를 점유하지 않고 클릭이 전달된다.
- 텍스트 입력은 첫 문자 후 포커스가 빠지므로 문자마다 재클릭한다.
  문자는 WM_CHAR로 보내면 한글까지 전달된다.
- Flet 리빌드로 요소가 재생성되므로 매 조회마다 descendants()를 새로 읽는다.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

pywinauto = pytest.importorskip("pywinauto", reason="pywinauto 미설치 환경")
import win32api  # noqa: E402
import win32con  # noqa: E402
import win32gui  # noqa: E402
from pywinauto import Desktop  # noqa: E402

# 대상이 동기 입력(SendMessage/WM_CHAR 등)을 처리 중이면 UIA cross-process 호출이
# 0x8001010d(RPC_E_CANTCALLOUT_ININPUTSYNCCALL) 등으로 즉시 실패한다.
# 이 HRESULT는 일시적이라 짧게 재시도하면 수렴한다.
_TRANSIENT_UIA_HRESULTS = {-2147417843, -2147417846}


def _retry_uia(func, *, attempts: int = 4, delay: float = 0.3):
    """일시적 UIA COM 오류를 재시도로 흡수한다. 비일시 오류는 그대로 올린다."""
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:
            hresult = getattr(exc, "hresult", None)
            if hresult is None:
                args = getattr(exc, "args", ())
                hresult = args[0] if args else None
            if hresult not in _TRANSIENT_UIA_HRESULTS or attempt == attempts - 1:
                raise
            time.sleep(delay)


class NativeWindowDriver:
    """네이티브 Flet 창 하나를 UIA로 관측하고 메시지로 조작한다."""

    def __init__(self, window: Any, desktop: Any, title: str) -> None:
        self._desktop = desktop
        self._title = title
        # hwnd 기준 바인딩: 런타임 전환 등으로 창 제목이 바뀌어도 같은 창을 찾는다.
        # 창이 재생성돼 hwnd가 무효화되면 _window_spec()에서 제목으로 재바인딩한다.
        self._hwnd = int(window.wrapper_object().handle)
        self._window = desktop.window(handle=self._hwnd)
        self._hwnd_view = self._resolve_view_hwnd()

    def _window_descendants(self) -> list:
        """창의 descendants — hwnd 무효화(재생성/제목 변경) 시 제목으로 재바인딩한다."""
        from pywinauto.findwindows import ElementNotFoundError

        try:
            return _retry_uia(self._window.descendants)
        except ElementNotFoundError:
            window = self._desktop.window(title=self._title)
            window.wait("exists", timeout=5)
            self._hwnd = int(window.wrapper_object().handle)
            self._window = self._desktop.window(handle=self._hwnd)
            return _retry_uia(self._window.descendants)

    def _resolve_view_hwnd(self) -> int:
        """FLUTTERVIEW는 재생성될 수 있어 매번 새로 해석한다."""
        pane = self._window.child_window(class_name="FLUTTERVIEW")
        pane.wait("exists", timeout=5)
        return pane.handle

    @property
    def hwnd(self) -> int:
        """최상위 창 핸들 — 와치독의 프로세스 창 감시용."""
        return self._hwnd

    @classmethod
    def wait_for_window(cls, title: str, timeout: float = 30.0) -> "NativeWindowDriver":
        desktop = Desktop(backend="uia")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                window = desktop.window(title=title)
                if window.exists(timeout=1):
                    return cls(window, desktop, title)
            except Exception:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"네이티브 창을 찾지 못했습니다: {title}")

    def focus(self) -> None:
        """스크린샷 등 관측용 전면 활성화(입력은 PostMessage라 무관)."""
        self._window.set_focus()
        time.sleep(0.3)

    def elements(self, control_type: str | None = None) -> list:
        result = []
        for d in self._window_descendants():
            try:
                if control_type is None or d.element_info.control_type == control_type:
                    result.append(d)
            except Exception:
                continue
        return result

    def find(
        self,
        control_type: str,
        name_part: str | None = None,
        *,
        exact: bool = False,
    ):
        for d in self.elements(control_type):
            try:
                name = d.window_text()
            except Exception:
                continue
            if name_part is None or (name == name_part if exact else name_part in name):
                return d
        return None

    def texts(self) -> list[str]:
        """모든 시맨틱 요소의 이름을 반환한다(버튼명도 포함)."""
        result = []
        for d in self.elements():
            try:
                name = d.window_text()
                if name:
                    result.append(name)
            except Exception:
                continue
        return result

    def has_text(self, name_part: str, value_part: str | None = None) -> bool:
        for text in self.texts():
            if name_part not in text:
                continue
            if value_part is None or value_part in text:
                return True
        return False

    def wait_for_text(
        self, name_part: str, value_part: str | None = None, timeout: float = 15.0
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.has_text(name_part, value_part):
                return
            time.sleep(0.4)
        raise AssertionError(
            f"텍스트 대기 시간 초과: {name_part!r} / {value_part!r}. 현재 텍스트={self.texts()}"
        )

    def wait_for_tree(self, min_elements: int = 8, timeout: float = 30.0) -> None:
        """Flet 리빌드/브리지 재활성화로 시맨틱 트리가 통째로 빠지는 구간을 흡수한다."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.elements()) >= min_elements:
                return
            time.sleep(0.4)
        raise AssertionError(
            f"시맨틱 트리 대기 시간 초과: 요소 {len(self.elements())}개. "
            f"minimized={self._window.is_minimized()}"
        )

    def wait_for_element(
        self,
        control_type: str,
        name_part: str,
        *,
        exact: bool = False,
        timeout: float = 20.0,
    ):
        """Flet 리빌드 동안 요소가 잠시 빠질 수 있어 존재할 때까지 폴링한다."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            element = self.find(control_type, name_part, exact=exact)
            if element is not None:
                return element
            time.sleep(0.4)
        names = [d.window_text() for d in self.elements(control_type)]
        raise AssertionError(
            f"요소 대기 시간 초과: {control_type} {name_part!r} (exact={exact}). "
            f"현재 {control_type} 목록={names}, 텍스트={self.texts()}"
        )

    # --- PostMessage 입력 (물리 커서·키보드 미점유) ---

    def _post_click_at(self, x: int, y: int) -> None:
        hwnd = self._resolve_view_hwnd()
        lparam = win32api.MAKELONG(x, y)
        win32gui.PostMessage(
            hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam
        )
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
        time.sleep(0.4)

    def _post_char(self, ch: str) -> None:
        win32gui.PostMessage(
            self._resolve_view_hwnd(), win32con.WM_CHAR, ord(ch), 0
        )
        time.sleep(0.15)

    def press_escape(self) -> None:
        """열린 팝업/메뉴를 해제한다. Flutter는 하드웨어 키 메시지로 처리한다."""
        hwnd = self._resolve_view_hwnd()
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
        time.sleep(0.3)

    def is_element_enabled(self, element: Any) -> bool:
        """UIA 일시 오류를 재시도로 흡수한 enabled 조회."""
        return bool(_retry_uia(lambda: element.is_enabled()))

    def click_element(self, element: Any) -> None:
        rect = _retry_uia(element.rectangle)
        x, y = win32gui.ScreenToClient(
            self._resolve_view_hwnd(),
            ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2),
        )
        self._post_click_at(x, y)

    def click(self, control_type: str, name_part: str, *, exact: bool = False) -> None:
        element = self.wait_for_element(control_type, name_part, exact=exact)
        self.click_element(element)

    def type_text(self, edit: Any, text: str) -> None:
        """첫 문자 입력 후 포커스가 빠지는 Flutter 특성상 문자마다 재클릭한다."""
        for ch in text:
            self.click_element(edit)
            self._post_char(ch)

    def type_in_edit(self, name_part: str, text: str) -> None:
        edit = self.find("Edit", name_part)
        if edit is None:
            raise AssertionError(f"Edit을 찾지 못했습니다: {name_part!r}")
        self.type_text(edit, text)
