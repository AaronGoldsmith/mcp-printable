---
name: blender-app
description: "Use when you need to open, restart, kill, or manage Blender application instances — including launching multiple instances on different ports when one Blender's serial command queue isn't fast enough for parallel scene work. For modeling techniques, use more specific skills like print-in-place. For any task involving launching Blender, restarting after a crash, or suppressing the splash screen."
---

This skill is a Claude-specific shim. Full guidance is in [`docs/blender/blender-app.md`](../../../docs/blender/blender-app.md).

Quick reference:
- After `python install.py` or `printable-install-addon`, fully restart Blender (Python modules don't hot-reload)
- Wait ~5–8 seconds after launch before connecting (addon needs to initialize)
- **Multiple MCP clients can share one Blender** (since v0.1.4 — connections are short-lived per command, addon's listen backlog absorbs them). Don't reach for multi-instance just to support a second client.
- **Multiple Blender INSTANCES** are only needed when you want truly independent scenes / parallel scene-level work — and only then do they need different TCP ports (set via `--python-expr` startup script). See the doc for the launch recipe.
- Splash screen suppression: `bpy.context.preferences.view.show_splash = False` (persistent)
