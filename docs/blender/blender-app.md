# Blender Application Control

How to launch, restart, kill, and run multiple Blender instances for the Printable MCP. The Blender backend requires a running Blender app with the addon enabled — the MCP server connects via TCP to that running instance.

## Launch

```bash
# Windows (in background)
start "" "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe"

# macOS
open -a Blender

# Linux
blender &
```

After launch, wait ~5–8 seconds before connecting — the addon needs time to initialize.

## Kill and restart

```bash
# Windows
taskkill /IM blender.exe /F
sleep 2
start "" "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe"

# macOS / Linux
pkill -f blender
sleep 2
blender &
```

After reinstalling the addon (`python install.py`), Blender must be **fully restarted** — Python modules are not hot-reloaded. No need to manually re-enable the addon if it was already enabled.

## Suppress the splash screen

The splash blocks the scene and confuses rendering. Suppress via the MCP:

```python
# Call via blender_execute_code
import bpy
bpy.context.preferences.view.show_splash = False
```

Persistent across restarts (saved to user preferences).

## One Blender, multiple MCP clients (default — start here)

Since v0.1.4, **a single Blender instance handles multiple MCP clients on the same port**. Claude Desktop, Claude Code, Goose — all of them can have `printable` enabled at the same time and they won't lock each other out. Each client opens a short-lived TCP connection per command and closes it when done; the addon's accept loop picks up the next waiting client. Commands still serialize on Blender's main thread (one execution at a time, that hasn't changed), but the *connect-time* contention is gone.

**You almost certainly don't need multi-instance Blender.** Only reach for it when one of these is true:

- You want **independent scenes** that shouldn't see each other's geometry (e.g., agent A drafts a wheel in one scene, agent B drafts an axle in another, then a third assembly step imports both STLs into a clean scene).
- The **command queue throughput** of a single Blender main thread is the bottleneck — i.e., agents are spending more time waiting in line than working. Rare in practice for FDM-print modeling.

If neither applies, run one Blender on the default port `9876` and let multiple clients share it.

## Multiple Blender instances (true parallel scene work)

Each instance needs a different TCP port. The addon reads its port from Blender preferences.

### Launch with a specific port

```bash
# Instance 1 (default port 9876)
start "" "blender.exe"

# Instance 2 (port 9877)
start "" "blender.exe" --python-expr "import bpy; bpy.app.timers.register(lambda: setattr(bpy.context.preferences.addons['printable_blender'].preferences, 'port', 9877) or 0.0, first_interval=2.0)"
```

Or pass a startup script for cleaner syntax:

```python
# startup_9877.py — pass as: blender.exe --python startup_9877.py
import bpy

def set_port():
    prefs = bpy.context.preferences.addons.get('printable_blender')
    if prefs:
        prefs.preferences.port = 9877
    return None  # run once

bpy.app.timers.register(set_port, first_interval=3.0)
```

### Connect the MCP server to multiple instances

In `server.py`, instantiate multiple `BlenderConnection` objects:

```python
blender_9876 = BlenderConnection(port=9876)
blender_9877 = BlenderConnection(port=9877)
blender_9878 = BlenderConnection(port=9878)
```

### Use case: parallel agent teams

Each instance has its own independent scene. Agents can work simultaneously without TCP serialization. Merge results via `blender_export_stl` + `blender_import_stl`:

1. Agent A on :9876 → exports `part_a.stl`
2. Agent B on :9877 → exports `part_b.stl`
3. Assembly agent on :9878 → imports both, positions, validates

## Multiple windows vs multiple instances

| | Multiple windows | Multiple instances |
|---|---|---|
| How | Window > New Window | Separate `blender.exe` processes |
| Scene | Shared (same data) | Independent |
| TCP port | Same (one addon) | Different per instance |
| Parallel work | No | Yes |
| Use for | Extra viewpoints | Parallel agent teams |

## Verify connection

```python
# Via blender_execute_code — check which instance you're talking to
import bpy
print(f"Port: {bpy.context.preferences.addons['printable_blender'].preferences.port}")
print(f"Objects: {[o.name for o in bpy.data.objects]}")
```

## Reload addon without restarting (development only)

```python
# Via blender_execute_code — force module reload after installing new code
import importlib
from printable_blender import handlers, utils
importlib.reload(utils)
importlib.reload(handlers)
print("Modules reloaded")
```

The `bl_info` version may still show the old number after reload, but the running code is current.
