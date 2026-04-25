---
id: cube-with-hole
prompt: "Build me a 20mm cube with a 5mm cylindrical hole through the center along the Z axis. Export as STL."
applies_policies: ["always-clear-scene", "prefer-typed-boolean"]
budget:
  max_tool_calls: 30
  max_wall_seconds: 240
---

# Cube with through-hole

Tests boolean discipline: a hole through a cube must be cut with `blender_boolean DIFFERENCE`, not by editing the cube mesh manually or by adding a raw `BOOLEAN` modifier inside `execute_code`.

## What pass looks like

Cube + cylinder primitives created (via `execute_code` is fine), then `blender_boolean(operation="DIFFERENCE", target=cube, cutter=cylinder)`. Final scene has one watertight object with a hole.

## What fail looks like

- Agent writes `obj.modifiers.new(type='BOOLEAN', ...)` inside `blender_execute_code` — bypasses the typed tool.
- Agent uses `bpy.ops.object.join()` — different family of failure but caught by `prefer-typed-boolean`.
- Agent tries to manually delete cube faces and rebuild the hole geometry by hand in `bmesh` — usually produces non-manifold mesh.

## Why this is in the suite

Hole-through-a-block is the simplest non-trivial boolean and the most common request. If `prefer-typed-boolean` regresses here, it'll regress everywhere.
