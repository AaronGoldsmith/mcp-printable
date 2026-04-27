---
id: cube-with-hole
prompt: "Build me a 20mm cube with a 5mm cylindrical hole through the center along the Z axis, and bevel the top and bottom edges of the hole at 0.5mm. Export as STL."
applies_policies: ["always-clear-scene", "prefer-typed-boolean"]
budget:
  max_tool_calls: 30
  max_wall_seconds: 240
---

# Cube with through-hole and beveled edges

Tests two things stacked:
1. **Boolean discipline** — a hole through a cube must be cut with `blender_boolean DIFFERENCE`, not by editing the cube mesh manually or by adding a raw `BOOLEAN` modifier inside `execute_code`.
2. **Selective edge bevel** — the agent must select the *correct* edges (the top + bottom rims of the hole, two circles) and apply a bevel only to those, not to every edge of the cube.

## What pass looks like

- `blender_clear_scene` → cube + cylinder primitives (via `execute_code` is fine) → `blender_boolean(operation="DIFFERENCE", target=cube, cutter=cylinder)`.
- Then a targeted bevel: in `execute_code`, select the two circular edge loops at `z = 0` and `z = 20` belonging to the hole (typically by face-normal or vertex-position filter, NOT by selecting all edges), and apply a 0.5mm bevel.
- Final: one watertight object, hole through the middle, both rims chamfered, no bevel on the cube's outer corners.

## What fail looks like

- Agent writes `obj.modifiers.new(type='BOOLEAN', ...)` inside `blender_execute_code` — bypasses the typed tool. **Caught by `prefer-typed-boolean`.**
- Agent uses `bpy.ops.object.join()` — same family. **Caught by `prefer-typed-boolean`.**
- Agent applies `bevel` to all selected edges without filtering to just the hole rim — result is a beveled cube AND beveled hole, which fails the spec.
- Agent tries to manually delete cube faces and rebuild the hole geometry by hand in `bmesh` — usually produces non-manifold mesh.

## Why this is in the suite

The bevel step makes the agent commit to a particular set of edges, not "all of them." Past failures have included models where the agent indiscriminately beveled everything because edge selection in `bmesh` is fiddly. Combining boolean + selective edge op in one scenario tests whether the agent can keep the modifications scoped.
