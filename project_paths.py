from __future__ import annotations

from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parent
RESOURCE_DATA_DIR = Path("Resources") / "data"
RESOURCE_DATA_FILE = RESOURCE_DATA_DIR / "data.xlsx"
LEGACY_DATA_FILE = Path("data.xlsx")
RESOURCE_TEMPLATES_DIR = Path("Resources") / "templates"
RESOURCE_RECEIPT_TEMPLATE_FILE = RESOURCE_TEMPLATES_DIR / "receipt_layout.json"
RESOURCE_PRODUCT_TEMPLATE_FILE = RESOURCE_TEMPLATES_DIR / "product_receipt_layout.json"
RESOURCE_LEGACY_TPL_FILE = RESOURCE_TEMPLATES_DIR / "receipt_default.tpl"
LEGACY_TEMPLATES_DIR = Path("templates")


def resolve_project_path(path: str | Path) -> Path:
    """Resolve repo-relative paths against the project root."""
    target = Path(path)
    if target.is_absolute():
        return target
    if target.parts and target.parts[0] == "templates":
        target = RESOURCE_TEMPLATES_DIR.joinpath(*target.parts[1:])
    return PROJECT_ROOT / target


def resolve_managed_data_file_path() -> Path:
    """Return the canonical managed Excel data path used by the app."""
    return resolve_project_path(RESOURCE_DATA_FILE)


def resolve_managed_templates_dir_path() -> Path:
    """Return the canonical managed template directory path used by the app."""
    return resolve_project_path(RESOURCE_TEMPLATES_DIR)


def resolve_managed_receipt_template_path() -> Path:
    return resolve_project_path(RESOURCE_RECEIPT_TEMPLATE_FILE)


def resolve_managed_product_template_path() -> Path:
    return resolve_project_path(RESOURCE_PRODUCT_TEMPLATE_FILE)


def ensure_managed_templates_dir() -> Path:
    """Create the managed template directory and migrate legacy templates when needed."""
    target_dir = resolve_managed_templates_dir_path()
    target_dir.mkdir(parents=True, exist_ok=True)

    legacy_dir = PROJECT_ROOT / LEGACY_TEMPLATES_DIR
    if legacy_dir.exists():
        for source in legacy_dir.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(legacy_dir)
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
    if not target.exists() and legacy.exists():
        shutil.copy2(str(legacy), str(target))
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
