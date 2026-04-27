# Setup

End-to-end install for the Printable MCP server + Blender addon, plus the gotchas that aren't obvious until they bite.

## 1. Install dependencies

```bash
uv sync                  # or: pip install .
```

## 2. Install the Blender addon — from a terminal

```bash
python install.py
```

This copies `addon/` to `<blender-config>/scripts/addons/printable_blender/`.

> [!WARNING]
> **Do NOT use Blender's `Edit > Preferences > Add-ons > Install from Disk`** on `install.py`.
> That dialog treats the file you point at as the addon itself, copies the lone `install.py`
> into `scripts/addons/`, and then registers nothing because `install.py` has no `bl_info`.
> You'll see a misleading **`Modules Installed ()`** popup (note the empty parens) and the
> TCP server will never start.
>
> Always run `python install.py` from a shell in the project root.

After install, verify the directory exists:

```
<blender-config>/scripts/addons/printable_blender/
  __init__.py
  handlers.py
  utils.py
```

If you instead see `install.py` and/or `addon.py` loose in `scripts/addons/`, the GUI-install
pitfall above happened. Delete those stray files and re-run `python install.py` from the terminal.

## 3. Enable the addon in Blender

`Edit > Preferences > Add-ons` → search **`Printable`** → tick **"Printable Blender Bridge"**.

You should see this in Blender's system console:

```
[Printable Bridge] Listening on 127.0.0.1:9876
```

## 4. Verify the TCP bridge

From any terminal:

```bash
python - <<'PY'
import socket, struct, json, uuid
s = socket.socket(); s.connect(('127.0.0.1', 9876))
msg = json.dumps({'id': str(uuid.uuid4()), 'command': 'get_scene_info', 'params': {}}).encode()
s.sendall(struct.pack('>I', len(msg)) + msg)
n = struct.unpack('>I', s.recv(4))[0]
print(json.loads(s.recv(n)))
PY
```

A successful response means the agent can talk to Blender.

## 5. Wire the MCP server into your agent

See [README.md](README.md#setup) for the per-agent config snippet.

---

## Reinstalling / updating the addon

Python modules don't hot-reload in Blender. After running `python install.py` again:

- **First-time install of a new module name** (e.g. you renamed the addon): toggling enabled in
  Preferences is enough — the new module loads fresh.
- **Updating an already-loaded addon**: you must **fully restart Blender**. Disabling and
  re-enabling the addon does *not* re-import the Python modules.

Save your `.blend` first if you want to keep the scene.

### Dev-mode hot-reload (advanced)

Inside the running Blender, via `blender_execute_code`:

```python
import importlib
from printable_blender import handlers, utils
importlib.reload(utils)
importlib.reload(handlers)
```

This reloads handler logic without a restart. The version shown in `bl_info` may still read
the old number, but the running code is current. Note: changes to `__init__.py` (the TCP server
itself) still require a full restart.

## "Missing Add-ons" warnings after a rename

If the addon was previously installed under a different module name (e.g. `claude_blender`)
and you've since renamed/replaced it (e.g. with `printable_blender`), Blender will show the
old name under **Preferences → Add-ons → Missing Add-ons** because it's still listed in
`userpref.blend` as previously enabled, but the directory no longer exists.

This is harmless. To clean it up: click the trash/X next to the missing entry, or just save
preferences after enabling the new addon — Blender will drop the dead reference on next load.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ConnectionRefusedError` on port 9876 | Addon not enabled, or addon errored on load — check Blender's system console |
| `Modules Installed ()` (empty parens) | You used Blender's "Install from Disk" on `install.py` — see [step 2](#2-install-the-blender-addon--from-a-terminal) |
| Addon enables but immediately disables | Python error during `register()` — open `Window > Toggle System Console` to read it |
| Code changes not taking effect | Module cache — restart Blender, or use the dev-mode reload above |
| Missing Add-ons shows `claude_blender` (or other old name) | Stale reference from a previous install — see above, harmless |
| TCP listening but commands hang | Blender is in a modal state (e.g. modal operator running, viewport editing) — main-thread timer is blocked |
