---
id: print-in-place-path-traceable
type: outcome
applies_to: ["basic-hinge", "wheel-on-axle", "ball-socket-joint"]
severity: critical
---

# Every moving part has a continuous print path to the bed

In any print-in-place mechanism, every moving part must have a continuous, uninterrupted print path from the build plate to itself — and that path must NOT pass through the static part it moves against.

If a moving part has no way to get from the bed to itself without touching the static part, it cannot be printed. It will either fuse to the static part (gap too small) or collapse mid-print (no support).

## Pass criteria

- Each part the user can articulate (rotate, slide, flex) has a path from `Z = 0` (build plate) to its final position made of either (a) its own geometry that touches the bed, or (b) a continuous bridge of its own material whose start point sits on the bed.
- That path does not pass through any other part's geometry.
- All articulating surface pairs (the two parts that move against each other) have ≥ 0.3mm clearance.
- Where a moving part needs support during printing (e.g. a wheel resting on the bed), the support gap is 0mm to the bed — not 0.3mm.

## Fail criteria

- A "floating trapped part": a moving part fully enclosed by a static part with no escape route to the bed (e.g. ball floating inside a closed socket).
- Uniform 0.3mm clearance applied to ALL surfaces including the moving part's bottom — leaves it printing on thin air with no bridging.
- Articulating surfaces touch (0mm gap) anywhere — will fuse.
- Coplanar faces between moving and static parts at the same Z — will fuse.

## Judge guidance (LLM-judged)

The judge sees:
1. The full tool-call trace
2. The final scene state (object names, dimensions, positions)
3. Renders from `blender_render_tiled` (iso, front, right, top)
4. Cross-section gallery from `blender_cross_section_gallery`
5. Output of `blender_check_clearance` for any part pairs the agent identified as articulating

The judge must trace the print path for each moving part:
1. Identify which part is the static "host" and which is the moving "guest"
2. Find the lowest point of the moving part — does it sit at Z=0 (bed) or rest on something?
3. If it doesn't sit at Z=0, what bridges from Z=0 to it? Is that bridge made of its own geometry, or is it relying on contact with the static part?
4. Trace upward from the bed: does the bridge pass through the static part's material?

Render evidence is necessary but not sufficient. Cross-sections are required — silhouettes from `render_tiled` cannot show whether a part is internally floating.

If the judge can't determine the path from the available evidence, that's a FAIL with reason "insufficient validation" — the agent should have called `blender_cross_section_gallery` for verification.

Reference: [`docs/design/print-in-place.md`](../../docs/design/print-in-place.md) — the cardinal rule and the "Floating Trapped Part" failure mode.
