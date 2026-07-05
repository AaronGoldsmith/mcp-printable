# Changelog

All notable changes to `mcp-printable` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Checkpoint save hardened against "Cannot overwrite used library"** —
  `auto_save_checkpoint` now drops any stale `bpy.data.libraries` entry
  pointing at the checkpoint path before saving (covers the case where a
  restore failed mid-way and never unlinked), and writes to a unique temp
  file swapped in via atomic `os.replace` so the checkpoint on disk is never
  half-written and no `.blend1` clutter accumulates. (#17, #22)

## [0.2.3] — 2026-07-03

### Added
- **`blender_boolean` exposes `use_self` / `use_hole_tolerant`** (EXACT solver
  only) — needed for correct results when operands contain self-intersecting
  or overlapping multi-shell geometry. New `ANNIHILATION` warning when the
  result face count collapses by more than 75%, suggesting a `use_self=True`
  retry. (#25, closes #20)
- **Server/addon version mismatch warning** — the addon stamps its version
  into every response envelope; the server warns once per session when it
  differs and a new `blender_version_info` tool reports both versions
  explicitly. (#26, closes #19)

### Fixed
- **`blender_cross_section` camera always faces the exposed cut plane** —
  x/y-axis cuts (and z at `percent > 50`) previously rendered a silhouette
  from the wrong side. (#23, closes #18)
- **`blender_restore_checkpoint` no longer blocks subsequent checkpoint
  saves** — the checkpoint .blend is unlinked as a library after restore, and
  per-view-layer hide state now survives a restore. (#24, closes #17, #22)
- **`blender_check_intersection` no longer misreports flush contact as
  `VOLUMETRIC_OVERLAP`** on heavily coincident-face geometry — suspicious
  exact-boolean volumes are cross-checked with a deterministic Monte Carlo
  estimate, and contact is classified by mean penetration depth. New
  `contact_area_mm2`, `mean_penetration_um`, and `volume_method` result
  fields. (#27, closes #21)

## [0.2.2] — 2026-06-09

### Added
- **`blender_restore_checkpoint` tool** — rolls the scene back to the
  auto-saved checkpoint (written before every `blender_boolean` /
  `blender_execute_code`). Closes the recovery loop opened by the
  `DEGENERATE RESULT` warning: a destroyed mesh is now one call from restored.
  Restores by appending objects from the checkpoint `.blend`, so the addon's
  TCP server and the Blender UI session survive.
- **`blender_check_intersection` classifies contact** — new `contact_type`
  (`NONE` | `SURFACE_CONTACT` | `VOLUMETRIC_OVERLAP`) and `overlap_volume_mm3`
  fields, computed via a throwaway boolean intersect. Face-pair counts alone
  could not distinguish flush-fit assemblies (coincident faces, harmless) from
  real penetration (parts will fuse) — a long-standing false-alarm source when
  verifying joints.

### Changed
- **`scad_cross_section` places the slab on the model's real bounding box.**
  Previously `percent` mapped onto a fixed ±500mm presumed box, so anything
  but ~50% silently missed a normal-sized part and rendered a blank image.
  The code is now compiled to STL first (which also makes module/function/let
  definitions legal at file scope, simplifying the #10 fix), the bbox is read
  with trimesh, and the cut position plus model bounds are reported in the
  tool output. Costs a CGAL compile per call.

## [0.2.1] — 2026-06-09

### Fixed
- **`scad_cross_section` now accepts `module`/`function`/`let` definitions** —
  user code is wrapped in a top-level module (where definitions are legal)
  instead of a bare `intersection()` block, and `use <>`/`include <>` lines are
  hoisted to file scope. On render failure the error now echoes the wrapped
  source with line numbers. (#10)
- **`blender_boolean` reports `ok: false` when the result has 0 faces** with a
  `DEGENERATE RESULT` warning, instead of silently returning success after
  annihilating the mesh. Non-manifold warnings now include sample edge midpoint
  coordinates, and using the same object as target and cutter is rejected with
  a clear error. (#11)
- **`blender_validate` output no longer exceeds MCP token limits on dense
  meshes** — per-face issue lists (`overhangs.face_indices`, `thin_walls.faces`)
  are capped at 10 exemplars (thin walls keep the thinnest ones) unless the new
  `verbose=True` parameter is passed. (#12)
- **Overhang detection math corrected** (#12): the threshold comparison used
  `sin` where the geometry requires `cos` (coincidentally correct only at the
  default 45°), and the reported "worst angle" was the normal's angle rather
  than the surface's — a near-flat ceiling reported as `89.7deg from
  horizontal`, making vertical walls appear flagged. Worst angle now reports
  the surface angle from horizontal (0 = flat ceiling = worst) and summaries
  read `downward, <Ndeg from horizontal`.
- `blender_validate` HEALTH no longer reports a 0-face mesh as watertight/OK —
  empty meshes are flagged `EMPTY MESH` and fail the `ALL` verdict.

## [0.2.0] — 2026-05-26

### Changed
- **Consolidated print-validation tools into a single `blender_validate`.** The
  former `blender_mesh_health`, `blender_check_overhangs`,
  `blender_check_thin_walls`, and `blender_full_printability_check` tools are
  replaced by `blender_validate(checks=[...])`. Use `checks=['ALL']` for the
  full pre-export suite, or scope to `['HEALTH']`, `['OVERHANGS']`,
  `['THIN_WALLS']`, `['CLEARANCE']`. (#6)
- Renamed the internal `_claude_` object/material prefix to `_agent_` for
  generic MCP support; backward-compatible fallbacks retained so objects from
  older versions still resolve. (#6)
- Updated README, AGENTS.md, and the bundled `docs/` MCP resources to reference
  `blender_validate`. (#7)

### Fixed
- Synced the Blender addon's `bl_info["version"]` to the package version (it had
  been stuck at `0.1.0`). Added `scripts/sync_addon_version.py` and a test guard
  (`tests/test_version_sync.py`) to prevent future drift. (#7)

## [0.1.8] — 2026-05-02
### Fixed
- `blender_cross_section` now rejects passing both `object_name` and
  `object_names` instead of silently preferring one. (#5)

## [0.1.7] — 2026-05-02
### Added
- Multi-object `blender_cross_section` for verifying chain/joint assemblies in a
  single cut. (#4)

## [0.1.6] — 2026-05-02
### Fixed
- Corrected `blender_cross_section` cutter placement. (#3)

## [0.1.5] — 2026-04-29
### Changed
- Expanded agent guidance and tightened tool descriptions. (#2)

## [0.1.4]
### Changed
- Multi-client support for the Blender bridge: the MCP server closes its socket
  per command so multiple clients can interleave (e.g. Claude Desktop + Claude
  Code), and the addon backlog grows from `listen(1)` to `listen(8)`. Commands
  still serialize on Blender's main thread.

## [0.1.3]
### Changed
- `scad_compile`: resolve relative paths against the MCP working directory
  (matching `blender_export_stl` behavior).
- Docs: added Requirements section to README and image-capable-model note to
  AGENTS.md.

## [0.1.2]
### Fixed
- Bundle `docs/` in the wheel so `printable://` MCP resources serve real content
  instead of "Not found" stubs on installed copies.

## [0.1.1] — yanked
- Broken MCP resources (`docs/` not included in the wheel). Yanked on PyPI; use
  0.1.2 or later.

## [0.1.0] — yanked
- Initial release. Broken MCP resources (`docs/` not included in the wheel).
  Yanked on PyPI; use 0.1.2 or later.

[Unreleased]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.8...v0.2.0
[0.1.8]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/AaronGoldsmith/mcp-printable/releases/tag/v0.1.0
