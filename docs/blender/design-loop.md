# Blender Design Loop

The plan→build→verify→validate→export workflow when using the Blender backend of the Printable MCP. These are *always-on* rules — they apply to every modeling task in Blender and are reflected in the MCP server's `instructions` field so any agent that connects gets them automatically.

## The Loop

```
PLAN     → compute all coordinates, dimensions, and key positions in one
           execute_code call that PRINTS them. Verify the math BEFORE creating
           geometry.

BUILD    → 1–3 operations per execute_code. After each boolean, call
           blender_validate(checks=['HEALTH']) (watertight? face count sane? connected?).

VERIFY   → blender_render_tiled after each major step. focus_object/zoom for
           parts. blender_render_turntable for cylindrical features.
           blender_cross_section_gallery to see internal geometry.

VALIDATE → blender_check_intersection for parts that shouldn't overlap.
           blender_check_clearance_sweep for any joint or hinge.
           blender_validate(checks=['ALL']) on every part before export.

EXPORT   → blender_export_stl with no object args to bundle all parts into
           a single STL.
```

## Cardinal rules

1. **Always start with `blender_clear_scene`.** It auto-sets units to mm. Building geometry at default 1m scale produces zero-volume meshes that pass watertight checks but are useless. Check `units.is_mm` in `blender_get_scene_info` if anything looks wrong.
2. **Use `blender_boolean`, not `bpy.ops` booleans inside `execute_code`.** The typed tool handles context, modifier ordering, and connectivity check. Raw `bpy.ops.object.modifier_apply` swallows failures silently.
3. **Never use `bpy.ops.object.join()`.** It creates internal faces. Use `blender_boolean` UNION instead.
4. **Boolean union needs solid volume overlap, not coplanar face contact.** Coplanar union produces `connected_components > 1` — manifold but structurally floating. Center arms/tabs ON the plate edge with ~0.5mm overlap each side.
5. **Renders show silhouettes only.** Use cross-sections to verify internal geometry truth.

## Visual feedback hierarchy (most informative first)

1. `blender_cross_section_gallery` — the only way to verify internal geometry
2. `blender_render_printability_heatmap` — overhangs + thin walls visualized
3. `blender_render_tiled` — overall shape; useless for internals
4. `blender_render_turntable` — best for cylindrical features (barrels, pins)

Cross-sections should be called after every complex boolean sequence, not just at the end.

## FDM print defaults

| Constraint | Default | Notes |
|---|---|---|
| Min wall thickness | 0.8 mm | 2× 0.4mm nozzle |
| Min print-in-place clearance | 0.3 mm | See `docs/design/print-in-place.md` |
| Max overhang without support | 45° | from horizontal |
| Min layer height | 0.2 mm | |

Prefer self-supporting geometry (chamfers, 45° ramps) over added supports — supports inside joints fuse parts together.

## Common failure modes

### Monolithic execute_code
Agents that write 40+ lines of bpy in one `execute_code` call then check the result are unreliable. Booleans fail silently. The fix is the build phase rule: one operation per call, `blender_validate(checks=['HEALTH'])` after each.

### Unit-scale bug
Default Blender scene has `scale_length=1.0` (meters). Agent builds at this scale → the HEALTH check says watertight=true, 0 non-manifold, but `volume_mm3=0.0`. Always start with `blender_clear_scene` (auto-sets mm) and verify `units.is_mm` in `get_scene_info`.

### Render camera black-out
- `focus_object + zoom > 1.5` can put the camera inside the object → black render.
- Cross-section camera `clip_start` is not auto-scaled to object size → can go black for mm-scale objects.
- Camera minimum distance is clamped to 1mm.

### Boolean degenerate faces
EXACT solver often produces 2–4 micro-degenerate faces (~1e-10 area) as precision artifacts. Mesh stays watertight and non-manifold-free; this is acceptable. Do NOT remove these with `bm.faces.remove()` — that creates real holes. Slicers handle micro-faces gracefully.

## When to escape to `execute_code`

The typed tools cover ~90% of design work. Use `execute_code` for:
- Custom bmesh operations (e.g. dissolve specific edges, custom vertex groups)
- Geometry nodes (build node groups via `bpy.data.node_groups.new(name, 'GeometryNodeTree')`)
- Inspection that the typed tools don't expose
- Anything where you'd otherwise call multiple typed tools in lockstep

Each `execute_code` call auto-saves a `.blend` checkpoint to a temp dir before running, so a crash inside `execute_code` doesn't lose the scene.
