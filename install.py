#!/usr/bin/env python3
"""Install the Printable Blender addon into Blender's addon directory."""

import os
import sys
import shutil
import platform
import glob

ADDON_DIR_NAME = "printable_blender"

def find_blender_addon_paths():
    """Auto-detect Blender addon directories."""
    system = platform.system()
    paths = []

    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            # Blender stores per-version dirs: AppData/Roaming/Blender Foundation/Blender/X.Y/
            base = os.path.join(appdata, "Blender Foundation", "Blender")
            if os.path.isdir(base):
                for version_dir in sorted(glob.glob(os.path.join(base, "*.*")), reverse=True):
                    scripts = os.path.join(version_dir, "scripts", "addons")
                    paths.append(scripts)
    elif system == "Darwin":
        home = os.path.expanduser("~")
        base = os.path.join(home, "Library", "Application Support", "Blender")
        if os.path.isdir(base):
            for version_dir in sorted(glob.glob(os.path.join(base, "*.*")), reverse=True):
                scripts = os.path.join(version_dir, "scripts", "addons")
                paths.append(scripts)
    else:  # Linux
        home = os.path.expanduser("~")
        base = os.path.join(home, ".config", "blender")
        if os.path.isdir(base):
            for version_dir in sorted(glob.glob(os.path.join(base, "*.*")), reverse=True):
                scripts = os.path.join(version_dir, "scripts", "addons")
                paths.append(scripts)

    return paths


def install(target_path=None):
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "addon")

    if not os.path.isdir(source):
        print("ERROR: addon/ directory not found next to install.py")
        sys.exit(1)

    if target_path:
        paths = [target_path]
    else:
        paths = find_blender_addon_paths()

    if not paths:
        print("ERROR: Could not auto-detect Blender addon directory.")
        print("Run with explicit path:  python install.py <blender-addons-path>")
        print("Typical paths:")
        print("  Windows: %APPDATA%/Blender Foundation/Blender/4.4/scripts/addons")
        print("  macOS:   ~/Library/Application Support/Blender/4.4/scripts/addons")
        print("  Linux:   ~/.config/blender/4.4/scripts/addons")
        sys.exit(1)

    # Use the first (newest version) path found
    addon_dir = os.path.join(paths[0], ADDON_DIR_NAME)
    os.makedirs(paths[0], exist_ok=True)

    # Self-heal: clean up stray files left behind if someone previously used
    # Blender's "Install from Disk" on install.py itself (a known footgun
    # documented in SETUP.md). Those files have no bl_info, won't register,
    # and cause "Warning: add-on missing 'bl_info'" spam in the Blender console.
    for stray in ("install.py", "addon.py"):
        stray_path = os.path.join(paths[0], stray)
        if os.path.isfile(stray_path):
            os.remove(stray_path)
            print(f"Cleaned up stray file from prior bad install: {stray_path}")

    if os.path.exists(addon_dir):
        print(f"Removing existing addon at: {addon_dir}")
        shutil.rmtree(addon_dir)

    shutil.copytree(source, addon_dir)
    print(f"Addon installed to: {addon_dir}")

    # Blender 5.0+ expects extensions/user_default to exist even for legacy addons
    version_dir = os.path.dirname(os.path.dirname(addon_dir))  # e.g., .../Blender/5.0/
    extensions_default = os.path.join(version_dir, "extensions", "user_default")
    if not os.path.exists(extensions_default):
        os.makedirs(extensions_default, exist_ok=True)
        print(f"Created missing extensions dir: {extensions_default}")

    print()
    print("Next steps:")
    print("  1. Open Blender (restart if already running)")
    print("  2. Edit > Preferences > Add-ons")
    print('  3. Search for "Printable Blender" and enable it')
    print("  4. You should see '[Printable Bridge] Listening on 127.0.0.1:9876' in Blender's console")


def cli():
    """Console-script entry point for `printable-install-addon`."""
    target = sys.argv[1] if len(sys.argv) > 1 else None
    install(target)


if __name__ == "__main__":
    cli()
