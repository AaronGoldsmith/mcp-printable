---
id: always-clear-scene
type: procedural
applies_to: ["*"]
severity: critical
---

# Always clear scene first

Every modeling task must start by calling `blender_clear_scene` before any geometry-creating or geometry-modifying tool call. This auto-sets units to mm. Building geometry at Blender's default 1m scale produces zero-volume meshes that pass watertight checks but are useless for printing.

## Pass criteria

- The first call to a `blender_*` tool that creates or modifies geometry is `blender_clear_scene`.
- Read-only inspection calls (`blender_get_scene_info`, `blender_get_object_info`, `blender_get_screenshot`) before `blender_clear_scene` are fine — and in fact a recognized pattern.
- If the agent calls `blender_get_scene_info` first, observes an empty scene with `units.is_mm == True`, and proceeds without `clear_scene`, that's a pass — the goal is satisfied.

## Fail criteria

- Any call to `blender_execute_code`, `blender_boolean`, or any geometry-modifying tool BEFORE `blender_clear_scene` (and without the empty-scene-already-mm exception above).
- The agent never calls `blender_clear_scene` at all in a fresh-task scenario.

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
            return Fail(f"first geometry call was {call.tool} before any clear_scene")
    return Fail("agent never called blender_clear_scene and never verified mm units")
```
