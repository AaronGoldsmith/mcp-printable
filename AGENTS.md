# AGENTS.md

Cross-agent instructions for working with **Printable** — an MCP server for AI-driven 3D modeling, optimized for FDM-printable geometry.

This file is the entry point for any AI coding agent (Codex, Cursor, Goose, Sourcegraph Amp, JetBrains AI, etc.). Claude Code reads it too, plus the project-specific `CLAUDE.md` if present.

For human-facing setup and overview, see [README.md](README.md).

## What this project ships

- An MCP server (`server.py`) exposing **30 tools** across two backends:
  - **Blender** (`blender_*`, 25 tools) — TCP to a bundled Blender addon. Requires Blender 3.6+ + addon enabled.
  - **OpenSCAD** (`scad_*`, 5 tools) — shells out to the `openscad` CLI. Requires OpenSCAD installed.
- Domain-knowledge **MCP resources** under `printable://…` URIs (5 of them).
- **Always-on rules** baked into the MCP server's `instructions` field — every connecting agent sees them automatically on `initialize`.

## Required: image-capable model

Many tools (`blender_render_*`, `blender_cross_section*`, `blender_get_screenshot`, `scad_render_views`, `scad_cross_section`) return base64 PNGs that the model is expected to *look at* — that's how the design loop catches shape-level mistakes the geometry math doesn't. If you're a text-only model, you'll receive the images but be unable to interpret them; treat that as a hard limitation and tell the user up front rather than continuing without visual feedback.

## Always-on rules

These are also embedded in the MCP `instructions` field, but if your agent doesn't surface that, here they are directly:

### Blender
- Start every modeling task with `blender_clear_scene` — it auto-sets units to mm. Building at the default 1m scale produces zero-volume meshes that pass watertight checks but are useless.
- Use `blender_boolean` for boolean operations. **Never** use `bpy.ops.object.join()` (creates internal faces) or raw `bpy` boolean modifiers in `execute_code` (failures are silent).
- 1–3 operations per `blender_execute_code`, then `blender_mesh_health` to verify (watertight? face count sane? `connected_components == 1`?).
- Renders show silhouettes only — use `blender_cross_section_gallery` to verify internal geometry truth.
- For any joint/hinge, you **must** call `blender_check_clearance_sweep` before export.
- Run `blender_full_printability_check` on every part before `blender_export_stl`.

### OpenSCAD
- Use `$fn=24` during iterative design, raise to `60+` for final export.
- Cross-section verifies internal truth — `scad_render_views` shows the silhouette only.
- After `scad_compile`, **always** run `scad_validate_printability` on the STL. Watertight + winding-consistent + sane volume are the minimum bar.

### Design loop (both backends)

`plan` (compute coords → print → verify math) → `build` (small steps → mesh_health) → `verify` (renders + cross-sections) → `validate` (printability + clearance) → `export` (bundled STL).

For mechanism design (hinges, ball-sockets, snap fits, articulated chains), see the print-in-place doc or skill — backend-agnostic, applies to both Blender and OpenSCAD.

## Skills

This repo bundles four skills as Claude Code SKILL.md shims under [`.claude/skills/`](.claude/skills/). They're thin pointers into `docs/` content that's also exposed as MCP resources, so the underlying knowledge reaches your agent regardless.

- `print-in-place` — design rules for moving-parts mechanisms (hinges, ball-sockets, snap fits, living hinges). Read first before any moving-parts task.
- `blender-design-loop` — full plan→build→verify→validate→export workflow with failure-mode reference.
- `image-displacement` — turn a 2D image into 3D printable relief via PIL preprocessing + the Blender displace modifier.
- `blender-app` — launch / restart / multi-instance Blender setup.

**To make these auto-discoverable in your agent**, copy each skill directory into your agent's project-skills location:

| Agent | Skills location | How to install |
|---|---|---|
| Claude Code | `.claude/skills/` | already there — just clone & launch `claude` in the repo |
| Codex / Cursor / others that follow [agents.md](https://agents.md) | `.agents/skills/` (if supported) | `cp -r .claude/skills/* .agents/skills/` |
| Goose | uses [recipes](https://block.github.io/goose/docs/), not skills — read this file + the MCP resources directly | n/a |

If your agent doesn't load project-level skill files, you still get the same content via:
- The MCP `instructions` field (always-on rules, sent on `initialize`)
- MCP resources (`printable://design/print-in-place`, `printable://blender/design-loop`, etc.)
- Reading [`docs/`](docs/) directly from the filesystem

## MCP resources

Fetch via `resources/read` if your client supports it; otherwise read the file path.

| URI | File |
|---|---|
| `printable://design/print-in-place` | [docs/design/print-in-place.md](docs/design/print-in-place.md) |
| `printable://blender/design-loop` | [docs/blender/design-loop.md](docs/blender/design-loop.md) |
| `printable://blender/image-displacement` | [docs/blender/image-displacement.md](docs/blender/image-displacement.md) |
| `printable://blender/blender-app` | [docs/blender/blender-app.md](docs/blender/blender-app.md) |
| `printable://openscad/backend` | [docs/openscad/README.md](docs/openscad/README.md) |

## Connecting your agent

The MCP server runs as a stdio process. After `pip install mcp-printable` (or installing from source), the command is just `printable` — no args.

Example MCP server config:

```json
{
  "mcpServers": {
    "printable": {
      "command": "printable"
    }
  }
}
```

Full setup including the Blender addon: [SETUP.md](SETUP.md).
