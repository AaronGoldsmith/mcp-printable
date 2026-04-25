# OpenSCAD Backend

Code-first parametric backend. No addon, no app, no TCP — the MCP server shells
out directly to the `openscad` CLI for compile/render/cross-section, and uses
[`trimesh`](https://github.com/mikedh/trimesh) for mesh I/O and analysis.

Pick this backend when:
- You want code-first parametric modeling (variables, modules, `for` loops)
- You don't have Blender installed
- You're working from an existing `.scad` file or library

Pick the Blender backend when you need direct mesh manipulation, sculpt-like
work, displacement modifiers, or richer rendering.

## Setup

```bash
# 1. Install OpenSCAD: https://openscad.org/downloads.html
#    Defaults the MCP looks for:
#      Windows: C:\Program Files\OpenSCAD\openscad.exe (or " (x86)")
#      macOS:   /Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD
#      Linux:   /usr/bin/openscad or /usr/local/bin/openscad
#    Override with env var: OPENSCAD_BIN=/path/to/openscad

# 2. Python deps come from pyproject.toml (trimesh + numpy)
uv sync   # or: pip install .
```

If `openscad` is missing, every `scad_*` tool returns a graceful error message
pointing at the install URL — the Blender backend is unaffected.

## Tools

| Tool | Purpose |
|---|---|
| `scad_compile` | Compile SCAD code to STL via the CGAL renderer. Returns the STL path or a structured error. |
| `scad_render_views` | Multi-angle PNG composite (iso, front, right, top by default). `preview=True` uses fast OpenCSG. |
| `scad_cross_section` | Slice the model with a thin slab on X/Y/Z to verify internal geometry. |
| `scad_validate_printability` | Run trimesh checks (watertight, winding, volume, bbox, body count) + overhang analysis on an STL. Returns PASS/WARN/FAIL. |
| `scad_import_stl` | Return a SCAD `import("...")` snippet for using an external STL inside SCAD code. |

## Validator: trimesh + a thin overhang pass

Mesh quality is delegated to [`trimesh`](https://github.com/mikedh/trimesh):
- `is_watertight` — every edge shared by exactly 2 faces
- `is_winding_consistent` — face normals all point outward
- `volume`, `area`, `bounds`, `euler_number`
- `mesh.split()` — body (connected component) count

Overhang analysis is computed on top of `mesh.face_normals` + `mesh.area_faces`:
faces whose normal points more than `max_overhang_deg` away from vertical (i.e.
< `max_overhang_deg` from straight-down) are flagged. Faces sitting on the build
plate (centroid near z-min, normal pointing down) are skipped. Convention
matches PrusaSlicer/Cura.

## Cross-backend handoff (STL)

Both directions work via STL — no shared state needed:

```
SCAD -> Blender:  scad_compile -> blender_import_stl
Blender -> SCAD:  blender_export_stl -> scad_import_stl
```

### Caveat — STL imports in CGAL render mode

From hard-won experience (materialize project): `import("file.stl")` works
reliably in OpenCSG **preview** mode regardless of path. In CGAL **render**
mode (the default for `-o output.stl` export), absolute paths silently produce
empty output. Workaround: place the STL adjacent to the .scad and use a
relative name. `scad_import_stl` notes this in the returned snippet.

## Always-on rules (also embedded in MCP `instructions`)

- **`$fn=24` during iteration, raise to 60+ for final export.** Renders with
  `$fn=60+` of any model with multiple spheres/cylinders take 5–20 seconds
  each — agents with finite budgets burn through them on noise.
- **Cross-section is the only honest test of internals.** Renders show
  silhouette only.
- **Always validate before declaring done.** `scad_compile` -> `scad_validate_printability`
  is the minimum bar.
- **Multi-part designs need separate STLs.** Slicers merge separate shells in
  one STL into a single solid — the "gap between two shapes" doesn't survive
  slicing. Use a `part` variable + `if (part=="...")` blocks; compile each
  part to its own STL. (Or: import the fused STL into the slicer and use
  "Split to objects.")
- **Print-in-place rules are the same as Blender.** See
  [`printable://design/print-in-place`](../design/print-in-place.md). Cardinal
  rule: every moving part needs a continuous print path to the bed that
  doesn't pass through the static part.

## Reference

[`materialize`](https://github.com/aarongoldsmith/materialize) (private) —
the original CLI-based OpenSCAD agent workflow that informed this backend.
Its `bin/scad-*.sh` scripts are the predecessors of these MCP tools, and its
`learnings.md` is the source of the pitfalls captured above.
