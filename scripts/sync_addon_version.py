#!/usr/bin/env python3
"""Sync the Blender addon's ``bl_info["version"]`` to the package version.

The addon is copied into Blender's own Python as bare ``.py`` files, where
``mcp-printable`` is not pip-installed — so ``bl_info["version"]`` cannot be
derived from package metadata at runtime and must be a static tuple Blender can
parse. This script keeps that tuple in lockstep with ``pyproject.toml`` so the
two never drift.

Usage:
    python scripts/sync_addon_version.py            # rewrite addon to match pyproject
    python scripts/sync_addon_version.py --check     # exit 1 if out of sync (CI/test)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
ADDON_INIT = REPO_ROOT / "addon" / "__init__.py"

# Matches:  "version": (0, 2, 0),
_VERSION_TUPLE_RE = re.compile(
    r'("version"\s*:\s*)\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)'
)
# Matches the [project] table's:  version = "0.2.0"
# Regex rather than tomllib so this runs on Python 3.10 (requires-python =
# ">=3.10"); tomllib is stdlib only on 3.11+.
_PYPROJECT_VERSION_RE = re.compile(
    r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', re.MULTILINE
)


def package_version() -> tuple[int, int, int]:
    """Read ``version`` from pyproject.toml as an (x, y, z) tuple."""
    m = _PYPROJECT_VERSION_RE.search(PYPROJECT.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(
            f'Could not find a plain `version = "X.Y.Z"` line in {PYPROJECT}; '
            "update sync_addon_version.py if you adopt pre-release tags."
        )
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def addon_version() -> tuple[int, int, int]:
    """Read the ``bl_info['version']`` tuple from addon/__init__.py."""
    m = _VERSION_TUPLE_RE.search(ADDON_INIT.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f'Could not find a bl_info "version" tuple in {ADDON_INIT}')
    return (int(m.group(2)), int(m.group(3)), int(m.group(4)))


def sync(check_only: bool = False) -> int:
    pkg = package_version()
    addon = addon_version()
    if pkg == addon:
        print(f"addon bl_info version already matches pyproject: {pkg}")
        return 0

    if check_only:
        print(
            f"VERSION MISMATCH: pyproject={pkg} but addon bl_info={addon}.\n"
            "Run: python scripts/sync_addon_version.py",
            file=sys.stderr,
        )
        return 1

    text = ADDON_INIT.read_text(encoding="utf-8")
    new_text = _VERSION_TUPLE_RE.sub(
        lambda m: f"{m.group(1)}({pkg[0]}, {pkg[1]}, {pkg[2]})", text, count=1
    )
    ADDON_INIT.write_text(new_text, encoding="utf-8")
    print(f"updated addon bl_info version {addon} -> {pkg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(sync(check_only="--check" in sys.argv[1:]))
