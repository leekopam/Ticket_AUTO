from __future__ import annotations

from pathlib import Path
import shutil
import sys


def _resolve_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_bundle_root(runtime_root: Path) -> Path:
    if not getattr(sys, "frozen", False):
        return runtime_root

    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    candidates.append(runtime_root / "_internal")
    candidates.append(runtime_root)

    for candidate in candidates:
        if (candidate / "Resources").exists():
            return candidate
    return runtime_root


PROJECT_ROOT = _resolve_runtime_root()
BUNDLE_ROOT = _resolve_bundle_root(PROJECT_ROOT)
RESOURCE_DATA_DIR = Path("Resources") / "data"
RESOURCE_DATA_FILE = RESOURCE_DATA_DIR / "data.xlsx"
RESOURCE_SOUND_DIR = Path("Resources") / "sound"
LEGACY_DATA_FILE = Path("data.xlsx")
RESOURCE_TEMPLATES_DIR = Path("Resources") / "templates"
RESOURCE_RECEIPT_TEMPLATE_FILE = RESOURCE_TEMPLATES_DIR / "receipt_layout.json"
RESOURCE_PRODUCT_TEMPLATE_FILE = RESOURCE_TEMPLATES_DIR / "product_receipt_layout.json"
RESOURCE_APP_ICON_FILE = Path("Resources") / "app.ico"
RESOURCE_LEGACY_TPL_FILE = RESOURCE_TEMPLATES_DIR / "receipt_default.tpl"
LEGACY_TEMPLATES_DIR = Path("templates")
LEGACY_SOUND_DIR = Path("sound")


def _normalize_relative_path(path: str | Path) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target
    if target.parts and target.parts[0] == "templates":
        target = RESOURCE_TEMPLATES_DIR.joinpath(*target.parts[1:])
    return target


def resolve_project_path(path: str | Path) -> Path:
    """Resolve repo-relative paths against the writable runtime root."""
    target = _normalize_relative_path(path)
    if target.is_absolute():
        return target
    return PROJECT_ROOT / target


def resolve_bundle_path(path: str | Path) -> Path:
    """Resolve repo-relative paths against bundled read-only assets."""
    target = _normalize_relative_path(path)
    if target.is_absolute():
        return target
    return BUNDLE_ROOT / target


def resolve_runtime_file_path(path: str | Path) -> Path:
    """Resolve a persisted file path against the writable runtime root when needed."""
    raw = str(path or "").strip()
    if not raw:
        return Path()
    target = Path(raw).expanduser()
    if target.is_absolute():
        return target
    return resolve_project_path(target)


def make_project_relative_path(path: str | Path) -> str:
    """Store project-managed file paths as portable relative paths when possible."""
    raw = str(path or "").strip()
    if not raw:
        return ""

    target = Path(raw).expanduser()
    if not target.is_absolute():
        return target.as_posix()

    try:
        resolved = target.resolve()
    except OSError:
        resolved = target

    candidate_roots: list[Path] = []
    for root in (PROJECT_ROOT, BUNDLE_ROOT):
        try:
            normalized_root = root.resolve()
        except OSError:
            normalized_root = root
        if normalized_root not in candidate_roots:
            candidate_roots.append(normalized_root)

    for root in candidate_roots:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return target.as_posix()


def resolve_managed_data_file_path() -> Path:
    """Return the canonical managed Excel data path used by the app."""
    return resolve_project_path(RESOURCE_DATA_FILE)


def resolve_managed_sound_dir_path() -> Path:
    """Return the canonical managed sound directory used by the app."""
    return resolve_project_path(RESOURCE_SOUND_DIR)


def resolve_managed_templates_dir_path() -> Path:
    """Return the canonical managed template directory path used by the app."""
    return resolve_project_path(RESOURCE_TEMPLATES_DIR)


def resolve_managed_receipt_template_path() -> Path:
    return resolve_project_path(RESOURCE_RECEIPT_TEMPLATE_FILE)


def resolve_managed_product_template_path() -> Path:
    return resolve_project_path(RESOURCE_PRODUCT_TEMPLATE_FILE)


def resolve_app_icon_path() -> Path:
    managed = resolve_project_path(RESOURCE_APP_ICON_FILE)
    if managed.exists():
        return managed
    bundled = resolve_bundle_path(RESOURCE_APP_ICON_FILE)
    if bundled.exists():
        return bundled
    return managed


def ensure_managed_sound_dir() -> Path:
    """Create the managed sound directory and seed bundled sounds when needed."""
    target_dir = resolve_managed_sound_dir_path()
    target_dir.mkdir(parents=True, exist_ok=True)

    legacy_dir = PROJECT_ROOT / LEGACY_SOUND_DIR
    bundled_dir = resolve_bundle_path(RESOURCE_SOUND_DIR)
    source_dirs: list[Path] = []
    if legacy_dir.exists():
        source_dirs.append(legacy_dir)
    if bundled_dir.exists() and bundled_dir != target_dir and bundled_dir not in source_dirs:
        source_dirs.append(bundled_dir)

    for source_dir in source_dirs:
        for source in source_dir.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(source_dir)
            destination = target_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(str(source), str(destination))
    return target_dir


def ensure_managed_templates_dir() -> Path:
    """Create the managed template directory and migrate legacy templates when needed."""
    target_dir = resolve_managed_templates_dir_path()
    target_dir.mkdir(parents=True, exist_ok=True)

    legacy_dir = PROJECT_ROOT / LEGACY_TEMPLATES_DIR
    bundled_dir = resolve_bundle_path(RESOURCE_TEMPLATES_DIR)
    source_dirs: list[Path] = []
    if legacy_dir.exists():
        source_dirs.append(legacy_dir)
    if bundled_dir.exists() and bundled_dir != target_dir and bundled_dir not in source_dirs:
        source_dirs.append(bundled_dir)

    for source_dir in source_dirs:
        for source in source_dir.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(source_dir)
            destination = target_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(str(source), str(destination))
    return target_dir


def ensure_managed_data_file() -> Path:
    """Create the managed data directory and migrate legacy root data.xlsx when needed."""
    target = resolve_managed_data_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    legacy = resolve_project_path(LEGACY_DATA_FILE)
    bundled = resolve_bundle_path(RESOURCE_DATA_FILE)
    if not target.exists():
        if legacy.exists():
            shutil.copy2(str(legacy), str(target))
        elif bundled.exists() and bundled != target:
            shutil.copy2(str(bundled), str(target))
    return target


def copy_data_file_to_managed_location(src_path: str | Path) -> Path:
    """Copy a user-selected Excel file into the managed app data location."""
    target = resolve_managed_data_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    source = Path(src_path)
    if source.resolve() == target.resolve():
        return target

    shutil.copy2(str(source), str(target))
    return target
