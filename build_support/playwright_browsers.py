from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


FROZEN_PLAYWRIGHT_BROWSER_DEST_ROOT = Path("playwright") / "driver" / "package" / ".local-browsers"


class PlaywrightBrowserBundleError(RuntimeError):
    """Raised when a PyInstaller build cannot bundle the required Playwright browser."""


def collect_playwright_browser_datas(
    *,
    package_root: str | Path | None = None,
    browsers_path: str | Path | None = None,
) -> list[tuple[str, str]]:
    """Return PyInstaller data entries for the Chromium revision Playwright expects.

    Playwright's frozen driver looks for browser executables below
    ``playwright/driver/package/.local-browsers``. ``collect_all('playwright')``
    includes the driver package but not the user's browser cache, so the spec must
    copy the matching ``chromium-<revision>`` directory into that frozen location.
    """
    resolved_package_root = _resolve_playwright_package_root(package_root)
    revision = _read_browser_revision(resolved_package_root, browser_name="chromium")
    browser_dir_name = f"chromium-{revision}"
    browser_dir = _resolve_browser_cache_root(
        package_root=resolved_package_root,
        browsers_path=browsers_path,
    ) / browser_dir_name
    _ensure_browser_cache_is_usable(browser_dir)

    destination = (FROZEN_PLAYWRIGHT_BROWSER_DEST_ROOT / browser_dir_name).as_posix()
    return [(str(browser_dir), destination)]


def _resolve_playwright_package_root(package_root: str | Path | None) -> Path:
    if package_root is not None:
        return Path(package_root)

    playwright_spec = importlib.util.find_spec("playwright")
    if playwright_spec is None or playwright_spec.origin is None:
        raise PlaywrightBrowserBundleError(
            "playwright 패키지를 찾을 수 없습니다. 프로젝트 가상환경에서 PyInstaller 빌드를 실행해주세요."
        )

    return Path(playwright_spec.origin).resolve().parent / "driver" / "package"


def _read_browser_revision(package_root: Path, *, browser_name: str) -> str:
    browsers_json = package_root / "browsers.json"
    if not browsers_json.exists():
        raise PlaywrightBrowserBundleError(f"Playwright browsers.json을 찾을 수 없습니다: {browsers_json}")

    try:
        payload: dict[str, Any] = json.loads(browsers_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlaywrightBrowserBundleError(f"Playwright browsers.json을 읽을 수 없습니다: {browsers_json}") from exc

    for browser in payload.get("browsers", []):
        if browser.get("name") != browser_name:
            continue
        revision = str(browser.get("revision") or "").strip()
        if revision:
            return revision

    raise PlaywrightBrowserBundleError(
        f"Playwright browsers.json에서 {browser_name!r} revision을 찾을 수 없습니다: {browsers_json}"
    )


def _resolve_browser_cache_root(
    *,
    package_root: Path,
    browsers_path: str | Path | None,
) -> Path:
    if browsers_path is not None:
        return Path(browsers_path).expanduser()

    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path:
        if env_path == "0":
            return package_root / ".local-browsers"
        return Path(env_path).expanduser()

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ms-playwright"
        return Path.home() / "AppData" / "Local" / "ms-playwright"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"

    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    cache_root = Path(xdg_cache_home).expanduser() if xdg_cache_home else Path.home() / ".cache"
    return cache_root / "ms-playwright"


def _ensure_browser_cache_is_usable(browser_dir: Path) -> None:
    if not browser_dir.exists():
        raise PlaywrightBrowserBundleError(
            "Playwright Chromium 브라우저 캐시를 찾을 수 없습니다: "
            f"{browser_dir}\n"
            "빌드 전에 프로젝트 가상환경에서 `python -m playwright install chromium`을 실행해주세요."
        )

    if os.name == "nt":
        chrome_exe = browser_dir / "chrome-win64" / "chrome.exe"
        if not chrome_exe.exists():
            raise PlaywrightBrowserBundleError(
                "Playwright Chromium 실행 파일을 찾을 수 없습니다: "
                f"{chrome_exe}\n"
                "브라우저 캐시가 손상되었을 수 있습니다. `python -m playwright install chromium`을 다시 실행해주세요."
            )
