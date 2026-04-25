# Print-in-Place Flip Tile (Closed Bore)

Validated recipe for tic-tac-toe-style flip tiles that pivot inside closed bores. No pins, no sockets — tiles are retained by solid bore end walls.

## Mechanism

Bore is just long enough to contain the tile with clearance on each end. Tile rotates about the bore axis to flip between two engraved faces (e.g. X / O). Adjacent cells share inter-bore walls.

```
top view (X,Y plane), single cell:

       bore end wall    bore end wall
       │                              │
       │   ┌──────────────────────┐   │
       │   │       TILE           │   │   ← tile rotates around X axis
       │   └──────────────────────┘   │
       │                              │

      X clearance: 0.4 mm each side (tile end ↔ end wall)
      radial clearance: 0.43 mm (tile corner ↔ bore wall)
```

## Why an earlier "cone-pin pivot" version failed

Cone-pin pivots required sockets in adjacent cell walls. With socket depth 4 mm × 2 sides and only 2 mm of inter-bore wall, sockets overlapped by 6 mm. The closed-bore design eliminates sockets entirely and gives 9.2 mm of solid wall between bores.

## Validated Parameters (single cell)

| Parameter | Value |
|---|---|
| tile face | 22 × 22 mm (X × Y) |
| tile thickness | 2.5 mm |
| bore radius | 11.5 mm (tile diagonal 11.07 + 0.43 clearance) |
| bore length | 22.8 mm (tile_x + 0.4 each end) |
| bore axis Z | board_face_z + pivot_depth = -8 + 1.55 = -6.45 mm |
| tile center Z | bore_axis_z - 0.3 = -6.75 mm |
| radial clearance (tile corner → bore wall) | 0.43 mm — passes >0.3 |
| X clearance (tile end → end wall) | 0.40 mm — passes >0.3 |
| inter-bore wall (cell pitch − bore length) | 32 − 22.8 = 9.2 mm |

## Engravings (cut from tile faces)

- **X face** (toward board front, Z=-8 mm): two rectangular bars at ±45°, 13 × 2.5 × 0.6 mm, cutter center at Z=-7.7 mm
- **O face** (toward board back, Z=-5.5 mm): annulus, outer_r=7.5 mm / inner_r=5 mm / depth=0.6 mm, cutter center at Z=-5.8 mm

## Board geometry

- Board: 106 × 106 × 16 mm, face at Z=-8, back at Z=+8
- Cell layout: 3 × 3 grid, cell pitch 32 mm, offsets [-32, 0, +32] in X and Y
- Bore axes aligned along X (tiles flip around X)
- Slot at board face: bore circle (r=11.5, axis at Z=-6.45) cuts a chord 2·sqrt(11.5² - 1.55²) = 22.79 mm wide. Tile face (22 mm) fits with 0.40 mm clearance each side.

## Print orientation

- **Print face DOWN** (Z=-8 surface on bed) — tiles print inside bores, face flush with board face
- Bore upper arc (top at Z=+5.05 mm) bridges 23 mm — within FDM bridging capability
- No supports needed with good bridge settings
- After printing: flex/twist tiles to break the 0.4 mm fusion gap

## Build notes

- Each bore drilled individually (not combined) — avoids coplanar boolean failures
- 9 bore cutters can be applied as a single batch modifier stack
- Manifold/EXACT solver works; FAST solver removed in Blender 5.x
