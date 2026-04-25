---
id: simple-cube
prompt: "Build me a 10mm cube and export it as STL."
applies_policies: ["always-clear-scene"]
budget:
  max_tool_calls: 20
  max_wall_seconds: 180
---

# Simple cube

The trivial baseline. Tests that the most basic always-on rule (`clear_scene` first, units in mm) holds even on a task simple enough that the agent might be tempted to skip steps.

## What pass looks like

Tool sequence resembling: `blender_clear_scene` → `blender_execute_code` (create cube) → `blender_export_stl`.

## What fail looks like

- Agent calls `blender_execute_code` with cube creation code as the very first call, without `clear_scene`. Cube ends up at meter scale.
- Agent uses Blender's default startup cube without verifying scale (probably impossible if the MCP enforces clear, but checks the rule even if so).

## Why this is in the suite

This is the canary. If `simple-cube` regresses, every more-complex scenario will also regress. Run this first on any new agent or after any rule change.
