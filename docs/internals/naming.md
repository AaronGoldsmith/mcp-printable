# Naming conventions

Four distinct names live in this project. They look similar and have been a source of confusion — this doc lays out where each one is required and where it's freeform.

## The four names

| Name | Where it's defined | Why it has to be that |
|---|---|---|
| **`mcp-printable`** | `pyproject.toml` `[project] name` | The PyPI package name. What `pip install` consumes. Must match the trusted-publisher config on PyPI and the wheel filename. |
| **`printable`** | `pyproject.toml` `[project.scripts]` (the `printable = "server:main"` entry) | The CLI command name. What ends up on `PATH` after `pip install` and what users put as `command:` in their MCP client config. Also the entry-script invoked by `uvx --from mcp-printable printable`. |
| **`printable_blender`** (underscore) | (a) `server.py` — `mcp = FastMCP("printable_blender", ...)` → reported in `serverInfo.name` to MCP clients on `initialize`. (b) `addon/` Blender addon module — installed as `<blender-config>/scripts/addons/printable_blender/`, registered as `bl_idname = __package__`. | Internal plumbing. Renaming the FastMCP name is cosmetic but would change `serverInfo.name`. Renaming the addon module name **breaks every existing installed addon** — users would have to re-enable. Avoid renaming until the next major. |
| **MCP server entry key** in user's `mcpServers: {...}` config | Up to the user. We recommend just `"printable"` for consistency with the package + CLI names. Earlier versions of the README suggested `"printable-blender"` which created the misleading impression of a separate `"printable-openscad"` server — there isn't one; both backends live in the single `mcp-printable` server. | Freeform. |

## Quick rules

- **User-facing copy (README, AGENTS.md, examples)** → use `printable` for the entry key.
- **PyPI / install commands** → `mcp-printable`.
- **CLI invocation** → `printable` (or `uvx --from mcp-printable printable`).
- **`serverInfo.name` and the addon module name stay `printable_blender`** until a deliberate v0.x → v1.0 cleanup.

## If you do want to fully unify someday

A coordinated rename of the internal `printable_blender` to `printable` would touch:

- `server.py:32` — `FastMCP("printable_blender", ...)` → `FastMCP("printable", ...)`
- `tests/test_server.py` — assertion on `mcp.name`
- `addon/__init__.py` `bl_info["name"]` and the directory name `addon/` → would also need `install.py` `ADDON_DIR_NAME` updated, plus migration logic to remove the old `printable_blender/` addon dir before installing the new one
- Filename references like `printable_blender_scene.blend`, `printable_blender_checkpoint.blend` (used as temp/checkpoint names in the addon)
- Documentation references in `SETUP.md`, `docs/blender/blender-app.md`

It's a 1–2 hour cleanup with a real user-impact migration in the addon piece. Defer until there's a reason beyond cosmetics.
