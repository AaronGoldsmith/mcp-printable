"""Mock adapter — returns hand-crafted Traces for self-testing the judge plumbing.

No real agent involved. Used by `runner.py --self-test` to verify that policies
trigger the right verdicts on known-good and known-bad traces.
"""

from __future__ import annotations

from ..framework import ToolCall, Trace


def good_simple_cube() -> Trace:
    return Trace(
        scenario_id="simple-cube",
        agent="mock:good",
        calls=[
            ToolCall(0, "blender_clear_scene", {}, result={"ok": True}),
            ToolCall(1, "blender_execute_code", {"code": "bpy.ops.mesh.primitive_cube_add(size=0.01)"},
                     result={"ok": True}),
            ToolCall(2, "blender_export_stl", {}, result={"path": "/tmp/cube.stl"}),
        ],
    )


def bad_simple_cube_no_clear() -> Trace:
    return Trace(
        scenario_id="simple-cube",
        agent="mock:bad",
        calls=[
            ToolCall(0, "blender_execute_code", {"code": "bpy.ops.mesh.primitive_cube_add(size=1.0)"},
                     result={"ok": True}),
            ToolCall(1, "blender_export_stl", {}, result={"path": "/tmp/cube.stl"}),
        ],
    )


def good_cube_with_hole() -> Trace:
    return Trace(
        scenario_id="cube-with-hole",
        agent="mock:good",
        calls=[
            ToolCall(0, "blender_clear_scene", {}, result={"ok": True}),
            ToolCall(1, "blender_execute_code",
                     {"code": "bpy.ops.mesh.primitive_cube_add(size=0.02)\nbpy.ops.mesh.primitive_cylinder_add(radius=0.0025, depth=0.025)"},
                     result={"ok": True}),
            ToolCall(2, "blender_boolean",
                     {"operation": "DIFFERENCE", "target": "Cube", "cutter": "Cylinder"},
                     result={"ok": True, "watertight": True}),
            ToolCall(3, "blender_export_stl", {}, result={"path": "/tmp/cube_hole.stl"}),
        ],
    )


def bad_cube_with_hole_raw_modifier() -> Trace:
    return Trace(
        scenario_id="cube-with-hole",
        agent="mock:bad",
        calls=[
            ToolCall(0, "blender_clear_scene", {}, result={"ok": True}),
            ToolCall(1, "blender_execute_code", {
                "code": (
                    "import bpy\n"
                    "bpy.ops.mesh.primitive_cube_add(size=0.02)\n"
                    "cube = bpy.context.active_object\n"
                    "bpy.ops.mesh.primitive_cylinder_add(radius=0.0025)\n"
                    "cyl = bpy.context.active_object\n"
                    "mod = cube.modifiers.new(name='cut', type='BOOLEAN')\n"
                    "mod.object = cyl; mod.operation = 'DIFFERENCE'\n"
                    "bpy.ops.object.modifier_apply(modifier='cut')\n"
                )
            }, result={"ok": True}),
            ToolCall(2, "blender_export_stl", {}, result={"path": "/tmp/cube_hole.stl"}),
        ],
    )


REGISTRY = {
    ("simple-cube", "good"): good_simple_cube,
    ("simple-cube", "bad"): bad_simple_cube_no_clear,
    ("cube-with-hole", "good"): good_cube_with_hole,
    ("cube-with-hole", "bad"): bad_cube_with_hole_raw_modifier,
}


def run(prompt: str, scenario_id: str, variant: str = "good") -> Trace:
    """Return a hand-crafted trace for the given scenario.

    `variant` is "good" (should pass policies) or "bad" (deliberately violates).
    """
    key = (scenario_id, variant)
    if key not in REGISTRY:
        raise KeyError(f"no mock trace for {key}; available: {sorted(REGISTRY)}")
    return REGISTRY[key]()
