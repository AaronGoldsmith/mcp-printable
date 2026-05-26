---
name: blender-design-loop
description: "Use whenever building geometry in Blender via the Printable MCP. Covers the plan→build→verify→validate→export workflow, boolean rules, unit safety, visual feedback hierarchy, and common failure modes (monolithic execute_code, unit-scale bug, render black-out, boolean degenerate faces)."
---

This skill is a Codex-specific shim. The full, agent-agnostic guidance is in [`docs/blender/design-loop.md`](../../../docs/blender/design-loop.md).

Always-on rules (also in the MCP server's `instructions` field):
- Start with `blender_clear_scene` — auto-sets units to mm
- Use `blender_boolean`, never `bpy.ops.object.join()` or raw boolean modifiers in execute_code
- 1–3 operations per `execute_code` then `blender_validate(checks=['HEALTH'])`
- Renders show silhouettes only — use `cross_section_gallery` for internal truth
- `clearance_sweep` is mandatory for any joint or hinge
