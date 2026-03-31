from __future__ import annotations

import argparse
from pathlib import Path

from flet.__pyinstaller.win_utils import update_flet_view_icon


def _find_flet_view_executables(bundle_dir: Path) -> list[Path]:
    return sorted(path for path in bundle_dir.rglob("flet.exe") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch the embedded Flet viewer icon inside a PyInstaller bundle.",
    )
    parser.add_argument(
        "bundle_dir",
        nargs="?",
        default="dist/Ticket_AUTO",
        help="PyInstaller one-folder bundle directory",
    )
    parser.add_argument(
        "icon_path",
        nargs="?",
        default="Resources/app.ico",
        help="ICO file to copy into the embedded Flet viewer",
    )
    args = parser.parse_args()

    bundle_dir = Path(args.bundle_dir).resolve()
    icon_path = Path(args.icon_path).resolve()

    if not bundle_dir.exists():
        print(f"Bundle not found: {bundle_dir}")
        return 1
    if not icon_path.exists():
        print(f"Icon not found: {icon_path}")
        return 1

    targets = _find_flet_view_executables(bundle_dir)
    if not targets:
        print(f"No embedded flet.exe found under: {bundle_dir}")
        return 1

    for target in targets:
        print(f"Updating icon: {target}")
        update_flet_view_icon(str(target), str(icon_path))

    print(f"Updated {len(targets)} embedded Flet executable(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
