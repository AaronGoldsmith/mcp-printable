# Print-in-Place Design for FDM

Backend-agnostic design rules for any FDM-printed mechanism with parts that move relative to each other after coming off the bed: ball-and-socket joints, hinges, snap fits, peg-in-slot, articulated chains, living hinges.

## The Cardinal Rule

**Every moving part must have a continuous, uninterrupted print path from the build plate to itself — and that path must NOT pass through the static part it moves against.**

If the moving part has no way to get from the bed to itself without touching the static part, it cannot be printed. It will either fuse to the static part (too close) or collapse mid-print (no support).

```
WRONG — ball floating in socket:           RIGHT — ball on rod through socket:

    ┌──────┐                                   ┌──────┐
    │      │  ← socket                         │  ()  │  ← ball, inside socket
    │  ()  │  ← ball (no path to bed!)         │  ||  │  ← rod, through bottom
    │      │                                   │  ||  │
    └──────┘                                   └──┤├──┘
       ╳                                          ──    ← build plate
                                                  ✓
```

---

## LLM Failure Modes — Read These First

The most common mistakes when designing print-in-place geometry:

### Mistake 1: The Floating Trapped Part
Building a closed container (socket, box, cage) around a moving part without giving the inner part a route to the bed. The inner part has no support during printing.

**Fix:** Give the inner part a stem, rod, or tab that exits through a hole in the outer part and reaches the bed.

### Mistake 2: Uniform Clearance Everywhere
Applying 0.3mm clearance uniformly — including between the moving part's bottom and the socket floor. The moving part prints on thin air above a 0.3mm gap. Nothing bridges that gap.

**Fix:** 0mm gap where the moving part needs support (resting surface during print), 0.3mm gap on all articulating surfaces (sides, top).

### Mistake 3: Forgetting the Support Geometry is Part of the Design
Adding a rod or stem as an afterthought that you plan to cut off. That rod IS the design. Plan its diameter, clearance through the socket hole, and whether it stays in the final piece from the start.

### Mistake 4: Coplanar Faces Between Parts
Two surfaces of different parts that are exactly flush. FDM printers will fuse coplanar surfaces. Always add a gap. 0.3mm minimum. If two faces must be at the same Z height, offset one by at least 0.3mm.

### Mistake 5: Checking Clearance Without Checking the Path
Verifying ball-to-socket distance is 0.3mm and calling it done. Clearance distance is necessary but not sufficient. Ask: "How does this part get from the bed to where it is?" If you can't trace an uninterrupted path, it won't print.

---

## Clearance Values (FDM, 0.4mm nozzle, PLA/PETG)

| Gap type | Value | Notes |
|---|---|---|
| Minimum to prevent fusing | 0.15mm | Risky — printer-dependent |
| Standard articulating gap | 0.3mm | Use this by default |
| Conservative gap | 0.5mm | Use for first print of a new mechanism |
| Axial gap (e.g. between knuckles) | 0.3mm per side | |
| Radial gap (pin in bore) | 0.15mm per side = 0.3mm on diameter | |
| Z gap between coplanar layers | 0.3mm | Never 0mm |

### Known FDM Printer Behavior

- **Gap ≤ 0.15mm:** Will almost certainly fuse. Don't design this.
- **Gap 0.15–0.2mm:** May print-in-place with optimal settings. Use only for small detail features.
- **Gap 0.2–0.3mm:** Print-in-place works on most printers with some post-print manipulation.
- **Gap 0.3–0.5mm:** Reliable print-in-place on most FDM printers.
- **Gap > 0.5mm:** Reliable but may feel loose depending on mechanism.
- **Touching surfaces:** WILL FUSE. Even nominally 0mm gap surfaces fuse due to first-layer squish.
- **Bridging over gaps:** FDM can bridge ~50mm horizontally with good settings. Vertical bridging works for gaps up to ~2mm with supports; 0.3mm vertical gap is a 1–2 layer bridge — marginal, works on good printers.

---

## Mechanism Patterns

### 1. Ball-and-Socket Joint

