# Printable

**MCP server for AI-driven 3D modeling, optimized for FDM-printable geometry.**

Connect any MCP-capable agent (Claude Code, Goose, Cursor, Codex, Cline, etc.) to a 3D modeling backend and get tools tuned for the design loop that actually produces parts you can print.

## Why this exists

LLMs are good at writing geometry code but bad at the things that make a part actually printable: clearances on moving joints, overhangs, bridging, units, watertight meshes. Printable encodes those constraints into both the tool surface (typed booleans, printability checks, clearance sweeps, cross-sections) and the prose rules the agent reads (cardinal print-path rule, FDM clearance values, mechanism patterns).

The result: you can ask any MCP-aware agent "build me a hinge with 5mm barrel and 20×15mm flanges" and get a part that comes off the bed working.

## Backends

- **Blender** (`blender_*` tools, 25 of them). Full design loop with rendering, cross-sections, printability validation. Requires Blender 3.6+ installed and the included addon enabled.
- **OpenSCAD** (`scad_*` tools, 5 of them, see [`docs/openscad/README.md`](docs/openscad/README.md)). Code-first parametric backend. No app, no addon — shells out to the `openscad` CLI and uses [`trimesh`](https://github.com/mikedh/trimesh) for mesh validation. Requires OpenSCAD installed.

Cross-backend handoff happens via STL — both backends import and export it.

## Architecture

```
Agent  <--stdio/MCP-->  server.py  <--TCP :9876-->  Blender addon
                              \
                               +----shell-out------>  openscad CLI + trimesh
```

- `server.py` — FastMCP server. Exposes the tool surface and embeds the always-on rules in the MCP `instructions` field, with pointers into `docs/` for deeper guidance.
- `addon/` — Blender addon. TCP server on `127.0.0.1:9876`. Commands run on Blender's main thread via `bpy.app.timers`.
- `docs/` — agent-agnostic prose guidance: print-in-place rules, design loop, validated recipes. Also exposed as **MCP resources** under `printable://…` URIs (see [Documents](#documents) below) so any resource-aware MCP client can pull them via the protocol — no filesystem access required.
- `.claude/skills/` — thin Claude shims (description-triggered loading) that point into `docs/`. Other agents use resources or filesystem.
- `evals/` — policy-based regression tests that verify agents actually follow the rules. See [`evals/README.md`](evals/README.md).

## Setup

```bash
uv sync                        # MCP server + deps (or: pip install .)
python install.py              # Copies the addon into Blender's addon dir
```

Then in Blender: Preferences → Add-ons → enable **"Printable Blender Bridge"**.

Add to your agent's MCP config. For Claude Code:

```json
{
  "printable-blender": {
    "command": "python",
    "args": ["/path/to/printable/server.py"]
  }
}
```

(Key is namespaced so the planned `printable-openscad` backend can register alongside without collision.)

For other agents (Goose, Cursor, etc.) — wire it up using your agent's standard MCP server configuration.

## Tool families

### Blender (25 tools)

**Scene** — `blender_get_scene_info`, `blender_get_object_info`, `blender_clear_scene`, `blender_rename_object`

**Code** — `blender_execute_code` (arbitrary bpy/bmesh; auto-checkpoints), `blender_boolean` (typed UNION/DIFFERENCE/INTERSECT — *prefer this over execute_code*)

**Visual feedback** — `blender_get_screenshot`, `blender_render_tiled`, `blender_render_turntable`, `blender_cross_section`, `blender_cross_section_gallery`, `blender_render_printability_heatmap`, `blender_render_with_dimensions`, `blender_render_before_after`

**Print validation** — `blender_check_overhangs`, `blender_check_thin_walls`, `blender_check_clearance`, `blender_check_clearance_sweep`, `blender_check_intersection`, `blender_check_retention`, `blender_mesh_health`, `blender_full_printability_check`

**Export** — `blender_export_stl`, `blender_import_stl`, `blender_save_blend`

### OpenSCAD (5 tools)

`scad_compile`, `scad_render_views`, `scad_cross_section`, `scad_validate_printability`, `scad_import_stl`. Shells out to the `openscad` CLI; uses [`trimesh`](https://github.com/mikedh/trimesh) for mesh validation. Full docs: [`docs/openscad/README.md`](docs/openscad/README.md).

## The Design Loop

Always-on rules embedded in the MCP server's `instructions` field — every agent that connects sees them automatically. Summary:

1. **Plan.** Compute coordinates and dimensions in one `execute_code` call that PRINTS them. Verify the math BEFORE creating geometry.
2. **Build.** 1–3 operations per `execute_code`, then `blender_mesh_health`.
3. **Verify.** Renders for shape, cross-sections for internal truth.
4. **Validate.** `blender_check_clearance_sweep` for any joint. `blender_full_printability_check` before export.
5. **Export.** `blender_export_stl` (no args = bundle all parts).

Full doc: [`docs/blender/design-loop.md`](docs/blender/design-loop.md).

For mechanism design (hinges, ball-sockets, snap fits, articulated chains): [`docs/design/print-in-place.md`](docs/design/print-in-place.md). This is backend-agnostic — same rules apply if you're using OpenSCAD.

## Documents

Each doc is exposed both as a filesystem path and as an MCP resource. Resource-aware clients should prefer the URI.

| URI | File | Purpose |
|---|---|---|
| `printable://design/print-in-place` | [docs/design/print-in-place.md](docs/design/print-in-place.md) | FDM mechanism design: cardinal print-path rule, clearances, patterns, validation checklist (**backend-agnostic**) |
| `printable://design/recipes/wheel-on-axle` | [docs/design/print-in-place/recipes/wheel-on-axle.md](docs/design/print-in-place/recipes/wheel-on-axle.md) | Validated toy-car wheel parameters |
| `printable://design/recipes/flip-tile` | [docs/design/print-in-place/recipes/flip-tile.md](docs/design/print-in-place/recipes/flip-tile.md) | Validated closed-bore flip-tile parameters |
| `printable://blender/design-loop` | [docs/blender/design-loop.md](docs/blender/design-loop.md) | Plan→build→verify→validate→export workflow, boolean rules, failure modes |
| `printable://blender/image-displacement` | [docs/blender/image-displacement.md](docs/blender/image-displacement.md) | 2D image → printable 3D relief |
| `printable://blender/blender-app` | [docs/blender/blender-app.md](docs/blender/blender-app.md) | Launch / restart / multi-instance setup |
| `printable://openscad/backend` | [docs/openscad/README.md](docs/openscad/README.md) | OpenSCAD backend setup, tool reference, validator details, cross-backend handoff |

```
docs/
├── design/                          # backend-agnostic design rules
│   └── print-in-place.md
│       └── recipes/
│           ├── wheel-on-axle.md
│           └── flip-tile.md
├── blender/                         # Blender-specific
│   ├── design-loop.md
│   ├── image-displacement.md
│   └── blender-app.md
└── openscad/                        # OpenSCAD-specific
    └── README.md
```

## Testing

```bash
python -m pytest tests/ -v          # unit tests, no Blender required
python evals/runner.py              # policy-based regression evals (see evals/README.md)
```

Unit tests cover TCP protocol framing, image compositing (PIL), MCP tool registration. The eval suite checks that agents using the MCP actually follow the always-on rules (e.g. clear scene first, prefer `blender_boolean`, no monolithic `execute_code` blocks) — procedural checks from the tool trace, plus LLM-judged outcome policies for things like "moving parts have a continuous print path to the bed."

## Contributing

The interesting design surface is in `docs/`. If you discover a new mechanism pattern that prints reliably, add a recipe under `docs/design/print-in-place/recipes/` with validated parameters and any pitfalls. Recipes graduate from agent memory into the public docs once they're print-validated.

For new policies (rules an agent should always follow), add a markdown file to `evals/policies/` and a scenario to `evals/scenarios/` so the eval runner picks it up.

## License

[Add license]
