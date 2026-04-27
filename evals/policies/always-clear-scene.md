---
id: always-clear-scene
type: procedural
applies_to: ["simple-cube", "cube-with-hole", "basic-hinge"]
severity: high
---

# Verify mm units before geometry on a fresh modeling task

When the user prompt asks for a wholly new model (not "tweak this existing scene"), the agent must establish a known-good starting state — Blender in mm units, ideally an empty scene — before creating any geometry. Building at Blender's default 1m scale produces zero-volume meshes that pass watertight checks but are useless for printing.

This applies **only to fresh-build scenarios**. If the user is iterating on an existing scene ("add a fillet to this hinge"), the agent must NOT clear the scene — see [`docs/blender/design-loop.md`](../../docs/blender/design-loop.md) for the iterate-existing pattern.

## Pass criteria

Either:
- `blender_clear_scene` is the first geometry-touching call, OR
- `blender_get_scene_info` is called first; if it returns an empty scene with `units.is_mm == True`, the agent proceeds without `clear_scene`. (Inspection-first is the right pattern when the scene state is unknown.)

Read-only inspection calls (`blender_get_scene_info`, `blender_get_object_info`, `blender_get_screenshot`) before `blender_clear_scene` are always fine.

## Fail criteria

- Any call to `blender_execute_code`, `blender_boolean`, or any geometry-modifying tool BEFORE either `blender_clear_scene` or a `blender_get_scene_info` that confirms empty + mm.
- The agent creates geometry without ever verifying mm units.

## Programmatic check

```python
GEOMETRY_TOOLS = {
    "blender_execute_code", "blender_boolean", "blender_rename_object",
    "blender_save_blend", "blender_export_stl", "blender_import_stl",
}

def check(trace, scene_state, renders):
    blender_calls = [c for c in trace if c.tool.startswith("blender_")]
    if not blender_calls:
        return Skip("no blender calls in trace")

    for call in blender_calls:
        if call.tool == "blender_clear_scene":
            return Pass()
        if call.tool == "blender_get_scene_info":
            # Inspection-first pattern; check if scene was already mm + empty
            r = call.result or {}
            if r.get("units", {}).get("is_mm") and not r.get("objects"):
                return Pass(reason="scene already empty in mm — clear unnecessary")
            continue
        if call.tool in GEOMETRY_TOOLS:
            return Fail(f"first geometry call was {call.tool} before any clear_scene or mm check")
    return Fail("agent never verified mm units before exiting")
```