The ball must be part of a larger piece that prints on the bed. The socket prints around it.

**Correct design:**
```
Ball + rod = one printed piece
Socket = separate printed piece

Print orientation (flat bottom on bed):
- Socket flat bottom on bed
- Rod passes through hole in socket bottom, also sits on bed
- Ball is at top of rod, inside socket
- Clearance: 0.3mm between ball and socket cavity (all sides)
- Rod-to-hole clearance: 0.3mm per side (rod_r = hole_r - 0.3)
- Retention: socket collar smaller than ball (collar_r < ball_r)

Key dimensions (example):
  ball_r = 8mm
  cavity_r = ball_r + 0.3 = 8.3mm
  collar_r = ball_r - 1.5 = 6.5mm  (retains ball, allows ±30–40° movement)
  rod_r = 2.5mm
  rod_hole_r = rod_r + 0.3 = 2.8mm
```

**After printing:** Rock ball+rod back and forth to break any thin fused bridges. The clearance gap should free up with light manipulation.

### 2. Pin-Through-Barrel Hinge

```
Barrel (part of leaf A) prints with pin already inside it.
Pin = separate piece, sits on bed.

Print orientation: hinge axis horizontal, pin ends on bed

  Leaf A ┤├ Leaf B
         |
         pin (end sits on bed)

Clearances:
  barrel_bore_r = pin_r + 0.15  (0.15mm radial = 0.3mm on diameter)
  axial_gap = 0.3mm between knuckles

Critical: pin must have a FLAT END that sits on the bed.
A rounded pin end gives too small a contact patch.
```

Both leaf plates MUST be coplanar (same Z) when the hinge is in the open position.

### 3. Peg-in-Socket (Articulated Chain)

- Peg prints on bed as part of one segment
- Socket bore in adjacent segment prints around the peg
- Step/shelf height difference prevents coplanar fusion

### 4. Living Hinge (Single-Piece Flex)

No trapped parts. The hinge IS the thin section.

```
Minimum thickness for FDM living hinge:
  PETG: 0.4–0.6mm (2 perimeters at 0.2mm layer height)
  PLA:  0.6–0.8mm (PLA is brittle — thicker helps)
  TPU:  1.0–1.5mm (much more forgiving)

Print with hinge axis perpendicular to layer lines.
Hinge bends across layers (not with them).
```

### 5. Snap Fit

```
Cantilever snap:
  deflection = snap_height × clearance / arm_length

Arm must be able to deflect to clear the barb, then spring back.
  arm_thickness = 0.8–1.2mm (at 0.4mm nozzle)
  barb_height   = 0.5–1.0mm
  arm_length    = 5–10mm (longer = more flex)

Print perpendicular to layer lines for maximum flex strength.
Layer lines parallel to arm length = splits along layers = weak.
```

Snap-tab barbs must be **wedge-shaped** (45° ramp), not rectangular blocks — rectangular = 90° overhang and fails to print. The same applies to the groove ceiling on the receiving box.

---

## Validation Checklist

Before finalizing any print-in-place design:

1. **Trace the print path:** Starting from the build plate, can you reach every part of the moving piece without passing through the static piece? If no → redesign.
2. **Check all gaps:** Every surface pair between moving and static parts has ≥ 0.3mm clearance. Including at the bottom where the moving part is supported.
3. **Verify retention:** The collar/barb/cap that retains the moving part is smaller than the moving part's widest point.
4. **Check articulation range:** Does the joint have the intended range of motion? Does any geometry collide at the limits of travel?
5. **Verify no coplanar faces:** No two surfaces from different parts sit at exactly the same Z height.
6. **Print orientation:** Which face goes on the bed? Does that orientation create overhangs that need supports? (Supports inside joints will fuse — avoid them.)

---

## Worked recipes

Validated parameter sets for common mechanisms (wheel-on-axle, flip-tile, ball-and-socket, snap fit) are planned for a future release — see the Roadmap in the project README. Until they land, derive parameters from the clearance + retention rules above and verify with `blender_check_clearance_sweep` + `blender_check_retention` before exporting.
