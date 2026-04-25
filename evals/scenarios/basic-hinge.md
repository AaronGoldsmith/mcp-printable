---
id: basic-hinge
prompt: "Build me a parametric pin-through-barrel hinge with a 5mm barrel diameter and two 20×15mm flanges. The hinge should be print-in-place — both leaves should come off the bed already articulating without assembly."
applies_policies:
  - always-clear-scene
  - prefer-typed-boolean
  - print-in-place-path-traceable
budget:
  max_tool_calls: 80
  max_wall_seconds: 600
---

# Basic print-in-place hinge

The first real mechanism. Tests procedural rules (clear scene, typed boolean) and the outcome rule (every moving part has a print path) together.

## What pass looks like

- Workflow: clear → plan (compute coordinates, print them) → build (cube/cylinder primitives, booleans) → render + cross-sections → clearance sweep → printability check → export.
- Final geometry: two leaves with interleaved knuckles, a pin already inside the bore, axial gaps of ~0.3mm between knuckles, radial gap ~0.15mm between pin and bore.
- Both leaves are coplanar at the bottom (Z=0) and lie flat on the bed.
- Pin has a flat end that sits on the bed.

## What fail looks like

- Agent skips clear_scene → triggers `always-clear-scene` failure.
- Agent uses raw boolean modifiers in execute_code → triggers `prefer-typed-boolean`.
- Agent builds a hinge where the pin floats inside the bore with no path to the bed (no flat end on the bed) → `print-in-place-path-traceable` fails.
- Agent uses uniform 0.3mm clearance on all knuckle faces including bottom, leaving the pin floating mid-air → `print-in-place-path-traceable` fails.
- Agent never calls `blender_check_clearance_sweep` → flagged but not yet a separate policy.

## Notes for human reviewers

Common LLM mistake: making the bore radius equal to the pin radius + 0.3mm (treating it as a single-side gap when it's a diametral gap). Should be pin_r + 0.15 radial = 0.3mm on diameter.

Common LLM mistake: forgetting that both leaves must be coplanar in the open position. If one leaf is offset in Z, the hinge can't open flat.
