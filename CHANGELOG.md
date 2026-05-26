# Changelog

All notable changes to `mcp-printable` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/AaronGoldsmith/mcp-printable/compare/v0.2.0...HEAD
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
