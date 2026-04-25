---
id: prefer-typed-boolean
type: procedural
applies_to: ["cube-with-hole", "basic-hinge", "wheel-on-axle"]
severity: high
---

# Prefer typed boolean over raw modifier code

For boolean operations (UNION / DIFFERENCE / INTERSECT), the agent must use the typed `blender_boolean` tool, not raw boolean modifiers inside `blender_execute_code`. The typed tool handles context, modifier ordering, and runs a connectivity check that catches coplanar-face union failures (manifold-but-disconnected geometry).

## Pass criteria

- All boolean operations in the trace go through `blender_boolean`.
- Zero `execute_code` calls contain a `BOOLEAN` modifier.

## Fail criteria

- Any `blender_execute_code` call whose code body adds a boolean modifier (e.g. `mod = obj.modifiers.new(name='X', type='BOOLEAN')` or `bpy.ops.object.modifier_add(type='BOOLEAN')`).
- Use of `bpy.ops.object.join()` (creates internal faces — a different failure but in the same family).

Also-fail (informational, not strict): zero `blender_boolean` calls *and* multiple geometry primitives created — likely the agent is doing manual mesh merging in `bmesh` instead of booleans, which is brittle.

## Programmatic check

```python
import re

BOOLEAN_PATTERNS = [
    re.compile(r"modifiers\.new\([^)]*type\s*=\s*['\"]BOOLEAN['\"]"),
    re.compile(r"modifier_add\([^)]*type\s*=\s*['\"]BOOLEAN['\"]"),
    re.compile(r"bpy\.ops\.object\.join\("),
]

def check(trace, scene_state, renders):
    code_calls = [c for c in trace if c.tool == "blender_execute_code"]
    offenders = []
    for c in code_calls:
        code = (c.args or {}).get("code", "")
        for pat in BOOLEAN_PATTERNS:
            if pat.search(code):
                offenders.append((c.call_index, pat.pattern))
                break
    if offenders:
        first = offenders[0]
        return Fail(
            f"{len(offenders)} execute_code call(s) used raw booleans/join "
            f"(first at call #{first[0]}, pattern {first[1]!r}). "
            f"Use blender_boolean instead."
        )
    return Pass()
```
