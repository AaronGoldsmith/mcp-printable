# Print-in-Place Wheel on Axle

Validated recipe for a toy-car-style wheel that prints in place around a body-spanning axle with retention caps. Backend-agnostic parameter set.

## Mechanism

Axle passes completely through the body as one continuous cylinder. Retaining caps at each axle end are part of the body (NOT the wheel). The cap radius is larger than the wheel bore — wheel can't pull off the axle end.

```
       cap         wheel        body        wheel        cap
        ╗            ╗           ║            ╗           ╗
       ┌─┐         ┌─┐         ┌───┐         ┌─┐         ┌─┐
   ────┤ ├─────────┤ ├─────────┤   ├─────────┤ ├─────────┤ ├────  axle
       └─┘         └─┘         └───┘         └─┘         └─┘
        Z=0 (build plate) ─────────────────────────────────
```

## Validated Dimensions

| Parameter | Value | Notes |
|---|---|---|
| Axle radius | 3.0 mm | |
| Wheel bore radius | 3.3 mm | 0.3 mm radial clearance per side |
| Cap radius | 5.5 mm | Must be > bore to retain wheel |
| Cap thickness | 2.0 mm | |
| Cap axial gap | 0.3 mm | From wheel outer face |
| Wheel inner face to body side | 0.5 mm min | Use ≥ 0.5 mm, not just 0.3 |
| Wheel bottom Z | 0 | Same as body bottom — both on bed |

### Axle half-length formula
```
axle_half_len = wheel_outer_Y + cap_axial_gap + cap_thickness
              = e.g. 25 + 0.3 + 2 = 27.3 mm
```

## Critical pitfalls

### Gusset coplanarity at the bed
If you add gussets under exposed axle stubs to eliminate mid-air overhangs, the gusset bottom face (Z=0) will be coplanar with the wheel bottom (Z=0). That's a fusion zone.

**Fix:** Cut a circular pocket from the body at each wheel footprint position **after** unioning the gussets:
- pocket radius = wheel_radius + 0.1 mm
- pocket depth = 0.8 mm
- center at Z=0 (sinks just below the wheel footprint)

### Wheel-well overhang ceiling
Cylindrical wheel wells create arch ceilings at `Z = axle_Z + well_radius`. The arch top is a hard overhang.

**Fix:** Subtract a rectangular box from the arch top up to body top to open the well upward. Print bridges across the open well.

### Object-origin mismatch in clearance sweeps
Some validators (e.g. Blender's `check_clearance_sweep`) rotate around the object **origin**, not the geometry center. After creating wheels procedurally (origin at world 0,0,0), reset origin to bounds-center before sweeping, or sweeps will rotate around world origin and report false collisions.

### Boolean re-cut trick (Blender EXACT solver)
If a DIFFERENCE boolean returns "face count unchanged" (cutter didn't overlap), recreate the cutter with **more segments** (e.g. 32 → 64) and a slightly different depth. Different topology can force the solver to detect the overlap.

**Why:** EXACT solver can miss very thin overlap zones with identical segment counts on second-pass enlargements.

## Print orientation

- Body, wheels, axle caps all sit on the bed at Z=0
- Axle is horizontal, passes through body
- No supports needed if wheel-well roofs are opened (see above)
- After printing: rotate wheels back and forth to break any thin fusion at the 0.3 mm radial gap
