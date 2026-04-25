---
name: blender-app
description: "Use when you need to open, restart, kill, or manage Blender application instances — including launching multiple instances on different ports for parallel agent work. For modeling techniques, use more specific skills like print-in-place. For any task involving launching Blender, restarting after a crash, suppressing the splash screen, or setting up multiple Blender instances for parallel agent teams."
---

This skill is a Claude-specific shim. Full guidance is in [`docs/blender/blender-app.md`](../../../docs/blender/blender-app.md).

Quick reference:
- After `python install.py`, fully restart Blender (Python modules don't hot-reload)
- Wait ~5–8 seconds after launch before connecting (addon needs to initialize)
- Multiple instances need different TCP ports — set via `--python-expr` startup script
- Splash screen suppression: `bpy.context.preferences.view.show_splash = False` (persistent)
