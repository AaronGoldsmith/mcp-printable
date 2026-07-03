"""Tests for MCP server tool registration and compliance.

Verifies all tools are registered with correct names, annotations, and schemas.
Does not require Blender.
"""

import pytest
from server import mcp


class TestToolRegistration:
    """Verify tools across both backends are registered with correct MCP metadata."""

    def _get_tool_names(self) -> list[str]:
        """Extract registered tool names from the FastMCP server."""
        # FastMCP stores tools internally; access via the _tool_manager
        tools = mcp._tool_manager._tools
        return sorted(tools.keys())

    def test_all_tools_registered(self):
        """All tools should be registered (Blender + OpenSCAD backends)."""
        names = self._get_tool_names()
        expected = sorted([
            # Blender backend (24)
            "blender_get_scene_info",
            "blender_get_object_info",
            "blender_clear_scene",
            "blender_restore_checkpoint",
            "blender_rename_object",
            "blender_boolean",
            "blender_execute_code",
            "blender_get_screenshot",
            "blender_render_tiled",
            "blender_cross_section",
            "blender_cross_section_gallery",
            "blender_render_printability_heatmap",
            "blender_render_turntable",
            "blender_render_with_dimensions",
            "blender_render_before_after",
            "blender_check_intersection",
            "blender_check_retention",
            "blender_check_clearance",
            "blender_check_clearance_sweep",
            "blender_validate",
            "blender_export_stl",
            "blender_import_stl",
            "blender_save_blend",
            "blender_version_info",
            # OpenSCAD backend (5)
            "scad_compile",
            "scad_render_views",
            "scad_cross_section",
            "scad_validate_printability",
            "scad_import_stl",
        ])
        assert names == expected, f"Missing or extra tools: {set(expected) ^ set(names)}"

    def test_all_tools_have_backend_prefix(self):
        """Every tool name should start with a backend prefix ('blender_' or 'scad_')."""
        valid_prefixes = ("blender_", "scad_")
        for name in self._get_tool_names():
            assert name.startswith(valid_prefixes), (
                f"Tool '{name}' missing backend prefix; expected one of {valid_prefixes}"
            )

    def test_all_tools_have_descriptions(self):
        """Every tool must have a non-empty description."""
        tools = mcp._tool_manager._tools
        for name, tool in tools.items():
            assert tool.description, f"Tool '{name}' has no description"
            assert len(tool.description) > 10, f"Tool '{name}' description too short"

    def test_read_only_tools_annotated(self):
        """Read-only tools should have readOnlyHint=True."""
        read_only_tools = [
            "blender_get_scene_info",
            "blender_get_object_info",
            "blender_get_screenshot",
            "blender_render_tiled",
            "blender_cross_section",
            "blender_cross_section_gallery",
            "blender_render_turntable",
            "blender_render_with_dimensions",
            "blender_check_intersection",
            "blender_check_retention",
            "blender_check_clearance",
            "blender_check_clearance_sweep",
            "blender_validate",
            "blender_version_info",
        ]
        tools = mcp._tool_manager._tools
        for name in read_only_tools:
            tool = tools[name]
            annotations = tool.annotations
            assert annotations is not None, f"Tool '{name}' has no annotations"
            assert annotations.readOnlyHint is True, (
                f"Tool '{name}' should be readOnlyHint=True"
            )

    def test_destructive_tools_annotated(self):
        """Destructive tools should have destructiveHint=True."""
        destructive_tools = [
            "blender_clear_scene",
            "blender_restore_checkpoint",
            "blender_boolean",
            "blender_execute_code",
            "blender_render_before_after",
        ]
        tools = mcp._tool_manager._tools
        for name in destructive_tools:
            tool = tools[name]
            annotations = tool.annotations
            assert annotations is not None, f"Tool '{name}' has no annotations"
            assert annotations.destructiveHint is True, (
                f"Tool '{name}' should be destructiveHint=True"
            )

    def test_no_open_world_tools(self):
        """No tools should have openWorldHint=True (all are local Blender ops)."""
        tools = mcp._tool_manager._tools
        for name, tool in tools.items():
            if tool.annotations:
                assert tool.annotations.openWorldHint is False, (
                    f"Tool '{name}' should be openWorldHint=False (local only)"
                )

    def test_server_name(self):
        """Server name identifies this as the printable Blender backend."""
        assert mcp.name == "printable_blender"


class TestToolSchemas:
    """Verify tool input schemas are correct."""

    def test_execute_code_requires_code(self):
        tools = mcp._tool_manager._tools
        tool = tools["blender_execute_code"]
        schema = tool.parameters
        assert "code" in schema.get("properties", {}), "execute_code missing 'code' param"
        assert "code" in schema.get("required", []), "execute_code 'code' should be required"

    def test_validate_has_defaults(self):
        tools = mcp._tool_manager._tools
        tool = tools["blender_validate"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "object_name" in props
        assert "overhang_angle" in props
        # overhang_angle should have a default
        assert props["overhang_angle"].get("default") == 45.0, "overhang_angle should have default=45.0"
        assert props["min_wall_mm"].get("default") == 0.8, "min_wall_mm should have default=0.8"

    def test_cross_section_has_axis_param(self):
        tools = mcp._tool_manager._tools
        tool = tools["blender_cross_section"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "axis" in props
        assert "percent" in props
        assert "object_name" in props

    def test_boolean_exact_solver_params(self):
        tools = mcp._tool_manager._tools
        tool = tools["blender_boolean"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "use_self" in props
        assert props["use_self"].get("default") is False, "use_self should default to False"
        assert "use_hole_tolerant" in props
        assert props["use_hole_tolerant"].get("default") is False, "use_hole_tolerant should default to False"

    def test_clearance_sweep_params(self):
        tools = mcp._tool_manager._tools
        tool = tools["blender_check_clearance_sweep"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "inner_object" in props
        assert "outer_object" in props
        assert "axis" in props
        assert "steps" in props
