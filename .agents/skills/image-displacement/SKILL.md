---
name: image-displacement
description: Apply image-based displacement to a Blender primitive to create raised, printable texture while preserving primitive shape and a flat base. Use when the user wants to turn a 2D image (texture, pattern, artwork) into 3D relief on a cube/plate/other primitive.
---

This skill is a Codex-specific shim. The full guidance is in [`docs/blender/image-displacement.md`](../../../docs/blender/image-displacement.md).

Pipeline: PIL preprocessing (blur, posterize, histogram stretch, gamma) → Blender primitive with subsurf → vertex group with smoothstep edge falloff → Displace modifier with `mid_level=0` and strength ~6% of object size.

For batch runs over many source images, split parameters into a `DEFAULTS` dict plus sparse `OVERRIDES` keyed by image index or filename — keeps per-image tuning visible in one place. See the doc for the full rationale.
