# Image Displacement to Printable Relief (Blender)

Turn a 2D image (texture, pattern, artwork) into raised geometry on a Blender primitive. Output is printable (flat base) and preserves the primitive's silhouette.

## Pipeline

1. **Preprocess the image** (PIL + numpy, outside Blender):
   - Load, downsize if > 2048px
   - Convert to grayscale
   - Gaussian blur (radius 6 for sharp patterns, 10–16 for organic)
   - Resize to displacement resolution (256 for soft, 512 for sharp)
   - Histogram stretch (5th–95th percentile → [0, 1])
   - Gamma (>1 crushes midtones for lower peak coverage; <1 boosts low-contrast sources)
   - **Posterize sharp patterns** (`np.round(arr * (n-1)) / (n-1)`, n=2 or 3) so bricks/mazes become flat plateaus, not noisy bumps
   - Save as 8-bit PNG

2. **Build the primitive** (Blender):
   - `mesh.primitive_cube_add(size=2.0)` (or chosen primitive)
   - UV unwrap: `bpy.ops.uv.cube_project()` in edit mode
   - Simple Subsurf modifier (NOT Catmull-Clark — that rounds the cube). Levels 6 if disp ≤ 256px, 7 if disp ≥ 512px. Apply immediately.

3. **Displace mask vertex group** — this is what keeps the primitive recognizable:
   - Iterate verts, skip bottom face (`co.z < -1 + epsilon` in local space)
   - For each remaining vert, compute distance-from-edge: find the vert's dominant axis, take min distance to the two perpendicular faces (`1 - |other_coord|`)
   - Weight = `smoothstep(edge_distance / edge_falloff)` where `edge_falloff` ≈ 0.15 (7.5% of half-extent)
   - Add to vertex group only if weight > 0

4. **Displace modifier**:
   - Load image → ImageTexture
   - `texture_coords='UV'`, `mid_level=0.0` (pushes outward only — keeps flat base), `strength=0.10–0.13` for a 2-unit cube (~5–6% of size)
   - Set `vertex_group='displace_mask'`
   - Apply modifier

## Calibration rules

| Symptom | Fix |
|---|---|
| "Ferrofluid" spiky side profile | Lower strength to ~6% of object size |
| Bricks/mazes look like noisy bumps | Add `posterize=2` (binary plateaus) or `3` |
| Cube loses its cube shape | Increase `edge_falloff`; confirm smoothstep not step |
| Base isn't flat | `mid_level=0`, bottom verts excluded from vg |
| Surface has micro-aliasing spikes | Increase subsurf level, or increase blur radius |
| Low-contrast source (e.g. sand dune) washes out | Gamma < 1.0 (e.g. 0.7) and/or tighter histogram stretch |
| Pattern reads in color but looks bad in clay | Geometry is wrong — fix in matcap/clay before judging |

## Per-item parameters

For batches with varied source character, use a `DEFAULTS + OVERRIDES` pattern in a config module:

```python
DEFAULTS = {'blur_radius': 6, 'disp_size': 512, 'strength': 0.12,
            'gamma': 1.2, 'edge_falloff': 0.15, 'posterize': None}
OVERRIDES = {
    6: {'posterize': 2, 'strength': 0.10},      # maze
    4: {'blur_radius': 16, 'disp_size': 256, 'gamma': 1.4},  # soft
}
def params_for(key): return {**DEFAULTS, **OVERRIDES.get(key, {})}
```

Tune defaults for the dominant character, override the minority.

## Validation

- Render **clay view** (flat matcap, no color) from front/right elevations. Side silhouette should read the pattern's character (flat for plateaus, rolling for organic) — not a uniform forest of spikes.
- Render top view to verify the pattern is legible.
- For print: verify the bottom face is flat. `blender_validate(checks=['OVERHANGS'])` should find no faces below the build plate; the lowest verts should still be at the original base Z.

## Why these rules (lessons from real batches)

- **Strength is small.** On a 2-unit cube, 0.20+ reads as "ferrofluid spikes" from the side. 0.10–0.13 (5–6% of size) looks like real texture. Default low; raise only after confirming the silhouette still reads cleanly.
- **Posterize sharp patterns into plateaus.** Bricks, mazes, circuits should have FLAT tops and sharp walls — not noisy bumps. Source images have interior brightness variation inside each "brick" region; without posterization each plateau becomes its own bumpy surface.
- **`mid_level=0` preserves a flat base.** Default 0.5 pushes half the surface inward, sinking below the original base plane and ruining printability. Combined with a vertex group that excludes the bottom face, displacement only pushes outward.
- **Smoothstep edge-falloff preserves the silhouette.** Without it, displacement hits the cube edges and destroys the cube shape. A hard step creates visible creasing; smoothstep blends cleanly.
- **Subsurf must resolve the displacement map.** 512px disp needs subsurf 7. 256px disp is fine at subsurf 6. Mesh resolution < texture resolution = per-vertex aliasing spikes.
- **Clay/matcap is the honest test.** Color masks rough geometry. Things that look fine textured look bad in clay. If a mesh only looks good with color, the geometry is bad.

## Batch pipeline pattern

When processing many source images with per-image character, split parameters into a `DEFAULTS` dict plus a sparse `OVERRIDES` dict keyed by image index (or filename). A `params_for(key)` helper merges them. Keeps the common case short and makes per-image tuning visible in one place. Validated this way on a 19-cube run: organic images got higher `blur_radius` and slight gamma boost; brick/maze patterns got `posterize=2` for true plateaus; low-contrast sources got `gamma<1` to lift midtones.
