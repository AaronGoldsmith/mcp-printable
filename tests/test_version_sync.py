"""Guard: the Blender addon's bl_info version must match the package version.

The addon ships as bare .py files inside Blender's Python, so its version can't
be derived from package metadata at runtime — it's a static tuple that drifts
silently if you bump pyproject.toml and forget the addon. This test fails loudly
when that happens. Fix with: python scripts/sync_addon_version.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from sync_addon_version import addon_version, package_version  # noqa: E402


def test_addon_version_matches_pyproject():
    pkg = package_version()
    addon = addon_version()
    assert addon == pkg, (
        f"addon bl_info version {addon} != pyproject version {pkg}. "
        "Run: python scripts/sync_addon_version.py"
    )
