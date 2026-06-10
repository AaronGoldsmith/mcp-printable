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
- `docs/` — agent-agnostic prose guidance: print-in-place rules, design loop, image displacement. Also exposed as **MCP resources** under `printable://…` URIs (see [Documents](#documents) below) so any resource-aware MCP client can pull them via the protocol — no filesystem access required.
- `.claude/skills/` — thin Claude shims (description-triggered loading) that point into `docs/`. Other agents use resources or filesystem.
- `evals/` — policy-based regression tests that verify agents actually follow the rules. See [`evals/README.md`](evals/README.md).

## Status

v0.1.x — alpha. Blender backend is feature-complete and dogfooded against real prints; OpenSCAD backend covers the parametric workflow but has fewer validation tools. API is stable enough to use but may shift before 1.0.

## Requirements

- **Python 3.10+** for the MCP server.
- **Blender 3.6+** if you're using the Blender backend (the bundled addon needs to be installed once and enabled in Blender's Preferences).
- **OpenSCAD CLI** if you're using the SCAD backend (auto-discovered on PATH and in the standard install locations on Windows / macOS / Linux).
- **An image-capable agent model.** The visual-feedback tools (`blender_get_screenshot`, `blender_render_tiled`, `blender_render_turntable`, `blender_cross_section*`, `blender_render_printability_heatmap`, `blender_render_with_dimensions`, `blender_render_before_after`, `scad_render_views`, `scad_cross_section`) return base64-encoded PNGs that the agent has to actually *see* to use them. Text-only models will still get tool results but can't interpret the rendered geometry — the design loop relies on the agent looking at renders and cross-sections to verify what it's built. Examples that work well: Claude Sonnet 4.x+, GPT-4o/5, Gemini 2.x Pro/Flash. Text-only models will technically run but won't catch shape-level mistakes.

## Setup

### Install from PyPI

```bash
pip install mcp-printable       # or: uv pip install mcp-printable
printable-install-addon         # copies the bundled addon into Blender's addon dir
```

Then in Blender: Preferences → Add-ons → enable **"Printable Blender Bridge"**.

### Install from source

```bash
git clone https://github.com/AaronGoldsmith/mcp-printable
cd mcp-printable
uv sync                         # or: pip install .
python install.py               # equivalent to printable-install-addon
```

> [!IMPORTANT]
> Run the install step from a terminal — **NOT** Blender's "Install from Disk" dialog. See [`SETUP.md`](SETUP.md) for why and other gotchas.

### Wire into your agent

For Claude Code (`~/.claude.json` or project `.mcp.json`), pick one of two options.

**Option A — `uvx` (recommended; no prior install needed):**

```json
{
  "mcpServers": {
    "printable": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "mcp-printable", "printable"]
    }
  }
}
```

`uvx` will pull `mcp-printable` from PyPI on first run and cache it. To pick up new releases later, run `uvx --refresh --from mcp-printable printable` once or pin a version with `mcp-printable@0.1.2`.

**Option B — global `pip install` (simpler if you already have `mcp-printable` on PATH):**

```json
{
  "mcpServers": {
    "printable": {
      "command": "printable"
    }
  }
}
```

Requires `pip install mcp-printable` to have put `printable` on your PATH first.

For other agents (Goose, Cursor, etc.) — same command, wrapped in your agent's MCP server configuration.

### Agent skills (optional)

This repo bundles four [Claude Code skills](https://code.claude.com/docs/en/skills) under [`.claude/skills/`](.claude/skills/) — short shims that point at the same `docs/` content the MCP exposes as resources. Claude Code auto-discovers them when you launch it in the repo:

```bash
git clone https://github.com/AaronGoldsmith/mcp-printable
cd mcp-printable && claude
```

The 4 skills:
- `print-in-place` — design rules for moving-parts mechanisms (hinges, ball-sockets, snap fits)
- `blender-design-loop` — plan→build→verify→validate→export workflow
- `image-displacement` — turn a 2D image into 3D printable relief
- `blender-app` — launch / restart / multi-instance Blender setup

For Claude Code, copy a skill into `~/.claude/skills/` to make it available across all projects. **For other agents** (Codex, Cursor, Goose, ...) — see [AGENTS.md](AGENTS.md), which links each skill into the equivalent location for your agent and explains the MCP-resource fallback for agents that don't load project skills.

## Tool families

### Blender (23 tools)

**Scene** — `blender_get_scene_info`, `blender_get_object_info`, `blender_clear_scene`, `blender_restore_checkpoint` (roll back to the auto-saved checkpoint after a destructive mistake), `blender_rename_object`

**Code** — `blender_execute_code` (arbitrary bpy/bmesh; auto-checkpoints), `blender_boolean` (typed UNION/DIFFERENCE/INTERSECT — *prefer this over execute_code*)

**Visual feedback** — `blender_get_screenshot`, `blender_render_tiled`, `blender_render_turntable`, `blender_cross_section`, `blender_cross_section_gallery`, `blender_render_printability_heatmap`, `blender_render_with_dimensions`, `blender_render_before_after`

**Print validation** — `blender_validate` (one tool for HEALTH / OVERHANGS / THIN_WALLS / CLEARANCE checks; `checks=['ALL']` runs the full suite — replaces the former `mesh_health` / `check_overhangs` / `check_thin_walls` / `full_printability_check` tools), `blender_check_clearance`, `blender_check_clearance_sweep`, `blender_check_intersection`, `blender_check_retention`

**Export** — `blender_export_stl`, `blender_import_stl`, `blender_save_blend`

### OpenSCAD (5 tools)

`scad_compile`, `scad_render_views`, `scad_cross_section`, `scad_validate_printability`, `scad_import_stl`. Shells out to the `openscad` CLI; uses [`trimesh`](https://github.com/mikedh/trimesh) for mesh validation. Full docs: [`docs/openscad/README.md`](docs/openscad/README.md).

## The Design Loop

Always-on rules embedded in the MCP server's `instructions` field — every agent that connects sees them automatically. Summary:

1. **Plan.** Compute coordinates and dimensions in one `execute_code` call that PRINTS them. Verify the math BEFORE creating geometry.
2. **Build.** 1–3 operations per `execute_code`, then `blender_validate(checks=['HEALTH'])`.
3. **Verify.** Renders for shape, cross-sections for internal truth.
4. **Validate.** `blender_check_clearance_sweep` for any joint. `blender_validate(checks=['ALL'])` before export.
5. **Export.** `blender_export_stl` (no args = bundle all parts).

Full doc: [`docs/blender/design-loop.md`](docs/blender/design-loop.md).

For mechanism design (hinges, ball-sockets, snap fits, articulated chains): [`docs/design/print-in-place.md`](docs/design/print-in-place.md). This is backend-agnostic — same rules apply if you're using OpenSCAD.

## Documents

Every doc below is served two ways:

1. **As an MCP resource** under `printable://…` — the **preferred** path. Resource-aware clients fetch via `resources/read`, get the same content the maintainer ships, and don't need filesystem access to the project. Resources travel with the MCP server itself, so a `pip install mcp-printable` user has the docs even without cloning the repo.
2. **As a file in `docs/`** — fallback for filesystem-based clients, and for humans browsing the repo.

If you're writing an MCP client, prefer the URI. The filesystem path is documented mainly so a human can click through from this README.

| URI | File | Purpose |
|---|---|---|
| `printable://design/print-in-place` | [docs/design/print-in-place.md](docs/design/print-in-place.md) | FDM mechanism design: cardinal print-path rule, clearances, patterns, validation checklist (**backend-agnostic**) |
| `printable://blender/design-loop` | [docs/blender/design-loop.md](docs/blender/design-loop.md) | Plan→build→verify→validate→export workflow, boolean rules, failure modes |
| `printable://blender/image-displacement` | [docs/blender/image-displacement.md](docs/blender/image-displacement.md) | 2D image → printable 3D relief |
| `printable://blender/blender-app` | [docs/blender/blender-app.md](docs/blender/blender-app.md) | Launch / restart / multi-instance setup |
| `printable://openscad/backend` | [docs/openscad/README.md](docs/openscad/README.md) | OpenSCAD backend setup, tool reference, validator details, cross-backend handoff |

```
docs/
├── design/                          # backend-agnostic design rules
│   └── print-in-place.md
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

## Roadmap

Things on the list, not yet shipped:

- **Validated parameter recipes** — turnkey parameter sets for common print-in-place mechanisms (wheel-on-axle, flip-tile, ball-and-socket, snap fit) so an agent can ask for "a toy car wheel" and get a known-good geometry without re-solving the clearance + retention math each time. Earlier drafts existed but weren't dialed in enough to ship as authoritative; new ones will land once they're validated against real prints.
- **Blender app-lifecycle tools** (`blender_launch` / `blender_status` / `blender_kill`) — let the agent spin Blender up itself instead of needing the user to start it first.
- **Second OpenSCAD parity pass** — match Blender's clearance-sweep / retention / thin-wall checks on the SCAD side.

## Contributing

The interesting design surface is in `docs/`. New always-on rules belong in [`docs/design/`](docs/design/) (backend-agnostic) or [`docs/blender/`](docs/blender/) / [`docs/openscad/`](docs/openscad/) (backend-specific). If a rule should be enforced, also add a policy file under [`evals/policies/`](evals/policies/) and a scenario under [`evals/scenarios/`](evals/scenarios/) so the eval runner picks it up.

## License

MIT — see [`LICENSE`](LICENSE).
